# 06. Метрики и использование логов

Этот документ фиксирует не shortlist KPI, а семейства аналитических срезов и то, из каких логов они считаются.

## Общий принцип

Метрики делятся по назначению:

- core success
- subsequence success
- timing / efficiency
- safety
- recovery / rollback
- robustness under dropout / noise
- single-sensor comparison
- action-level degradation

Каждая группа имеет:

- цель;
- required log fields;
- единицу агрегации;
- тип сравнения.

Сырая семантика колонок описана в [05_logging_schema.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docs/05_logging_schema.md).  
Здесь тот же набор полей объясняется с точки зрения **зачем он нужен именно для конкретной метрики**.

## 1. Core task success

## Цель

Понять, насколько модель достигает target scenario outcome.

## Источники

- `episodes.csv`
- `steps.csv`

## Required fields

- `episode_id`
- `sequence_id`
- `subtasks_total`
- `subtasks_solved`
- `status`
- `terminate_reason`
- `oracle_success_current_step`

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `episode_id` | Идентификатор оцениваемого эпизода. | Нужен как базовая единица episode-level success. |
| `sequence_id` | Какой именно official scenario оценивался. | Нужен, чтобы сравнивать success только на одинаковых sequence. |
| `subtasks_total` | Полный размер целевой цепочки. | Нужен как знаменатель для normalized success. |
| `subtasks_solved` | Сколько подзадач реально завершено. | Нужен как числитель для core success и partial completion. |
| `status` | Финальный статус эпизода. | Нужен, чтобы отделять full success от partial success, fail, timeout и abort. |
| `terminate_reason` | Почему эпизод закончился. | Нужен для объяснения потери success и для раздельной статистики fail modes. |
| `oracle_success_current_step` | На каких шагах oracle фиксировал успех. | Нужен, чтобы проверить, когда именно была достигнута цель, а не только итоговый статус. |

## Единица агрегации

- `episode`
- `action_level_id`
- `scenario_profile_id`

## Тип сравнения

- `within-family`
- `cross-family`
- `ideal vs stress`
- `L1 vs L4`

## 2. Subtask-chain / subsequence success

## Цель

Понять, насколько глубоко модель проходит в official sequence из 5 подзадач.

## Источники

- `steps.csv`
- `episodes.csv`

## Required fields

- `subtask_idx`
- `subsequence_success_len`
- `subtask_status`
- `subtasks_solved`

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `subtask_idx` | Индекс текущей подзадачи в sequence. | Нужен, чтобы понимать глубину прохождения по цепочке. |
| `subsequence_success_len` | Длина правильно решенного префикса sequence. | Нужна как основная величина chain success. |
| `subtask_status` | Состояние текущей подзадачи. | Нужен для отличия успешно завершенной подзадачи от зависшей или проваленной. |
| `subtasks_solved` | Сколько подзадач завершено к концу эпизода. | Нужен как итоговая episode-level проверка partial progress. |

## Единица агрегации

- `subtask`
- `episode`

## Тип сравнения

- `ideal vs stress`
- `L1 vs L4`
- `within-family`

## 3. Timing / efficiency

## Цель

Разделить:

- время предикта модели;
- время исполнения действия;
- время симуляционного шага;
- время oracle-проверки;
- полный wall-clock.

## Источники

- `episodes.csv`
- `steps.csv`

