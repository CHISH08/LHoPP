import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


@dataclass(frozen=True)
class EventSpec:
    episode_id: str
    event_id: str
    event_type: str
    event_source: str
    start_step: int
    end_step: int
    subtask_idx: int
    payload: Dict[str, Any]


@dataclass(frozen=True)
class ConditionContract:
    condition_id: str
    action_level_id: str
    action_repr: str
    scenario_profile_id: str
    observation_profile_id: str
    decision_granularity: str
    active_channels: List[str]
    active_modalities: List[str]
    active_sensor_mask_template: Dict[str, int]
    action_contract: Dict[str, Any]
    runtime_contract: Dict[str, Any]
    perturbation_contract: Dict[str, Any]


@dataclass(frozen=True)
class EpisodeContract:
    episode_id: str
    pair_id: str
    baseline_episode_id: str
    sequence_id: str
    initial_state_id: str
    condition_id: str
    condition_id_seeded: str
    track: str
    selection_seed: int
    action_level_id: str
    scenario_profile_id: str
    observation_profile_id: str
    decision_granularity: str
    safety_mode: str
    termination_policy_id: str
    failure_policy_id: str
    perturbation_schedule_id: str
    subtasks_total: int
    max_subtask_steps: int
    max_episode_steps: int
    max_time_sec: int
    active_modalities: List[str]
    active_sensor_mask: Dict[str, int]
    active_channels: List[str]
    subtask_list: List[str]
    instruction_texts: List[str]
    initial_state: Dict[str, Any]


@dataclass(frozen=True)
class ProtocolContracts:
    task_manifest: Dict[str, Any]
    benchmark_manifest: Dict[str, Any]
    scenario_contract_manifest: Dict[str, Any]
    schema_refs: Dict[str, Any]
    conditions: Dict[str, ConditionContract]
    episodes: List[EpisodeContract]
    events_by_episode: Dict[str, List[EventSpec]]


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _json_cell(value: str) -> Any:
    text = value.strip()
    if not text:
        return None
    return json.loads(text)


def _load_conditions(path: Path) -> Dict[str, ConditionContract]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise RuntimeError(f"Expected list in {path}")
    out: Dict[str, ConditionContract] = {}
    for row in raw:
        cid = str(row["condition_id"])
        out[cid] = ConditionContract(
            condition_id=cid,
            action_level_id=str(row["action_level_id"]),
            action_repr=str(row["action_repr"]),
            scenario_profile_id=str(row["scenario_profile_id"]),
            observation_profile_id=str(row["observation_profile_id"]),
            decision_granularity=str(row["decision_granularity"]),
            active_channels=list(row["observation_contract"]["active_channels"]),
            active_modalities=list(row["observation_contract"]["active_modalities"]),
            active_sensor_mask_template=dict(row["observation_contract"]["active_sensor_mask_template"]),
            action_contract=dict(row["action_contract"]),
            runtime_contract=dict(row["runtime_contract"]),
            perturbation_contract=dict(row["perturbation_contract"]),
        )
    return out


def _load_episodes(path: Path) -> List[EpisodeContract]:
    rows = _read_csv(path)
    out: List[EpisodeContract] = []
    for row in rows:
        out.append(
            EpisodeContract(
                episode_id=str(row["episode_id"]),
                pair_id=str(row["pair_id"]),
                baseline_episode_id=str(row["baseline_episode_id"]),
                sequence_id=str(row["sequence_id"]),
                initial_state_id=str(row["initial_state_id"]),
                condition_id=str(row["condition_id"]),
                condition_id_seeded=str(row["condition_id_seeded"]),
                track=str(row["track"]),
                selection_seed=int(row["selection_seed"]),
                action_level_id=str(row["action_level_id"]),
                scenario_profile_id=str(row["scenario_profile_id"]),
                observation_profile_id=str(row["observation_profile_id"]),
                decision_granularity=str(row["decision_granularity"]),
                safety_mode=str(row["safety_mode"]),
                termination_policy_id=str(row["termination_policy_id"]),
                failure_policy_id=str(row["failure_policy_id"]),
                perturbation_schedule_id=str(row["perturbation_schedule_id"]),
                subtasks_total=int(row["subtasks_total"]),
                max_subtask_steps=int(row["max_subtask_steps"]),
                max_episode_steps=int(row["max_episode_steps"]),
                max_time_sec=int(row["max_time_sec"]),
                active_modalities=list(_json_cell(row["active_modalities_json"])),
                active_sensor_mask=dict(_json_cell(row["active_sensor_mask_json"])),
                active_channels=list(_json_cell(row["active_channels_json"])),
                subtask_list=list(_json_cell(row["subtask_list_json"])),
                instruction_texts=list(_json_cell(row["instruction_texts_json"])),
                initial_state=dict(_json_cell(row["initial_state_json"])),
            )
        )
    return sorted(out, key=lambda x: x.episode_id)


def _load_events(path: Path) -> Dict[str, List[EventSpec]]:
    rows = _read_csv(path)
    out: Dict[str, List[EventSpec]] = {}
    for row in rows:
        spec = EventSpec(
            episode_id=str(row["episode_id"]),
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            event_source=str(row["event_source"]),
            start_step=int(row["start_step"]),
            end_step=int(row["end_step"]),
            subtask_idx=int(row["subtask_idx"]),
            payload=dict(_json_cell(row["event_payload_json"]) or {}),
        )
        out.setdefault(spec.episode_id, []).append(spec)
    for episode_id in out:
        out[episode_id] = sorted(out[episode_id], key=lambda x: (x.start_step, x.end_step, x.event_id))
    return out


def load_protocol_contracts(protocol_root: Path, contracts_root: Path) -> ProtocolContracts:
    protocol_root = protocol_root.resolve()
    contracts_root = contracts_root.resolve()
    manifests_root = protocol_root / "manifest"
    task_manifest = _read_json(manifests_root / "task_manifest.json")
    benchmark_manifest = _read_json(manifests_root / "benchmark_manifest.json")
    scenario_contract_manifest = _read_json(contracts_root / "scenario_contract_manifest.json")
    schema_refs = _read_json(contracts_root / "schema_refs.json")
    conditions = _load_conditions(contracts_root / "conditions_contracts.json")
    episodes = _load_episodes(contracts_root / "episodes_contracts.csv")
    events_by_episode = _load_events(contracts_root / "events_schedule.csv")
    return ProtocolContracts(
        task_manifest=task_manifest,
        benchmark_manifest=benchmark_manifest,
        scenario_contract_manifest=scenario_contract_manifest,
        schema_refs=schema_refs,
        conditions=conditions,
        episodes=episodes,
        events_by_episode=events_by_episode,
    )

