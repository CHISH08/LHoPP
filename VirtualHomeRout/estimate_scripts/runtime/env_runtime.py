import base64
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from unity_scripts.env_pool import EnvSlot


ACTION_PATTERN = re.compile(r"^(<char\d+\s*\[|\[)")
SCENE_PATTERN = re.compile(r"TrimmedTestScene(\d+)_graph", re.IGNORECASE)
CHAR_PREFIX_PATTERN = re.compile(r"^\s*<char\d+>\s*", re.IGNORECASE)
SPACE_PATTERN = re.compile(r"\s+")
BRACKET_ACTION_PATTERN = re.compile(r"\[(.*?)\]")


@dataclass(frozen=True)
class TaskDefinition:
    task_id: str
    title: str
    actions: List[str]
    scene_idx: int


@dataclass(frozen=True)
class EventSpec:
    episode_id: str
    event_id: str
    event_type: str
    event_source: str
    start_step: int
    end_step: int
    payload: Dict[str, Any]


def parse_task_file(task_id: str, task_path: Path) -> TaskDefinition:
    lines = task_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    stripped = [x.strip() for x in lines]
    title = stripped[0] if stripped else task_path.name
    actions = [x for x in stripped if x and ACTION_PATTERN.match(x)]
    scene_idx = scene_index_from_task(task_id)
    return TaskDefinition(task_id=task_id, title=title, actions=actions, scene_idx=scene_idx)


def scene_index_from_task(task_id: str) -> int:
    m = SCENE_PATTERN.search(task_id)
    if not m:
        return 0
    return max(0, int(m.group(1)) - 1)


def normalize_action(action: str) -> str:
    if not action:
        return ""
    action = CHAR_PREFIX_PATTERN.sub("", action.strip())
    action = SPACE_PATTERN.sub(" ", action).strip()

    def _upper_brackets(match: re.Match) -> str:
        return f"[{match.group(1).strip().upper()}]"

    return BRACKET_ACTION_PATTERN.sub(_upper_brackets, action)


def active_events_at_step(events: List[EventSpec], step_idx: int) -> List[EventSpec]:
    return [x for x in events if x.start_step <= step_idx <= x.end_step]


def bootstrap_slot(slot: EnvSlot, scene_idx: int) -> Tuple[bool, str]:
    if slot.comm is None:
        return False, "slot comm is not initialized"
    try:
        if not slot.comm.check_connection():
            return False, "check_connection returned false"
        if not slot.comm.reset(scene_idx):
            return False, f"reset({scene_idx}) returned false"
        if not slot.comm.add_character():
            return False, "add_character returned false"
        return True, ""
    except Exception as exc:
        return False, str(exc)


def collect_graph(comm) -> Tuple[bool, Dict[str, Any], str]:
    try:
        ok, graph = comm.environment_graph()
        if ok and isinstance(graph, dict):
            return True, graph, ""
        return False, {}, "environment_graph invalid"
    except Exception as exc:
        return False, {}, str(exc)


def build_action_pool(task_actions: List[str], graph: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    seen = set()

    def add(action: str) -> None:
        a = action.strip()
        if not a:
            return
        n = normalize_action(a)
        if n in seen:
            return
        seen.add(n)
        out.append(a)

    for action in task_actions:
        add(action)
        add(f"<char0> {action}")

    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    verbs = ["Walk", "Find", "Open", "Close", "Grab", "SwitchOn", "SwitchOff"]
    objects: List[Tuple[str, int]] = []
    for node in nodes:
        class_name = node.get("class_name")
        node_id = node.get("id")
        if isinstance(class_name, str) and isinstance(node_id, int) and class_name != "character":
            objects.append((class_name, node_id))
    for class_name, node_id in sorted(objects, key=lambda x: (x[0], x[1]))[:24]:
        for verb in verbs:
            add(f"<char0> [{verb}] <{class_name}> ({node_id})")
    return out


def build_action_mask(
    scenario_level: str,
    action_pool: List[str],
    progress_idx: int,
    active_events: List[EventSpec],
) -> Tuple[List[str], int]:
    total = len(action_pool)
    prioritized: List[str] = []
    idxs = [progress_idx, progress_idx + 1, progress_idx - 1]
    for idx in idxs:
        if 0 <= idx < len(action_pool):
            prioritized.append(action_pool[idx])
    for action in action_pool:
        if action not in prioritized:
            prioritized.append(action)

    if scenario_level == "L3":
        strict_limit = 3
        has_distractors = any(
            ev.event_type == "action_mask_change" and bool(ev.payload.get("distractor_actions"))
            for ev in active_events
        )
        if has_distractors:
            strict_limit = 8
        allowed = prioritized[:strict_limit]
    elif scenario_level == "L2":
        allowed = prioritized[:12]
    elif scenario_level == "L5":
        allowed = prioritized[:18]
    else:
        allowed = prioritized[:16]
    return allowed, total


def validate_action(proposed: str, allowed_mask: List[str]) -> Tuple[bool, str]:
    if not proposed:
        return False, "empty_action"
    if not ACTION_PATTERN.match(proposed) and not CHAR_PREFIX_PATTERN.match(proposed):
        return False, "invalid_action_format"
    allowed_norm = {normalize_action(x) for x in allowed_mask}
    if normalize_action(proposed) not in allowed_norm:
        return False, "action_not_allowed"
    return True, ""


def execute_action(comm, action_exec: str, time_scale: float, skip_animation: bool) -> Tuple[bool, Any, float]:
    started = time.perf_counter()
    try:
        success, message = comm.render_script(
            [action_exec],
            find_solution=True,
            recording=False,
            image_synthesis=[],
            skip_animation=skip_animation,
            time_scale=time_scale,
            processing_time_limit=20,
        )
        return bool(success), message, time.perf_counter() - started
    except Exception as exc:
        return False, str(exc), time.perf_counter() - started


def choose_injected_action(
    allowed_mask: List[str],
    action_pool: List[str],
    model_action_exec: str,
    episode_id: str,
    step_idx: int,
    event_id: str,
) -> str:
    model_norm = normalize_action(model_action_exec)
    candidates = [x for x in allowed_mask if normalize_action(x) != model_norm]
    if not candidates:
        candidates = [x for x in action_pool if normalize_action(x) != model_norm]
    if not candidates:
        return ""
    raw = f"{episode_id}:{step_idx}:{event_id}:inject".encode("utf-8")
    idx = int(hashlib.sha256(raw).hexdigest()[:8], 16) % len(candidates)
    return candidates[idx]


def _b64_png(image: np.ndarray) -> str:
    ok, buff = cv2.imencode(".png", image)
    if not ok:
        return ""
    return base64.b64encode(buff.tobytes()).decode("ascii")


def _noise_seed(episode_id: str, step_idx: int, event_id: str) -> int:
    raw = f"{episode_id}:{step_idx}:{event_id}:sensor_noise".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16)