## Required fields

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

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `decision_time_total_sec` | Суммарный decision overhead на эпизод. | Нужен для итоговой стоимости принятия решений. |
| `predict_time_total_sec` | Суммарный inference time модели. | Нужен для сравнения вычислительной цены разных моделей. |
| `executor_time_total_sec` | Суммарное время executor-а. | Нужен для отделения стоимости исполнения от стоимости предикта. |
| `env_step_time_total_sec` | Суммарное время шагов среды. | Нужен, чтобы не списывать simulator cost на модель. |
| `oracle_check_time_total_sec` | Суммарная стоимость oracle checking. | Нужен для корректной декомпозиции wall-clock. |
| `episode_wallclock_total_sec` | Полное время эпизода. | Нужен для end-to-end efficiency. |
| `decision_time_ms` | Decision overhead на одном шаге. | Нужен для поиска spikes и анализа step-level latency. |
| `predict_time_ms` | Model inference на одном шаге. | Нужен для latency distribution и percentile metrics. |
| `executor_time_ms` | Исполнение/канонизация на одном шаге. | Нужен для анализа real execution cost. |
| `env_step_time_ms` | Стоимость одного simulator step. | Нужен для анализа throughput среды. |
| `oracle_check_time_ms` | Стоимость oracle check на одном шаге. | Нужен для per-step decomposition latency. |
| `wallclock_step_time_ms` | Полный wall-clock одного шага. | Нужен для сравнения perceived step cost между методами. |

## Единица агрегации

- `step`
- `episode`
- `pair`

## Тип сравнения

- `ideal vs stress`
- `L1 vs L4`
- `within-family`
- `cross-family`

## 4. Safety metrics

## Цель

Понять, что делает модель при blackout или потере достоверных наблюдений:

- останавливается;
- продолжает;
- переходит в fallback;
- нарушает safety expectations.

## Источники

- `steps.csv`
- `events.csv`

## Required fields

- `scenario_profile_id`
- `safety_flag`
- `safety_reaction`
- `action_valid_flag`
- `model_error_code`
- `event_type`
- `reaction_type`
- `termination_reason`

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `scenario_profile_id` | В каком safety profile измеряется поведение. | Нужен для сравнения `safe_abstain` и `best_effort`. |
| `safety_flag` | Находился ли шаг в safety-critical condition. | Нужен для отбора только релевантных шагов для safety metrics. |
| `safety_reaction` | Что именно сделала модель или раннер в safety context. | Нужен для подсчета stop, abstain, fallback и unsafe continuation. |
| `action_valid_flag` | Было ли действие валидным с точки зрения контракта. | Нужен для отделения unsafe behavior от format-level invalid output. |
| `model_error_code` | Была ли ошибка модели. | Нужен, чтобы понимать, вызван ли safety fail invalid response'ом. |
| `event_type` | Какое внешнее событие вызвало safety context. | Нужен для различения blackout, forced constraint и других safety triggers. |
| `reaction_type` | Какая реакция последовала после события. | Нужен для event-level safety analysis. |
| `termination_reason` | Чем закончился эпизод или шаг. | Нужен для статистики safety stop, safety abort и unsafe continuation to failure. |

## Единица агрегации

- `step`
- `episode`

## Тип сравнения

- `safe_abstain vs best_effort`
- `within-family`
- `cross-family`

## 5. Recovery / rollback metrics

## Цель

Понять, как модель восстанавливается после forced deviation или ошибочного действия.

## Источники

- `steps.csv`
- `events.csv`
- `pair_id`

## Required fields

- `pair_id`
- `event_type`
- `recovery_phase`
- `rollback_attempted`
- `rollback_success`
- `replan_event`
- `resolve_latency_steps`
- `subsequence_success_len`

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `pair_id` | Связка stress-run с paired `ideal`. | Нужен для расчета recovery delta относительно baseline. |
| `event_type` | Какое отклонение произошло. | Нужен, чтобы различать recovery after wrong-action, dropout-induced drift и другие случаи. |
| `recovery_phase` | На какой стадии recovery находилась модель. | Нужен для разбиения recovery на detect, replan, rollback, resume. |
| `rollback_attempted` | Пыталась ли система откатиться. | Нужен для rollback-attempt rate. |
| `rollback_success` | Удался ли rollback. | Нужен для rollback success rate. |
| `replan_event` | Была ли инициирована перепланировка. | Нужен для replan frequency и recovery policy analysis. |
| `resolve_latency_steps` | За сколько шагов удалось закрыть событие. | Нужен как основная мера recovery latency. |
| `subsequence_success_len` | Сколько целевой последовательности удалось сохранить. | Нужен для оценки того, насколько recovery salvages the original goal chain. |

