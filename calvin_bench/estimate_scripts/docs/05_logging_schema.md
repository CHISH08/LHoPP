# 05. Схема логирования (CSV-first протокол)

## Принцип

Для CALVIN benchmark первичен не один итоговый KPI, а полный набор сырых логов, пригодных для разных аналитических задач:

- success / chain success
- timing / efficiency
- safety
- recovery / rollback / replan
- robustness to sensor loss / noise
- single-sensor comparison
- action-level degradation

Поэтому этот документ описывает не просто файлы, а **что означает каждая колонка и для какой аналитики она нужна**.

## 1. Основные файлы

Структура артефактов:

```text
calvin_bench/estimate_scripts/runs/
  {run_id}/
    manifest/
      benchmark_manifest.json
      benchmark_manifest.sha256
      task_manifest.json
      task_manifest.sha256
    cells/
      {condition_id}/
        episodes.csv
        steps.csv
        events.csv
        metadata.json
    logs/
      episodes_all.csv
      steps_all.csv
      events_all.csv
      episodes_index.csv
    run_summary.json
    run_overview.json
```

## 2. `episodes.csv`

Назначение:

- итог эпизода;
- сравнение baseline/stress;
- агрегирование по уровню действия;
- агрегирование по scenario profile.

### Колонки `episodes.csv`

| Колонка | Что означает | Зачем нужна |
| --- | --- | --- |
| `run_id` | Идентификатор benchmark-run. | Нужен для объединения всех эпизодов одного запуска. |
| `track` | Логический трек benchmark'а. | Нужен, чтобы не смешивать разные протокольные режимы и experimental branches. |
| `model_id` | Идентификатор конкретной модели или checkpoint. | Нужен для сравнения между моделями и checkpoint-level анализа. |
| `model_family` | Семейство модели, например planner, LLM, policy, VLA. | Нужен для within-family и cross-family агрегирования. |
| `episode_id` | Уникальный идентификатор эпизода. | Нужен для связи episode-level результата с `steps.csv` и `events.csv`. |
| `sequence_id` | Идентификатор official CALVIN sequence. | Нужен для воспроизводимости и честного сравнения на одном сценарии. |
| `initial_state_id` | Идентификатор стартового состояния среды. | Нужен для контроля, что модели стартуют из одной и той же конфигурации мира. |
| `condition_id` | Идентификатор полного condition contract. | Нужен для связи результата с конкретной комбинацией action level, sensors и stress profile. |
| `scenario_profile_id` | Активный baseline/stress/safety/recovery profile. | Нужен для аналитики по разным условиям исполнения. |
| `action_level_id` | Активный уровень действия `L1-L4`. | Нужен для сравнения сложности интерфейса действия. |
| `observation_profile_id` | Активный observation contract. | Нужен для сравнения по датчикам и для проверки sensor constraints. |
| `pair_id` | Идентификатор baseline/stress пары. | Нужен для вычисления degradation delta и recovery delta. |
| `baseline_episode_id` | Ссылка на paired `ideal` episode. | Нужна для быстрых join-ов и аудита paired comparison. |
| `subtasks_total` | Число целевых подзадач в сценарии. | Нужен как знаменатель для success и chain metrics. |
| `subtasks_solved` | Число успешно решенных подзадач. | Нужен как числитель для core success и partial success. |
| `max_subtask_steps` | Максимальный шаговый бюджет на подзадачу. | Нужен для fairness и анализа budget pressure. |
| `max_episode_steps` | Максимальный шаговый бюджет на эпизод. | Нужен для сравнения end-to-end эффективности. |
| `max_time_sec` | Максимальный wall-clock budget эпизода. | Нужен для timeout-based evaluation и latency-aware comparison. |
| `status` | Итоговый статус эпизода. | Нужен, чтобы различать full success, partial success, fail, timeout, abort и safety stop. |
| `steps_total` | Общее число реально выполненных low-level шагов. | Нужен для efficiency и step-budget анализа. |
| `terminate_reason` | Причина завершения эпизода. | Нужна для объяснения, почему rollout закончился: success, fail, timeout, safety stop и т.д. |
| `decision_time_total_sec` | Суммарное время полного цикла принятия решений. | Нужен для общей оценки overhead на стороне модели и adapter-а. |
| `predict_time_total_sec` | Суммарное чистое время предикта модели. | Нужен для сравнения inference cost между методами. |
| `executor_time_total_sec` | Суммарное время executor-а или action canonicalization. | Нужен для отделения стоимости исполнения от стоимости предикта. |
| `env_step_time_total_sec` | Суммарное время шагов среды. | Нужен для отделения simulator cost от model cost. |
| `oracle_check_time_total_sec` | Суммарное время oracle-проверок. | Нужен, чтобы не смешивать время task oracle с временем модели и среды. |
| `episode_wallclock_total_sec` | Полный wall-clock эпизода. | Нужен для end-to-end latency metric. |
| `started_at_utc` | Время старта эпизода. | Нужен для аудита и трассировки timeline run-а. |
| `finished_at_utc` | Время окончания эпизода. | Нужен для аудита и расчета duration, если нужно перепроверить total timing. |
| `manifest_hash` | Хэш manifest-а сценария и условий. | Нужен для воспроизводимости и проверки, что строка относится к правильной версии scenario contract. |

