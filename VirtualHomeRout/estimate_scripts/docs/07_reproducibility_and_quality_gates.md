# 07. Воспроизводимость и quality gates

## 1) Manifest/version/hash policy

Каждый запуск обязан фиксировать:

- `protocol_version`
- `benchmark_manifest.json` + `benchmark_manifest.sha256`
- `task_manifest.json` + `task_manifest.sha256`
- `code_version` (commit hash/версия пакета)
- `unity_version` и путь к executable

Изменение любого из этих пунктов создает новый `run_id`.

## 2) Детерминизм

Детерминизм считается подтвержденным, если:

- `task_manifest_hash` совпадает;
- `condition_config_hash` совпадает;
- `event_schedule_hash` совпадает;
- при повторном прогоне с теми же входами совпадают `episode_id` и порядок шагов.

## 3) Статусы benchmark-cell

Каждая ячейка `(model_id, track, condition_id)` получает один статус:

- `ok` - валидно и готово к сравнению;
- `unsupported` - модель не поддерживает интерфейс условия;
- `error` - технический сбой исполнения;
- `invalid` - нарушены требования протокола/логов.

`unsupported` и `error` нельзя тихо удалять из финального отчета.

## 4) Quality gates (обязательны до сравнения)

Cell считается валидной только при выполнении всех проверок:

1. Совпадает hash манифестов и версия протокола.
2. Совпадают seeds, budgets и success oracle с эталонной конфигурацией.
3. Полны логи `episode/step/event`.
4. `condition_id` соответствует правилам naming.
5. Маски сенсоров/действий реально применены и отражены в step-log.
6. Нет конфликтов в `episode_id`, `pair_id`, `event_id`.

При нарушении любого пункта: `status=invalid`.

## 5) Coverage и причины исключения

В итоговом `coverage_report.json` обязательно:

- общее число cell;
- число `ok/unsupported/error/invalid`;
- доля покрытия по сценариям `L1..L5`;
- недобор по strata (`easy/medium/hard`);
- список причин `invalid` с кодами.

## 6) Минимальный deterministic workflow

1. Сгенерировать `task_manifest` и hash.
2. Сгенерировать `benchmark_manifest` (условия, seeds, budgets) и hash.
3. Для каждой модели прогнать все `condition_id`.
4. Проверить quality gates на уровне cell.
5. Сформировать coverage/validity отчеты.
6. Выполнять сравнение только по `status=ok`.
