# 10. Полный Интеграционный Тест (Шаги 1→3→2→4)

## Назначение

Этот тест проверяет работоспособность всей цепочки:

1. `step 1` — сбор детерминированного protocol bundle.
2. `step 3` — генерация контрактов сценариев.
3. `step 2` — подъем Unity-сред и probe.
4. `step 4` — запуск бенчмарка с HTTP-моделью.

Тест создает отдельную папку артефактов с префиксом:

- `estimate_scripts/test_runs/test-YYYYMMDD_HHMMSS`

Внутри сохраняются:

- логи команд,
- логи mock-модели,
- protocol bundle,
- step2/step4 run-папки,
- CSV/JSON артефакты,
- кадры (если включено),
- итоговый отчет.

## Скрипт

- `estimate_scripts/tests/test_full_pipeline.py`

## Запуск

```powershell
cd c:\Users\User\code\paper\LHoPP\VirtualHomeRout

python estimate_scripts/tests/test_full_pipeline.py `
  --repo-root . `
  --unity-exe dataset/windows_exec.v2.3.0/VirtualHome.exe `
  --per-stratum 2 `
  --parallel-workers 2 `
  --max-episodes 4 `
  --save-frames
```

Строгий режим по worker errors:

```powershell
python estimate_scripts/tests/test_full_pipeline.py ... --allow-worker-errors
```

## Ключевые проверки

### Шаг 1

- `selected_tasks.csv` создан;
- `task_manifest.json` и `benchmark_manifest.json` созданы;
- число выбранных задач равно `per_stratum * 3`.

### Шаг 3

- `episodes_contracts.csv`, `steps_contracts.csv`, `events_schedule.csv`;
- `conditions_contracts.json`, `schema_refs.json`, `scenario_contract_manifest.json`.

### Шаг 2

- `env_registry.csv` содержит `parallel_workers` строк;
- все слоты `ready`;
- `health_report.json -> overall_status=ready`.

### Шаг 4

- `run_summary.json -> status=completed`;
- `episodes_total == --max-episodes`;
- `steps_total > 0`;
- `cells/*/{episodes.csv,steps.csv,events.csv,metadata.json}` существуют;
- заполнены step timing поля:
  - `decision_time_step_sec`
  - `sim_exec_time_step_sec`
  - `episode_wallclock_step_sec`
- при `--save-frames`:
  - `frames_manifest.csv` непустой;
  - все `frame_path` существуют.

### Strict-blind и async

По логам mock-модели:

- нет forbidden keys в запросе модели:
  - `condition_id`
  - `scenario_tag`
  - `event_type`
- есть запросы от expected worker slots;
- при `parallel-workers>1` и `max-episodes>1`:
  - `server_stats.max_inflight >= 2`.

## Обработка отказов

Скрипт учитывает и явно фиксирует:

- отсутствие входных артефактов;
- падение команд шагов (`returncode != 0`);
- timeout команд;
- недоступность Unity портов / mock-порта;
- отсутствие/пустоту обязательных CSV/JSON;
- strict-blind нарушения;
- отсутствие кадров при включенном `--save-frames`;
- ошибки воркеров шага 4 (можно ослабить `--allow-worker-errors`).
- известный кейс `OpenEXR codec is disabled` на depth probe шага 2:
  - фиксируется как warning,
  - тест продолжает шаг 4 (так как шаг 4 использует `frame_mode=normal`).

## Выходные отчеты

В `test-.../report`:

- `summary.md` — читабельный итог PASS/FAIL;
- `checks.json` — все проверки с `ok/severity/details`;
- `config.json` — параметры теста;
- `step_summaries.json` — метаданные запусков шагов;
- `artifacts.json` — ссылки на ключевые папки/файлы.