## 3. `steps.csv`

Назначение:

- full decision trace;
- per-step timing;
- safety behavior;
- subsequence behavior;
- recovery / rollback / replan analysis.

### Колонки `steps.csv`

| Колонка | Что означает | Зачем нужна |
| --- | --- | --- |
| `run_id` | Идентификатор benchmark-run. | Нужен для связи шага с общим запуском. |
| `episode_id` | Идентификатор эпизода. | Нужен для связи шага с episode-level outcome. |
| `step_idx` | Индекс low-level шага. | Нужен для построения полного timeline и step-level метрик. |
| `subtask_idx` | Индекс текущей подзадачи. | Нужен для chain-success и subsequence анализа. |
| `timestamp_utc` | Временная отметка шага. | Нужна для аудита, ordering и поиска runtime spikes. |
| `sequence_id` | Идентификатор official sequence. | Нужен для свертки шагов по одному и тому же сценарию. |
| `condition_id` | Идентификатор condition contract. | Нужен для связи шага с точной конфигурацией запуска. |
| `scenario_profile_id` | Активный scenario profile. | Нужен для разделения baseline, noise, dropout, safety и recovery step traces. |
| `action_level_id` | Активный action level. | Нужен для сравнения step behavior на `L1-L4`. |
| `observation_profile_id` | Активный observation contract. | Нужен для sensor comparison и sensor-dropout analysis. |
| `pair_id` | Идентификатор baseline/stress пары. | Нужен для per-step delta analysis относительно paired `ideal`. |
| `current_instruction_text` | Текущая текстовая инструкция. | Нужна, чтобы понимать семантическую цель шага и аудировать language-conditioned decisions. |
| `oracle_target_subtask` | Каноническая цель подзадачи. | Нужна для success oracle и для сравнения с действиями модели. |
| `decision_granularity` | Тип решения: `symbolic_subtask` или `control_step`. | Нужен, чтобы не смешивать L1 symbolic decisions с low-level control steps. |
| `active_modalities` | Человекочитаемый список доступных модальностей. | Нужен для интерпретации условий, в которых модель принимала решение. |
| `sensor_mask_before` | Маска сенсоров до применения шага. | Нужна для sensor-dropout и safety analysis. |
| `sensor_mask_after` | Маска сенсоров после применения шага. | Нужна для отслеживания динамических blackout/dropout events. |
| `model_input_summary` | Краткая summary того, что было подано модели. | Нужна для аудита без записи огромных raw tensors в CSV. |
| `model_output_raw` | Исходный ответ модели. | Нужен для post-hoc анализа decision quality и parsing errors. |
| `executor_applied_action` | Действие, реально переданное в executor/env. | Нужно для проверки, не отличается ли исполненное действие от сырого ответа модели. |
| `model_status` | Статус ответа модели на этом шаге. | Нужен для отделения валидного ответа от error/unsupported cases. |
| `model_error_code` | Код ошибки модели. | Нужен для статистики invalid actions и runtime failures. |
| `model_error_message` | Текстовое пояснение ошибки. | Нужно для детальной отладки конкретных сбоев. |
| `action_valid_flag` | Прошел ли ответ модели валидацию action contract. | Нужен для validity metrics и safety analysis. |
| `oracle_success_current_step` | Зафиксировал ли oracle успех целевой подзадачи на этом шаге. | Нужен для success timing и chain completion analysis. |
| `subtask_status` | Статус текущей подзадачи после шага. | Нужен, чтобы понимать, идет ли подзадача, завершена ли она или уже провалена. |
| `episode_status` | Статус всего эпизода после шага. | Нужен для понимания, продолжается ли эпизод, завершился ли он или вошел в abort state. |
| `subsequence_success_len` | Длина уже успешно пройденного префикса official sequence. | Нужна для chain success и partial progress metrics. |
| `noise_applied_flag` | Был ли применен noise на этом шаге. | Нужен для robustness under noise и для отделения noisy vs clean steps. |
| `safety_flag` | Находится ли шаг в safety-critical context или зафиксировано safety condition. | Нужен для safety metrics и фильтрации опасных ситуаций. |
| `safety_reaction` | Как модель или раннер среагировали в safety context. | Нужна для сравнения stop/abstain/continue/fallback behavior. |
| `rollback_attempted` | Была ли попытка rollback на этом шаге. | Нужна для recovery analysis. |
| `rollback_success` | Сработал ли rollback. | Нужен для rollback success rate. |
| `recovery_phase` | Текущая стадия recovery. | Нужна, чтобы отличать detection, replan, rollback, resume и post-recovery execution. |
| `replan_event` | Был ли инициирован replan. | Нужен для replan frequency и latency analysis. |
| `decision_time_ms` | Полное время decision loop на шаге. | Нужно для total per-step overhead. |
| `predict_time_ms` | Время чистого model inference на шаге. | Нужно для сравнения model latency между методами. |
| `executor_time_ms` | Время executor-а на шаге. | Нужно для отделения inference cost от execution cost. |
| `env_step_time_ms` | Время simulator step на шаге. | Нужно для анализа влияния среды на throughput. |
| `oracle_check_time_ms` | Время oracle-проверки на шаге. | Нужно для оценки стоимости успех-проверки. |
| `wallclock_step_time_ms` | Полный wall-clock одного шага. | Нужно для сравнения end-to-end latency. |
| `budget_left_subtask_steps` | Остаток шагового бюджета подзадачи. | Нужен для budget-aware analysis. |
| `budget_left_episode_steps` | Остаток шагового бюджета эпизода. | Нужен для end-to-end efficiency и early-exit analysis. |
| `budget_left_time_ms` | Остаток time budget. | Нужен для timeout-aware comparison. |
| `termination_reason` | Причина завершения, если шаг был последним. | Нужна для связи финального шага с outcome эпизода. |
| `notes` | Дополнительная текстовая заметка раннера. | Нужна для редких или семейно-специфичных случаев, которые не покрыты явными колонками. |

