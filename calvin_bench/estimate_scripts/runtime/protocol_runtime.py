import base64
import hashlib
import io
import json
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from .scenario_loader import EventSpec


SENSOR_CHANNELS = [
    "rgb_static",
    "rgb_gripper",
    "rgb_tactile",
    "depth_static",
    "depth_gripper",
    "depth_tactile",
    "robot_obs",
    "scene_obs",
]


def active_events_at_step(events: List[EventSpec], step_idx: int, subtask_idx: int) -> List[EventSpec]:
    active: List[EventSpec] = []
    for event in events:
        if event.start_step <= step_idx <= event.end_step:
            if event.subtask_idx in (0, subtask_idx):
                active.append(event)
    return active


def flatten_obs_channels(obs: Dict[str, Any]) -> Dict[str, Any]:
    channels: Dict[str, Any] = {}
    rgb_obs = obs.get("rgb_obs", {})
    depth_obs = obs.get("depth_obs", {})
    if isinstance(rgb_obs, dict):
        channels.update(rgb_obs)
    if isinstance(depth_obs, dict):
        channels.update(depth_obs)
    if "robot_obs" in obs:
        channels["robot_obs"] = obs["robot_obs"]
    if "scene_obs" in obs:
        channels["scene_obs"] = obs["scene_obs"]
    return channels


def _mask_to_modalities(mask: Dict[str, int]) -> List[str]:
    modalities: List[str] = []
    if any(mask.get(ch, 0) for ch in ("rgb_static", "rgb_gripper", "rgb_tactile")):
        modalities.append("rgb")
    if any(mask.get(ch, 0) for ch in ("depth_static", "depth_gripper", "depth_tactile")):
        modalities.append("depth")
    if mask.get("rgb_tactile", 0) or mask.get("depth_tactile", 0):
        modalities.append("tactile")
    if mask.get("robot_obs", 0) or mask.get("scene_obs", 0):
        modalities.append("state")
    return modalities


def _drop_group_channels(group: str) -> List[str]:
    group = group.strip().lower()
    if group == "rgb":
        return ["rgb_static", "rgb_gripper", "rgb_tactile"]
    if group == "depth":
        return ["depth_static", "depth_gripper", "depth_tactile"]
    if group == "state":
        return ["robot_obs", "scene_obs"]
    if group == "tactile":
        return ["rgb_tactile", "depth_tactile"]
    return []


def _noise_std(profile_id: str, channel: str) -> float:
    profile = profile_id.strip().lower()
    sigma = 0.03
    if "sigma_" in profile:
        try:
            sigma = float(profile.split("sigma_")[-1].split("_")[0])
        except Exception:
            sigma = 0.03
    if channel.startswith("rgb_"):
        return sigma * 255.0
    return sigma


def _apply_noise(
    array: np.ndarray,
    channel: str,
    profile_id: str,
    episode_id: str,
    event_id: str,
    step_idx: int,
) -> np.ndarray:
    seed_text = f"{episode_id}:{event_id}:{step_idx}:{channel}:{profile_id}".encode("utf-8")
    seed = int(hashlib.sha256(seed_text).hexdigest()[:16], 16) % (2**32)
    rng = np.random.default_rng(seed)
    arr = np.asarray(array)
    std = _noise_std(profile_id, channel)
    if arr.dtype == np.uint8:
        noisy = np.clip(arr.astype(np.float32) + rng.normal(0.0, std, size=arr.shape), 0.0, 255.0).astype(np.uint8)
        return noisy
    noisy = arr.astype(np.float32) + rng.normal(0.0, std, size=arr.shape).astype(np.float32)
    return noisy.astype(arr.dtype if np.issubdtype(arr.dtype, np.floating) else np.float32)


