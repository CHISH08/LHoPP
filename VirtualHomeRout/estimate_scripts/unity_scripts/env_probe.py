import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .env_pool import EnvSlot, UnityEnvPool


IMAGE_MODES = [
    "normal",
    "seg_inst",
    "seg_class",
    "depth",
    "flow",
    "albedo",
    "illumination",
    "surf_normals",
]

PREFERRED_TARGETS = [
    "chair",
    "sofa",
    "bed",
    "table",
    "fridge",
    "microwave",
    "cabinet",
    "door",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"unity_bootstrap_{stamp}"


def _exc_to_str(exc: Exception) -> str:
    explicit = getattr(exc, "message", None)
    if explicit:
        return str(explicit)
    return str(exc)


def _safe_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _timed_call(fn) -> Tuple[float, Any]:
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def _record_sensor(
    rows: List[Dict[str, Any]],
    run_id: str,
    slot: EnvSlot,
    probe_start_ts: float,
    sensor_name: str,
    sensor_mode: str,
    success: bool,
    latency_sec: float,
    message: str,
    error: str,
) -> None:
    rows.append(
        {
            "run_id": run_id,
            "slot_id": slot.slot_id,
            "worker_id": slot.worker_id,
            "port": slot.port,
            "sensor_name": sensor_name,
            "sensor_mode": sensor_mode,
            "success": str(bool(success)).lower(),
            "latency_sec": f"{latency_sec:.6f}",
            "env_wallclock_step_sec": f"{time.perf_counter() - probe_start_ts:.6f}",
            "message": message,
            "error": error,
        }
    )


def _record_interaction(
    rows: List[Dict[str, Any]],
    run_id: str,
    slot: EnvSlot,
    probe_start_ts: float,
    operation: str,
    action_line: str,
    success: bool,
    latency_sec: float,
    message: str,
    error: str,
) -> None:
    rows.append(
        {
            "run_id": run_id,
            "slot_id": slot.slot_id,
            "worker_id": slot.worker_id,
            "port": slot.port,
            "operation": operation,
            "action_line": action_line,
            "success": str(bool(success)).lower(),
            "latency_sec": f"{latency_sec:.6f}",
            "sim_exec_time_step_sec": f"{latency_sec:.6f}",
            "env_wallclock_step_sec": f"{time.perf_counter() - probe_start_ts:.6f}",
            "message": message,
            "error": error,
        }
    )


def _build_probe_actions(graph: Dict[str, Any]) -> List[str]:
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    actions: List[str] = [
        "[Walk] <chair> (1)",
        "<char0> [Walk] <chair> (1)",
        "[Find] <chair> (1)",
        "<char0> [Find] <chair> (1)",
        "[Walk] <door> (1)",
        "[Find] <door> (1)",
        "<char0> [Walk] <tv> (1)",
    ]
    if not nodes:
        return actions

    candidates: List[Tuple[str, int]] = []
    for pref in PREFERRED_TARGETS:
        for node in nodes:
            if node.get("class_name") == pref and isinstance(node.get("id"), int):
                candidates.append((node["class_name"], node["id"]))
                break

    for node in nodes[:150]:
        name = node.get("class_name")
        node_id = node.get("id")
        if not isinstance(name, str) or not isinstance(node_id, int):
            continue
        if name == "character":
            continue
        candidates.append((name, node_id))

    for idx, (name, node_id) in enumerate(candidates):
        if idx >= 12:
            break
        actions.extend(
            [
                f"<char0> [Find] <{name}> ({node_id})",
                f"[Find] <{name}> ({node_id})",
                f"[Walk] <{name}> ({node_id})",
            ]
        )
    # Stable order + de-dup
    deduped: List[str] = []
    seen = set()
    for action in actions:
        if action in seen:
            continue
        seen.add(action)
        deduped.append(action)
    return deduped


def _probe_slot(
    run_id: str,
    slot: EnvSlot,
    scene_id: int,
    image_width: int,
    image_height: int,
    time_scale: float,
    skip_animation: bool,
) -> Tuple[bool, List[str], List[Dict[str, Any]], List[Dict[str, Any]]]:
    sensor_rows: List[Dict[str, Any]] = []
    interaction_rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    probe_start = time.perf_counter()

    if slot.comm is None:
        errors.append("slot comm is not initialized")
        return False, errors, sensor_rows, interaction_rows

    comm = slot.comm

    # 1) check_connection
    try:
        latency, ok = _timed_call(comm.check_connection)
        _record_sensor(
            sensor_rows,
            run_id,
            slot,
            probe_start,
            sensor_name="check_connection",
            sensor_mode="n/a",
            success=bool(ok),
            latency_sec=latency,
            message="ok" if ok else "check_connection returned false",
            error="",
        )
        if not ok:
            errors.append("check_connection returned false")
    except Exception as exc:
        latency = 0.0
        err = _exc_to_str(exc)
        _record_sensor(
            sensor_rows,
            run_id,
            slot,
            probe_start,
            sensor_name="check_connection",
            sensor_mode="n/a",
            success=False,
            latency_sec=latency,
            message="",
            error=err,
        )
        errors.append(f"check_connection failed: {err}")
        return False, errors, sensor_rows, interaction_rows

    # 2) reset + add_character
    try:
        latency, ok_reset = _timed_call(lambda: comm.reset(scene_id))
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="reset_init",
            action_line=f"reset({scene_id})",
            success=bool(ok_reset),
            latency_sec=latency,
            message="ok" if ok_reset else "reset returned false",
            error="",
        )
        if not ok_reset:
            errors.append("reset(scene_id) returned false")
    except Exception as exc:
        err = _exc_to_str(exc)
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="reset_init",
            action_line=f"reset({scene_id})",
            success=False,
            latency_sec=0.0,
            message="",
            error=err,
        )
        errors.append(f"reset(scene_id) failed: {err}")
        return False, errors, sensor_rows, interaction_rows

    try:
        latency, ok_add = _timed_call(comm.add_character)
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="add_character_init",
            action_line="add_character()",
            success=bool(ok_add),
            latency_sec=latency,
            message="ok" if ok_add else "add_character returned false",
            error="",
        )
        if not ok_add:
            errors.append("add_character returned false")
    except Exception as exc:
        err = _exc_to_str(exc)
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="add_character_init",
            action_line="add_character()",
            success=False,
            latency_sec=0.0,
            message="",
            error=err,
        )
        errors.append(f"add_character failed: {err}")
        return False, errors, sensor_rows, interaction_rows

    # 3) environment_graph
    graph: Dict[str, Any] = {}
    try:
        latency, graph_resp = _timed_call(comm.environment_graph)
        ok_graph, graph = graph_resp
        msg = ""
        if ok_graph and isinstance(graph, dict):
            msg = f"nodes={len(graph.get('nodes', []))}, edges={len(graph.get('edges', []))}"
        _record_sensor(
            sensor_rows,
            run_id,
            slot,
            probe_start,
            sensor_name="environment_graph",
            sensor_mode="graph",
            success=bool(ok_graph and isinstance(graph, dict)),
            latency_sec=latency,
            message=msg,
            error="" if ok_graph else "environment_graph returned false",
        )
        if not ok_graph or not isinstance(graph, dict):
            errors.append("environment_graph is not available")
    except Exception as exc:
        err = _exc_to_str(exc)
        _record_sensor(
            sensor_rows,
            run_id,
            slot,
            probe_start,
            sensor_name="environment_graph",
            sensor_mode="graph",
            success=False,
            latency_sec=0.0,
            message="",
            error=err,
        )
        errors.append(f"environment_graph failed: {err}")

    if errors:
        return False, errors, sensor_rows, interaction_rows

    # 4) camera_count
    first_camera_id: Optional[int] = None
    camera_ids: List[int] = []
    try:
        latency, count_resp = _timed_call(comm.camera_count)
        ok_count, camera_count = count_resp
        if ok_count and isinstance(camera_count, int) and camera_count > 0:
            first_camera_id = 0
            camera_ids = list(range(camera_count))
        _record_sensor(
            sensor_rows,
            run_id,
            slot,
            probe_start,
            sensor_name="camera_count",
            sensor_mode="count",
            success=bool(ok_count and isinstance(camera_count, int) and camera_count > 0),
            latency_sec=latency,
            message=f"count={camera_count}",
            error="" if ok_count else "camera_count returned false",
        )
        if not ok_count or not isinstance(camera_count, int) or camera_count <= 0:
            errors.append("camera_count is not positive")
    except Exception as exc:
        err = _exc_to_str(exc)
        _record_sensor(
            sensor_rows,
            run_id,
            slot,
            probe_start,
            sensor_name="camera_count",
            sensor_mode="count",
            success=False,
            latency_sec=0.0,
            message="",
            error=err,
        )
        errors.append(f"camera_count failed: {err}")

    # 5) camera_data for all cameras
    if not errors:
        try:
            latency, data_resp = _timed_call(lambda: comm.camera_data(camera_ids))
            ok_data, camera_data = data_resp
            _record_sensor(
                sensor_rows,
                run_id,
                slot,
                probe_start,
                sensor_name="camera_data",
                sensor_mode="all",
                success=bool(ok_data and isinstance(camera_data, list) and len(camera_data) == len(camera_ids)),
                latency_sec=latency,
                message=f"returned={len(camera_data) if isinstance(camera_data, list) else 'n/a'}",
                error="" if ok_data else "camera_data returned false",
            )
            if not ok_data or not isinstance(camera_data, list) or len(camera_data) != len(camera_ids):
                errors.append("camera_data mismatch")
        except Exception as exc:
            err = _exc_to_str(exc)
            _record_sensor(
                sensor_rows,
                run_id,
                slot,
                probe_start,
                sensor_name="camera_data",
                sensor_mode="all",
                success=False,
                latency_sec=0.0,
                message="",
                error=err,
            )
            errors.append(f"camera_data failed: {err}")

    # 6) camera_image for all required modes
    if not errors and first_camera_id is not None:
        for mode in IMAGE_MODES:
            try:
                latency, image_resp = _timed_call(
                    lambda m=mode: comm.camera_image(
                        [first_camera_id],
                        mode=m,
                        image_width=image_width,
                        image_height=image_height,
                    )
                )
                ok_img, images = image_resp
                ok_mode = bool(ok_img and isinstance(images, list) and len(images) > 0)
                _record_sensor(
                    sensor_rows,
                    run_id,
                    slot,
                    probe_start,
                    sensor_name="camera_image",
                    sensor_mode=mode,
                    success=ok_mode,
                    latency_sec=latency,
                    message=f"frames={len(images) if isinstance(images, list) else 0}",
                    error="" if ok_mode else "camera_image returned empty or false",
                )
                if not ok_mode:
                    errors.append(f"camera_image mode failed: {mode}")
            except Exception as exc:
                err = _exc_to_str(exc)
                _record_sensor(
                    sensor_rows,
                    run_id,
                    slot,
                    probe_start,
                    sensor_name="camera_image",
                    sensor_mode=mode,
                    success=False,
                    latency_sec=0.0,
                    message="",
                    error=err,
                )
                errors.append(f"camera_image mode failed: {mode}: {err}")

    # 7) get_visible_objects + instance_colors
    if not errors and first_camera_id is not None:
        try:
            latency, vis_resp = _timed_call(lambda: comm.get_visible_objects(first_camera_id))
            ok_vis, visible = vis_resp
            _record_sensor(
                sensor_rows,
                run_id,
                slot,
                probe_start,
                sensor_name="get_visible_objects",
                sensor_mode="normal",
                success=bool(ok_vis),
                latency_sec=latency,
                message=f"objects={len(visible) if isinstance(visible, list) else 'n/a'}",
                error="" if ok_vis else "get_visible_objects returned false",
            )
            if not ok_vis:
                errors.append("get_visible_objects returned false")
        except Exception as exc:
            err = _exc_to_str(exc)
            _record_sensor(
                sensor_rows,
                run_id,
                slot,
                probe_start,
                sensor_name="get_visible_objects",
                sensor_mode="normal",
                success=False,
                latency_sec=0.0,
                message="",
                error=err,
            )
            errors.append(f"get_visible_objects failed: {err}")

        try:
            latency, colors_resp = _timed_call(comm.instance_colors)
            ok_colors, mapping = colors_resp
            _record_sensor(
                sensor_rows,
                run_id,
                slot,
                probe_start,
                sensor_name="instance_colors",
                sensor_mode="seg_inst",
                success=bool(ok_colors and isinstance(mapping, dict)),
                latency_sec=latency,
                message=f"items={len(mapping) if isinstance(mapping, dict) else 'n/a'}",
                error="" if ok_colors else "instance_colors returned false",
            )
            if not ok_colors or not isinstance(mapping, dict):
                errors.append("instance_colors is unavailable")
        except Exception as exc:
            err = _exc_to_str(exc)
            _record_sensor(
                sensor_rows,
                run_id,
                slot,
                probe_start,
                sensor_name="instance_colors",
                sensor_mode="seg_inst",
                success=False,
                latency_sec=0.0,
                message="",
                error=err,
            )
            errors.append(f"instance_colors failed: {err}")

    # 8) interaction test with short valid action
    if not errors:
        action_candidates = _build_probe_actions(graph)
        if not action_candidates:
            errors.append("no probe action candidates found from environment_graph")
        else:
            probe_succeeded = False
            last_error = ""
            for action_line in action_candidates[:12]:
                try:
                    latency, render_resp = _timed_call(
                        lambda: comm.render_script(
                            [action_line],
                            find_solution=True,
                            recording=False,
                            image_synthesis=[],
                            skip_animation=skip_animation,
                            time_scale=time_scale,
                            processing_time_limit=20,
                        )
                    )
                    ok_render, render_msg = render_resp
                    _record_interaction(
                        interaction_rows,
                        run_id,
                        slot,
                        probe_start,
                        operation="render_script_probe",
                        action_line=action_line,
                        success=bool(ok_render),
                        latency_sec=latency,
                        message=_safe_json(render_msg),
                        error="" if ok_render else "render_script returned false",
                    )
                    if ok_render:
                        probe_succeeded = True
                        break
                    last_error = f"render_script returned false: {_safe_json(render_msg)}"
                except Exception as exc:
                    err = _exc_to_str(exc)
                    last_error = err
                    _record_interaction(
                        interaction_rows,
                        run_id,
                        slot,
                        probe_start,
                        operation="render_script_probe",
                        action_line=action_line,
                        success=False,
                        latency_sec=0.0,
                        message="",
                        error=err,
                    )
            if not probe_succeeded:
                errors.append(f"no valid action candidate rendered successfully ({last_error})")

    # 9) return to init state for standby
    try:
        latency, ok_reset = _timed_call(lambda: comm.reset(scene_id))
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="reset_standby",
            action_line=f"reset({scene_id})",
            success=bool(ok_reset),
            latency_sec=latency,
            message="ok" if ok_reset else "reset returned false",
            error="",
        )
        if not ok_reset:
            errors.append("reset_standby returned false")
    except Exception as exc:
        err = _exc_to_str(exc)
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="reset_standby",
            action_line=f"reset({scene_id})",
            success=False,
            latency_sec=0.0,
            message="",
            error=err,
        )
        errors.append(f"reset_standby failed: {err}")

    try:
        latency, ok_add = _timed_call(comm.add_character)
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="add_character_standby",
            action_line="add_character()",
            success=bool(ok_add),
            latency_sec=latency,
            message="ok" if ok_add else "add_character returned false",
            error="",
        )
        if not ok_add:
            errors.append("add_character_standby returned false")
    except Exception as exc:
        err = _exc_to_str(exc)
        _record_interaction(
            interaction_rows,
            run_id,
            slot,
            probe_start,
            operation="add_character_standby",
            action_line="add_character()",
            success=False,
            latency_sec=0.0,
            message="",
            error=err,
        )
        errors.append(f"add_character_standby failed: {err}")

    return len(errors) == 0, errors, sensor_rows, interaction_rows


