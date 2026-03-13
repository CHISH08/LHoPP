# 04. Контракты входа/выхода (ModelAdapter)

Этот документ фиксирует протокольный интерфейс `ModelAdapter` (без реализации кода).

## Интерфейс `ModelAdapter` (логический)

- `reset(episode_context) -> None`
- `predict(step_payload) -> model_response`
- `close() -> None`

## Вход эпизода (`episode_context`)

Обязательные поля:

- `run_id`
- `track`
- `episode_id`
- `task_id`
- `condition_id`
- `seed`
- `stratum` (`easy|medium|hard`)
- `max_steps`
- `max_time_sec`
- `action_whitelist_version`

## Вход шага (`step_payload`)

Обязательные поля:

- `episode_id`
- `step_idx`
- `task_instruction` (описание цели)
- `history_actions` (выполненные действия)
- `history_events` (инъекции, сбои, ограничения)
- `available_actions_mask` (допустимые действия на шаге)
- `active_modalities`
- `observation_bundle`
- `budget_left_steps`
- `budget_left_time_sec`

## Различия по трекам

## unified_ranking

- Минимальный и одинаковый набор `observation_bundle` для всех моделей.
- Запрещены дополнительные приватные поля.

## family_native_diagnostic

- Допускаются расширенные поля в `observation_bundle_ext`.
- Расширения обязаны логироваться и версионироваться.
- Результаты этого трека не идут в главное межсемейное ранжирование.

## Выход модели (`model_response`)

Обязательные поля:

- `status` (`ok|unsupported|error`)
- `action_raw` (строка, как вернула модель)
- `action_exec` (канонизированное действие для Unity; может быть пустым при `error/unsupported`)
- `model_latency_sec` (абсолютное время ответа)
- `error_code` (если `status != ok`)
- `error_message` (если `status != ok`)
- `tokens_in` (опционально, абсолютное число)
- `tokens_out` (опционально, абсолютное число)
- `meta` (опционально, сериализуемый словарь)

## Допустимое действие

Действие считается допустимым, если одновременно:

- имеет корректный формат VirtualHome-команды;
- входит в `available_actions_mask`;
- не нарушает активные ограничения условия (`condition_id`);
- прошло канонизацию для Unity-выполнения.

## Коды ошибок (минимальный набор)

- `unsupported_model_interface`
- `empty_action`
- `invalid_action_format`
- `action_not_allowed`
- `timeout_model`
- `runtime_exception`
