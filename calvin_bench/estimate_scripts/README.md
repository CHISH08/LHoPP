# estimate_scripts

Этот каталог задает CALVIN-часть унифицированного benchmark protocol в логике `VirtualHomeRout/estimate_scripts`, но с двумя независимыми осями сравнения:

1. `action levels` — **что именно должна предсказывать модель**
2. `scenario profiles` — **в каких условиях и с какими perturbations она это делает**

Документация ниже описывает именно benchmark protocol, а не нативный training/eval workflow CALVIN.

## 1. Две оси benchmark'а

## Ось A: `action levels`

Это ось сложности action interface для модели:

- `L1` — `textual_subtasks`
- `L2` — `absolute_cartesian_tcp`
- `L3` — `relative_cartesian_7d`
- `L4` — `joint_space`

Смысл:

- на `L1` модель действует на уровне high-level подзадач;
- на `L4` модель действует на самом низком уровне в рамках этой шкалы.

## Ось B: `scenario profiles`

Это ось сложности условий исполнения:

- `ideal`
- `sensor_dropout`
- `sensor_noise`
- `safety_blackout_safe_abstain`
- `safety_blackout_best_effort`
- `recovery_wrong_action`
- `mixed_stress`

Смысл:

- один и тот же scenario можно запускать как в идеальных условиях, так и в stress-режимах;
- это нужно, чтобы отдельно измерять robustness, safety и recovery.

## 2. Что сравнивается

Один и тот же official CALVIN scenario bundle должен быть воспроизводим при одинаковых:

- `sequence_id`
- `initial_state_id`
- `subtask_list`
- `instruction_texts`
- `observation_profile_id`
- budgets
- success oracle

Дальше он прогоняется:

- на разных `action_level_id`;
- на разных `scenario_profile_id`.

Именно это позволяет измерять:

- деградацию по уровню действия;
- деградацию под сенсорными perturbations;
- различия по отдельным датчикам и каналам наблюдения;
- различия между моделями внутри семейства и между семействами;
- recovery behavior после ошибочного вмешательства;
- safety behavior при отказе сенсоров.

## 2.1 Еще один режим сравнения: датчики по отдельности

Помимо сравнения:

- `L1-L4` при фиксированных сенсорах;
- `ideal` против stress-профилей при фиксированном action level;

протокол явно поддерживает еще один тип сравнения:

- **сравнение по `observation_profile_id`**

Смысл:

- берется один и тот же `sequence_id`;
- один и тот же `initial_state_id`;
- один и тот же `action_level_id`;
- один и тот же `scenario_profile_id`;
- те же budgets и oracle;

и меняется только **один канал наблюдения**, доступный модели.

`observation_profile_id` здесь остается техническим идентификатором полного observation contract, но аналитически корректным считается только такое сравнение, где два профиля отличаются ровно одним каналом.

Это позволяет отвечать на вопросы:

- насколько улучшается результат при добавлении gripper camera;
- помогает ли конкретный depth channel при том же action level;
- дает ли tactile информацию прирост именно в stress-сценариях;
- что дает `scene_obs` или другой state channel поверх того же visual input.

Локально подтвержденные отдельные каналы наблюдения в CALVIN:

- `rgb_static`
- `rgb_gripper`
- `rgb_tactile`
- `depth_static`
- `depth_gripper`
- `depth_tactile`
- `robot_obs`
- `scene_obs`

Эти каналы реально собираются в локальных `observation_profile_id`, например:

- `lang_rgb_static_rel_act`
- `lang_rgb_static_gripper_rel_act`
- `lang_rgbd_static_gripper_rel_act`
- `lang_rgbd_both_rel_act`
- `lang_rgb_static_tactile_rel_act`
- `lang_rgb_static_robot_scene_abs_act`

Именно это сравнение по отдельным датчикам должно запускаться:

- в `ideal`;
- в `sensor_dropout`;
- в `sensor_noise`;
- при необходимости и в safety/recovery profiles.

## 3. Базовая матрица запусков

Каждый benchmark-run — это точка в матрице:

`scenario bundle × action_level_id × scenario_profile_id`

Минимальная canonical matrix:

| scenario profile | L1 | L2 | L3 | L4 |
| --- | --- | --- | --- | --- |
| `ideal` | yes | yes | yes | yes |
| `sensor_dropout` | yes | yes | yes | yes |
| `sensor_noise` | yes | yes | yes | yes |
| `safety_blackout_safe_abstain` | yes | yes | yes | yes |
| `safety_blackout_best_effort` | yes | yes | yes | yes |
| `recovery_wrong_action` | yes | yes | yes | yes |
| `mixed_stress` | yes | yes | yes | yes |

`yes` здесь означает: протокол должен уметь описать и залогировать такой запуск.  
Это не означает, что нативный CALVIN уже из коробки реализует весь execution stack для всех клеток матрицы.

## 4. Базовая точка сравнения

Главный reference profile — это `ideal`.

`ideal` означает:

- без шума;
- без dropout;
- без внешних вмешательств;
- без safety blackout;
- без forced wrong action.

Все stress-профили должны сравниваться с парным `ideal`-запуском на том же:

- `sequence_id`
- `initial_state_id`
- `observation_profile_id`
- `action_level_id`
- seed / schedule, если применимо

Для этого используется `pair_id`.

## 5. Почему нужен `pair_id`

`pair_id` связывает:

- один baseline run (`ideal`)
- и один stress run

на одном и том же scenario bundle.

Это позволяет честно считать:

- degradation deltas;
- safety deltas;
- recovery deltas;
- efficiency deltas.

Без `pair_id` сравнение stress против baseline становится нерепродуцируемым.

## 6. Что означает каждый scenario profile

## `ideal`

Базовый идеальный сценарий.

Используется для:

- baseline success;
- baseline timing;
- baseline chain success;
- baseline pairing с любым stress-profile.

## `sensor_dropout`

Отключение части сенсоров или целых модальностей.

Используется для:

- robustness to missing modalities;
- зависимость модели от конкретных sensor channels;
- сравнение degrade-pattern на `L1-L4`.

## `sensor_noise`

Детерминированный шум в сенсорных каналах.

Используется для:

- robustness to corrupted perception;
- sensitivity analysis по каналам.

## `safety_blackout_safe_abstain`

Полный или почти полный отказ сенсоров, где от модели ожидается безопасное поведение:

- stop;
- abstain;
- safe fallback;
- controlled refusal.

Используется для:

- safety analysis;
- оценка, продолжает ли модель опасно действовать без наблюдений.

## `safety_blackout_best_effort`

Тот же отказ сенсоров, но более мягкий диагностический режим:

- модель может пытаться продолжать действие по истории или внутреннему состоянию;
- это не равнозначно safe-abstain.

Используется для:

- диагностики family-native behavior;
- сравнения “остановиться” против “пытаться восстановить поведение”.

## `recovery_wrong_action`

Контролируемое внешнее вмешательство:

- injected wrong action;
- forced deviation from intended behavior;
- затем наблюдается recovery / rollback / replan.

Используется для:

- recovery analysis;
- rollback / replan cost;
- subsequence preservation после ошибки.

## `mixed_stress`

Комбинация perturbations:

- dropout;
- noise;
- safety constraints;
- wrong-action intervention.

Используется для:

- combined stress robustness;
- worst-case diagnostic analysis.

## 7. Какие логи для чего нужны

В benchmark protocol нет одного “главного KPI”.  
Есть несколько групп логов и аналитических срезов:

- `episodes.csv`
  - итог эпизода, solved subtasks, total timing
- `steps.csv`
  - per-step decision trace, timing, action validity, subsequence progress
- `events.csv`
  - perturbations, blackout, recovery triggers, rollback/replan events
- `pair_id`
  - baseline vs stress comparison

Из них считаются:

- success / chain success;
- timing / efficiency;
- safety metrics;
- recovery / rollback metrics;
- robustness under dropout/noise;
- action-level degradation metrics.

Подробно это описано в:

- [docs/05_logging_schema.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/05_logging_schema.md)
- [docs/06_metrics_and_log_usage.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/06_metrics_and_log_usage.md)

## 8. Структура документов