## 4. `events.csv`

Назначение:

- perturbation trace;
- blackout trace;
- wrong-action injections;
- recovery event trace;
- safety event trace.

### Колонки `events.csv`

| Колонка | Что означает | Зачем нужна |
| --- | --- | --- |
| `run_id` | Идентификатор benchmark-run. | Нужен для связи события с конкретным запуском. |
| `episode_id` | Идентификатор эпизода. | Нужен для связи события с episode-level outcome. |
| `event_id` | Идентификатор события. | Нужен для уникальной трассировки конкретного perturbation или reaction event. |
| `step_idx` | Шаг, на котором произошло событие. | Нужен для выравнивания event timeline со `steps.csv`. |
| `subtask_idx` | Подзадача, на которой произошло событие. | Нужен для subtask-level safety и recovery analysis. |
| `timestamp_utc` | Временная отметка события. | Нужна для аудита и реконструкции очередности событий. |
| `sequence_id` | Идентификатор official sequence. | Нужен для группировки событий по одному сценарию. |
| `condition_id` | Идентификатор активного condition contract. | Нужен для связи события с конкретной конфигурацией запуска. |
| `scenario_profile_id` | Профиль, в рамках которого произошло событие. | Нужен, чтобы отделять noise/dropout/safety/recovery events друг от друга. |
| `action_level_id` | Активный уровень действия. | Нужен для сравнения, как разные action levels переживают одинаковые perturbations. |
| `pair_id` | Идентификатор baseline/stress пары. | Нужен для delta analysis между event traces stress-run и paired baseline. |
| `event_type` | Тип события. | Нужен для статистики blackout, noise, wrong-action injection, rollback, replan и других event classes. |
| `event_source` | Источник события. | Нужен, чтобы различать benchmark-injected events, runtime-validator events и model-originated events. |
| `event_payload_json` | Структурированные детали события. | Нужен для хранения параметров noise, масок сенсоров, injected action и других event-specific данных. |
| `noise_applied_flag` | Содержало ли событие noise injection. | Нужен для быстрого фильтра noisy events без полного parsing payload. |
| `resolved_flag` | Было ли событие разрешено или закрыто. | Нужен для оценки recovery success и lingering failures. |
| `resolve_step_idx` | На каком шаге событие было разрешено. | Нужен для расчета recovery latency. |
| `resolve_latency_steps` | Сколько шагов потребовалось на разрешение события. | Нужен для recovery/replan/rollback metrics. |
| `reaction_type` | Какая реакция последовала после события. | Нужна для анализа continue/stop/replan/rollback/fallback behavior. |

