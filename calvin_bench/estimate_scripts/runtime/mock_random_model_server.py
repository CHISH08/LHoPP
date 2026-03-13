import argparse
import json
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict


class RandomPolicy:
    def __init__(self, seed: int, min_delay_ms: int, max_delay_ms: int) -> None:
        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._min_delay_ms = max(0, min_delay_ms)
        self._max_delay_ms = max(self._min_delay_ms, max_delay_ms)

    def _rand_uniform(self, low: float, high: float) -> float:
        with self._lock:
            return self._rng.uniform(low, high)

    def _rand_choice(self, values: list[Any]) -> Any:
        with self._lock:
            return self._rng.choice(values)

    def _rand_int(self, low: int, high: int) -> int:
        with self._lock:
            return self._rng.randint(low, high)

    def predict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        time.sleep(self._rand_uniform(self._min_delay_ms / 1000.0, self._max_delay_ms / 1000.0))
        level = str(payload.get("action_level_id", "L3")).upper()
        out: Dict[str, Any] = {
            "status": "ok",
            "action_raw": "",
            "action_exec": "",
            "action_level_id": level,
            "model_latency_sec": 0.0,
            "error_code": "",
            "error_message": "",
            "safety_intent": "",
            "fallback_mode": "",
            "replan_requested": False,
            "rollback_requested": False,
            "tokens_in": self._rand_int(20, 120),
            "tokens_out": self._rand_int(5, 80),
            "meta": {"policy": "mock_random_http"},
        }

        if level == "L1":
            allowed = payload.get("active_constraints", {}).get("allowed_symbolic_subtasks", [])
            if isinstance(allowed, list) and allowed:
                action = str(self._rand_choice([str(x) for x in allowed]))
                out["action_raw"] = action
                out["action_exec"] = action
            else:
                out["status"] = "error"
                out["error_code"] = "constraint_violation"
                out["error_message"] = "allowed_symbolic_subtasks is empty"
            return out

        if level in {"L2", "L3"}:
            vector = [self._rand_uniform(-1.0, 1.0) for _ in range(6)]
            vector.append(float(self._rand_choice([-1, 1])))
            out["action_raw"] = vector
            out["action_exec"] = vector
            return out

        if level == "L4":
            vector = [self._rand_uniform(-1.0, 1.0) for _ in range(7)]
            vector.append(float(self._rand_choice([-1, 1])))
            out["action_raw"] = vector
            out["action_exec"] = vector
            return out

        out["status"] = "unsupported"
        out["error_code"] = "unsupported_model_interface"
        out["error_message"] = f"unknown action level: {level}"
        return out


class RandomModelService:
    def __init__(self, policy: RandomPolicy, requests_log_path: Path, stats_path: Path) -> None:
        self.policy = policy
        self.requests_log_path = requests_log_path.resolve()
        self.stats_path = stats_path.resolve()
        self.requests_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stats: Dict[str, Any] = {
            "started_at_unix": time.time(),
            "requests_total": 0,
            "requests_ok": 0,
            "requests_error": 0,
            "last_request_unix": 0.0,
        }
        self._write_stats()

    def _write_stats(self) -> None:
        self.stats_path.write_text(json.dumps(self._stats, ensure_ascii=False, indent=2), encoding="utf-8")

    def handle_predict(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            response = self.policy.predict(payload)
            response["model_latency_sec"] = max(
                float(response.get("model_latency_sec", 0.0) or 0.0),
                time.perf_counter() - started,
            )
            status = str(response.get("status", "error") or "error")
        except Exception as exc:
            response = {
                "status": "error",
                "action_raw": "",
                "action_exec": "",
                "action_level_id": str(payload.get("action_level_id", "")),
                "model_latency_sec": time.perf_counter() - started,
                "error_code": "runtime_exception",
                "error_message": str(exc),
                "safety_intent": "",
                "fallback_mode": "",
                "replan_requested": False,
                "rollback_requested": False,
                "tokens_in": 0,
                "tokens_out": 0,
                "meta": {"server_exception": True},
            }
            status = "error"

        with self._lock:
            self._stats["requests_total"] += 1
            self._stats["last_request_unix"] = time.time()
            if status == "ok":
                self._stats["requests_ok"] += 1
            else:
                self._stats["requests_error"] += 1
            self._write_stats()

            row = {
                "ts_unix": time.time(),
                "status": status,
                "episode_id": str(payload.get("episode_id", "")),
                "step_idx": payload.get("step_idx", ""),
                "action_level_id": str(payload.get("action_level_id", "")),
                "scenario_profile_id": str(payload.get("scenario_profile_id", "")),
                "observation_profile_id": str(payload.get("observation_profile_id", "")),
                "error_code": str(response.get("error_code", "")),
            }
            with self.requests_log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

        return response


class RequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    service: RandomModelService | None = None

    def log_message(self, format: str, *args: Any) -> None:
        del format, args

    def _read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length > 0 else b"{}"
        body = json.loads(raw.decode("utf-8", errors="ignore"))
        if not isinstance(body, dict):
            raise RuntimeError("payload_not_object")
        return body

    def _write_json(self, code: int, body: Dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._write_json(404, {"status": "error", "error": "not_found"})
            return
        self._write_json(200, {"status": "ok"})

    def do_POST(self) -> None:
        if self.path != "/predict":
            self._write_json(404, {"status": "error", "error": "not_found"})
            return
        if self.service is None:
            self._write_json(500, {"status": "error", "error": "service_not_initialized"})
            return
        try:
            payload = self._read_json_body()
            response = self.service.handle_predict(payload)
            self._write_json(200, response)
        except Exception as exc:
            self._write_json(
                500,
                {
                    "status": "error",
                    "error_code": "runtime_exception",
                    "error_message": str(exc),
                },
            )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mock random HTTP model server for CALVIN step3 runs.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--min-delay-ms", type=int, default=1)
    parser.add_argument("--max-delay-ms", type=int, default=10)
    parser.add_argument(
        "--requests-log-path",
        default="calvin_bench/estimate_scripts/runs/mock_http_model/requests.jsonl",
    )
    parser.add_argument(
        "--stats-path",
        default="calvin_bench/estimate_scripts/runs/mock_http_model/server_stats.json",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    policy = RandomPolicy(seed=args.seed, min_delay_ms=args.min_delay_ms, max_delay_ms=args.max_delay_ms)
    service = RandomModelService(
        policy=policy,
        requests_log_path=Path(args.requests_log_path),
        stats_path=Path(args.stats_path),
    )
    RequestHandler.service = service

    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    server.daemon_threads = True
    print(f"[mock_random_model_server] listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