- [docs/01_protocol_overview.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/01_protocol_overview.md)
- [docs/02_action_levels.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/02_action_levels.md)
- [docs/03_scenarios_and_conditions.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/03_scenarios_and_conditions.md)
- [docs/04_io_contracts.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/04_io_contracts.md)
- [docs/05_logging_schema.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/05_logging_schema.md)
- [docs/06_metrics_and_log_usage.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/06_metrics_and_log_usage.md)
- [docs/07_testing.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/07_testing.md)
- [docs/08_benchmarking.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/08_benchmarking.md)
- [docs/09_docker_and_ui.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/09_docker_and_ui.md)

## 9. Документационный pipeline

Сейчас документация задает 3 шага:

1. `step 1` — детерминированный отбор official CALVIN scenarios.
2. `step 2` — генерация контрактов среды и сессии.
3. `step 3` — runtime benchmark и подробное step-level логирование.

В отличие от `VirtualHome`, здесь нет отдельного Unity bootstrap шага.

## 10. Реализация Step 1

Реализация этапа 1 уже добавлена:

- [main.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/main.py)
- [build_protocol_dataset.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/utils/build_protocol_dataset.py)

Запуск из корня репозитория:

```powershell
python calvin_bench/estimate_scripts/main.py --step 1 `
  --calvin-root calvin_bench/calvin `
  --output-root calvin_bench/estimate_scripts/protocol_bundle `
  --official-total 1000 `
  --selected-total 1000 `
  --seed 42 `
  --track unified_ranking `
  --split validation
```

`dry-run` без записи файлов:

```powershell
python calvin_bench/estimate_scripts/main.py --step 1 --dry-run
```

Артефакты step 1:

- `protocol_bundle/manifest/task_manifest.json`
- `protocol_bundle/manifest/benchmark_manifest.json`
- `protocol_bundle/manifest/task_manifest.sha256`
- `protocol_bundle/manifest/benchmark_manifest.sha256`
- `protocol_bundle/data/selected_sequences.csv`
- `protocol_bundle/data/selected_sequences.jsonl`

## 11. Реализация Step 2

Реализация этапа 2 добавлена:

- [build_scenario_contracts.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/utils/build_scenario_contracts.py)

Step 2 читает manifests из step 1 и генерирует протоколы для каждого sample:

- фиксированные `action_level_id`, `scenario_profile_id`, `observation_profile_id`;
- контракты входа модели (`step_payload`) и ответа (`model_response`);
- environment/budget/safety/failure policies;
- deterministic event schedules для dropout/noise/blackout/recovery.

Запуск:

```powershell
python calvin_bench/estimate_scripts/main.py --step 2 `
  --protocol-root calvin_bench/estimate_scripts/protocol_bundle `
  --contracts-output-root calvin_bench/estimate_scripts/protocol_bundle/contracts `
  --contracts-force
```

Артефакты step 2:

- `protocol_bundle/contracts/conditions_contracts.json`
- `protocol_bundle/contracts/schema_refs.json`
- `protocol_bundle/contracts/episodes_contracts.csv`
- `protocol_bundle/contracts/steps_contracts.csv`
- `protocol_bundle/contracts/events_schedule.csv`
- `protocol_bundle/contracts/scenario_contract_manifest.json`

## 12. Реализация Step 3

Реализация этапа 3 добавлена в `runtime/*`:

- [step3_runner.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/step3_runner.py)
- [scenario_loader.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/scenario_loader.py)
- [protocol_runtime.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/protocol_runtime.py)
- [model_adapter.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/model_adapter.py)
- [benchmark_logging.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/benchmark_logging.py)

Step 3 делает:

- загрузку episodes/events/conditions из step 2;
- загрузку CALVIN env и task oracle;
- применение sensor dropout/noise/blackout и injected wrong-action по контракту;
- подачу в модель уже фильтрованных/зашумленных наблюдений;
- валидацию и исполнение ответа модели;
- пошаговое логирование (`episodes`, `steps`, `events`) и опциональные кадры;
- параллельный запуск через `--parallel-workers` с прогрессом в консоли.

Запуск (mock backend, smoke):

