import sys
import time
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, TextIO


def _ensure_simulation_import_path() -> None:
    # Required for depth/EXR decoding in OpenCV used by VirtualHome comm.
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    repo_root = Path(__file__).resolve().parents[2]
    simulation_path = repo_root / "virtualhome" / "virtualhome" / "simulation"
    if not simulation_path.exists():
        raise FileNotFoundError(f"VirtualHome simulation folder not found: {simulation_path}")
    sim_path_str = str(simulation_path)
    if sim_path_str not in sys.path:
        sys.path.append(sim_path_str)


_ensure_simulation_import_path()

from unity_simulator.comm_unity import UnityCommunication  # noqa: E402


@dataclass
class EnvSlot:
    slot_id: int
    worker_id: int
    port: int
    process: Optional[subprocess.Popen] = None
    comm: Optional[UnityCommunication] = None
    startup_time_sec: Optional[float] = None
    pid: Optional[int] = None
    status: str = "pending"
    error: str = ""


class UnityEnvPool:
    def __init__(
        self,
        unity_exe: Path,
        parallel_workers: int,
        base_port: int,
        startup_timeout_sec: float = 120.0,
        no_graphics: bool = False,
        logging: bool = False,
    ) -> None:
        if parallel_workers <= 0:
            raise ValueError("parallel_workers must be > 0")
        self.unity_exe = unity_exe.resolve()
        if not self.unity_exe.exists():
            raise FileNotFoundError(f"Unity executable not found: {self.unity_exe}")
        self.parallel_workers = parallel_workers
        self.base_port = base_port
        self.startup_timeout_sec = startup_timeout_sec
        self.no_graphics = no_graphics
        self.logging = logging
        self.log_dir = self.unity_exe.parent
        self.slots: List[EnvSlot] = [
            EnvSlot(slot_id=i, worker_id=i, port=base_port + i) for i in range(parallel_workers)
        ]

    def start_all(self) -> List[EnvSlot]:
        for slot in self.slots:
            self._start_slot(slot)
        return self.slots

    def _start_slot(self, slot: EnvSlot) -> None:
        start_ts = time.perf_counter()
        std_stream: TextIO | int = subprocess.DEVNULL
        try:
            log_path = self.log_dir / f"Player_{slot.port}.log"
            cmd = [
                str(self.unity_exe),
                "-batchmode",
                f"-http-port={slot.port}",
                f"-logFile {log_path.name}",
            ]
            if self.no_graphics:
                cmd.append("-nographics")
            if self.logging:
                std_stream = (self.log_dir / f"port_{slot.port}.txt").open("w", encoding="utf-8")
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.unity_exe.parent),
                stdout=std_stream,
                stderr=std_stream,
                start_new_session=True,
            )
            comm = UnityCommunication(port=str(slot.port), timeout_wait=8)
            self._wait_for_connection(comm, timeout_sec=self.startup_timeout_sec)

            slot.process = proc
            slot.comm = comm
            slot.startup_time_sec = time.perf_counter() - start_ts
            slot.pid = proc.pid
            slot.status = "ready"
            slot.error = ""
        except Exception as exc:  # pragma: no cover - depends on runtime env
            slot.startup_time_sec = time.perf_counter() - start_ts
            slot.status = "error"
            slot.error = str(exc)
            # Best-effort cleanup for partially initialized slot.
            try:
                if slot.process is not None and slot.process.poll() is None:
                    slot.process.kill()
            except Exception:
                pass
            slot.process = None
            slot.comm = None
        finally:
            if self.logging and std_stream not in (None, subprocess.DEVNULL):
                try:
                    std_stream.close()
                except Exception:
                    pass

    @staticmethod
    def _wait_for_connection(comm: UnityCommunication, timeout_sec: float) -> None:
        deadline = time.perf_counter() + timeout_sec
        last_error = "timeout"
        while time.perf_counter() < deadline:
            try:
                ok = comm.check_connection()
                if ok:
                    return
                last_error = "check_connection returned false"
            except Exception as exc:  # pragma: no cover - runtime dependent
                last_error = str(exc)
            time.sleep(1.0)
        raise TimeoutError(f"Unity connection not ready within {timeout_sec}s: {last_error}")

    def close_all(self) -> None:
        for slot in self.slots:
            try:
                if slot.process is not None and slot.process.poll() is None:
                    slot.process.kill()
                    try:
                        slot.process.wait(timeout=3)
                    except Exception:
                        pass
            except Exception:
                pass
            slot.process = None
            slot.comm = None

    def __enter__(self) -> "UnityEnvPool":
        self.start_all()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close_all()
