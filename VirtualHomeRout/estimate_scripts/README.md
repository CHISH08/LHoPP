# estimate_scripts

Пайплайн состоит из 4 шагов:

1. `step 1` — сбор детерминированного набора задач (`protocol_bundle`).
2. `step 2` — bootstrap и проверка Unity-сред.
3. `step 3` — генерация машиночитаемых контрактов сценариев.
4. `step 4` — запуск модели в Unity и логирование.

Запускать из корня репозитория:

```powershell
cd c:\Users\User\code\paper\LHoPP\VirtualHomeRout
```

---

## Шаг 1: Сбор Набора Задач (`--step 1`)

Что делает:

- читает `executable_programs`;
- детерминированно выбирает задачи по стратам `easy|medium|hard`;
- копирует выбранные `.txt` в `protocol_bundle/data/tasks`;
- создает манифесты.

### Флаги шага 1

- `--dataset-root` (default: `virtualhome/virtualhome/dataset/programs_processed_precond_nograb_morepreconds/executable_programs`)
  - Путь к исходным task-файлам.
- `--output-root` (default: `estimate_scripts/protocol_bundle`)
  - Куда писать bundle (`data/`, `manifest/`).
- `--seed` (default: `42`)
  - Seed детерминированной выборки.
- `--per-stratum` (default: `30`)
  - Сколько задач брать на каждую страту.
- `--track` (default: `unified_ranking`)
  - Имя трека для `condition_id`.
- `--dry-run` (flag, default: `False`)
  - Только расчет выборки без записи файлов.

### Пример команды шага 1

```powershell
python estimate_scripts/main.py --step 1 `
  --dataset-root virtualhome/virtualhome/dataset/programs_processed_precond_nograb_morepreconds/executable_programs `
  --output-root estimate_scripts/protocol_bundle `
  --seed 42 `
  --per-stratum 30 `
  --track unified_ranking
