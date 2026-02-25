import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import Any, Dict, List, Optional, Tuple

import requests

ACTION_RE = re.compile(r"\[(?P<verb>[^\]]+)\]\s*(?:<(?P<obj>[^>]+)>)?")
PAREN_CONTENT_RE = re.compile(r"\(([^)]*)\)")


# =========================
# Configs / Specs
# =========================

@dataclass
class ScenarioConfig:
    name: str
    noise_level: float = 0.0
    action_dropout_prob: float = 0.0
    collision_prob: float = 0.0
    chunk_size: int = 3
    allow_disable_protected: bool = False
    critical_dropout_prob: float = 0.0
    sensor_dropout_prob: float = 0.0


@dataclass
class RunConfig:
    dataset_root: str

    controller: str = "local"   # local|http
    url: str = ""               # used if controller=http
    arch: str = "reactive"      # reactive|planner|hierarchical|hybrid
    method_name: str = "model"
    run_name: Optional[str] = None

    # metrics mode:
    # online  -> OFFLINE metrics only (no Unity)
    # offline -> ONLINE(Unity) metrics
    # all     -> both
    metrics_mode: str = "all"

    # tasks
    max_tasks: int = 50
    max_easy: int = 5
    max_medium: int = 5
    max_hard: int = 5
    repeats_per_task: int = 3
    ideal_first_run: bool = True

    # parallelism
    parallel_workers: int = 1
    env_slots: int = 1  # number of Unity instances when Unity enabled
    allow_shared_env_slots: bool = False

    # episode limits
    max_steps_multiplier: float = 2.0
    seed: int = 42
    timeout_sec: int = 60

    # outputs
    benchmarks_root: str = "benchmarks/virtualhome"
    out_json: Optional[str] = None
    out_telemetry_jsonl: Optional[str] = None
    save_traces: bool = True
    save_example_videos: bool = True
    video_examples_per_difficulty: int = 1
    video_frame_rate: int = 5
    video_image_width: int = 640
    video_image_height: int = 480
    video_time_scale: float = 4.0
    video_skip_animation: bool = False
    video_http_timeout_sec: int = 120
    video_processing_time_limit_sec: int = 60
    strict_action_whitelist: bool = True
    strict_unity_task_filter: bool = True

    # Unity runtime (used only when metrics_mode in {offline,all})
    unity_url: str = "127.0.0.1"
    unity_ports: Tuple[str, ...] = tuple()  # AUTO-assigned
    unity_executable: Optional[str] = None

    unity_start_timeout_sec: int = 90
    unity_step_time_limit: int = 60
    unity_step_skip_animation: bool = True
    unity_find_solution: bool = True
    unity_preflight_scene_id: int = 0
    unity_time_scale: float = 8.0
    unity_episode_time_limit_sec: int = 180
    unity_max_no_progress_steps: int = 25

    unity_fast: bool = True
    unity_headless: bool = False          # False -> show windows (NO -batchmode)
    unity_log_mode: str = "files"         # files|none
    keep_unity_alive: bool = True

    protected_actions: Tuple[str, ...] = (
        "walk", "find", "grab", "open", "close", "putin", "putback", "switchon", "switchoff",
    )


@dataclass
class Task:
    task_id: str
    title: str
    description: str
    actions: List[str]
    difficulty: str


@dataclass
class EpisodeSpec:
    task: Task
    scenario: ScenarioConfig
    repeat_idx: int
    seed: int
    is_ideal_run: bool
    env_slot: int


# =========================
# Logging
# =========================

class RunLogger:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        with self._lock:
            print(line)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


# =========================
# Ports: auto-pick (no reservation)
# =========================

def pick_free_ports(host: str, n: int) -> Tuple[str, ...]:
    ports: List[int] = []
    used = set()
    for _ in range(n):
        while True:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind((host, 0))
            port = int(s.getsockname()[1])
            s.close()
            if port not in used:
                used.add(port)
                ports.append(port)
                break
    return tuple(str(p) for p in ports)


def pick_one_free_port(host: str, used: set) -> str:
    while True:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((host, 0))
        port = int(s.getsockname()[1])
        s.close()
        if port not in used:
            used.add(port)
            return str(port)


# =========================
# Policies: 4 arch via one interface (stubbed)
# =========================

class PolicyBase:
    def __init__(self, seed: int, arch: str):
        self.seed = seed
        self.arch = arch

    def act(self, telemetry: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class RandomPolicy(PolicyBase):
    def _deterministic_index(self, telemetry: Dict[str, Any], n: int) -> int:
        t = telemetry.get("task", {})
        runtime = telemetry.get("runtime", {})
        key = (
            f"{self.seed}::{self.arch}::{t.get('id','')}::{telemetry.get('scenario',{}).get('name','')}::"
            f"{telemetry.get('repeat_idx',0)}::{telemetry.get('step',0)}::{runtime.get('env_slot',0)}"
        ).encode("utf-8")
        return int(hashlib.sha256(key).hexdigest()[:12], 16) % max(1, n)

    def act(self, telemetry: Dict[str, Any]) -> Dict[str, Any]:
        obs = telemetry.get("observation", {})
        allowed = obs.get("allowed_actions", []) or obs.get("available_actions", [])
        disabled = set(obs.get("disabled_actions", []))
        candidates = [a for a in allowed if extract_verb_obj(a)[0] not in disabled]
        if not candidates:
            action = "TASK_UNFEASIBLE"
        else:
            action = candidates[self._deterministic_index(telemetry, len(candidates))]
        return {"action": action, "arch": self.arch, "controller": "local_random"}


class LocalPolicyClient:
    def __init__(self, policy: PolicyBase):
        self.policy = policy

    def act(self, telemetry: Dict[str, Any]) -> Tuple[str, float, Optional[str], Dict[str, Any]]:
        t0 = time.perf_counter()
        try:
            out = self.policy.act(telemetry) or {}
            latency = time.perf_counter() - t0
            action = out.get("action", "")
            if not action:
                return "", latency, "empty_action", out
            return str(action), latency, None, out
        except Exception as e:
            latency = time.perf_counter() - t0
            return "", latency, str(e), {}


class HttpPolicyClient:
    def __init__(self, url: str, timeout_sec: int = 60, route_by_env_slot: bool = True):
        self.url = url
        self.timeout_sec = timeout_sec
        self.route_by_env_slot = route_by_env_slot

    def _endpoint_for_slot(self, env_slot: int) -> str:
        if "{env_slot}" in self.url:
            return self.url.format(env_slot=env_slot)
        if self.route_by_env_slot:
            sep = "&" if "?" in self.url else "?"
            return f"{self.url}{sep}env_slot={env_slot}"
        return self.url

    def act(self, telemetry: Dict[str, Any]) -> Tuple[str, float, Optional[str], Dict[str, Any]]:
        env_slot = int(telemetry.get("runtime", {}).get("env_slot", 0))
        endpoint = self._endpoint_for_slot(env_slot)
        t0 = time.perf_counter()
        try:
            r = requests.post(
                endpoint,
                json=telemetry,
                timeout=self.timeout_sec,
                headers={"X-Env-Slot": str(env_slot)},
            )
            latency = time.perf_counter() - t0
            r.raise_for_status()
            data = r.json()
            action = data.get("action")
            if not action:
                return "", latency, "empty_action", data
            return str(action), latency, None, data
        except Exception as e:
            latency = time.perf_counter() - t0
            return "", latency, str(e), {}


# =========================
# Unity runtime: custom launcher (NO UnityLauncher from VH)
# =========================

def ensure_simulation_import_path() -> Path:
    sim_path = Path("virtualhome/virtualhome/simulation").resolve()
    if str(sim_path) not in sys.path:
        sys.path.append(str(sim_path))
    return sim_path


def unity_idle_ok(url: str, port: str, timeout_sec: float = 2.0) -> bool:
    address = f"http://{url}:{port}"
    try:
        r = requests.post(address, json={"id": str(time.time()), "action": "idle"}, timeout=timeout_sec)
        if r.status_code != 200:
            return False
        data = r.json()
        return bool(data.get("success", False))
    except Exception:
        return False


def wait_for_unity(url: str, port: str, total_timeout_sec: int, logger: RunLogger, slot: int) -> bool:
    deadline = time.time() + max(1, total_timeout_sec)
    while time.time() < deadline:
        if unity_idle_ok(url, port, timeout_sec=2.0):
            return True
        time.sleep(0.5)
    logger.log(f"unity_wait_timeout slot={slot} port={port} timeout_sec={total_timeout_sec}")
    return False


def launch_virtualhome_exe(
    exe_path: Path,
    port: str,
    log_dir: Path,
    headless: bool,
    log_mode: str,
) -> subprocess.Popen:
    exe_path = exe_path.resolve()
    exe_dir = exe_path.parent
    log_dir = log_dir.resolve()

    args: List[str] = [str(exe_path)]

    # Local recommended windowed flags (from VH README) :contentReference[oaicite:3]{index=3}
    args += ["-screen-fullscreen", "0", "-screen-quality", "4"]

    if headless:
        args += ["-batchmode", "-nographics"]

    # VH uses -http-port=PORT (see their own launcher) :contentReference[oaicite:4]{index=4}
    args += [f"-http-port={port}"]

    stdout_target = subprocess.DEVNULL
    stderr_target = subprocess.DEVNULL

    if log_mode == "files":
        log_dir.mkdir(parents=True, exist_ok=True)
        player_log = (log_dir / f"Player_{port}.log").resolve()
        # Unity -logFile takes a path argument :contentReference[oaicite:5]{index=5}
        # Unity on Windows can misparse backslashes in command-line args (\v, \u, ...).
        # Pass a normalized absolute path with forward slashes.
        args += ["-logFile", player_log.as_posix()]
        # Also capture stdout/stderr to help debugging (keeps console clean)
        stdout_target = open(log_dir / f"stdout_{port}.log", "wb")
        stderr_target = open(log_dir / f"stderr_{port}.log", "wb")

    # Important on Windows: run from exe dir so *_Data resolves reliably.
    # (VH launcher uses cwd matching logic too) :contentReference[oaicite:6]{index=6}
    creationflags = 0
    if os.name == "nt":
        # keep in separate process group; avoids ctrl-c killing child accidentally
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        args,
        cwd=str(exe_dir),
        stdout=stdout_target,
        stderr=stderr_target,
        creationflags=creationflags,
    )
    return proc