## Единица агрегации

- `step`
- `episode`
- `pair`

## Тип сравнения

- `ideal vs recovery_wrong_action`
- `within-family`
- `cross-family`

## 6. Robustness under dropout/noise

## Цель

Измерить устойчивость к отсутствующим и искаженным сенсорам.

## Источники

- `steps.csv`
- `events.csv`
- `pair_id`

## Required fields

- `pair_id`
- `scenario_profile_id`
- `sensor_mask_before`
- `sensor_mask_after`
- `noise_applied_flag`
- `subtasks_solved`
- `action_valid_flag`
- `predict_time_ms`
- `executor_time_ms`
- `env_step_time_ms`
- `wallclock_step_time_ms`

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `pair_id` | Связка stress-run с paired baseline. | Нужен для честного degradation delta. |
| `scenario_profile_id` | Какой именно stress profile активен: dropout или noise. | Нужен для разделения missing-modality robustness и corrupted-perception robustness. |
| `sensor_mask_before` | Какие сенсоры были доступны до шага. | Нужен для определения исходного состояния восприятия. |
| `sensor_mask_after` | Какие сенсоры были доступны после события/шага. | Нужен для фиксации фактического dropout или blackout transition. |
| `noise_applied_flag` | Был ли реально применен noise. | Нужен для фильтрации шагов, где corruption действительно присутствовал. |
| `subtasks_solved` | Сколько подзадач удалось решить под stress. | Нужен как outcome robustness. |
| `action_valid_flag` | Не сломался ли action output под corrupt observations. | Нужен для анализа robustness not only in success, but also in output validity. |
| `predict_time_ms` | Изменилось ли время предикта под stress. | Нужен для latency robustness. |
| `executor_time_ms` | Изменилось ли время исполнения ответа. | Нужен для проверки, не ведет ли noisy output к более тяжелому execution path. |
| `env_step_time_ms` | Изменилось ли время шага среды. | Нужен для отделения simulator-side effects from model-side effects. |
| `wallclock_step_time_ms` | Полное время шага под stress. | Нужен для итоговой step-level robustness по latency. |

## Единица агрегации

- `step`
- `episode`
- `pair`

## Тип сравнения

- `ideal vs sensor_dropout`
- `ideal vs sensor_noise`
- `L1 vs L4`

## 7. Single-sensor comparison metrics

## Цель

Измерить, как меняются:

- success;
- partial success;
- timing;
- safety;
- robustness;

при фиксированных:

- `sequence_id`
- `action_level_id`
- `scenario_profile_id`

и двух `observation_profile_id`, которые различаются ровно одним каналом наблюдения.

## Источники

- `episodes.csv`
- `steps.csv`
- `events.csv`

## Required fields