def apply_protocol_to_observation(
    raw_obs: Dict[str, Any],
    base_sensor_mask: Dict[str, int],
    active_events: List[EventSpec],
    episode_id: str,
    step_idx: int,
) -> Dict[str, Any]:
    channels = flatten_obs_channels(raw_obs)
    before_mask = dict(base_sensor_mask)
    after_mask = dict(base_sensor_mask)
    noise_targets: Dict[str, str] = {}

    for event in active_events:
        payload = event.payload
        if event.event_type == "sensor_dropout_partial":
            channel = str(payload.get("drop_channel", "")).strip()
            if channel:
                after_mask[channel] = 0
        elif event.event_type == "sensor_dropout_whole_modality":
            drop_channels = payload.get("drop_channels")
            if isinstance(drop_channels, list):
                for ch in drop_channels:
                    after_mask[str(ch)] = 0
            else:
                group = str(payload.get("drop_group", "")).strip()
                for ch in _drop_group_channels(group):
                    after_mask[ch] = 0
        elif event.event_type == "sensor_blackout":
            targets = payload.get("target_channels", [])
            if isinstance(targets, list):
                for ch in targets:
                    after_mask[str(ch)] = 0
            elif str(targets).strip().lower() == "all":
                for ch in SENSOR_CHANNELS:
                    after_mask[ch] = 0
        elif event.event_type == "sensor_noise":
            profile_id = str(payload.get("noise_profile", "gaussian_sigma_0.03"))
            targets = payload.get("target_channels", [])
            if isinstance(targets, list):
                for ch in targets:
                    noise_targets[str(ch)] = profile_id
        elif event.event_type == "mixed_noise":
            profile_id = str(payload.get("noise_profile", "gaussian_sigma_0.03"))
            for ch in SENSOR_CHANNELS:
                if before_mask.get(ch, 0) == 1:
                    noise_targets[ch] = profile_id

    filtered: Dict[str, Any] = {}
    noise_applied = False
    noise_profile = "none"
    for channel, value in channels.items():
        if after_mask.get(channel, 0) != 1:
            continue
        array = np.asarray(value)
        if channel in noise_targets:
            profile_id = noise_targets[channel]
            array = _apply_noise(
                array=array,
                channel=channel,
                profile_id=profile_id,
                episode_id=episode_id,
                event_id="noise",
                step_idx=step_idx,
            )
            noise_applied = True
            noise_profile = profile_id
        filtered[channel] = array

    dropped_modalities = []
    for channel, is_on in before_mask.items():
        if is_on == 1 and after_mask.get(channel, 0) == 0:
            dropped_modalities.append(channel)

    return {
        "channels_filtered": filtered,
        "sensor_mask_before": before_mask,
        "sensor_mask_after": after_mask,
        "active_modalities": _mask_to_modalities(after_mask),
        "dropped_modalities": dropped_modalities,
        "noise_applied_flag": noise_applied,
        "noise_profile": noise_profile,
    }


def _encode_rgb_png(image: np.ndarray) -> Dict[str, Any]:
    ok, buff = cv2.imencode(".png", image)
    if not ok:
        return {"encoding": "png_base64", "shape": list(image.shape), "dtype": str(image.dtype), "data": ""}
    payload = base64.b64encode(buff.tobytes()).decode("ascii")
    return {
        "encoding": "png_base64",
        "shape": list(image.shape),
        "dtype": str(image.dtype),
        "data": payload,
    }


def _encode_npy_base64(array: np.ndarray) -> Dict[str, Any]:
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=False)
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {
        "encoding": "npy_base64",
        "shape": list(array.shape),
        "dtype": str(array.dtype),
        "data": payload,
    }