@dataclass
class UnitySlot:
    slot: int
    port: str
    comm: Any
    proc: subprocess.Popen
    lock: threading.Lock
    owner_tid: Optional[int] = None


@dataclass
class UnityRuntime:
    slots: Dict[int, UnitySlot]

    def close(self, keep_alive: bool = True) -> None:
        for s in self.slots.values():
            try:
                s.comm.close()
            except Exception:
                pass
            if not keep_alive:
                try:
                    s.proc.kill()
                except Exception:
                    pass


def build_unity_runtime(cfg: RunConfig, logger: RunLogger, run_root: Path) -> UnityRuntime:
    ensure_simulation_import_path()
    from unity_simulator.comm_unity import UnityCommunication  # VirtualHome API :contentReference[oaicite:7]{index=7}

    exe_abs = Path(cfg.unity_executable).resolve() if cfg.unity_executable else None
    if exe_abs is None or not exe_abs.exists():
        raise SystemExit(f"--unity-executable is missing or not found: {cfg.unity_executable}")

    used_ports = set(int(p) for p in cfg.unity_ports)
    unity_logs = run_root / "unity_logs"

    slots: Dict[int, UnitySlot] = {}

    for slot in range(cfg.env_slots):
        port = cfg.unity_ports[slot]
        ok = False

        for attempt in range(1, 6):
            proc = launch_virtualhome_exe(
                exe_path=exe_abs,
                port=port,
                log_dir=unity_logs,
                headless=cfg.unity_headless,
                log_mode=cfg.unity_log_mode,
            )
            logger.log(f"unity_started slot={slot} port={port} pid={proc.pid} headless={cfg.unity_headless}")

            if not wait_for_unity(cfg.unity_url, port, cfg.unity_start_timeout_sec, logger, slot):
                try:
                    proc.kill()
                except Exception:
                    pass
                port = pick_one_free_port(cfg.unity_url, used_ports)
                logger.log(f"unity_retry slot={slot} attempt={attempt} new_port={port} reason=start_timeout")
                continue

            # Connect WITHOUT file_name => no internal UnityLauncher, no 'Getting connection...' hang :contentReference[oaicite:8]{index=8}
            comm = UnityCommunication(url=cfg.unity_url, port=port, file_name=None, timeout_wait=30)

            # Preflight reset + add_character
            try:
                comm.reset(cfg.unity_preflight_scene_id)
                comm.add_character()
            except Exception as e:
                logger.log(f"unity_preflight_failed slot={slot} port={port} err={e}")
                try:
                    proc.kill()
                except Exception:
                    pass
                port = pick_one_free_port(cfg.unity_url, used_ports)
                logger.log(f"unity_retry slot={slot} attempt={attempt} new_port={port} reason=preflight_failed")
                continue

            slots[slot] = UnitySlot(slot=slot, port=port, comm=comm, proc=proc, lock=threading.Lock(), owner_tid=None)
            logger.log(f"unity_preflight_ok slot={slot} port={port}")
            ok = True
            break

        if not ok:
            raise SystemExit(f"Failed to launch Unity for slot={slot} after retries. Check {unity_logs}.")

    cfg.unity_ports = tuple(slots[i].port for i in range(cfg.env_slots))
    return UnityRuntime(slots=slots)


# =========================
# Actions / Parsing
# =========================

def normalize_action(action: str) -> str:
    action = action.strip()
    return action if action.lower().startswith("<char") else f"<char0> {action}"


