# Full Pipeline Test Report

- status: **FAIL**
- created_at_utc: `2026-03-11T15:47:54.921202+00:00`
- failed_checks: `3`
- warning_checks: `0`

## Artifacts
- test_root: `C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748`
- logs_dir: `C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\logs`
- report_dir: `C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\report`
- protocol_root: `C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle`
- step2_runs_root: `C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\runs_step2`
- step4_runs_root: `C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\runs_step4`
- mock_root: `C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\mock_model`
- selected_base_port: `8090`
- selected_model_port: `19000`

## Checks
- [OK] `unity_exe_exists`: unity_exe=C:\Users\User\code\paper\LHoPP\VirtualHomeRout\dataset\windows_exec.v2.3.0\VirtualHome.exe
- [OK] `step1_exit_code`: returncode=0
- [OK] `step1_json_summary`: json summary parsed
- [OK] `step1_selected_csv_exists`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\data\selected_tasks.csv
- [OK] `step1_task_manifest_exists`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\manifest\task_manifest.json
- [OK] `step1_benchmark_manifest_exists`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\manifest\benchmark_manifest.json
- [OK] `step1_selected_rows_count`: selected_rows=3 expected=3
- [OK] `step3_exit_code`: returncode=0
- [OK] `step3_json_summary`: json summary parsed
- [OK] `step3_artifact_episodes_contracts.csv`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\contracts\episodes_contracts.csv
- [OK] `step3_artifact_steps_contracts.csv`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\contracts\steps_contracts.csv
- [OK] `step3_artifact_events_schedule.csv`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\contracts\events_schedule.csv
- [OK] `step3_artifact_conditions_contracts.json`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\contracts\conditions_contracts.json
- [OK] `step3_artifact_schema_refs.json`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\contracts\schema_refs.json
- [OK] `step3_artifact_scenario_contract_manifest.json`: C:\Users\User\code\paper\LHoPP\VirtualHomeRout\estimate_scripts\test_runs\test-20260311_154748\protocol_bundle\contracts\scenario_contract_manifest.json
- [FAIL] `step2_exit_code`: returncode=1
- [FAIL] `step2_json_summary`: json summary parsed
- [FAIL] `pipeline_exception`: RuntimeError: Step 2 failed
