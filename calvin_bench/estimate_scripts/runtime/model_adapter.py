import importlib
import importlib.util
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import requests


MODEL_RESPONSE_FIELDS = [
    "status",
    "action_raw",
    "action_exec",
    "action_level_id",
    "model_latency_sec",
    "error_code",
    "error_message",
    "safety_intent",
    "fallback_mode",
    "replan_requested",
    "rollback_requested",
    "tokens_in",
    "tokens_out",
    "meta",
]


def _response_template() -> Dict[str, Any]:
    return {
        "status": "error",
        "action_raw": "",
        "action_exec": "",
        "action_level_id": "",
        "model_latency_sec": 0.0,
        "error_code": "",
        "error_message": "",
        "safety_intent": "",
        "fallback_mode": "",
        "replan_requested": False,
        "rollback_requested": False,
        "tokens_in": 0,
        "tokens_out": 0,
        "meta": {},
    }


class BaseModelAdapter:
    def reset(self, episode_context: Dict[str, Any]) -> None:
        del episode_context

    def predict(self, step_payload: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError

    def close(self) -> None:
        return


@dataclass(frozen=True)
class HTTPModelConfig:
    host: str
    port: int
    timeout_sec: float
    endpoint: str = "/predict"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.endpoint}"


class HTTPModelAdapter(BaseModelAdapter):
    def __init__(self, config: HTTPModelConfig) -> None:
        self.config = config
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def predict(self, step_payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        out = _response_template()
        try:
            response = self._session.post(
                self.config.url,
                json=step_payload,
                timeout=self.config.timeout_sec,
            )
            out["model_latency_sec"] = time.perf_counter() - started
            if response.status_code != 200:
                out["status"] = "error"
                out["error_code"] = "runtime_exception"
                out["error_message"] = f"http_status_{response.status_code}"
                return out
            body = response.json()
            if not isinstance(body, dict):
                out["status"] = "error"
                out["error_code"] = "runtime_exception"
                out["error_message"] = "response_json_not_object"
                return out
            for field in MODEL_RESPONSE_FIELDS:
                if field in body:
                    out[field] = body[field]
            if not out["status"]:
                out["status"] = "error"
                out["error_code"] = "runtime_exception"
                out["error_message"] = "missing_status"
            return out
        except requests.Timeout:
            out["model_latency_sec"] = time.perf_counter() - started
            out["status"] = "error"
            out["error_code"] = "timeout_model"
            out["error_message"] = f"timeout>{self.config.timeout_sec}s"
            return out
        except Exception as exc:
            out["model_latency_sec"] = time.perf_counter() - started
            out["status"] = "error"
            out["error_code"] = "runtime_exception"
            out["error_message"] = str(exc)
            return out


def _load_python_class(spec: str):
    if ":" not in spec:
        raise ValueError("python model spec must be <module_or_file>:<ClassName>")
    module_or_file, class_name = spec.rsplit(":", 1)
    module_or_file = module_or_file.strip()
    class_name = class_name.strip()
    if not class_name:
        raise ValueError(f"Invalid python model spec: {spec}")

    if module_or_file.endswith(".py") or Path(module_or_file).exists():
        file_path = Path(module_or_file).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Python model file does not exist: {file_path}")
        module_name = f"calvin_runtime_model_{abs(hash(str(file_path)))}"
        spec_obj = importlib.util.spec_from_file_location(module_name, file_path)
        if spec_obj is None or spec_obj.loader is None:
            raise RuntimeError(f"Could not load model module from {file_path}")
        module = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(module)
    else:
        module = importlib.import_module(module_or_file)

    cls = getattr(module, class_name, None)
    if cls is None:
        raise AttributeError(f"Class {class_name} not found in {module_or_file}")
    return cls


class PythonModelAdapter(BaseModelAdapter):
    def __init__(self, model_spec: str, init_kwargs: Dict[str, Any] | None = None) -> None:
        cls = _load_python_class(model_spec)
        self._model = cls(**(init_kwargs or {}))
        self._has_predict = callable(getattr(self._model, "predict", None))
        self._has_step = callable(getattr(self._model, "step", None))
        if not self._has_predict and not self._has_step:
            raise RuntimeError("Python model must implement predict(step_payload) or step(obs, goal).")

    def reset(self, episode_context: Dict[str, Any]) -> None:
        reset_fn = getattr(self._model, "reset", None)
        if not callable(reset_fn):
            return
        try:
            reset_fn(episode_context)
        except TypeError:
            reset_fn()

    def predict(self, step_payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        out = _response_template()
        try:
            if self._has_predict:
                raw = self._model.predict(step_payload)
            else:
                raw = self._model.step(step_payload["observation_bundle_raw"], step_payload["current_instruction_text"])
            out["model_latency_sec"] = time.perf_counter() - started
            if isinstance(raw, dict):
                for field in MODEL_RESPONSE_FIELDS:
                    if field in raw:
                        out[field] = raw[field]
                if not out["action_exec"] and out["action_raw"]:
                    out["action_exec"] = out["action_raw"]
            else:
                out["status"] = "ok"
                out["action_raw"] = raw
                out["action_exec"] = raw
            if not out["status"]:
                out["status"] = "error"
                out["error_code"] = "runtime_exception"
                out["error_message"] = "missing_status"
            return out
        except Exception as exc:
            out["model_latency_sec"] = time.perf_counter() - started
            out["status"] = "error"
            out["error_code"] = "runtime_exception"
            out["error_message"] = str(exc)
            return out


class MockRandomAdapter(BaseModelAdapter):
    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    def predict(self, step_payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        level = str(step_payload.get("action_level_id", "L3"))
        out = _response_template()
        out["status"] = "ok"
        out["action_level_id"] = level
        if level == "L1":
            allowed = step_payload.get("active_constraints", {}).get("allowed_symbolic_subtasks", [])
            if not isinstance(allowed, list) or not allowed:
                out["status"] = "error"
                out["error_code"] = "constraint_violation"
                out["error_message"] = "no_symbolic_candidates"
            else:
                out["action_exec"] = str(self._rng.choice(allowed))
                out["action_raw"] = out["action_exec"]
        elif level in {"L2", "L3"}:
            values = [self._rng.uniform(-1.0, 1.0) for _ in range(6)]
            values.append(float(self._rng.choice([-1, 1])))
            out["action_exec"] = values
            out["action_raw"] = values
        elif level == "L4":
            values = [self._rng.uniform(-1.0, 1.0) for _ in range(7)]
            values.append(float(self._rng.choice([-1, 1])))
            out["action_exec"] = values
            out["action_raw"] = values
        else:
            out["status"] = "unsupported"
            out["error_code"] = "unsupported_model_interface"
            out["error_message"] = f"unknown action level: {level}"
        out["model_latency_sec"] = time.perf_counter() - started
        return out


def build_model_adapter(
    backend: str,
    model_host: str,
    model_port: int,
    model_timeout_sec: float,
    python_model_spec: str | None,
    python_model_kwargs: Dict[str, Any] | None,
    seed: int,
) -> BaseModelAdapter:
    backend = backend.strip().lower()
    if backend == "http":
        return HTTPModelAdapter(
            HTTPModelConfig(
                host=model_host,
                port=model_port,
                timeout_sec=model_timeout_sec,
            )
        )
    if backend == "python":
        if not python_model_spec:
            raise ValueError("--python-model-spec is required when --model-backend=python")
        return PythonModelAdapter(python_model_spec, init_kwargs=python_model_kwargs or {})
    if backend == "mock_random":
        return MockRandomAdapter(seed=seed)
    raise ValueError(f"Unsupported model backend: {backend}")