## 5. Логи по аналитическому назначению

## A. `success / chain success`

Использовать:

- `episodes.csv`
- `steps.csv`

Основные поля:

- `subtasks_solved`
- `subtasks_total`
- `oracle_success_current_step`
- `subtask_status`
- `subsequence_success_len`
- `action_level_id`
- `scenario_profile_id`

Что считать:

- overall success;
- solved subtasks;
- chain success / subsequence success;
- degradation relative to `ideal`.

## B. `efficiency / timing`

Использовать:

- `episodes.csv`
- `steps.csv`

Основные поля:

- `decision_time_total_sec`
- `predict_time_total_sec`
- `executor_time_total_sec`
- `env_step_time_total_sec`
- `oracle_check_time_total_sec`
- `episode_wallclock_total_sec`
- `decision_time_ms`
- `predict_time_ms`
- `executor_time_ms`
- `env_step_time_ms`
- `oracle_check_time_ms`
- `wallclock_step_time_ms`

Что считать:

- cost of model inference;
- cost of execution;
- cost of oracle checking;
- total wall-clock;
- efficiency delta across `L1-L4`;
- timing delta between `ideal` and stress.

## C. `safety`

Использовать:

- `steps.csv`
- `events.csv`

Основные поля:

- `scenario_profile_id`
- `safety_flag`
- `safety_reaction`
- `action_valid_flag`
- `model_error_code`
- `event_type`
- `reaction_type`
- `termination_reason`

Что считать:

- unsafe continuation rate;
- safe stop / abstain rate;
- fallback frequency;
- invalid action under blackout;
- difference between `safe_abstain` and `best_effort`.

## D. `recovery / rollback / replan`

Использовать:

- `steps.csv`
- `events.csv`
- `pair_id`