- `observation_profile_id`
- `action_level_id`
- `scenario_profile_id`
- `subtasks_total`
- `subtasks_solved`
- `subsequence_success_len`
- `predict_time_ms`
- `executor_time_ms`
- `safety_flag`
- `safety_reaction`

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `observation_profile_id` | Какой именно sensor contract сравнивается. | Нужен, чтобы строить однофакторные sensor comparisons. |
| `action_level_id` | На каком уровне действия проводится sensor comparison. | Нужен, чтобы не смешивать sensor effect с изменением action interface. |
| `scenario_profile_id` | В каких условиях измеряется sensor effect. | Нужен, чтобы отдельно смотреть sensor gain в `ideal`, `sensor_noise` и других профилях. |
| `subtasks_total` | Полный размер целевой последовательности. | Нужен для normalized comparison между profiles с разными outcome lengths. |
| `subtasks_solved` | Сколько подзадач решено при данном observation profile. | Нужен как главный outcome sensor gain/loss. |
| `subsequence_success_len` | Какой префикс sequence удалось пройти. | Нужен, чтобы видеть, помогает ли датчик в глубине long-horizon chain, а не только в final success. |
| `predict_time_ms` | Как датчик влияет на latency предикта. | Нужен для оценки цены дополнительного сенсора. |
| `executor_time_ms` | Как датчик влияет на стоимость исполнения. | Нужен, чтобы видеть, ведет ли richer sensing к более сложному control behavior. |
| `safety_flag` | Возникает ли safety-critical context при данном sensor profile. | Нужен для анализа безопасности sensor configurations. |
| `safety_reaction` | Как меняется safety behavior при добавлении одного датчика. | Нужен, чтобы оценивать, помогает ли конкретный sensor safe stop/fallback behavior. |

## Единица агрегации

- `episode`
- `step`

## Тип сравнения

- `rgb_static` vs `rgb_static + rgb_gripper`
- `rgb_static + rgb_gripper` vs `rgb_static + rgb_gripper + depth_gripper`
- `rgb_static + rgb_gripper + depth_gripper` vs `rgb_static + rgb_gripper + depth_static + depth_gripper`
- `rgb_static` vs `rgb_static + rgb_tactile`

## 8. Action-level degradation metrics

## Цель

Измерить, как меняются:

- success;
- timing;
- safety;
- recovery;

при переходе от `L1` к `L4` на одном и том же scenario bundle.

## Источники

- `episodes.csv`
- `steps.csv`
- `events.csv`
- `pair_id`

## Required fields

- `action_level_id`
- `scenario_profile_id`
- `pair_id`
- `subtasks_total`
- `subtasks_solved`
- `predict_time_total_sec`
- `executor_time_total_sec`
- `safety_flag`
- `rollback_success`
- `replan_event`

### Зачем нужны эти поля

| Поле | Что означает в этой метрике | Зачем нужно |
| --- | --- | --- |
| `action_level_id` | Какой action level измеряется. | Нужен как главная ось comparison между `L1-L4`. |
| `scenario_profile_id` | В каких условиях измеряется degradation. | Нужен, чтобы сравнивать action levels отдельно в `ideal`, safety, noise и recovery режимах. |
| `pair_id` | Связка baseline/stress runs. | Нужен, если degradation по action level измеряется внутри stress-profile относительно paired baseline. |
| `subtasks_total` | Размер целевой последовательности. | Нужен для нормализации success across action levels. |
| `subtasks_solved` | Сколько sequence удалось выполнить на данном action level. | Нужен как основной quality outcome. |
| `predict_time_total_sec` | Полная стоимость inference на данном action level. | Нужен для измерения latency degradation or improvement across levels. |
| `executor_time_total_sec` | Полная стоимость исполнения решений на данном action level. | Нужен, чтобы видеть реальную цену более низкоуровневого control. |
| `safety_flag` | Возникают ли safety-critical conditions на данном level. | Нужен для сравнения safety burden across levels. |
| `rollback_success` | Удается ли восстановление на данном level. | Нужен для сравнения recovery capability на разных уровнях действия. |
| `replan_event` | Насколько часто требуется перепланирование. | Нужен для сравнения stability и control difficulty across `L1-L4`. |

## Единица агрегации

- `episode`
- `pair`

## Тип сравнения

- `L1 vs L2`
- `L2 vs L3`
- `L3 vs L4`
- `within-family`
- `cross-family`

## 9. Короткое правило использования логов

- `episodes.csv`
  - итог и агрегаты
- `steps.csv`
  - per-step behavior
- `events.csv`
  - perturbation provenance
- `pair_id`
  - baseline/stress delta analysis

Если в аналитике нет явного указания, из какого источника берется вывод, такая аналитика считается недостаточно трассируемой.