```

### Выход шага 1

- `estimate_scripts/protocol_bundle/data/selected_tasks.csv`
- `estimate_scripts/protocol_bundle/data/tasks/...`
- `estimate_scripts/protocol_bundle/manifest/task_manifest.json`
- `estimate_scripts/protocol_bundle/manifest/benchmark_manifest.json`
- `estimate_scripts/protocol_bundle/manifest/*.sha256`

---

## Шаг 2: Unity Bootstrap (`--step 2`)

Что делает:

- поднимает `N` Unity-процессов;
- проверяет соединение, reset, сенсоры и пробное действие;
- пишет технические логи;
- держит среды в idle (или выходит по таймауту).

### Флаги шага 2

- `--unity-exe` (default: `dataset/windows_exec.v2.3.0/VirtualHome.exe`)
  - Путь к Unity executable.
- `--parallel-workers` (default: `1`)
  - Количество Unity-сред.
- `--base-port` (default: `8090`)
  - Базовый порт Unity (`slot i -> base_port + i`).
- `--scene-id` (default: `0`)
  - Сцена для `reset(scene_id)`.
- `--time-scale` (default: `1.0`)
  - Time scale для probe-действий.
- `--skip-animation` (flag, default: `False`)
  - Пробовать действия без анимации.
- `--image-width` (default: `320`)
  - Ширина probe-кадра.
- `--image-height` (default: `240`)
  - Высота probe-кадра.
- `--run-root` (default: `estimate_scripts/runs`)
  - Куда писать `unity_bootstrap_<timestamp>`.
- `--standby-seconds` (default: `0`)
  - `0` = ждать `Ctrl+C`, `>0` = выйти по таймеру.

### Пример команды шага 2

```powershell
python estimate_scripts/main.py --step 2 `
  --unity-exe dataset/windows_exec.v2.3.0/VirtualHome.exe `
  --parallel-workers 2 `
  --base-port 8090 `
  --scene-id 0 `
  --run-root estimate_scripts/runs
```

### Выход шага 2

- `estimate_scripts/runs/unity_bootstrap_<ts>/env_setup/env_registry.csv`
- `estimate_scripts/runs/unity_bootstrap_<ts>/env_setup/sensor_probe.csv`
- `estimate_scripts/runs/unity_bootstrap_<ts>/env_setup/interaction_probe.csv`
- `estimate_scripts/runs/unity_bootstrap_<ts>/env_setup/health_report.json`

---

## Шаг 3: Контракты Сценариев (`--step 3`)

Что делает:

- читает `task_manifest.json` и `benchmark_manifest.json` из шага 1;
- генерирует контракты эпизодов/шагов/событий;
- фиксирует hash/signature.

### Флаги шага 3

- `--protocol-root` (default: `estimate_scripts/protocol_bundle`)
  - Корень bundle шага 1.
- `--contracts-output-root` (default: `estimate_scripts/protocol_bundle/contracts`)
  - Куда писать contracts.
- `--contracts-force` (flag, default: `False`)
  - Принудительно пересоздать контракты.

### Пример команды шага 3

```powershell
python estimate_scripts/main.py --step 3 `
  --protocol-root estimate_scripts/protocol_bundle `
  --contracts-output-root estimate_scripts/protocol_bundle/contracts
```

### Выход шага 3

- `estimate_scripts/protocol_bundle/contracts/conditions_contracts.json`
- `estimate_scripts/protocol_bundle/contracts/episodes_contracts.csv`
- `estimate_scripts/protocol_bundle/contracts/steps_contracts.csv`
- `estimate_scripts/protocol_bundle/contracts/events_schedule.csv`
- `estimate_scripts/protocol_bundle/contracts/schema_refs.json`
- `estimate_scripts/protocol_bundle/contracts/scenario_contract_manifest.json`

---

## Шаг 4: Бенчмарк Модели В Unity (`--step 4`)

Что делает:

- поднимает пул Unity-сред;
- читает контракты шага 3;
- на каждом шаге формирует payload и отправляет в модель по `POST /predict`;
- валидирует/исполняет действие в Unity;
- пишет `episodes/steps/events`, кадры и summary.

### Флаги шага 4

- `--unity-exe` (default: `dataset/windows_exec.v2.3.0/VirtualHome.exe`)
  - Unity executable.
- `--parallel-workers` (default: `1`)
  - Количество параллельных сред/воркеров.
- `--base-port` (default: `8090`)
  - Базовый порт Unity API.
- `--time-scale` (default: `1.0`)
  - Ускорение выполнения действий.
- `--skip-animation` (flag, default: `False`)
  - Выполнение действий без анимации (если поддерживается).
- `--image-width` (default: `320`)
  - Ширина изображений наблюдений/кадров.
- `--image-height` (default: `240`)
  - Высота изображений наблюдений/кадров.
- `--run-root` (default: `estimate_scripts/runs`)
  - Куда писать `vh_step4_<timestamp>`.

- `--protocol-root` (default: `estimate_scripts/protocol_bundle`)
  - Корень bundle шага 1.
- `--contracts-root` (default: `estimate_scripts/protocol_bundle/contracts`)
  - Контракты шага 3.
- `--tasks-root` (default: `estimate_scripts/protocol_bundle/data/tasks`)
  - Task-файлы, которые исполняются.

- `--model-id` (default: `model_default`)
  - ID модели в логах.
- `--model-family` (default: `unknown`)
  - Семейство модели в логах.
- `--model-host` (default: `127.0.0.1`)
  - Хост сервера модели.
- `--model-port` (default: `9000`)
  - Порт сервера модели.
- `--model-timeout-sec` (default: `30.0`)
  - Таймаут запроса к модели.

- `--max-episodes` (default: `0`)
  - Ограничение числа эпизодов (`0` = все).
- `--save-frames` (flag, default: `False`)
  - Сохранять PNG на каждом шаге.
- `--frame-camera-index` (default: `0`)
  - Индекс камеры для сохранения кадров.
- `--frame-mode` (default: `normal`)
  - Режим кадра: `normal|seg_inst|seg_class|depth|flow|albedo|illumination|surf_normals`.
- `--video-fps` (default: `5`)
  - FPS-метаданные для последующей склейки видео.

### Пример команды шага 4

```powershell
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
  --frame-camera-index 0 `
  --frame-mode normal
```

### Выход шага 4

- `estimate_scripts/runs/vh_step4_<ts>/run_summary.json`
- `estimate_scripts/runs/vh_step4_<ts>/run_overview.json`
- `estimate_scripts/runs/vh_step4_<ts>/README.md`
- `estimate_scripts/runs/vh_step4_<ts>/env_setup/env_registry.csv`
- `estimate_scripts/runs/vh_step4_<ts>/manifest/*`
- `estimate_scripts/runs/vh_step4_<ts>/cells/{condition_id}/*`
- `estimate_scripts/runs/vh_step4_<ts>/logs/episodes_all.csv`
- `estimate_scripts/runs/vh_step4_<ts>/logs/steps_all.csv`
- `estimate_scripts/runs/vh_step4_<ts>/logs/events_all.csv`
- `estimate_scripts/runs/vh_step4_<ts>/logs/episodes_index.csv`
- `estimate_scripts/runs/vh_step4_<ts>/frames_manifest.csv`
- `estimate_scripts/runs/vh_step4_<ts>/frames/{model_id}/{episode_id}/step_XXXX.png` (если `--save-frames`)

---

## Контракт модели (`step 4`)

Endpoint:

- `POST /predict`

Ключевые поля запроса:

- `request_id`, `run_id`, `episode_id`, `step_idx`
- `task_instruction`
- `history_actions`, `history_events`
- `available_actions_mask`
- `active_modalities`
- `observation_bundle`
- `budget_left_steps`, `budget_left_time_sec`
- `worker_slot`

Ключевые поля ответа:

- `status` (`ok|unsupported|error`)
- `action_raw`, `action_exec`
- `model_latency_sec`
- `error_code`, `error_message`
- `tokens_in`, `tokens_out`
- `meta`

Strict-blind:

- `condition_id`, `scenario_tag`, `event_type` в модель не отправляются.

---

## Как читать логи шага 4

Рекомендуемый порядок:

1. `run_overview.json`
2. `logs/episodes_index.csv`
3. `logs/episodes_all.csv`
4. `logs/steps_all.csv`
5. `logs/events_all.csv`
6. `frames_manifest.csv`

---

## Тестовые команды

Smoke test (`step 4`):

```powershell
python estimate_scripts/tests/test_step4_smoke.py `
  --repo-root . `
  --unity-exe dataset/windows_exec.v2.3.0/VirtualHome.exe `
  --parallel-workers 2 `
  --base-port 8090 `
  --model-port 19000 `
  --max-episodes 4 `
  --save-frames
```

Full pipeline test (`1->2->3->4`):

```powershell
python estimate_scripts/tests/test_full_pipeline.py `
  --repo-root . `
  --unity-exe dataset/windows_exec.v2.3.0/VirtualHome.exe `
  --per-stratum 1 `
  --parallel-workers 2 `
  --max-episodes 3 `
  --step2-standby-seconds 3 `
  --save-frames
```

---

## Docker + Gradio UI

Для контейнерного запуска с веб-интерфейсом:

- docker файлы: `estimate_scripts/docker/`
- основной UI-скрипт: `estimate_scripts/runtime/gradio_benchmark_app.py`

Быстрый старт:

```powershell
cd estimate_scripts/docker
copy .env.example .env
docker compose --env-file .env up --build
```

UI откроется на `http://localhost:7860`.

Важно:

- Unity и dataset должны быть подключены как volume (см. `docker/.env.example`).
- В Linux-контейнере нужен Linux Unity executable (`.x86_64`), Windows `.exe` не запустится.
