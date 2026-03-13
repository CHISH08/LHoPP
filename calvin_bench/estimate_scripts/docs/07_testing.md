# 07. Тестирование (Smoke + Runtime)

Этот документ фиксирует практический набор тестов для `calvin_bench/estimate_scripts`.

## Что проверяем

1. `step 1`: детерминированная генерация выборки.
2. `step 2`: генерация контрактов и схем логирования.
3. `step 3`: runtime-интеграция (модель + сценарии + протоколы + логи + кадры).

## Тесты

## `test_protocol_steps_smoke.py`

Путь:

- [test_protocol_steps_smoke.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/tests/test_protocol_steps_smoke.py)

Проверяет:

- step1 строит bundle с ожидаемым `selected_total`;
- step1 детерминирован при одинаковом seed;
- step2 генерирует все обязательные артефакты;
- `conditions_total == 168`;
- `episodes_total == selected_total * 168`.

Запуск:

```powershell
python calvin_bench/estimate_scripts/tests/test_protocol_steps_smoke.py `
  --repo-root . `
  --calvin-root calvin_bench/calvin `
  --official-total 40 `
  --selected-total 8 `
  --seed 42
```

## `test_step3_smoke.py`

Путь:

- [test_step3_smoke.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/tests/test_step3_smoke.py)

Проверяет:

- step3 раннер выполняет эпизоды параллельно;
- в раннер подаются наблюдения после применения протоколов;
- создаются `run_summary/run_overview`;
- создаются `logs/episodes_all.csv`, `logs/steps_all.csv`, `logs/events_all.csv`, `logs/episodes_index.csv`;
- создаются `cells/{condition_id}/*`;
- при `--save-frames` создаются PNG и `frames_manifest.csv`.

Важно:

- тест не требует реального `hydra`/pybullet runtime;
- внутри теста env/oracle заменяются на fake-реализации;
- модель используется детерминированная test-модель:
  - [mock_step3_model.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/tests/mock_step3_model.py)

Запуск:

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

## Критерий PASS

- Скрипт завершается с кодом `0`;
- В stdout печатается JSON с `"status": "pass"`;
- В указанном `test_root` присутствуют ожидаемые артефакты.

## Когда нужен отдельный full-run

Smoke-тесты проверяют протокол и runtime-логику.
Для проверки реальной CALVIN-интеграции с настоящей средой и настоящей моделью запускайте step3 как benchmark run (см. [08_benchmarking.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/08_benchmarking.md)).


## `test_step3_http_host_smoke.py`

Путь:

- [test_step3_http_host_smoke.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/tests/test_step3_http_host_smoke.py)

Проверяет:

- поднимается локальный host HTTP mock model server (`/predict`);
- step3 раннер реально ходит в модель по `model-backend=http`;
- считаются `episodes/steps/events`, `worker_errors_total=0`;
- в `requests.jsonl` сервера число запросов равно `steps_total`;
- пишутся `server_stats.json` и runtime-логи.

Запуск:

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
