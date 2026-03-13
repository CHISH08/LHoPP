# 08. Запуск Бенчмаркинга (Step 3)

Этот документ описывает практический запуск `step 3`:

- загрузка scenario/contracts;
- применение protocol conditions;
- вызов модели;
- исполнение действий;
- логирование и сбор артефактов.

## Входы

- `protocol_root`: артефакты step1.
- `contracts_root`: артефакты step2.
- `dataset_path`: локальный CALVIN dataset.
- модельный backend (`http|python|mock_random`).

## Базовый запуск (mock backend)

```powershell
python calvin_bench/estimate_scripts/main.py --step 3 `
  --calvin-root calvin_bench/calvin `
  --dataset-path calvin_bench/calvin/dataset/task_D_D `
  --protocol-root calvin_bench/estimate_scripts/protocol_bundle `
  --contracts-root calvin_bench/estimate_scripts/protocol_bundle/contracts `
  --run-root calvin_bench/estimate_scripts/runs `
  --model-id mock_model `
  --model-family mock `
  --model-backend mock_random `
  --parallel-workers 2 `
  --max-episodes 20 `
  --save-frames
```

## Запуск с HTTP моделью

```powershell
python calvin_bench/estimate_scripts/main.py --step 3 `
  --model-backend http `
  --model-host 127.0.0.1 `
  --model-port 9000 `
  --model-timeout-sec 30
```

Контракт HTTP endpoint:

- `POST /predict`
- вход: `step_payload` (из контрактов step2)
- выход: `model_response` полями:
  - `status`
  - `action_raw`
  - `action_exec`
  - `action_level_id`
  - `model_latency_sec`
  - `error_code`
  - `error_message`
  - `safety_intent`
  - `fallback_mode`
  - `replan_requested`
  - `rollback_requested`
  - `tokens_in`
  - `tokens_out`
  - `meta`

## Запуск с локальной Python-моделью

```powershell
python calvin_bench/estimate_scripts/main.py --step 3 `
  --model-backend python `
  --python-model-spec path/to/model_adapter.py:MyModel `
  --python-model-kwargs '{"device":"cuda:0"}'
```

Требования к Python модели:

- метод `predict(step_payload)` или `step(obs, goal)`;
- опционально `reset(episode_context)`.

## Параллелизм и размер прогона

- `--parallel-workers`: число воркеров.
- `--benchmark-size`: лимит эпизодов.
- `--max-episodes`: лимит для smoke/коротких прогонов.

Если задан `max_episodes > 0`, он используется как фактический лимит.

## Полезные флаги

- `--save-frames`: сохранять кадры.
- `--allow-subtask-skip`: разрешать переход к следующей подзадаче при исчерпании per-subtask budget.
- `--allow-incompatible-conditions`: не пропускать клетки с несовместимыми action/observation.
- `--show-gui`: запускать env в GUI режиме.

## Прогресс во время прогона

Во время выполнения раннер печатает:

- `run_id`
- количество эпизодов
- число воркеров
- прогресс `completed/total` и elapsed time

## Выходные артефакты

Папка запуска:

- `calvin_bench/estimate_scripts/runs/calvin_step3_<timestamp>/`

Ключевые файлы:

- `run_summary.json`
- `run_overview.json`
- `manifest/*`
- `cells/{condition_id}/episodes.csv`
- `cells/{condition_id}/steps.csv`
- `cells/{condition_id}/events.csv`
- `logs/episodes_all.csv`
- `logs/steps_all.csv`
- `logs/events_all.csv`
- `logs/episodes_index.csv`
- `frames_manifest.csv`
- `frames/{model_id}/{episode_id}/step_XXXX.png` (если включено `--save-frames`)

## Что смотреть в первую очередь

1. `run_overview.json`
2. `run_summary.json`
3. `logs/episodes_index.csv`
4. `logs/episodes_all.csv`
5. `logs/steps_all.csv`
6. `logs/events_all.csv`

## Диагностика типичных проблем

- `No module named 'hydra'`:
  - не хватает runtime зависимостей CALVIN env.
- `worker_errors_total > 0`:
  - смотреть `worker_errors_preview` в `run_summary.json`.
- `episodes_total == 0`:
  - проверить `dataset_path`, `contracts_root`, backend модели, ошибки воркеров.


## Локальный host random model server (для `http` backend)

Сервер:

- [mock_random_model_server.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/mock_random_model_server.py)

Запуск сервера в отдельной консоли:

```powershell
python calvin_bench/estimate_scripts/runtime/mock_random_model_server.py `
  --host 127.0.0.1 `
  --port 19090 `
  --seed 42 `
  --requests-log-path calvin_bench/estimate_scripts/runs/mock_http_model/requests.jsonl `
  --stats-path calvin_bench/estimate_scripts/runs/mock_http_model/server_stats.json
```

Запуск step3 с этим сервером:

```powershell
python calvin_bench/estimate_scripts/main.py --step 3 `
  --calvin-root calvin_bench/calvin `
  --dataset-path calvin_bench/calvin/dataset/task_D_D `
  --protocol-root calvin_bench/estimate_scripts/protocol_bundle `
  --contracts-root calvin_bench/estimate_scripts/protocol_bundle/contracts `
  --run-root calvin_bench/estimate_scripts/runs `
  --model-id host_random_http `
  --model-family mock `
  --model-backend http `
  --model-host 127.0.0.1 `
  --model-port 19090 `
  --parallel-workers 2 `
  --max-episodes 20
```

Полезно проверить после прогона:

- `calvin_bench/estimate_scripts/runs/mock_http_model/requests.jsonl`
- `calvin_bench/estimate_scripts/runs/mock_http_model/server_stats.json`
- `runs/calvin_step3_<timestamp>/logs/steps_all.csv`

## UI запуск (через Docker)

Для интерактивного запуска шагов 1/2/3 через браузер см.:

- [09_docker_and_ui.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/09_docker_and_ui.md)
- [docker/README.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/README.md)