def build_model_observation_bundle(channels_filtered: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    encoded_channels: Dict[str, Any] = {}
    raw_channels: Dict[str, Any] = {}
    summary: Dict[str, Any] = {}
    for channel, value in channels_filtered.items():
        arr = np.asarray(value)
        raw_channels[channel] = arr
        if channel.startswith("rgb_"):
            encoded_channels[channel] = _encode_rgb_png(arr)
        elif channel.startswith("depth_"):
            encoded_channels[channel] = _encode_npy_base64(arr)
        else:
            encoded_channels[channel] = {
                "encoding": "float_list",
                "shape": list(arr.shape),
                "dtype": str(arr.dtype),
                "data": arr.astype(np.float32).tolist(),
            }
        summary[channel] = {
            "shape": list(arr.shape),
            "dtype": str(arr.dtype),
            "min": float(np.min(arr)) if arr.size else 0.0,
            "max": float(np.max(arr)) if arr.size else 0.0,
        }
    return (
        {"channels": encoded_channels},
        {"channels": raw_channels},
        {"channels": summary},
    )


def _coerce_numeric_vector(action: Any) -> List[float] | None:
    if isinstance(action, str):
        text = action.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [float(x) for x in parsed]
        except Exception:
            return None
        return None
    if isinstance(action, np.ndarray):
        return [float(x) for x in action.reshape(-1).tolist()]
    if isinstance(action, (list, tuple)):
        try:
            return [float(x) for x in action]
        except Exception:
            return None
    return None


def canonicalize_model_action(action_level_id: str, action_value: Any) -> Tuple[bool, Any, str]:
    level = action_level_id.strip().upper()
    if level == "L1":
        if not isinstance(action_value, str):
            if action_value is None:
                return False, "", "invalid_action_format"
            action_value = str(action_value)
        value = action_value.strip()
        if not value:
            return False, "", "empty_action"
        return True, value, ""

    vector = _coerce_numeric_vector(action_value)
    if vector is None:
        return False, [], "invalid_action_format"
    if level in {"L2", "L3"} and len(vector) == 7:
        vector[-1] = 1.0 if vector[-1] > 0 else -1.0
        return True, vector, ""
    if level == "L4" and len(vector) == 8:
        vector[-1] = 1.0 if vector[-1] > 0 else -1.0
        return True, vector, ""
    return False, vector, "invalid_action_format"


def action_to_env_command(action_level_id: str, action_value: Any) -> Tuple[bool, Any, str]:
    level = action_level_id.strip().upper()
    if level == "L3":
        vector = np.asarray(action_value, dtype=np.float32)
        return True, vector, ""
    if level == "L2":
        vector = np.asarray(action_value, dtype=np.float32)
        pos = vector[:3]
        orn = vector[3:6]
        gripper = np.asarray([1.0 if vector[6] > 0 else -1.0], dtype=np.float32)
        return True, [pos, orn, gripper], ""
    if level == "L4":
        vector = np.asarray(action_value, dtype=np.float32)
        pos = vector[:3]
        orn = vector[3:7]
        gripper = np.asarray([1.0 if vector[7] > 0 else -1.0], dtype=np.float32)
        return True, [pos, orn, gripper], ""
    if level == "L1":
        return False, None, "unsupported_model_interface"
    return False, None, "action_level_mismatch"


def deterministic_wrong_action(
    action_level_id: str,
    episode_id: str,
    step_idx: int,
    target_subtask: str,
    subtask_candidates: List[str],
) -> Any:
    token = f"{episode_id}:{step_idx}:{target_subtask}:{action_level_id}".encode("utf-8")
    seed = int(hashlib.sha256(token).hexdigest()[:16], 16) % (2**32)
    rng = np.random.default_rng(seed)
    level = action_level_id.strip().upper()
    if level == "L1":
        candidates = [s for s in subtask_candidates if s != target_subtask]
        if not candidates:
            return target_subtask
        idx = int(rng.integers(0, len(candidates)))
        return candidates[idx]
    if level in {"L2", "L3"}:
        vec = rng.uniform(low=-1.0, high=1.0, size=(7,)).astype(np.float32)
        vec[-1] = float(1 if vec[-1] > 0 else -1)
        return vec.tolist()
    if level == "L4":
        vec = rng.uniform(low=-1.0, high=1.0, size=(8,)).astype(np.float32)
        vec[-1] = float(1 if vec[-1] > 0 else -1)
        return vec.tolist()
    return ""

