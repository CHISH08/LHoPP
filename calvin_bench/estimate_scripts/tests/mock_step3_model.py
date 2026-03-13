from __future__ import annotations

from typing import Any, Dict


class DeterministicStep3Model:
    """
    Deterministic model adapter for step3 tests.

    - L1: returns current oracle target subtask.
    - L2/L3: returns fixed 7D action.
    - L4: returns fixed 8D action.
    """

    def __init__(self, **kwargs: Any) -> None:
        del kwargs

    def reset(self, episode_context: Dict[str, Any] | None = None) -> None:
        del episode_context

    def predict(self, step_payload: Dict[str, Any]) -> Dict[str, Any]:
        level = str(step_payload.get("action_level_id", "L3")).upper()
        if level == "L1":
            action = str(step_payload.get("oracle_target_subtask", ""))
        elif level in {"L2", "L3"}:
            action = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        elif level == "L4":
            action = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0]
        else:
            return {
                "status": "unsupported",
                "action_raw": "",
                "action_exec": "",
                "action_level_id": level,
                "model_latency_sec": 0.0,
                "error_code": "unsupported_model_interface",
                "error_message": f"Unsupported action level: {level}",
                "safety_intent": "",
                "fallback_mode": "",
                "replan_requested": False,
                "rollback_requested": False,
                "tokens_in": 0,
                "tokens_out": 0,
                "meta": {},
            }
        return {
            "status": "ok",
            "action_raw": action,
            "action_exec": action,
            "action_level_id": level,
            "model_latency_sec": 0.0001,
            "error_code": "",
            "error_message": "",
            "safety_intent": "",
            "fallback_mode": "",
            "replan_requested": False,
            "rollback_requested": False,
            "tokens_in": 0,
            "tokens_out": 0,
            "meta": {"source": "deterministic_step3_model"},
        }