def canonicalize_action_for_unity(action: str) -> str:
    if not isinstance(action, str):
        return ""
    raw = action.strip()
    if not raw:
        return ""
    if raw == "TASK_UNFEASIBLE":
        return raw

    normalized = normalize_action(raw)

    def _normalize_paren(match: re.Match[str]) -> str:
        token = (match.group(1) or "").strip()
        if re.fullmatch(r"-?\d+\.\d+", token):
            return f" ({int(float(token))})"
        if re.fullmatch(r"-?\d+", token):
            return f" ({int(token)})"
        return f" ({token})"

    normalized = PAREN_CONTENT_RE.sub(_normalize_paren, normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def extract_verb_obj(action: str) -> Tuple[str, str]:
    m = ACTION_RE.search((action or "").lower())
    return ("", "") if not m else (m.group("verb"), (m.group("obj") or ""))


def validate_action_format(action: str) -> Tuple[bool, str]:
    if not isinstance(action, str) or not action.strip():
        return False, "empty_or_non_string"
    if action.strip() == "TASK_UNFEASIBLE":
        return True, "ok"
    if "[" not in action or "]" not in action:
        return False, "missing_verb_brackets"
    if not action.strip().lower().startswith("<char"):
        return False, "missing_char_prefix"
    return True, "ok"


def action_category(verb: str) -> str:
    v = (verb or "").lower()
    if v in {"walk", "run", "find", "walktowards", "walkforward", "turnleft", "turnright"}:
        return "navigation"
    if v in {"open", "close", "switchon", "switchoff", "plugin", "plugout"}:
        return "state_change"
    if v in {"grab", "putin", "putback", "put", "drop", "touch", "move", "pour", "wipe"}:
        return "manipulation"
    if v in {"sit", "standup", "lie", "wakeup", "sleep", "watch"}:
        return "body_pose"
    return "other"


def contradiction_count(history: List[str]) -> int:
    opposite = {"open": "close", "close": "open", "switchon": "switchoff", "switchoff": "switchon"}
    cnt = 0
    for prev, cur in zip(history, history[1:]):
        pv, po = extract_verb_obj(prev)
        cv, co = extract_verb_obj(cur)
        if po and po == co and (opposite.get(pv) == cv or (pv == cv and pv in opposite)):
            cnt += 1
    return cnt


def classify_reject_reason(msg: str) -> str:
    s = (msg or "").lower()
    if "timeout" in s:
        return "timeout"
    if "not found" in s or "cannot find" in s:
        return "not_found"
    if "collision" in s:
        return "collision"
    if "failed" in s:
        return "failed"
    return "other"


# =========================
# Offline sequence metrics
# =========================

def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def percentile(values: List[float], q: int) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    k = int(round((q / 100.0) * (len(xs) - 1)))
    return xs[k]


# =========================
# Dataset loading
# =========================

def parse_executable_program(path: Path) -> Task:
    lines = [x.strip() for x in path.read_text(encoding="utf-8", errors="ignore").splitlines()]
    title = lines[0] if lines else path.stem
    description = lines[1] if len(lines) > 1 else ""
    raw_actions = [x for x in lines[4:] if x]
    actions = [canonicalize_action_for_unity(x) for x in raw_actions]
    n = len(actions)
    difficulty = "easy" if n <= 5 else "medium" if n <= 12 else "hard"
    return Task(task_id=str(path), title=title, description=description, actions=actions, difficulty=difficulty)


def load_tasks(dataset_root: Path, max_tasks: int) -> List[Task]:
    files = sorted(dataset_root.rglob("*.txt"))
    tasks: List[Task] = []
    for p in files:
        try:
            t = parse_executable_program(p)
            if t.actions:
                tasks.append(t)
        except Exception:
            continue
        if len(tasks) >= max_tasks:
            break
    return tasks


def select_tasks_by_difficulty(tasks: List[Task], max_easy: int, max_medium: int, max_hard: int, max_tasks: int) -> List[Task]:
    limits = {"easy": max_easy, "medium": max_medium, "hard": max_hard}
    used = {"easy": 0, "medium": 0, "hard": 0}
    selected: List[Task] = []
    for t in tasks:
        if used[t.difficulty] >= limits[t.difficulty]:
            continue
        selected.append(t)
        used[t.difficulty] += 1
        if len(selected) >= max_tasks:
            break
    return selected


def classify_task_precheck_reason(msg: str) -> str:
    s = (msg or "").lower()
    if "requested value" in s:
        return "unsupported_verb_or_token"
    if "unknown object" in s:
        return "unknown_object"
    if "error parsing script" in s:
        return "parse_error"
    if "timeout" in s:
        return "timeout"
    if "cannot find" in s or "not found" in s:
        return "not_found"
    return "other"


def probe_unity_supported_verbs(tasks: List[Task], slot: UnitySlot, cfg: RunConfig, logger: RunLogger) -> set:
    verbs = sorted({extract_verb_obj(a)[0] for t in tasks for a in t.actions if extract_verb_obj(a)[0]})
    exe_sig = ""
    try:
        exe_path = Path(cfg.unity_executable or "")
        if exe_path.exists():
            st = exe_path.stat()
            exe_sig = f"{exe_path.resolve()}::{int(st.st_size)}::{int(st.st_mtime)}"
    except Exception:
        exe_sig = str(cfg.unity_executable or "")
    cache_key = hashlib.sha256(f"verb_probe_v1::{exe_sig}".encode("utf-8")).hexdigest()[:16]
    cache_dir = Path(cfg.benchmarks_root) / ".cache"
    cache_path = cache_dir / f"unity_supported_verbs_{cache_key}.json"
    try:
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            cached = set(str(x).lower() for x in (data.get("supported_verbs") or []))
            if cached:
                logger.log(f"unity_verb_probe_cache_hit file={cache_path} count={len(cached)}")
                return cached
    except Exception:
        pass

    supported: set = set()
    rejected = 0
    for i, verb in enumerate(verbs, start=1):
        probe_action = f"<char0> [{verb.upper()}] <chair> (1)"
        ok = False
        msg = ""
        try:
            with slot.lock:
                slot.comm.reset(0)
                slot.comm.add_character()
                ok, raw_msg = slot.comm.render_script(
                    script=[probe_action],
                    find_solution=False,
                    processing_time_limit=2,
                    skip_execution=True,
                    recording=False,
                    skip_animation=True,
                )
            msg = str(raw_msg)
        except Exception as e:
            msg = str(e)

        # Unsupported action token in Unity parser.
        if "requested value" in msg.lower():
            rejected += 1
        else:
            supported.add(verb)

        if i % 10 == 0 or i == len(verbs):
            logger.log(f"unity_verb_probe progress={i}/{len(verbs)} supported={len(supported)} rejected={rejected}")
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"supported_verbs": sorted(supported)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.log(f"unity_verb_probe_cache_saved file={cache_path} count={len(supported)}")
    except Exception:
        pass
    return supported


def unity_precheck_task(task: Task, slot: UnitySlot, cfg: RunConfig) -> Tuple[bool, str]:
    scene_id = infer_scene_id(task.task_id)
    with slot.lock:
        slot.comm.reset(scene_id)
        slot.comm.add_character()
        ok, msg = slot.comm.render_script(
            script=task.actions,
            find_solution=True,
            processing_time_limit=min(max(3, cfg.unity_step_time_limit), 8),
            skip_execution=True,
            recording=False,
            skip_animation=True,
        )
    return bool(ok), str(msg)


def select_tasks_by_difficulty_with_unity_precheck(
    tasks: List[Task],
    max_easy: int,
    max_medium: int,
    max_hard: int,
    max_tasks: int,
    runtime: UnityRuntime,
    cfg: RunConfig,
    logger: RunLogger,
) -> List[Task]:
    limits = {"easy": max_easy, "medium": max_medium, "hard": max_hard}
    used = {"easy": 0, "medium": 0, "hard": 0}
    selected: List[Task] = []
    reject_hist: Counter[str] = Counter()

    if not runtime.slots:
        raise RuntimeError("unity runtime has no slots for task precheck")
    slot = runtime.slots[sorted(runtime.slots.keys())[0]]

    supported_verbs = probe_unity_supported_verbs(tasks, slot, cfg, logger)
    with slot.lock:
        slot.comm.reset(0)
        slot.comm.add_character()

    checked = 0
    max_checks = 8000
    for t in tasks:
        if used[t.difficulty] >= limits[t.difficulty]:
            continue

        if checked >= max_checks:
            break
        checked += 1
        if checked % 250 == 0:
            logger.log(
                f"unity_task_filter_progress checked={checked} selected={len(selected)} "
                f"used={used} rejects={dict(reject_hist)}"
            )
        verbs_ok = True
        for a in t.actions:
            v, _ = extract_verb_obj(a)
            if v and v not in supported_verbs:
                verbs_ok = False
                break
        if not verbs_ok:
            reject_hist["unsupported_verb_or_token"] += 1
            continue

        # Fast parser-only precheck. Reject only parsing-level errors.
        ok, msg = unity_precheck_task(t, slot, cfg)
        if (not ok) and ("error parsing script" in msg.lower() or "requested value" in msg.lower() or "unknown object" in msg.lower()):
            reject_hist[classify_task_precheck_reason(msg)] += 1
            continue

        selected.append(t)
        used[t.difficulty] += 1
        if len(selected) >= max_tasks:
            break
        if all(used[d] >= limits[d] for d in limits):
            break

    logger.log(
        "unity_task_filter "
        f"checked={checked} selected={len(selected)} "
        f"need_easy={max_easy} got_easy={used['easy']} "
        f"need_medium={max_medium} got_medium={used['medium']} "
        f"need_hard={max_hard} got_hard={used['hard']} "
        f"reject_hist={dict(reject_hist)}"
    )
    return selected


# =========================
# Scenarios
# =========================

def default_scenarios() -> List[ScenarioConfig]:
    return [
        ScenarioConfig(name="clean", noise_level=0.0, action_dropout_prob=0.0, collision_prob=0.0, chunk_size=3, sensor_dropout_prob=0.0),
        ScenarioConfig(name="noise", noise_level=0.2, action_dropout_prob=0.0, collision_prob=0.1, chunk_size=3, sensor_dropout_prob=0.1),
        ScenarioConfig(name="dropout_safe", noise_level=0.0, action_dropout_prob=0.2, collision_prob=0.1, chunk_size=3, sensor_dropout_prob=0.2),
        ScenarioConfig(name="dropout_hard", noise_level=0.0, action_dropout_prob=0.15, collision_prob=0.1, chunk_size=3, allow_disable_protected=True, critical_dropout_prob=0.05, sensor_dropout_prob=0.25),
        ScenarioConfig(name="stress", noise_level=0.25, action_dropout_prob=0.25, collision_prob=0.25, chunk_size=2, allow_disable_protected=True, critical_dropout_prob=0.08, sensor_dropout_prob=0.3),
    ]


# =========================
# Validation / paths
# =========================

def validate_runtime_config(cfg: RunConfig) -> None:
    if cfg.allow_shared_env_slots:
        raise ValueError("--allow-shared-env-slots is disabled in strict mode.")
    unity_enabled = cfg.metrics_mode in {"offline", "all"}
    if unity_enabled and cfg.env_slots < cfg.parallel_workers:
        raise ValueError("env_slots must be >= parallel_workers when Unity is enabled.")
    if unity_enabled and len(cfg.unity_ports) != cfg.env_slots:
        raise ValueError("internal error: unity_ports must have length env_slots.")


def run_signature(cfg: RunConfig) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    flags = f"t{cfg.max_tasks}_e{cfg.max_easy}m{cfg.max_medium}h{cfg.max_hard}_r{cfg.repeats_per_task}_p{cfg.parallel_workers}_slots{cfg.env_slots}_seed{cfg.seed}"
    auto = f"{cfg.method_name}_{cfg.controller}_{cfg.arch}_{cfg.metrics_mode}_{ts}_{flags}"
    return cfg.run_name or auto


def make_run_paths(cfg: RunConfig) -> Dict[str, Path]:
    root = Path(cfg.benchmarks_root) / run_signature(cfg)
    paths = {
        "root": root,
        "reports": root / "reports",
        "telemetry": root / "telemetry",
        "logs": root / "logs",
        "config": root / "config",
    }
    for p in paths.values():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def slugify_text(s: str, max_len: int = 64) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "_", (s or "").strip())
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = "task"
    return base[:max_len]


