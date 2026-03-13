# 02. Стратификация и отбор задач

## Источник задач

Базовый набор берется из:

- `VirtualHomeRout/virtualhome/virtualhome/dataset/programs_processed_precond_nograb_morepreconds/executable_programs`

Используются только валидные `.txt` executable-программы.

## Формальная стратификация сложности

Сложность определяется по числу атомарных действий в программе:

- `easy`: `actions_count <= 5`
- `medium`: `6 <= actions_count <= 12`
- `hard`: `actions_count >= 13`

## Размер выборки

На каждую страту выбирается:

- `30` задач `easy`
- `30` задач `medium`
- `30` задач `hard`

Итого базово:

- `90` задач на модель для одного уровня условий.

## Детерминированный отбор

Алгоритм отбора обязан быть полностью повторяемым:

1. Собрать все кандидатные задачи и вычислить `actions_count`.
2. Проставить страту (`easy/medium/hard`).
3. Отсортировать стабильно по `(relative_task_path, actions_count)`.
4. Для каждой страты выполнить детерминированный shuffle с фиксированным `selection_seed`.
5. Взять первые `30` задач из каждой страты.
6. Сохранить `task_manifest.json` и `task_manifest_hash`.

## Контроль дисбаланса

Если в страте меньше 30 задач:

- пометить запуск как `invalid` для ranking;
- зафиксировать недобор в `coverage_report`;
- не восполнять другими стратами.

## Формат таблицы coverage (обязательный в отчете)

| stratum | target_n | selected_n | missing_n | status |
| --- | ---: | ---: | ---: | --- |
| easy | 30 | ... | ... | ok/invalid |
| medium | 30 | ... | ... | ok/invalid |
| hard | 30 | ... | ... | ok/invalid |
