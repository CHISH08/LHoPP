import argparse
import asyncio
import json
import random
import signal
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional


FORBIDDEN_KEYS = {"condition_id", "scenario_tag", "event_type"}


@dataclass
class ServerStats:
    started_at_unix: float
    requests_total: int = 0
    requests_ok: int = 0
    requests_error: int = 0
    inflight: int = 0
    max_inflight: int = 0
    last_request_unix: float = 0.0


class AsyncModelServer:
    def __init__(
        self,
        host: str,
        port: int,
        min_delay_ms: int,
        max_delay_ms: int,
        requests_log_path: Path,
        stats_path: Path,
        seed: int,
    ) -> None:
        self.host = host
        self.port = port
        self.min_delay_ms = max(0, min_delay_ms)
        self.max_delay_ms = max(self.min_delay_ms, max_delay_ms)
        self.requests_log_path = requests_log_path.resolve()
        self.stats_path = stats_path.resolve()
        self.rng = random.Random(seed)
        self.stats = ServerStats(started_at_unix=time.time())
        self._server: Optional[asyncio.base_events.Server] = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self.requests_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.stats_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_stats()
        self._server = await asyncio.start_server(self._handle_client, self.host, self.port)
        sockets = self._server.sockets or []
        bound = sockets[0].getsockname() if sockets else (self.host, self.port)
        print(f"[mock_model] listening on {bound}", flush=True)
        async with self._server:
            await self._stop_event.wait()
        self._write_stats()
        print("[mock_model] stopped", flush=True)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await self._read_request(reader)
            if request is None:
                writer.close()
                await writer.wait_closed()
                return

            async with self._lock:
                self.stats.inflight += 1
                self.stats.max_inflight = max(self.stats.max_inflight, self.stats.inflight)
                self.stats.requests_total += 1
                self.stats.last_request_unix = time.time()
                inflight_now = self.stats.inflight
                self._write_stats()

            await asyncio.sleep(self.rng.uniform(self.min_delay_ms / 1000.0, self.max_delay_ms / 1000.0))
            response_body = self._build_response(request["json"])
            await self._write_response(writer, 200, response_body)

            req_json = request["json"]
            log_row = {
                "ts_unix": time.time(),
                "request_id": req_json.get("request_id", ""),
                "episode_id": req_json.get("episode_id", ""),
                "step_idx": req_json.get("step_idx", ""),
                "worker_slot": req_json.get("worker_slot", ""),
                "has_forbidden_keys": any(k in req_json for k in FORBIDDEN_KEYS),
                "payload_keys": sorted(list(req_json.keys())),
                "available_actions_mask_size": len(req_json.get("available_actions_mask", []) or []),
                "inflight_at_receive": inflight_now,
            }
            self._append_request_log(log_row)

            async with self._lock:
                self.stats.requests_ok += 1
                self.stats.inflight = max(0, self.stats.inflight - 1)
                self._write_stats()
        except Exception as exc:
            try:
                await self._write_response(
                    writer,
                    500,
                    {
                        "status": "error",
                        "action_raw": "",
                        "action_exec": "",
                        "model_latency_sec": 0.0,
                        "error_code": "runtime_exception",
                        "error_message": str(exc),
                        "tokens_in": 0,
                        "tokens_out": 0,
                        "meta": {"server_exception": True},
                    },
                )
            except Exception:
                pass
            async with self._lock:
                self.stats.requests_error += 1
                self.stats.inflight = max(0, self.stats.inflight - 1)
                self._write_stats()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _read_request(self, reader: asyncio.StreamReader) -> Optional[Dict[str, Any]]:
        line = await reader.readline()
        if not line:
            return None
        parts = line.decode("utf-8", errors="ignore").strip().split()
        if len(parts) < 3:
            raise RuntimeError("invalid_request_line")
        method, path = parts[0], parts[1]
        if method.upper() != "POST" or path != "/predict":
            raise RuntimeError(f"unsupported_route:{method}:{path}")

        headers: Dict[str, str] = {}
        while True:
            h = await reader.readline()
            if not h:
                break
            if h in (b"\r\n", b"\n"):
                break
            text = h.decode("utf-8", errors="ignore").strip()
            if ":" in text:
                k, v = text.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        content_length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(content_length) if content_length > 0 else b"{}"
        payload = json.loads(body.decode("utf-8", errors="ignore"))
        if not isinstance(payload, dict):
            raise RuntimeError("payload_not_object")
        return {"method": method, "path": path, "json": payload}

    async def _write_response(self, writer: asyncio.StreamWriter, status_code: int, body: Dict[str, Any]) -> None:
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        reason = "OK" if status_code == 200 else "ERROR"
        headers = [
            f"HTTP/1.1 {status_code} {reason}",
            "Content-Type: application/json; charset=utf-8",
            f"Content-Length: {len(body_bytes)}",
            "Connection: close",
            "",
            "",
        ]
        writer.write("\r\n".join(headers).encode("utf-8") + body_bytes)
        await writer.drain()

    def _build_response(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mask = payload.get("available_actions_mask", []) or []
        if isinstance(mask, list) and len(mask) > 0:
            action = str(mask[self.rng.randrange(len(mask))])
            return {
                "status": "ok",
                "action_raw": action,
                "action_exec": action,
                "model_latency_sec": 0.0,
                "error_code": "",
                "error_message": "",
                "tokens_in": self.rng.randint(10, 80),
                "tokens_out": self.rng.randint(3, 24),
                "meta": {"policy": "random_mask_action"},
            }
        return {
            "status": "error",
            "action_raw": "",
            "action_exec": "",
            "model_latency_sec": 0.0,
            "error_code": "empty_action",
            "error_message": "available_actions_mask is empty",
            "tokens_in": 0,
            "tokens_out": 0,
            "meta": {"policy": "no_mask"},
        }

    def _append_request_log(self, row: Dict[str, Any]) -> None:
        with self.requests_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _write_stats(self) -> None:
        self.stats_path.write_text(json.dumps(asdict(self.stats), ensure_ascii=False, indent=2), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Async mock model server for step4 benchmark tests.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19000)
    parser.add_argument("--min-delay-ms", type=int, default=30)
    parser.add_argument("--max-delay-ms", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--requests-log-path",
        default="estimate_scripts/runs/mock_model/requests.jsonl",
        help="Path for per-request JSONL log.",
    )
    parser.add_argument(
        "--stats-path",
        default="estimate_scripts/runs/mock_model/server_stats.json",
        help="Path for server stats JSON.",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> None:
    server = AsyncModelServer(
        host=args.host,
        port=args.port,
        min_delay_ms=args.min_delay_ms,
        max_delay_ms=args.max_delay_ms,
        requests_log_path=Path(args.requests_log_path),
        stats_path=Path(args.stats_path),
        seed=args.seed,
    )
    loop = asyncio.get_running_loop()
    stop_called = False

    def _signal_stop() -> None:
        nonlocal stop_called
        if stop_called:
            return
        stop_called = True
        loop.create_task(server.stop())

    try:
        loop.add_signal_handler(signal.SIGINT, _signal_stop)
        loop.add_signal_handler(signal.SIGTERM, _signal_stop)
    except NotImplementedError:
        pass

    await server.start()


def main() -> None:
    args = _build_parser().parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()