def _apply_noise(image: np.ndarray, episode_id: str, step_idx: int, event_id: str) -> np.ndarray:
    seed = _noise_seed(episode_id, step_idx, event_id)
    rng = np.random.default_rng(seed)
    arr = image.astype(np.float32)
    arr = np.clip(arr + rng.normal(0, 6.0, size=arr.shape), 0, 255)
    return arr.astype(np.uint8)


def _camera_subset(total: int, episode_id: str, condition_id: str) -> List[int]:
    if total <= 0:
        return []
    count = max(1, total // 2)
    base = list(range(total))
    seed = int(hashlib.sha256(f"{episode_id}:{condition_id}:subset".encode("utf-8")).hexdigest()[:8], 16)
    offset = seed % total
    rotated = base[offset:] + base[:offset]
    return sorted(rotated[:count])


def collect_observation(
    comm,
    episode_id: str,
    condition_id: str,
    active_modalities: List[str],
    active_events: List[EventSpec],
    step_idx: int,
    frame_camera_index: int,
    frame_mode: str,
    image_width: int,
    image_height: int,
    history_events: List[str],
) -> Tuple[Dict[str, Any], List[str], Optional[np.ndarray], str]:
    modalities = list(active_modalities)
    obs: Dict[str, Any] = {}
    frame: Optional[np.ndarray] = None

    ok_graph, graph, graph_err = collect_graph(comm)
    if not ok_graph:
        return {}, [], None, graph_err

    blackout_targets = []
    noise_events = []
    for ev in active_events:
        if ev.event_type == "sensor_blackout":
            blackout_targets.append(str(ev.payload.get("target", "")).lower())
        elif ev.event_type == "sensor_noise":
            noise_events.append(ev)

    if "all_sensors" in blackout_targets:
        modalities = [x for x in modalities if x not in ("graph", "camera")]

    if "graph" in modalities:
        obs["graph"] = graph

    if "camera" in modalities:
        ok_count, camera_count = comm.camera_count()
        if not ok_count or not isinstance(camera_count, int):
            return {}, [], None, "camera_count unavailable"
        allowed_ids = list(range(max(0, camera_count)))
        if "camera_subset" in blackout_targets:
            allowed_ids = _camera_subset(camera_count, episode_id, condition_id)
        if "all_cameras" in blackout_targets:
            allowed_ids = []

        cam_idx = frame_camera_index if frame_camera_index in allowed_ids else (allowed_ids[0] if allowed_ids else -1)
        cameras = []
        if cam_idx >= 0:
            ok_img, images = comm.camera_image([cam_idx], mode=frame_mode, image_width=image_width, image_height=image_height)
            if not ok_img or not isinstance(images, list) or len(images) == 0:
                return {}, [], None, "camera_image unavailable"
            image = images[0]
            for ev in noise_events:
                image = _apply_noise(image=image, episode_id=episode_id, step_idx=step_idx, event_id=ev.event_id)
            frame = image
            cameras.append(
                {
                    "camera_index": cam_idx,
                    "mode": frame_mode,
                    "shape": list(image.shape),
                    "image_b64_png": _b64_png(image),
                }
            )
        obs["camera"] = cameras

    if "state" in modalities:
        obs["state"] = {
            "graph_nodes": len(graph.get("nodes", [])),
            "graph_edges": len(graph.get("edges", [])),
            "history_events_size": len(history_events),
        }

    obs["history"] = {"events": history_events[-20:]}
    return obs, modalities, frame, ""


def collect_debug_frame(
    comm,
    frame_camera_index: int,
    frame_mode: str,
    image_width: int,
    image_height: int,
) -> Tuple[Optional[np.ndarray], str]:
    try:
        ok_count, camera_count = comm.camera_count()
        if not ok_count or not isinstance(camera_count, int):
            return None, "camera_count unavailable"
        if camera_count <= 0:
            return None, "no_cameras_available"
        cam_idx = frame_camera_index if 0 <= frame_camera_index < camera_count else 0
        ok_img, images = comm.camera_image([cam_idx], mode=frame_mode, image_width=image_width, image_height=image_height)
        if not ok_img or not isinstance(images, list) or len(images) == 0:
            return None, "camera_image unavailable"
        return images[0], ""
    except Exception as exc:
        return None, str(exc)