def select_video_example_tasks(tasks: List[Task], per_difficulty: int) -> Dict[str, List[Task]]:
    want = max(0, int(per_difficulty))
    out: Dict[str, List[Task]] = {"easy": [], "medium": [], "hard": []}
    if want == 0:
        return out
    for t in tasks:
        d = t.difficulty
        if d in out:
            out[d].append(t)
    for d in out:
        out[d].sort(key=lambda t: hashlib.sha256(t.task_id.encode("utf-8")).hexdigest())
    return out


def extract_successful_actions_for_video(row: Dict[str, Any]) -> List[str]:
    step_trace = row.get("step_trace", []) or []
    actions: List[str] = []
    for rec in step_trace:
        if rec.get("online_sim_ok") is not True:
            continue
        pred = str(rec.get("prediction", "")).strip()
        if not pred or pred == "TASK_UNFEASIBLE":
            continue
        actions.append(pred)
    return actions


def build_video_action_candidates(rows: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    best: Dict[str, List[str]] = {}
    for row in rows:
        if row.get("scenario") != "clean":
            continue
        task_id = str(row.get("task_id", ""))
        if not task_id:
            continue
        actions = extract_successful_actions_for_video(row)
        if not actions:
            continue
        prev = best.get(task_id)
        if prev is None or len(actions) > len(prev):
            best[task_id] = actions
    return best


def video_prefix_lengths(total_actions: int) -> List[int]:
    n = max(1, int(total_actions))
    cands = [
        n,
        max(1, int(n * 0.75)),
        max(1, int(n * 0.5)),
        max(1, int(n * 0.33)),
        1,
    ]
    out: List[int] = []
    for c in cands:
        if c not in out:
            out.append(c)
    return out


def build_mp4_from_png_frames(example_dir: Path, out_mp4: Path, fps: int) -> Tuple[bool, str]:
    frame_files = sorted(example_dir.rglob("Action_*_normal.png"))
    if not frame_files:
        return False, "no_png_frames"
    try:
        import cv2  # type: ignore
    except Exception as e:
        return False, f"opencv_import_failed: {e}"

    first = cv2.imread(str(frame_files[0]))
    if first is None:
        return False, "first_frame_read_failed"

    h, w = first.shape[:2]
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(out_mp4), cv2.VideoWriter_fourcc(*"mp4v"), float(max(1, fps)), (w, h))
    if not writer.isOpened():
        return False, "video_writer_open_failed"

    written = 0
    try:
        for fp in frame_files:
            frame = cv2.imread(str(fp))
            if frame is None:
                continue
            if frame.shape[0] != h or frame.shape[1] != w:
                frame = cv2.resize(frame, (w, h))
            writer.write(frame)
            written += 1
    finally:
        writer.release()

    if written == 0 or not out_mp4.exists():
        return False, "no_frames_written"
    return True, f"frames={written}"