Основные поля:

- `recovery_phase`
- `rollback_attempted`
- `rollback_success`
- `replan_event`
- `resolve_latency_steps`
- `subsequence_success_len`
- `event_type`
- `reaction_type`

Что считать:

- recovery latency;
- rollback success rate;
- replan frequency;
- amount of subsequence salvaged after intervention;
- delta vs paired `ideal`.

## E. `robustness to sensor loss/noise`

Использовать:

- `steps.csv`
- `events.csv`
- `pair_id`

Основные поля:

- `sensor_mask_before`
- `sensor_mask_after`
- `noise_applied_flag`
- `scenario_profile_id`
- `subtasks_solved`
- `action_valid_flag`
- `predict_time_ms`
- `executor_time_ms`
- `env_step_time_ms`
- `wallclock_step_time_ms`

Что считать:

- degradation under dropout;
- degradation under noise;
- invalid-action shift under corrupted sensing;
- timing shift under stress.

## F. `single-sensor comparison`

Использовать:

- `episodes.csv`
- `steps.csv`
- `events.csv`

Основные поля:

- `observation_profile_id`
- `scenario_profile_id`
- `action_level_id`
- `subtasks_total`
- `subtasks_solved`
- `subsequence_success_len`
- `predict_time_ms`
- `executor_time_ms`
- `safety_flag`
- `safety_reaction`

Что считать:

- gain/loss from adding sensors;
- delta from adding or removing exactly one sensor channel;
- robustness delta for one channel under the same stress profile;
- whether extra sensors help more on `L1` or on `L4`.

## G. `subsequence behavior`

Использовать:

- `steps.csv`
- `episodes.csv`

Основные поля:

- `subtask_idx`
- `oracle_success_current_step`
- `subtask_status`
- `subsequence_success_len`
- `steps_total`

Что считать:

- progress depth into the 5-subtask sequence;
- partial success;
- how much of the target sequence was preserved after perturbation.

## 6. Дополнительные требования для `L1`

Так как `L1` работает на symbolic level, для него дополнительно нужны поля:

- `selected_symbolic_subtask`
- `executor_id`
- `executor_version`
- `executor_invocation_id`
- `executor_status`
- `executor_low_level_steps`

### Расшифровка дополнительных `L1` полей

| Поле | Что означает | Зачем нужно |
| --- | --- | --- |
| `selected_symbolic_subtask` | Символическая подзадача, выбранная моделью на `L1`. | Нужна для аудита symbolic planning decisions. |
| `executor_id` | Идентификатор subtask executor-а. | Нужен, чтобы фиксировать, какой именно исполнитель стоял за symbolic action. |
| `executor_version` | Версия executor-а. | Нужна для воспроизводимости и fair comparison между `L1` runs. |
| `executor_invocation_id` | Идентификатор конкретного вызова executor-а. | Нужен для join-а symbolic action и его низкоуровневого исполнения. |
| `executor_status` | Статус выполнения executor-а. | Нужен для отделения ошибки symbolic plan от ошибки исполнения executor-а. |
| `executor_low_level_steps` | Сколько низкоуровневых шагов заняло исполнение symbolic action. | Нужен для fairness против `L2-L4` и оценки реальной стоимости `L1` решения. |

Без этих полей нельзя честно сравнивать:

- число high-level decisions;
- стоимость исполнения выбранной symbolic action;
- fairness против `L2-L4`.

## 7. Что должно быть источником каких выводов

- только `episodes.csv`
  - нельзя использовать для recovery trace
- только `steps.csv`
  - нельзя использовать для perturbation provenance без `events.csv`
- `events.csv` без `pair_id`
  - нельзя использовать для degradation delta

Поэтому правило такое:

- итоговый успех — из `episodes.csv`
- per-step behavior — из `steps.csv`
- perturbation provenance — из `events.csv`
- baseline/stress deltas — через `pair_id`
