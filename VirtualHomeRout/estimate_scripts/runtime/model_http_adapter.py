import time
from dataclasses import dataclass
from typing import Any, Dict

import requests


MODEL_RESPONSE_FIELDS = [
    "status",
    "action_raw",
    "action_exec",
    "model_latency_sec",
    "error_code",
    "error_message",
    "tokens_in",
    "tokens_out",
    "meta",
]


@dataclass(frozen=True)
class ModelHTTPConfig:
    host: str
    port: int
    timeout_sec: float
    endpoint: str = "/predict"

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}{self.endpoint}"


def _response_template() -> Dict[str, Any]:
    return {
        "status": "error",
        "action_raw": "",
        "action_exec": "",
        "model_latency_sec": 0.0,
        "error_code": "",
        "error_message": "",
        "tokens_in": 0,
        "tokens_out": 0,
        "meta": {},
    }


class ModelHTTPAdapter:
    def __init__(self, config: ModelHTTPConfig) -> None:
        self.config = config
        self._session = requests.Session()

    def close(self) -> None:
        self._session.close()

    def predict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        out = _response_template()
        try:
            resp = self._session.post(
                self.config.url,
                json=payload,
                timeout=self.config.timeout_sec,
            )
            latency = time.perf_counter() - started
            out["model_latency_sec"] = latency
            if resp.status_code != 200:
                out["status"] = "error"
                out["error_code"] = "runtime_exception"
                out["error_message"] = f"http_status_{resp.status_code}"
                return out

            try:
                data = resp.json()
            except Exception as exc:
                out["status"] = "error"
                out["error_code"] = "runtime_exception"
                out["error_message"] = f"invalid_json: {exc}"
                return out

            if not isinstance(data, dict):
                out["status"] = "error"
                out["error_code"] = "runtime_exception"
                out["error_message"] = "response_json_not_object"
                return out

            for key in MODEL_RESPONSE_FIELDS:
                if key in data:
                    out[key] = data[key]

            if not out.get("status"):
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
