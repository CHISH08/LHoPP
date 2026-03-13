# 09. Тестирование Шага 4 (Smoke + async mock model)

## Что тестируем

Smoke-тест проверяет end-to-end pipeline шага 4:

1. Поднимается асинхронная mock-модель на HTTP-порту.
2. `step 4` запускается с несколькими Unity-средами.
3. Проверяется, что:
   - run завершился успешно;
   - CSV-логи созданы и заполнены;
   - кадры сохраняются (если включены);
   - strict-blind не нарушен;
   - модель принимает запросы параллельно (`max_inflight >= 2`).

## Компоненты теста

- mock сервер: `estimate_scripts/runtime/mock_async_model_server.py`
- smoke-тест: `estimate_scripts/tests/test_step4_smoke.py`

## Запуск smoke-теста

```powershell
cd c:\Users\User\code\paper\LHoPP\VirtualHomeRout

python estimate_scripts/tests/test_step4_smoke.py `
  --repo-root . `
  --unity-exe dataset/windows_exec.v2.3.0/VirtualHome.exe `
  --parallel-workers 2 `
  --base-port 8090 `
  --model-port 19000 `
  --model-timeout-sec 8 `
  --max-episodes 8 `
  --save-frames
```

## Что делает тест под капотом

1. Запускает async mock server (`POST /predict`) c случайным выбором действия из `available_actions_mask`.
2. Запускает `python estimate_scripts/main.py --step 4 ...`.
3. После завершения валидирует:
   - `run_summary.json` (`status=completed`);
   - `episodes_total == --max-episodes`;
   - `cells/*/{episodes,steps,events}.csv` существуют и не пустые;
   - `frames_manifest.csv` существует (и непустой при `--save-frames`);
   - в логах mock-модели нет запрещенных ключей (`condition_id`, `scenario_tag`, `event_type`);
   - есть запросы от worker-слотов;
   - при `parallel-workers > 1` зафиксирован `max_inflight >= 2`.

## Артефакты теста

Основные:

- benchmark run: `estimate_scripts/runs/vh_step4_*`
- mock model logs:
  - `estimate_scripts/runs/mock_model_smoke/requests.jsonl`
  - `estimate_scripts/runs/mock_model_smoke/server_stats.json`

## Интерпретация результата

- Успех: тест выводит `[smoke] PASS`.
- Провал: тест кидает `RuntimeError` с точной причиной (что именно не сошлось).

## Рекомендации

- Для CI/быстрой локальной проверки используйте:
  - `--max-episodes 2..8`
  - `--parallel-workers 2`
- Для детального профилирования уже используйте полный запуск (`--max-episodes 0`).
- Для проверки всех шагов (`1→3→2→4`) используйте интеграционный сценарий из `10_full_pipeline_test.md`.
