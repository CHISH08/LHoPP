# 08. Шаг 4: Запуск Бенчмаркинга Моделей (Unity + HTTP model)

## Цель

`step 4` запускает полноценный runtime-бенчмарк:

- поднимает `N` Unity-сред (`one-env-per-worker`);
- детерминированно распределяет эпизоды;
- на каждом шаге запрашивает модель по `POST /predict`;
- выполняет действия в Unity;
- пишет `episodes/steps/events` CSV и (опционально) кадры.

## Входные зависимости

Перед запуском должны существовать артефакты:

- шага 1:
  - `estimate_scripts/protocol_bundle/manifest/task_manifest.json`
  - `estimate_scripts/protocol_bundle/manifest/benchmark_manifest.json`
  - `estimate_scripts/protocol_bundle/data/tasks/...`
- шага 3:
  - `estimate_scripts/protocol_bundle/contracts/episodes_contracts.csv`
  - `estimate_scripts/protocol_bundle/contracts/events_schedule.csv`
  - `estimate_scripts/protocol_bundle/contracts/scenario_contract_manifest.json`

Если чего-то нет, `step 4` завершится ошибкой preflight.

## Контракт модели

Модель должна слушать HTTP и принимать:

- `POST /predict`
- JSON-поля:
  - `request_id`, `run_id`, `model_id`, `episode_id`, `step_idx`
  - `task_instruction`
  - `history_actions`, `history_events`
  - `available_actions_mask`
  - `active_modalities`
  - `observation_bundle`
  - `budget_left_steps`, `budget_left_time_sec`
  - `worker_slot`

Ответ модели:

- `status`: `ok|unsupported|error`
- `action_raw`, `action_exec`
- `model_latency_sec`
- `error_code`, `error_message`
- `tokens_in`, `tokens_out`
- `meta`

## Принцип strict-blind

В payload модели не отправляются:

- `condition_id`
- `scenario_tag`
- типы perturbation (`event_type` и др.)

Модель получает только уже подготовленные входы:

- ограниченный `available_actions_mask`;
- сенсоры после blackout/noise согласно сценарию.

## Основной запуск

```powershell
cd c:\Users\User\code\paper\LHoPP\VirtualHomeRout

python estimate_scripts/main.py --step 4 `
  --unity-exe dataset/windows_exec.v2.3.0/VirtualHome.exe `
  --parallel-workers 2 `
  --base-port 8090 `
  --protocol-root estimate_scripts/protocol_bundle `
  --contracts-root estimate_scripts/protocol_bundle/contracts `
  --tasks-root estimate_scripts/protocol_bundle/data/tasks `
  --run-root estimate_scripts/runs `
  --model-id my_model `
  --model-family llm `
  --model-host 127.0.0.1 `
  --model-port 9000 `
  --model-timeout-sec 30 `
  --save-frames `
  --frame-mode normal `
  --frame-camera-index 0
```

## Быстрый smoke-запуск

Чтобы быстро проверить runtime без полного прогона:

```powershell
python estimate_scripts/main.py --step 4 `
  --unity-exe dataset/windows_exec.v2.3.0/VirtualHome.exe `
  --parallel-workers 1 `
  --base-port 8090 `
  --protocol-root estimate_scripts/protocol_bundle `
  --contracts-root estimate_scripts/protocol_bundle/contracts `
  --tasks-root estimate_scripts/protocol_bundle/data/tasks `
  --run-root estimate_scripts/runs `
  --model-id smoke_model `
  --model-family smoke `
  --model-host 127.0.0.1 `
  --model-port 9000 `
  --model-timeout-sec 5 `
  --max-episodes 2
```

`--max-episodes 0` (по умолчанию) = полный набор.

## Артефакты шага 4

В каталоге `estimate_scripts/runs/{run_id}`:

- `run_summary.json` — итог запуска и конфиг;
- `env_setup/env_registry.csv` — слоты Unity/порты/PID/status;
- `manifest/*` — копии входных manifest/contracts + hashes;
- `cells/{condition_id}/episodes.csv`;
- `cells/{condition_id}/steps.csv`;
- `cells/{condition_id}/events.csv`;
- `cells/{condition_id}/metadata.json`;
- `frames_manifest.csv` (+ `frames/.../*.png`, если включено `--save-frames`).

## Критерии корректного запуска

- `run_summary.json -> status = completed`;
- `worker_errors_total = 0` (или понятные диагностические причины);
- в `cells/*` присутствуют `episodes/steps/events` с данными;
- если `--save-frames`, в `frames_manifest.csv` есть строки.