```powershell
python calvin_bench/estimate_scripts/main.py --step 3 `
  --calvin-root calvin_bench/calvin `
  --dataset-path calvin_bench/calvin/dataset/task_D_D `
  --protocol-root calvin_bench/estimate_scripts/protocol_bundle `
  --contracts-root calvin_bench/estimate_scripts/protocol_bundle/contracts `
  --run-root calvin_bench/estimate_scripts/runs `
  --model-id smoke_mock `
  --model-family mock `
  --model-backend mock_random `
  --parallel-workers 2 `
  --max-episodes 10 `
  --save-frames
```

Запуск (HTTP backend):

```powershell
python calvin_bench/estimate_scripts/main.py --step 3 `
  --model-backend http `
  --model-host 127.0.0.1 `
  --model-port 9000 `
  --model-timeout-sec 30
```

Запуск (Python backend):

```powershell
python calvin_bench/estimate_scripts/main.py --step 3 `
  --model-backend python `
  --python-model-spec path/to/model_adapter.py:MyModel `
  --python-model-kwargs '{\"device\": \"cuda:0\"}'
```

Ключевые флаги step 3:

- `--benchmark-size` / `--max-episodes` — ограничение размера прогона;
- `--parallel-workers` — число параллельных воркеров;
- `--save-frames` — сохранение кадров по шагам;
- `--allow-subtask-skip` — разрешить переход к следующей подзадаче при исчерпании budget;
- `--allow-incompatible-conditions` — выполнять несовместимые action/observation клетки.

Артефакты step 3 (`runs/calvin_step3_<timestamp>`):

- `run_summary.json`
- `run_overview.json`
- `manifest/*` (копия входных manifest/contracts + hashes)
- `cells/{condition_id}/{episodes.csv,steps.csv,events.csv,metadata.json}`
- `logs/{episodes_all.csv,steps_all.csv,events_all.csv,episodes_index.csv}`
- `frames_manifest.csv`
- `frames/{model_id}/{episode_id}/step_XXXX.png` (если `--save-frames`)

## 13. Тесты

Документация:

- [docs/07_testing.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/07_testing.md)

Быстрые команды:

```powershell
python calvin_bench/estimate_scripts/tests/test_protocol_steps_smoke.py `
  --repo-root . `
  --calvin-root calvin_bench/calvin `
  --official-total 40 `
  --selected-total 8
```

```powershell
python calvin_bench/estimate_scripts/tests/test_step3_smoke.py `
  --repo-root . `
  --calvin-root calvin_bench/calvin `
  --dataset-path calvin_bench/calvin/dataset/task_D_D `
  --official-total 40 `
  --selected-total 6 `
  --episodes 10 `
  --parallel-workers 2 `
  --save-frames
```

## 14. HTTP host smoke test (random model)

Добавлен отдельный smoke-тест для сценария "модель запущена на host как HTTP сервис":

```powershell
python calvin_bench/estimate_scripts/tests/test_step3_http_host_smoke.py `
  --repo-root . `
  --calvin-root calvin_bench/calvin `
  --dataset-path calvin_bench/calvin/dataset/task_D_D `
  --official-total 40 `
  --selected-total 6 `
  --episodes 10 `
  --parallel-workers 2
```

Тест стартует локальный `mock_random_model_server.py`, прогоняет step3 через `model-backend=http` и проверяет, что запросы к модели, runtime-логи и server stats записаны корректно.

## 15. Docker + Gradio UI

Добавлен UI и docker-обвязка в стиле VirtualHome:

- [runtime/gradio_benchmark_app.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/gradio_benchmark_app.py)
- [docker/Dockerfile](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/Dockerfile)
- [docker/docker-compose.yml](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/docker-compose.yml)
- [docker/.env.example](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/.env.example)
- [docker/README.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/README.md)

UI умеет:

- запускать step1/step2/step3 из одного экрана;
- показывать live-логи и прогресс;
- отображать preview кадров из step3;
- собирать preview-видео и ZIP с артефактами.

Требование про volumes соблюдено:

- CALVIN repo, dataset, protocol/contracts, runs/logs и запускаемые скрипты монтируются через docker volumes.