def render_example_videos(
    cfg: RunConfig,
    tasks: List[Task],
    rows: List[Dict[str, Any]],
    runtime: Optional[UnityRuntime],
    run_root: Path,
    logger: RunLogger,
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    if not cfg.save_example_videos:
        return results
    if runtime is None:
        logger.log("video_examples_skipped reason=unity_runtime_unavailable")
        return results

    selected = select_video_example_tasks(tasks, cfg.video_examples_per_difficulty)
    episode_action_candidates = build_video_action_candidates(rows)
    video_root = run_root / "video_examples"
    raw_root = video_root / "raw"
    mp4_root = video_root / "mp4"
    raw_root.mkdir(parents=True, exist_ok=True)
    mp4_root.mkdir(parents=True, exist_ok=True)

    slot_ids = sorted(runtime.slots.keys())
    if not slot_ids:
        logger.log("video_examples_skipped reason=no_unity_slots")
        return results

    idx = 0
    want_per_diff = max(0, int(cfg.video_examples_per_difficulty))
    for difficulty in ("easy", "medium", "hard"):
        ok_count = 0
        attempts = 0
        rank = 0
        max_attempts = max(1, want_per_diff * 20)
        for task in selected.get(difficulty, []):
            if ok_count >= want_per_diff or attempts >= max_attempts:
                break
            attempts += 1
            rank += 1
            slot_id = slot_ids[idx % len(slot_ids)]
            idx += 1
            slot = runtime.slots[slot_id]

            task_name = Path(task.task_id).stem
            prefix = f"{difficulty}_{rank:02d}_{slugify_text(task_name)}"
            scene_id = infer_scene_id(task.task_id)
            out_folder = (raw_root / difficulty).resolve()
            expected_mp4 = out_folder / prefix / "Action_normal.mp4"
            copied_mp4 = mp4_root / f"{prefix}.mp4"

            rec: Dict[str, Any] = {
                "difficulty": difficulty,
                "task_id": task.task_id,
                "task_title": task.title,
                "scene_id": scene_id,
                "env_slot": slot_id,
                "output_folder": str((out_folder / prefix).resolve()),
                "expected_video_mp4": str(expected_mp4.resolve()),
                "video_mp4": None,
                "render_success": False,
                "success": False,
                "message": "",
            }

            try:
                ok = False
                msg: Any = ""
                used_len = len(task.actions)
                used_source = "gold_task"
                action_candidates: List[Tuple[str, List[str]]] = []
                from_episode = episode_action_candidates.get(task.task_id) or []
                if from_episode:
                    action_candidates.append(("episode_trace", from_episode))
                if from_episode != task.actions:
                    action_candidates.append(("gold_task", task.actions))

                for source_name, script_actions in action_candidates:
                    prefix_lens = video_prefix_lengths(len(script_actions))
                    for n_actions in prefix_lens:
                        prev_timeout_wait = getattr(slot.comm, "timeout_wait", 30)
                        try:
                            with slot.lock:
                                slot.comm.timeout_wait = max(10, int(cfg.video_http_timeout_sec))
                                slot.comm.reset(scene_id)
                                slot.comm.add_character()
                                ok, msg = slot.comm.render_script(
                                    script=script_actions[:n_actions],
                                    find_solution=True,
                                    processing_time_limit=max(15, cfg.video_processing_time_limit_sec, cfg.unity_step_time_limit),
                                    output_folder=out_folder.as_posix(),
                                    file_name_prefix=prefix,
                                    frame_rate=max(1, int(cfg.video_frame_rate)),
                                    image_synthesis=["normal"],
                                    image_width=max(64, int(cfg.video_image_width)),
                                    image_height=max(64, int(cfg.video_image_height)),
                                    recording=True,
                                    camera_mode=["AUTO"],
                                    time_scale=max(0.1, float(cfg.video_time_scale)),
                                    skip_animation=bool(cfg.video_skip_animation),
                                )
                        finally:
                            try:
                                slot.comm.timeout_wait = prev_timeout_wait
                            except Exception:
                                pass
                        used_len = n_actions
                        used_source = source_name
                        if ok:
                            break
                    if ok:
                        break

                rec["message"] = str(msg)
                rec["render_success"] = bool(ok)
                rec["used_actions_for_video"] = used_len
                rec["action_source"] = used_source

                source_mp4: Optional[Path] = expected_mp4 if expected_mp4.exists() else None
                if rec["render_success"] and source_mp4 is None:
                    generated_mp4 = out_folder / prefix / "video_normal.mp4"
                    built, build_msg = build_mp4_from_png_frames(out_folder / prefix, generated_mp4, cfg.video_frame_rate)
                    if built and generated_mp4.exists():
                        source_mp4 = generated_mp4
                    else:
                        rec["message"] = f"{rec['message']} (mp4_build_failed: {build_msg})"

                if rec["render_success"] and source_mp4 is not None:
                    shutil.copy2(source_mp4, copied_mp4)
                    rec["video_mp4"] = str(copied_mp4.resolve())
                    rec["success"] = True
                    ok_count += 1
            except Exception as e:
                rec["message"] = str(e)

            logger.log(
                f"video_example difficulty={difficulty} slot={slot_id} success={rec['success']} "
                f"prefix={prefix} mp4={'yes' if rec['video_mp4'] else 'no'}"
            )
            results.append(rec)

        if ok_count < want_per_diff:
            logger.log(
                f"video_example_shortage difficulty={difficulty} requested={want_per_diff} "
                f"saved={ok_count} attempts={attempts}"
            )

    return results


def episode_seed(base_seed: int, task_id: str, scenario_name: str, repeat_idx: int) -> int:
    payload = f"{base_seed}::{task_id}::{scenario_name}::{repeat_idx}".encode("utf-8")
    return int(hashlib.sha256(payload).hexdigest()[:12], 16)


def infer_scene_id(task_id: str) -> int:
    m = re.search(r"TrimmedTestScene(\d+)_graph", task_id)
    return max(0, int(m.group(1)) - 1) if m else 0


def choose_disabled_actions(expected_verb: str, task: Task, scenario: ScenarioConfig, protected: set, rng: random.Random) -> List[str]:
    verbs = sorted({extract_verb_obj(a)[0] for a in task.actions if extract_verb_obj(a)[0]})
    disabled: List[str] = []
    for v in verbs:
        if v in protected and not scenario.allow_disable_protected:
            continue
        p = scenario.critical_dropout_prob if v in protected else scenario.action_dropout_prob
        if rng.random() < p:
            disabled.append(v)
    if scenario.allow_disable_protected and expected_verb and expected_verb not in disabled:
        if rng.random() < max(scenario.critical_dropout_prob, scenario.action_dropout_prob) * 0.25:
            disabled.append(expected_verb)
    return sorted(set(disabled))


def choose_sensor_state(sensors: List[str], dropout_prob: float, rng: random.Random, is_ideal_run: bool) -> Tuple[List[str], List[str]]:
    if is_ideal_run:
        return sensors[:], []
    enabled, disabled = [], []
    for s in sensors:
        (disabled if rng.random() < dropout_prob else enabled).append(s)
    if not enabled:
        keep = sensors[rng.randrange(len(sensors))]
        enabled = [keep]
        disabled = [x for x in sensors if x != keep]
    return enabled, disabled


# =========================
# Episode execution
# =========================

def _empty_episode_result(spec: EpisodeSpec, setup_error: str) -> Dict[str, Any]:
    t, sc = spec.task, spec.scenario
    return {
        "task_id": t.task_id,
        "task_title": t.title,
        "difficulty": t.difficulty,
        "scenario": sc.name,
        "repeat_idx": spec.repeat_idx,
        "is_ideal_run": spec.is_ideal_run,
        "env_slot": spec.env_slot,
        "setup_error": setup_error,
        "steps_used": 0,
        "optimal_steps": len(t.actions),
        "error_actions": 1,
        "invalid_format": 0,
        "disabled_action_violations": 0,
        "online_success": None,
        "online_goal_completion_ratio": None,
        "sim_step_success_rate": None,
        "sim_reject_count": None,
        "sim_exception_count": None,
        "sim_exec_time_total_sec": None,
        "sim_exec_time_per_step_sec": None,
        "reject_reason_hist": {},
        "episode_wallclock_sec": 0.0,
        "decision_time_total_sec": 0.0,
        "mean_latency_sec": None,
        "p95_latency_sec": None,
        "step_trace": [{"step": 0, "reason": "episode_setup_failed", "error": setup_error}],
        "telemetry_trace": [],
    }


def run_episode(spec: EpisodeSpec, cfg: RunConfig, client: Any, unity_runtime: Optional[UnityRuntime]) -> Dict[str, Any]:
    use_unity = cfg.metrics_mode in {"offline", "all"}
    task, scenario = spec.task, spec.scenario
    rng = random.Random(spec.seed)
    gold = task.actions
    sensors = ["rgb", "depth", "segmentation", "graph_state", "history"]
    protected = set(cfg.protected_actions)

    max_steps = max(1, int(len(gold) * cfg.max_steps_multiplier))
    wall_t0 = time.perf_counter()

    step_trace: List[Dict[str, Any]] = []
    telemetry_trace: List[Dict[str, Any]] = []

    steps = 0
    errors = 0
    invalid_format = 0
    disabled_action_violations = 0
    latencies: List[float] = []
    decision_total = 0.0

    online_pos = 0
    no_progress_steps = 0
    episode_timeout = False
    no_progress_termination = False
    termination_reason = "completed"
    online_history: List[str] = []
    executed_actions: List[str] = []

    sim_attempts = 0
    sim_success_steps = 0
    sim_reject_count = 0
    sim_exception_count = 0
    sim_exec_time_total = 0.0
    reject_reason_hist = Counter()

    slot: Optional[UnitySlot] = None
    if use_unity:
        if unity_runtime is None:
            return _empty_episode_result(spec, "unity_required_but_runtime_is_none")
        slot = unity_runtime.slots.get(spec.env_slot)
        if slot is None:
            return _empty_episode_result(spec, f"missing unity slot env_slot={spec.env_slot}")

        with slot.lock:
            tid = threading.get_ident()
            if slot.owner_tid is not None and slot.owner_tid != tid:
                return _empty_episode_result(spec, f"slot_owner_conflict env_slot={spec.env_slot} owner={slot.owner_tid} me={tid}")
            slot.owner_tid = tid
            try:
                scene_id = infer_scene_id(task.task_id)
                slot.comm.reset(scene_id)
                slot.comm.add_character()
            except Exception as e:
                slot.owner_tid = None
                return _empty_episode_result(spec, f"unity_setup_failed slot={spec.env_slot} port={slot.port}: {e}")

    try:
        while steps < max_steps and online_pos < len(gold):
            if use_unity:
                elapsed_wall = time.perf_counter() - wall_t0
                if elapsed_wall >= max(1, cfg.unity_episode_time_limit_sec):
                    step_trace.append({
                        "step": steps,
                        "expected": gold[min(online_pos, len(gold) - 1)] if gold else "",
                        "prediction_raw": "",
                        "error": None,
                        "note": "episode_time_limit_exceeded",
                        "reason": "episode_time_limit_exceeded",
                        "online_sim_ok": None,
                        "online_match": False,
                    })
                    episode_timeout = True
                    termination_reason = "episode_time_limit_exceeded"
                    break
                if no_progress_steps >= max(1, cfg.unity_max_no_progress_steps):
                    step_trace.append({
                        "step": steps,
                        "expected": gold[min(online_pos, len(gold) - 1)] if gold else "",
                        "prediction_raw": "",
                        "error": None,
                        "note": "no_progress_timeout",
                        "reason": "no_progress_timeout",
                        "online_sim_ok": None,
                        "online_match": False,
                    })
                    no_progress_termination = True
                    termination_reason = "no_progress_timeout"
                    break

            steps += 1
            expected = gold[online_pos]
            expected_verb, _ = extract_verb_obj(expected)

            disabled_actions = [] if spec.is_ideal_run else choose_disabled_actions(expected_verb, task, scenario, protected, rng)
            collision = (rng.random() < scenario.collision_prob) if not spec.is_ideal_run else False
            noisy_expected = expected
            if (not spec.is_ideal_run) and scenario.noise_level > 0 and rng.random() < scenario.noise_level:
                noisy_expected = noisy_expected.replace("<", "[").replace(">", "]")

            enabled_sensors, disabled_sensors = choose_sensor_state(sensors, scenario.sensor_dropout_prob, rng, spec.is_ideal_run)

            allowed_actions_all = list(dict.fromkeys(task.actions))
            allowed_actions = [a for a in allowed_actions_all if extract_verb_obj(a)[0] not in disabled_actions]
            allowed_actions_all_set = {a.strip().lower() for a in allowed_actions_all}

            telemetry = {
                "event": "step",
                "task": {"id": task.task_id, "title": task.title, "description": task.description, "difficulty": task.difficulty},
                "scenario": asdict(scenario),
                "step": steps,
                "repeat_idx": spec.repeat_idx,
                "is_ideal_run": spec.is_ideal_run,
                "runtime": {"env_slot": spec.env_slot, "unity_port": (slot.port if slot else None), "parallel_workers": cfg.parallel_workers},
                "progress": {"online_index": online_pos, "total_actions": len(gold)},
                "observation": {
                    "history": online_history[-10:],
                    "expected_action_noisy": noisy_expected,
                    "disabled_actions": disabled_actions,
                    "available_actions": allowed_actions,
                    "allowed_actions": allowed_actions,
                    "collision": collision,
                    "enabled_sensors": enabled_sensors,
                    "disabled_sensors": disabled_sensors,
                },
                "instruction": "Return next atomic action string in VirtualHome format or TASK_UNFEASIBLE",
            }

            action, latency, err, raw_response = client.act(telemetry)
            latencies.append(latency)
            decision_total += latency

            rec = {
                "step": steps,
                "expected": expected,
                "prediction_raw": action,
                "error": None,
                "reason": "",
                "online_sim_ok": None,
                "online_match": False,
            }

            if cfg.save_traces:
                telemetry_trace.append({
                    "task_id": task.task_id,
                    "scenario": scenario.name,
                    "step": steps,
                    "env_slot": spec.env_slot,
                    "telemetry": telemetry,
                    "response": raw_response,
                    "response_error": err,
                    "latency_sec": latency,
                })

            if err:
                errors += 1
                no_progress_steps += 1
                rec["error"] = err
                rec["reason"] = "request_error"
                step_trace.append(rec)
                continue

            action = (action or "").strip()
            if action and action != "TASK_UNFEASIBLE":
                action = canonicalize_action_for_unity(action)
            rec["prediction"] = action

            ok_fmt, fmt_reason = validate_action_format(action)
            if not ok_fmt:
                errors += 1
                invalid_format += 1
                no_progress_steps += 1
                rec["error"] = fmt_reason
                rec["reason"] = fmt_reason
                step_trace.append(rec)
                continue

            if action == "TASK_UNFEASIBLE":
                errors += 1
                no_progress_steps += 1
                rec["reason"] = "unfeasible"
                step_trace.append(rec)
                continue

            if cfg.strict_action_whitelist and action.strip().lower() not in allowed_actions_all_set:
                errors += 1
                no_progress_steps += 1
                rec["error"] = "action_not_in_allowed_set"
                rec["reason"] = "action_not_in_allowed_set"
                step_trace.append(rec)
                continue

            pred_verb, _ = extract_verb_obj(action)
            if pred_verb in disabled_actions:
                errors += 1
                disabled_action_violations += 1
                no_progress_steps += 1
                rec["reason"] = "disabled_action_violation"
                step_trace.append(rec)
                continue

            if use_unity:
                assert slot is not None
                sim_attempts += 1
                sim_t0 = time.perf_counter()

                try:
                    processing_time_limit = cfg.unity_step_time_limit
                    skip_anim = cfg.unity_step_skip_animation
                    find_solution = cfg.unity_find_solution
                    time_scale = max(0.1, cfg.unity_time_scale)
                    if cfg.unity_fast:
                        processing_time_limit = min(processing_time_limit, 15)
                        skip_anim = True
                        time_scale = max(time_scale, 12.0)

                    with slot.lock:
                        sim_ok, sim_msg = slot.comm.render_script(
                            script=[action],
                            find_solution=find_solution,
                            processing_time_limit=processing_time_limit,
                            recording=False,
                            skip_animation=skip_anim,
                            time_scale=time_scale,
                        )

                    sim_exec_time_total += time.perf_counter() - sim_t0
                    rec["online_sim_ok"] = bool(sim_ok)
                    rec["online_sim_message"] = str(sim_msg)

                    if not sim_ok:
                        sim_reject_count += 1
                        reject_reason_hist[classify_reject_reason(str(sim_msg))] += 1
                        errors += 1
                        no_progress_steps += 1
                        rec["reason"] = "sim_reject"
                    else:
                        sim_success_steps += 1
                        executed_actions.append(action)
                        online_history.append(action)

                        if action.strip().lower() == expected.strip().lower():
                            online_pos += 1
                            no_progress_steps = 0
                            rec["online_match"] = True
                            rec["reason"] = "online_exact_match"
                        else:
                            errors += 1
                            no_progress_steps += 1
                            rec["reason"] = "online_wrong_action"
                except Exception as e:
                    sim_exec_time_total += time.perf_counter() - sim_t0
                    sim_exception_count += 1
                    errors += 1
                    no_progress_steps += 1
                    rec["online_sim_ok"] = False
                    rec["online_sim_message"] = str(e)
                    rec["reason"] = "sim_exception"
            else:
                executed_actions.append(action)
                online_history.append(action)

                if action.strip().lower() == expected.strip().lower():
                    online_pos += 1
                    no_progress_steps = 0
                    rec["online_match"] = True
                    rec["reason"] = "online_exact_match"
                else:
                    errors += 1
                    no_progress_steps += 1
                    rec["reason"] = "online_wrong_action"

            step_trace.append(rec)

        wall_sec = time.perf_counter() - wall_t0
        if termination_reason == "completed" and online_pos < len(gold):
            if steps >= max_steps:
                termination_reason = "max_steps_reached"
            else:
                termination_reason = "incomplete"

        res = {
            "task_id": task.task_id,
            "task_title": task.title,
            "difficulty": task.difficulty,
            "scenario": scenario.name,
            "repeat_idx": spec.repeat_idx,
            "is_ideal_run": spec.is_ideal_run,
            "env_slot": spec.env_slot,

            "steps_used": steps,
            "optimal_steps": len(gold),
            "error_actions": errors,
            "invalid_format": invalid_format,
            "disabled_action_violations": disabled_action_violations,

            "online_success": bool(online_pos == len(gold)),
            "online_goal_completion_ratio": safe_div(online_pos, max(1, len(gold))),

            "sim_step_success_rate": safe_div(sim_success_steps, max(1, sim_attempts)) if use_unity else None,
            "sim_reject_count": sim_reject_count if use_unity else None,
            "sim_exception_count": sim_exception_count if use_unity else None,
            "sim_exec_time_total_sec": sim_exec_time_total if use_unity else None,
            "sim_exec_time_per_step_sec": safe_div(sim_exec_time_total, max(1, sim_attempts)) if use_unity else None,
            "reject_reason_hist": dict(reject_reason_hist) if use_unity else {},

            "executed_actions_count": len(executed_actions),

            "episode_wallclock_sec": wall_sec,
            "decision_time_total_sec": decision_total,
            "decision_time_per_step_sec": safe_div(decision_total, max(1, steps)),
            "mean_latency_sec": (sum(latencies) / len(latencies)) if latencies else None,
            "p95_latency_sec": percentile(latencies, 95) if latencies else None,
            "no_progress_steps": no_progress_steps,
            "episode_timeout": episode_timeout,
            "no_progress_termination": no_progress_termination,
            "terminated_early": termination_reason != "completed",
            "termination_reason": termination_reason,
        }

        if cfg.save_traces:
            res["step_trace"] = step_trace
            res["telemetry_trace"] = telemetry_trace
        return res

    finally:
        if use_unity and slot is not None:
            with slot.lock:
                slot.owner_tid = None


# =========================
# Parallel execution
# =========================

def build_episode_specs(tasks: List[Task], scenarios: List[ScenarioConfig], cfg: RunConfig) -> List[EpisodeSpec]:
    specs: List[EpisodeSpec] = []
    idx = 0
    for t in tasks:
        for sc in scenarios:
            for r in range(max(1, cfg.repeats_per_task)):
                is_ideal = bool(cfg.ideal_first_run and sc.name == "clean" and r == 0)
                specs.append(EpisodeSpec(
                    task=t,
                    scenario=sc,
                    repeat_idx=r,
                    seed=episode_seed(cfg.seed, t.task_id, sc.name, r),
                    is_ideal_run=is_ideal,
                    env_slot=(idx % max(1, cfg.env_slots)),
                ))
                idx += 1
    return specs


def run_all_episodes(cfg: RunConfig, specs: List[EpisodeSpec], client: Any, runtime: Optional[UnityRuntime], logger: RunLogger) -> List[Dict[str, Any]]:
    unity_enabled = cfg.metrics_mode in {"offline", "all"}

    if unity_enabled:
        queues: Dict[int, Queue] = {s: Queue() for s in range(cfg.env_slots)}
        for sp in specs:
            queues[sp.env_slot].put(sp)

        results: List[Dict[str, Any]] = []
        res_lock = threading.Lock()

        def worker(slot: int) -> None:
            q = queues[slot]
            done = 0
            while True:
                try:
                    sp = q.get_nowait()
                except Empty:
                    return
                ep = run_episode(sp, cfg, client, runtime)
                with res_lock:
                    results.append(ep)
                done += 1
                if done % 10 == 0:
                    logger.log(f"slot={slot} progress={done}")
                q.task_done()

        threads: List[threading.Thread] = []
        for slot in range(cfg.env_slots):
            t = threading.Thread(target=worker, args=(slot,), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return results

    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        results: List[Dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=cfg.parallel_workers) as ex:
            futures = [ex.submit(run_episode, sp, cfg, client, None) for sp in specs]
            done = 0
            for f in as_completed(futures):
                results.append(f.result())
                done += 1
                if done % 10 == 0 or done == len(futures):
                    logger.log(f"progress {done}/{len(futures)}")
        return results


# =========================
# Reporting
# =========================

def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {}

    def avg_num(key: str) -> float:
        vals = [r.get(key) for r in rows]
        vals = [float(v) for v in vals if isinstance(v, (int, float, bool))]
        return sum(vals) / len(vals) if vals else 0.0

    out = {
        "episodes": len(rows),
        "online_success_rate": avg_num("online_success"),
        "online_goal_completion_ratio": avg_num("online_goal_completion_ratio"),
        "sim_step_success_rate": avg_num("sim_step_success_rate"),
        "sim_reject_count_avg": avg_num("sim_reject_count"),
        "sim_exception_count_avg": avg_num("sim_exception_count"),
        "sim_exec_time_per_step_sec": avg_num("sim_exec_time_per_step_sec"),
        "invalid_format": avg_num("invalid_format"),
        "disabled_action_violations": avg_num("disabled_action_violations"),
        "mean_latency_sec": avg_num("mean_latency_sec"),
        "p95_latency_sec": avg_num("p95_latency_sec"),
    }
    return out


def write_telemetry_jsonl(rows: List[Dict[str, Any]], out_path: Path) -> None:
    with out_path.open("w", encoding="utf-8") as f:
        for ep in rows:
            for item in ep.get("telemetry_trace", []) or []:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_episodes_csv(rows: List[Dict[str, Any]], out_csv: Path) -> None:
    cols = [
        "task_id","task_title","difficulty","scenario","repeat_idx","is_ideal_run","env_slot",
        "steps_used","optimal_steps","error_actions","invalid_format","disabled_action_violations",
        "online_success","online_goal_completion_ratio",
        "sim_step_success_rate","sim_reject_count","sim_exception_count","sim_exec_time_per_step_sec",
        "mean_latency_sec","p95_latency_sec",
    ]
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=cols)
        wr.writeheader()
        for r in rows:
            wr.writerow({k: r.get(k) for k in cols})


def write_summary_markdown(report: Dict[str, Any], md_path: Path) -> None:
    sa = report.get("summary_all", {})
    lines = [
        "# Benchmark summary",
        "",
        f"- Episodes: **{sa.get('episodes', 0)}**",
        f"- Online success rate: **{sa.get('online_success_rate', 0):.4f}**",
        f"- Online completion: **{sa.get('online_goal_completion_ratio', 0):.4f}**",
        f"- Sim step success rate: **{sa.get('sim_step_success_rate', 0):.4f}**",
        "",
        f"- Invalid format (avg): **{sa.get('invalid_format', 0):.4f}**",
        f"- Disabled action violations (avg): **{sa.get('disabled_action_violations', 0):.4f}**",
    ]
    md_path.write_text("\n".join(lines), encoding="utf-8")


# =========================
# Main
# =========================

def split_task_budget(total_tasks: int) -> Tuple[int, int, int]:
    total = max(1, int(total_tasks))
    base = total // 3
    rem = total % 3
    easy = base + (1 if rem >= 1 else 0)
    medium = base + (1 if rem >= 2 else 0)
    hard = base
    return easy, medium, hard


def main() -> None:
    parser = argparse.ArgumentParser(description="VirtualHome benchmark runner (Windows-safe Unity launch, auto ports, strict slot isolation).")

    # Minimal CLI.
    parser.add_argument("--mode", choices=["online", "offline", "all"], default="offline",
                        help="online: no Unity. offline: Unity. all: both.")
    parser.add_argument("--model", choices=["reactive", "planner", "hierarchical", "hybrid"], default="reactive",
                        help="Model family (currently random-policy stubs).")
    parser.add_argument("--tasks", type=int, default=50, help="Total task count (auto-split by difficulty).")
    parser.add_argument("--parallel", type=int, default=2, help="Parallel workers and Unity slots.")
    parser.add_argument("--speed", type=float, default=12.0, help="Unity speed (time_scale).")
    parser.add_argument("--repeats", type=int, default=3, help="Repeats per task/scenario.")
    parser.add_argument("--controller", choices=["local", "http"], default="local")
    parser.add_argument("--url", default=None)
    parser.add_argument("--method-name", default="model")
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--dataset-root", default="virtualhome/virtualhome/dataset/programs_processed_precond_nograb_morepreconds/executable_programs")
    parser.add_argument("--seed", type=int, default=42)

    # Compatibility flags (hidden).
    parser.add_argument("--metrics-mode", choices=["online", "offline", "all"], default=None, help=argparse.SUPPRESS)
    parser.add_argument("--arch", choices=["reactive","planner","hierarchical","hybrid"], default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-tasks", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-easy", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-medium", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-hard", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--repeats-per-task", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--parallel-workers", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--env-slots", type=int, default=None, help=argparse.SUPPRESS)

    parser.add_argument("--ideal-first-run", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)
    parser.add_argument("--timeout-sec", type=int, default=60, help=argparse.SUPPRESS)
    parser.add_argument("--max-steps-multiplier", type=float, default=2.0, help=argparse.SUPPRESS)

    parser.add_argument("--benchmarks-root", default="benchmarks/virtualhome", help=argparse.SUPPRESS)
    parser.add_argument("--out-json", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--out-telemetry-jsonl", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--save-traces", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)
    parser.add_argument("--save-example-videos", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)
    parser.add_argument("--video-examples-per-difficulty", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--video-frame-rate", type=int, default=5, help=argparse.SUPPRESS)
    parser.add_argument("--video-image-width", type=int, default=640, help=argparse.SUPPRESS)
    parser.add_argument("--video-image-height", type=int, default=480, help=argparse.SUPPRESS)
    parser.add_argument("--video-time-scale", type=float, default=4.0, help=argparse.SUPPRESS)
    parser.add_argument("--video-skip-animation", action=argparse.BooleanOptionalAction, default=False, help=argparse.SUPPRESS)
    parser.add_argument("--video-http-timeout-sec", type=int, default=120, help=argparse.SUPPRESS)
    parser.add_argument("--video-processing-time-limit-sec", type=int, default=60, help=argparse.SUPPRESS)
    parser.add_argument("--strict-action-whitelist", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)
    parser.add_argument("--strict-unity-task-filter", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)

    # Unity
    parser.add_argument("--unity-url", default="127.0.0.1", help=argparse.SUPPRESS)
    parser.add_argument("--unity-executable", default=None, help="Path to VirtualHome.exe")
    parser.add_argument("--unity-start-timeout-sec", type=int, default=90, help=argparse.SUPPRESS)
    parser.add_argument("--unity-step-time-limit", type=int, default=60, help=argparse.SUPPRESS)
    parser.add_argument("--unity-step-skip-animation", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)
    parser.add_argument("--unity-find-solution", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)
    parser.add_argument("--unity-preflight-scene-id", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--unity-time-scale", type=float, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--unity-episode-time-limit-sec", type=int, default=180, help=argparse.SUPPRESS)
    parser.add_argument("--unity-max-no-progress-steps", type=int, default=25, help=argparse.SUPPRESS)
    parser.add_argument("--unity-fast", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)
    parser.add_argument("--unity-headless", action=argparse.BooleanOptionalAction, default=False, help=argparse.SUPPRESS)
    parser.add_argument("--unity-log-mode", choices=["files","none"], default="files", help=argparse.SUPPRESS)
    parser.add_argument("--keep-unity-alive", action=argparse.BooleanOptionalAction, default=True, help=argparse.SUPPRESS)

    args = parser.parse_args()

    metrics_mode = args.metrics_mode or args.mode
    arch = args.arch or args.model
    total_tasks = int(args.max_tasks) if args.max_tasks is not None else int(args.tasks)
    repeats = int(args.repeats_per_task) if args.repeats_per_task is not None else int(args.repeats)
    parallel = int(args.parallel_workers) if args.parallel_workers is not None else int(args.parallel)
    env_slots_requested = int(args.env_slots) if args.env_slots is not None else parallel
    unity_time_scale = float(args.unity_time_scale) if args.unity_time_scale is not None else float(args.speed)
    easy_auto, medium_auto, hard_auto = split_task_budget(total_tasks)
    max_easy = int(args.max_easy) if args.max_easy is not None else easy_auto
    max_medium = int(args.max_medium) if args.max_medium is not None else medium_auto
    max_hard = int(args.max_hard) if args.max_hard is not None else hard_auto

    if args.controller == "http" and not args.url:
        raise SystemExit("--url is required for --controller=http")

    unity_enabled = metrics_mode in {"offline", "all"}
    if unity_enabled and not args.unity_executable:
        raise SystemExit("--unity-executable is required when mode is offline|all")

    env_slots = 1
    if unity_enabled:
        env_slots = max(1, env_slots_requested)

    unity_ports: Tuple[str, ...] = tuple()
    if unity_enabled:
        unity_ports = pick_free_ports(args.unity_url, env_slots)

    cfg = RunConfig(
        dataset_root=args.dataset_root,
        controller=args.controller,
        url=args.url or "",
        arch=arch,
        method_name=args.method_name,
        run_name=args.run_name,
        metrics_mode=metrics_mode,

        max_tasks=max(1, total_tasks),
        max_easy=max(0, max_easy),
        max_medium=max(0, max_medium),
        max_hard=max(0, max_hard),
        repeats_per_task=max(1, repeats),
        ideal_first_run=bool(args.ideal_first_run),

        parallel_workers=max(1, parallel),
        env_slots=max(1, env_slots),

        max_steps_multiplier=float(args.max_steps_multiplier),
        seed=args.seed,
        timeout_sec=args.timeout_sec,

        benchmarks_root=args.benchmarks_root,
        out_json=args.out_json,
        out_telemetry_jsonl=args.out_telemetry_jsonl,
        save_traces=bool(args.save_traces),
        save_example_videos=True if unity_enabled else False,
        video_examples_per_difficulty=max(0, int(args.video_examples_per_difficulty)),
        video_frame_rate=max(1, int(args.video_frame_rate)),
        video_image_width=max(64, int(args.video_image_width)),
        video_image_height=max(64, int(args.video_image_height)),
        video_time_scale=max(0.1, float(args.video_time_scale)),
        video_skip_animation=bool(args.video_skip_animation),
        video_http_timeout_sec=max(30, int(args.video_http_timeout_sec)),
        video_processing_time_limit_sec=max(30, int(args.video_processing_time_limit_sec)),
        strict_action_whitelist=bool(args.strict_action_whitelist),
        strict_unity_task_filter=bool(args.strict_unity_task_filter),

        unity_url=args.unity_url,
        unity_ports=unity_ports,
        unity_executable=args.unity_executable,
        unity_start_timeout_sec=args.unity_start_timeout_sec,
        unity_step_time_limit=max(1, args.unity_step_time_limit),
        unity_step_skip_animation=bool(args.unity_step_skip_animation),
        unity_find_solution=bool(args.unity_find_solution),
        unity_preflight_scene_id=args.unity_preflight_scene_id,
        unity_time_scale=max(0.1, float(unity_time_scale)),
        unity_episode_time_limit_sec=max(10, int(args.unity_episode_time_limit_sec)),
        unity_max_no_progress_steps=max(1, int(args.unity_max_no_progress_steps)),
        unity_fast=bool(args.unity_fast),
        unity_headless=bool(args.unity_headless),
        unity_log_mode=args.unity_log_mode,
        keep_unity_alive=bool(args.keep_unity_alive),
    )

    validate_runtime_config(cfg)

    paths = make_run_paths(cfg)
    logger = RunLogger(paths["logs"] / "run.log")
    logger.log(f"run_root={paths['root']}")
    logger.log(f"metrics_mode={cfg.metrics_mode} controller={cfg.controller} arch={cfg.arch}")

    if unity_enabled:
        logger.log(f"unity_enabled env_slots={cfg.env_slots} ports={','.join(cfg.unity_ports)} headless={cfg.unity_headless}")
        logger.log(f"unity_executable={str(Path(cfg.unity_executable).resolve())}")
        logger.log(f"unity_logs={paths['root'] / 'unity_logs'}")

    # client
    if cfg.controller == "http":
        client = HttpPolicyClient(cfg.url, cfg.timeout_sec, route_by_env_slot=True)
    else:
        policy = RandomPolicy(seed=cfg.seed, arch=cfg.arch)
        client = LocalPolicyClient(policy)

    runtime: Optional[UnityRuntime] = None
    if unity_enabled:
        runtime = build_unity_runtime(cfg, logger, paths["root"])

    # tasks
    all_tasks = load_tasks(Path(cfg.dataset_root), max_tasks=200000)
    if not all_tasks:
        raise SystemExit(f"No tasks found in {cfg.dataset_root}")

    if unity_enabled and cfg.strict_unity_task_filter:
        assert runtime is not None
        tasks = select_tasks_by_difficulty_with_unity_precheck(
            all_tasks,
            cfg.max_easy,
            cfg.max_medium,
            cfg.max_hard,
            cfg.max_tasks,
            runtime,
            cfg,
            logger,
        )
    else:
        tasks = select_tasks_by_difficulty(all_tasks, cfg.max_easy, cfg.max_medium, cfg.max_hard, cfg.max_tasks)

    if not tasks:
        raise SystemExit("No tasks selected with current limits (after compatibility filtering).")
    if unity_enabled and cfg.strict_unity_task_filter:
        got = Counter(t.difficulty for t in tasks)
        need = {"easy": cfg.max_easy, "medium": cfg.max_medium, "hard": cfg.max_hard}
        missing = {k: max(0, need[k] - int(got.get(k, 0))) for k in need}
        if any(v > 0 for v in missing.values()):
            logger.log(
                "warning:not_enough_unity_compatible_tasks "
                f"got={dict(got)} missing={missing} "
                "continuing_with_reduced_task_set"
            )

    scenarios = default_scenarios()
    specs = build_episode_specs(tasks, scenarios, cfg)
    logger.log(f"episodes_total={len(specs)}")

    # run
    t0 = time.time()
    rows = run_all_episodes(cfg, specs, client, runtime, logger)
    elapsed = time.time() - t0
    logger.log(f"run_elapsed_sec={elapsed:.3f} episodes={len(rows)}")

    video_examples: List[Dict[str, Any]] = []
    if cfg.save_example_videos:
        video_examples = render_example_videos(cfg, tasks, rows, runtime, paths["root"], logger)
        if video_examples:
            manifest_path = paths["root"] / "video_examples" / "video_examples_manifest.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(video_examples, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.log(f"saved video examples manifest: {manifest_path}")

    by_scenario: Dict[str, List[Dict[str, Any]]] = {}
    by_difficulty: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_scenario.setdefault(r["scenario"], []).append(r)
        by_difficulty.setdefault(r["difficulty"], []).append(r)

    report = {
        "run": {"name": paths["root"].name, "root": str(paths["root"]), "started_at": datetime.now().isoformat(), "elapsed_sec": elapsed},
        "config": asdict(cfg),
        "summary_all": aggregate(rows),
        "summary_by_scenario": {k: aggregate(v) for k, v in by_scenario.items()},
        "summary_by_difficulty": {k: aggregate(v) for k, v in by_difficulty.items()},
        "video_examples": video_examples,
        "episodes": rows,
    }

    report_json = paths["reports"] / "report.json"
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_episodes_csv(rows, paths["reports"] / "episodes.csv")
    write_summary_markdown(report, paths["reports"] / "summary.md")
    (paths["config"] / "config.json").write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2), encoding="utf-8")

    if cfg.save_traces:
        write_telemetry_jsonl(rows, paths["telemetry"] / "telemetry_steps.jsonl")

    if cfg.out_json:
        Path(cfg.out_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if cfg.out_telemetry_jsonl and cfg.save_traces:
        write_telemetry_jsonl(rows, Path(cfg.out_telemetry_jsonl))

    logger.log(f"saved report: {report_json}")
    logger.log("summary_all=" + json.dumps(report["summary_all"], ensure_ascii=False))

    if runtime is not None:
        runtime.close(keep_alive=cfg.keep_unity_alive)


if __name__ == "__main__":
    main()