def _build_registry_rows(run_id: str, slots: List[EnvSlot]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for slot in slots:
        rows.append(
            {
                "run_id": run_id,
                "slot_id": slot.slot_id,
                "worker_id": slot.worker_id,
                "port": slot.port,
                "pid": slot.pid if slot.pid is not None else "",
                "status": slot.status,
                "startup_time_sec": f"{slot.startup_time_sec:.6f}" if slot.startup_time_sec is not None else "",
                "error": slot.error,
            }
        )
    return rows


def _standby_loop(standby_seconds: int = 0) -> str:
    start = time.perf_counter()
    if standby_seconds > 0:
        print(f"[step2] Standby for {standby_seconds}s (timeout mode).")
    else:
        print("[step2] Standby mode active. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
            if standby_seconds > 0 and (time.perf_counter() - start) >= standby_seconds:
                return "timeout"
    except KeyboardInterrupt:
        return "keyboard_interrupt"


def run_unity_bootstrap(
    unity_exe: Path,
    run_root: Path,
    parallel_workers: int,
    base_port: int,
    scene_id: int,
    time_scale: float,
    skip_animation: bool,
    image_width: int,
    image_height: int,
    standby_seconds: int = 0,
) -> Dict[str, Any]:
    run_id = _build_run_id()
    env_setup_dir = run_root.resolve() / run_id / "env_setup"
    env_setup_dir.mkdir(parents=True, exist_ok=True)

    env_registry_path = env_setup_dir / "env_registry.csv"
    sensor_probe_path = env_setup_dir / "sensor_probe.csv"
    interaction_probe_path = env_setup_dir / "interaction_probe.csv"
    health_report_path = env_setup_dir / "health_report.json"

    sensor_rows: List[Dict[str, Any]] = []
    interaction_rows: List[Dict[str, Any]] = []
    slot_errors: Dict[str, List[str]] = {}
    standby_exit_reason = "not_entered"
    standby_entered_at_utc = ""

    pool = UnityEnvPool(
        unity_exe=unity_exe,
        parallel_workers=parallel_workers,
        base_port=base_port,
    )

    try:
        print(
            f"[step2] Starting Unity bootstrap: workers={parallel_workers}, "
            f"base_port={base_port}, scene_id={scene_id}"
        )
        print(f"[step2] Unity executable: {unity_exe.resolve()}")
        slots = pool.start_all()
        print(f"[step2] Run ID: {run_id}")
        print(f"[step2] Artifacts folder: {env_setup_dir}")

        for slot in slots:
            if slot.status != "ready":
                slot.status = "not_ready"
                slot_errors[str(slot.slot_id)] = [slot.error or "startup failed"]
                print(
                    f"[step2] Slot {slot.slot_id} not ready on port {slot.port}: "
                    f"{slot.error or 'startup failed'}"
                )
                continue

            print(
                f"[step2] Slot {slot.slot_id} ready on port {slot.port} "
                f"(pid={slot.pid}, startup={slot.startup_time_sec:.2f}s). Probing..."
            )
            ready, errors, slot_sensor_rows, slot_interaction_rows = _probe_slot(
                run_id=run_id,
                slot=slot,
                scene_id=scene_id,
                image_width=image_width,
                image_height=image_height,
                time_scale=time_scale,
                skip_animation=skip_animation,
            )
            sensor_rows.extend(slot_sensor_rows)
            interaction_rows.extend(slot_interaction_rows)

            if ready:
                slot.status = "ready"
                slot.error = ""
                print(f"[step2] Slot {slot.slot_id} probe passed.")
            else:
                slot.status = "not_ready"
                slot.error = "; ".join(errors)
                slot_errors[str(slot.slot_id)] = errors
                print(
                    f"[step2] Slot {slot.slot_id} probe failed: "
                    f"{'; '.join(errors)}"
                )

        _write_csv(
            env_registry_path,
            fieldnames=[
                "run_id",
                "slot_id",
                "worker_id",
                "port",
                "pid",
                "status",
                "startup_time_sec",
                "error",
            ],
            rows=_build_registry_rows(run_id, pool.slots),
        )
        _write_csv(
            sensor_probe_path,
            fieldnames=[
                "run_id",
                "slot_id",
                "worker_id",
                "port",
                "sensor_name",
                "sensor_mode",
                "success",
                "latency_sec",
                "env_wallclock_step_sec",
                "message",
                "error",
            ],
            rows=sensor_rows,
        )
        _write_csv(
            interaction_probe_path,
            fieldnames=[
                "run_id",
                "slot_id",
                "worker_id",
                "port",
                "operation",
                "action_line",
                "success",
                "latency_sec",
                "sim_exec_time_step_sec",
                "env_wallclock_step_sec",
                "message",
                "error",
            ],
            rows=interaction_rows,
        )

        ready_slots = [slot.slot_id for slot in pool.slots if slot.status == "ready"]
        not_ready_slots = [slot.slot_id for slot in pool.slots if slot.status != "ready"]
        all_ready = len(not_ready_slots) == 0

        if all_ready:
            print(
                "[step2] All slots are ready. Environments were reset to init state "
                "and are now waiting for next step commands."
            )
            standby_entered_at_utc = _utc_now()
            standby_exit_reason = _standby_loop(standby_seconds=standby_seconds)
        else:
            standby_exit_reason = "not_ready_exit"
            print(
                f"[step2] Bootstrap failed. Not-ready slots: {not_ready_slots}. "
                f"See {health_report_path}"
            )

        health_report = {
            "run_id": run_id,
            "created_at_utc": _utc_now(),
            "unity_exe": str(unity_exe.resolve()),
            "parallel_workers": parallel_workers,
            "base_port": base_port,
            "scene_id": scene_id,
            "time_scale": time_scale,
            "skip_animation": bool(skip_animation),
            "image_size": {"width": image_width, "height": image_height},
            "ready_slots": ready_slots,
            "not_ready_slots": not_ready_slots,
            "slot_errors": slot_errors,
            "overall_status": "ready" if all_ready else "not_ready",
            "standby_mode": "idle_until_ctrl_c_or_timeout",
            "standby_entered_at_utc": standby_entered_at_utc,
            "standby_exit_reason": standby_exit_reason,
        }
        health_report_path.write_text(
            json.dumps(health_report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if not all_ready:
            raise RuntimeError(
                f"Unity bootstrap failed: not_ready slots={not_ready_slots}. "
                f"See {health_report_path}"
            )

        return {
            "run_id": run_id,
            "env_setup_dir": str(env_setup_dir),
            "parallel_workers": parallel_workers,
            "ready_slots": ready_slots,
            "not_ready_slots": not_ready_slots,
            "status": "ready",
            "standby_exit_reason": standby_exit_reason,
        }
    finally:
        pool.close_all()
