from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


def _tag_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test for CALVIN estimate_scripts step1 + step2.")
    parser.add_argument("--repo-root", default=".", help="Path to repository root.")
    parser.add_argument("--calvin-root", default="calvin_bench/calvin", help="Path to local CALVIN repo.")
    parser.add_argument("--official-total", type=int, default=40, help="How many official sequences to generate in step1.")
    parser.add_argument("--selected-total", type=int, default=8, help="How many sequences to keep in deterministic bundle.")
    parser.add_argument("--seed", type=int, default=42, help="Deterministic seed for selection.")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    repo_root = Path(args.repo_root).resolve()
    calvin_root = (repo_root / args.calvin_root).resolve()
    if not calvin_root.exists():
        raise FileNotFoundError(f"CALVIN root does not exist: {calvin_root}")

    import sys

    estimate_root = repo_root / "calvin_bench" / "estimate_scripts"
    sys.path.insert(0, str(estimate_root))
    from utils.build_protocol_dataset import build_protocol_dataset
    from utils.build_scenario_contracts import build_scenario_contracts

    test_root = estimate_root / "test_runs" / f"test-protocol-{_tag_now()}"
    bundle_root_1 = test_root / "bundle_a"
    bundle_root_2 = test_root / "bundle_b"

    summary_a = build_protocol_dataset(
        calvin_root=calvin_root,
        output_root=bundle_root_1,
        seed=args.seed,
        official_total=args.official_total,
        selected_total=args.selected_total,
        track="unified_ranking",
        split="validation",
        dry_run=False,
    )
    summary_b = build_protocol_dataset(
        calvin_root=calvin_root,
        output_root=bundle_root_2,
        seed=args.seed,
        official_total=args.official_total,
        selected_total=args.selected_total,
        track="unified_ranking",
        split="validation",
        dry_run=False,
    )

    _assert(int(summary_a["selected_total"]) == args.selected_total, "step1 selected_total mismatch")
    _assert(int(summary_a["matrix_cells"]) == 28, "step1 matrix_cells must be 28 for 4x7")
    _assert(int(summary_a["selected_total"]) == int(summary_b["selected_total"]), "deterministic build selected_total mismatch")

    selected_csv_a = bundle_root_1 / "data" / "selected_sequences.csv"
    selected_csv_b = bundle_root_2 / "data" / "selected_sequences.csv"
    _assert(selected_csv_a.exists(), f"Missing file: {selected_csv_a}")
    _assert(selected_csv_b.exists(), f"Missing file: {selected_csv_b}")
    rows_a = _read_csv_rows(selected_csv_a)
    rows_b = _read_csv_rows(selected_csv_b)
    ids_a = [row["sequence_id"] for row in rows_a]
    ids_b = [row["sequence_id"] for row in rows_b]
    _assert(ids_a == ids_b, "step1 is not deterministic: sequence_id lists differ")

    contracts_summary = build_scenario_contracts(
        protocol_root=bundle_root_1,
        output_root=bundle_root_1 / "contracts",
        force=True,
    )

    _assert(contracts_summary["status"] == "generated", f"Unexpected step2 status: {contracts_summary['status']}")
    _assert(int(contracts_summary["conditions_total"]) == 168, "conditions_total must be 168")
    expected_episodes = 168 * args.selected_total
    _assert(int(contracts_summary["episodes_total"]) == expected_episodes, "episodes_total mismatch")
    _assert(int(contracts_summary["steps_total"]) == expected_episodes, "steps_total must equal episodes_total for step2 templates")
    _assert(int(contracts_summary["events_total"]) > 0, "events_total must be > 0")

    required_step2 = [
        bundle_root_1 / "contracts" / "conditions_contracts.json",
        bundle_root_1 / "contracts" / "schema_refs.json",
        bundle_root_1 / "contracts" / "episodes_contracts.csv",
        bundle_root_1 / "contracts" / "steps_contracts.csv",
        bundle_root_1 / "contracts" / "events_schedule.csv",
        bundle_root_1 / "contracts" / "scenario_contract_manifest.json",
    ]
    for path in required_step2:
        _assert(path.exists(), f"Missing step2 artifact: {path}")

    manifest = json.loads((bundle_root_1 / "contracts" / "scenario_contract_manifest.json").read_text(encoding="utf-8"))
    _assert(int(manifest["conditions_total"]) == 168, "scenario_contract_manifest conditions_total mismatch")
    _assert(int(manifest["episodes_total"]) == expected_episodes, "scenario_contract_manifest episodes_total mismatch")

    print(
        json.dumps(
            {
                "status": "pass",
                "test_root": str(test_root),
                "selected_total": args.selected_total,
                "conditions_total": int(contracts_summary["conditions_total"]),
                "episodes_total": int(contracts_summary["episodes_total"]),
                "events_total": int(contracts_summary["events_total"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

