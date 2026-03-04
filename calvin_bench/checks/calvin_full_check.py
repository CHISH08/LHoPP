import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

import numpy as np

BENCH_ROOT = Path(__file__).resolve().parents[1]
REPO = (BENCH_ROOT / "calvin").resolve()
DATASET = Path(os.environ.get("CALVIN_DATASET", REPO / "dataset" / "task_D_D")).resolve()
TRAIN = DATASET / "training"
VAL = DATASET / "validation"
REPORT_PATH = (BENCH_ROOT / "reports" / "calvin_full_check_report.json").resolve()

results = []


def _safe(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, dict):
        return {str(k): _safe(val) for k, val in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_safe(x) for x in v]
    return str(v)


def check(name, fn):
    try:
        details = fn()
        results.append({"check": name, "ok": True, "details": _safe(details)})
    except Exception as e:
        results.append(
            {
                "check": name,
                "ok": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
            }
        )


def check_paths():
    required = [
        REPO,
        DATASET,
        TRAIN,
        VAL,
        TRAIN / "scene_info.npy",
        TRAIN / "ep_start_end_ids.npy",
        VAL / "ep_start_end_ids.npy",
        VAL / "lang_annotations" / "auto_lang_ann.npy",
        VAL / "lang_annotations" / "embeddings.npy",
        VAL / ".hydra" / "merged_config.yaml",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        raise RuntimeError(f"Missing required paths: {missing}")
    return {"checked": len(required), "missing": 0}


def check_python_stack():
    import importlib

    mods = [
        "torch",
        "hydra",
        "omegaconf",
        "pybullet",
        "pytorch_lightning",
        "cv2",
        "numpy",
        "calvin_env",
        "calvin_agent",
        "tacto",
        "pyrender",
        "trimesh",
    ]
    versions = {}
    for m in mods:
        mod = importlib.import_module(m)
        versions[m] = getattr(mod, "__version__", "ok")
    return versions


def check_cuda():
    import pybullet as p
    import torch

    return {
        "torch_cuda_available": bool(torch.cuda.is_available()),
        "torch_cuda_device_count": int(torch.cuda.device_count()),
        "torch_version": torch.__version__,
        "pybullet_api_version": int(p.getAPIVersion()),
    }


def check_dataset_content():
    val_eps = sorted(VAL.glob("episode_*.npz"))
    train_eps = sorted(TRAIN.glob("episode_*.npz"))
    if not val_eps or not train_eps:
        raise RuntimeError("No episode files found in training/validation")

    sample = np.load(val_eps[0])
    keys = sorted(sample.files)
    expected = {
        "rgb_static",
        "rgb_gripper",
        "rgb_tactile",
        "depth_static",
        "depth_gripper",
        "depth_tactile",
        "robot_obs",
        "scene_obs",
        "actions",
        "rel_actions",
    }
    missing = sorted(expected - set(keys))
    if missing:
        raise RuntimeError(f"Sample episode missing keys: {missing}")

    scene_info = np.load(TRAIN / "scene_info.npy", allow_pickle=True).item()
    if not isinstance(scene_info, dict) or len(scene_info) == 0:
        raise RuntimeError("scene_info.npy is empty or invalid")

    return {
        "train_episode_count": len(train_eps),
        "val_episode_count": len(val_eps),
        "sample_episode": str(val_eps[0].name),
        "sample_keys_count": len(keys),
        "scene_info_entries": len(scene_info),
    }


def check_env_all_sensors():
    from calvin_env.envs.play_table_env import get_env

    env = get_env(VAL, show_gui=False)
    try:
        obs = env.reset()
        rgb_keys = sorted(obs["rgb_obs"].keys())
        depth_keys = sorted(obs["depth_obs"].keys())
        needed_rgb = {"rgb_static", "rgb_gripper", "rgb_tactile"}
        needed_depth = {"depth_static", "depth_gripper", "depth_tactile"}
        if not needed_rgb.issubset(set(rgb_keys)):
            raise RuntimeError(f"Missing rgb keys: {sorted(needed_rgb - set(rgb_keys))}")
        if not needed_depth.issubset(set(depth_keys)):
            raise RuntimeError(f"Missing depth keys: {sorted(needed_depth - set(depth_keys))}")

        action = np.array([0, 0, 0, 0, 0, 0, 1], dtype=np.float32)
        obs2, r, d, info = env.step(action)
        return {
            "rgb_keys": rgb_keys,
            "depth_keys": depth_keys,
            "step_reward": float(r),
            "step_done": bool(d),
            "info_keys": sorted(info.keys()),
            "post_step_rgb_tactile_shape": list(obs2["rgb_obs"]["rgb_tactile"].shape),
            "post_step_depth_tactile_shape": list(obs2["depth_obs"]["depth_tactile"].shape),
        }
    finally:
        env.close()


def check_env_static_only():
    from calvin_env.envs.play_table_env import get_env

    obs_space = {
        "rgb_obs": ["rgb_static"],
        "depth_obs": [],
        "state_obs": ["robot_obs"],
        "actions": ["rel_actions"],
        "language": ["language"],
    }
    env = get_env(VAL, show_gui=False, obs_space=obs_space)
    try:
        obs = env.reset()
        rgb_keys = sorted(obs["rgb_obs"].keys())
        if rgb_keys != ["rgb_static"]:
            raise RuntimeError(f"Unexpected static-only rgb keys: {rgb_keys}")
        obs2, r, d, info = env.step(np.array([0, 0, 0, 0, 0, 0, 1], dtype=np.float32))
        return {
            "rgb_keys": rgb_keys,
            "step_done": bool(d),
            "info_keys": sorted(info.keys()),
            "post_step_keys": sorted(obs2["rgb_obs"].keys()),
        }
    finally:
        env.close()


def check_eval_cli_help():
    cmd = [
        sys.executable,
        "evaluation/evaluate_policy.py",
        "--help",
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(REPO / "calvin_models" / "calvin_agent"),
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    out = proc.stdout + proc.stderr
    if "--custom_model" not in out:
        raise RuntimeError("evaluate_policy.py --help output missing --custom_model")
    return {"help_output_contains_custom_model": True, "returncode": proc.returncode}


def check_eval_rollout_path():
    from collections import defaultdict

    import calvin_agent.evaluation.evaluate_policy as ep
    import hydra
    from omegaconf import OmegaConf

    conf_dir = REPO / "calvin_models" / "conf"
    task_cfg = OmegaConf.load(conf_dir / "callbacks" / "rollout" / "tasks" / "new_playtable_tasks.yaml")
    task_oracle = hydra.utils.instantiate(task_cfg)
    val_annotations = OmegaConf.load(conf_dir / "annotations" / "new_playtable_validation.yaml")

    class DummyModel(ep.CalvinBaseModel):
        def reset(self):
            return None

        def step(self, obs, goal):
            return np.array([0, 0, 0, 0, 0, 0, 1], dtype=np.float32)

    env = ep.make_env(str(DATASET))
    old_len = ep.EP_LEN
    ep.EP_LEN = 10
    try:
        success = ep.rollout(env, DummyModel(), task_oracle, "open_drawer", val_annotations, defaultdict(list), False)
        if not isinstance(success, bool):
            raise RuntimeError(f"rollout returned non-bool: {type(success)}")
        return {"rollout_return_type": type(success).__name__, "rollout_success_value": bool(success)}
    finally:
        ep.EP_LEN = old_len
        env.close()


def check_write_permissions():
    target = BENCH_ROOT / "benchmarks" / "calvin" / "smoke_tmp"
    target.mkdir(parents=True, exist_ok=True)
    f = target / "write_test.txt"
    f.write_text("ok", encoding="utf-8")
    txt = f.read_text(encoding="utf-8")
    f.unlink(missing_ok=True)
    return {"dir": str(target), "roundtrip": txt}


check("paths", check_paths)
check("python_stack", check_python_stack)
check("cuda_and_pybullet", check_cuda)
check("dataset_content", check_dataset_content)
check("env_all_sensors", check_env_all_sensors)
check("env_static_only", check_env_static_only)
check("eval_cli_help", check_eval_cli_help)
check("eval_rollout_path", check_eval_rollout_path)
check("write_permissions", check_write_permissions)

status = "PASS" if all(r["ok"] for r in results) else "FAIL"
report = {
    "status": status,
    "bench_root": str(BENCH_ROOT),
    "repo": str(REPO),
    "dataset": str(DATASET),
    "checks": results,
}
REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print(f"FULL_CHECK_STATUS: {status}")
print(f"FULL_CHECK_REPORT: {REPORT_PATH}")
for r in results:
    if r["ok"]:
        print(f"[OK] {r['check']}")
    else:
        print(f"[FAIL] {r['check']}: {r.get('error', 'unknown')}")

if status != "PASS":
    sys.exit(1)
