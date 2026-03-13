# 04. Контракты входа/выхода (ModelAdapter)

Этот документ фиксирует логический benchmark interface для CALVIN с учетом двух осей:

- `action_level_id`
- `scenario_profile_id`

Главная цель этого файла: не просто перечислить поля, а объяснить, что означает каждое поле и зачем оно нужно в раннере, логировании и аналитике.

## 1. Интерфейс `ModelAdapter`

- `reset(episode_context) -> None`
- `predict(step_payload) -> model_response`
- `close() -> None`

## 2. Базовый принцип

Для конкретного benchmark-run фиксируются:

- один `action_level_id`;
- один `scenario_profile_id`;
- один `observation_profile_id`;
- один `condition_id`.

Модель не должна работать в неявно смешанном режиме.

`observation_profile_id` при этом является основным рычагом для режима сравнения по отдельным датчикам.

Для корректного сравнения по датчикам два `observation_profile_id` должны различаться ровно одним каналом наблюдения.

## 3. `episode_context`

`episode_context` передается в `reset(...)` и задает полный контракт конкретного эпизода до первого шага модели.

Обязательные поля:

- `run_id`
- `track`
- `episode_id`
- `sequence_id`
- `initial_state_id`
- `condition_id`
- `selection_seed`
- `action_level_id`
- `scenario_profile_id`
- `observation_profile_id`
- `pair_id`
- `baseline_episode_id`
- `subtasks_total`
- `max_subtask_steps`
- `max_episode_steps`
- `max_time_sec`
- `termination_policy_id`
- `failure_policy_id`
- `safety_mode`
- `perturbation_schedule_id`
- `manifest_hash`

### Расшифровка `episode_context`

| Поле | Что означает | Зачем нужно |
| --- | --- | --- |
| `run_id` | Идентификатор всего benchmark-run. | Нужен для объединения всех эпизодов, condition cells и итоговых агрегатов в один запуск. |
| `track` | Идентификатор логического трека benchmark'а. | Нужен, чтобы не смешивать разные режимы исполнения, например native-control и alignment-layer experiments. |
| `episode_id` | Уникальный идентификатор конкретного эпизода. | Нужен для связи `episode_context`, `steps.csv`, `events.csv` и итоговых метрик. |
| `sequence_id` | Идентификатор official CALVIN long-horizon sequence. | Нужен для воспроизводимости и для честного сравнения моделей на одной и той же последовательности из 5 подзадач. |
| `initial_state_id` | Идентификатор стартового состояния среды. | Нужен, чтобы сравнивать модели на одном и том же начальном мире, а не на разных раскладках объектов. |
| `condition_id` | Идентификатор полного условия запуска. | Нужен для ссылки на конкретную комбинацию action level, sensor profile, scenario profile, budget и perturbation schedule. |
| `selection_seed` | Seed, использованный при детерминированном выборе сценария. | Нужен, чтобы потом можно было точно повторить набор сценариев и проверить воспроизводимость sampling. |
| `action_level_id` | Один из `L1`, `L2`, `L3`, `L4`. | Нужен, чтобы модель и раннер одинаково понимали, в каком представлении приходит и исполняется действие. |
| `scenario_profile_id` | Профиль условий исполнения, например `ideal` или `sensor_noise`. | Нужен для понимания, является ли эпизод baseline, stress, safety или recovery-сценарием. |
| `observation_profile_id` | Идентификатор observation contract. | Нужен, чтобы фиксировать, какие именно датчики и state-каналы доступны модели на этом эпизоде. |
| `pair_id` | Идентификатор пары baseline/stress. | Нужен для вычисления degradation delta и recovery delta между парным `ideal` и stress-запуском. |
| `baseline_episode_id` | Явная ссылка на paired `ideal` episode. | Нужен для быстрых join-операций и для проверки, что stress-run действительно имеет корректный baseline. |
| `subtasks_total` | Число целевых подзадач в sequence. | Нужно как знаменатель для success, chain success и partial progress. В official CALVIN здесь обычно `5`. |
| `max_subtask_steps` | Максимальный бюджет control-шагов на одну подзадачу. | Нужен, чтобы одинаково ограничивать все модели по времени исполнения подзадачи. |
| `max_episode_steps` | Максимальный бюджет шагов на весь эпизод. | Нужен для сравнения end-to-end эффективности и для предотвращения бесконечных rollout'ов. |
| `max_time_sec` | Максимальный wall-clock budget на эпизод. | Нужен для сравнения latency-sensitive методов и для остановки слишком долгих прогонов. |
| `termination_policy_id` | Идентификатор правила завершения эпизода. | Нужен, чтобы явно фиксировать, заканчивается ли эпизод при первом fail, при timeout или по другой политике. |
| `failure_policy_id` | Идентификатор правила обработки ошибки. | Нужен, чтобы понимать, допускается ли recovery, rollback, safe stop или немедленный abort. |
| `safety_mode` | Один из `safe_abstain`, `best_effort`, `none`. | Нужен, чтобы зафиксировать ожидания к поведению модели в blackout и других safety-сценариях. |
| `perturbation_schedule_id` | Идентификатор расписания шумов, dropout и injected events. | Нужен, чтобы детерминированно повторять stress-сценарий шаг в шаг. |
| `manifest_hash` | Хэш manifest-а сценария и условий запуска. | Нужен для аудита, чтобы подтвердить, что результаты относятся именно к этой версии сценария и условий. |

## 4. `step_payload`

`step_payload` передается в `predict(...)` на каждом шаге и описывает то, что модель видит и в каких ограничениях принимает решение прямо сейчас.

Обязательные поля:

- `run_id`
- `episode_id`
- `step_idx`
- `subtask_idx`
- `current_instruction_text`
- `oracle_target_subtask`
- `action_level_id`
- `scenario_profile_id`
- `observation_profile_id`
- `active_modalities`
- `active_sensor_mask`
- `dropped_modalities`
- `noise_profile`
- `safety_contract`
- `event_context`
- `observation_bundle`
- `history_actions`
- `history_events`
- `budget_left_subtask_steps`
- `budget_left_episode_steps`
- `budget_left_time_sec`
- `active_constraints`

### Расшифровка `step_payload`

| Поле | Что означает | Зачем нужно |
| --- | --- | --- |
| `run_id` | Идентификатор текущего benchmark-run. | Нужен для join с run-level логами и для трассировки шага до общего запуска. |
| `episode_id` | Идентификатор эпизода, внутри которого находится шаг. | Нужен для связи конкретного шага с его `episode_context` и итоговым outcome. |
| `step_idx` | Номер low-level шага внутри эпизода. | Нужен для таймлайна, step-level метрик и восстановления порядка событий. |
| `subtask_idx` | Номер текущей подзадачи внутри official sequence. | Нужен для анализа progress по цепочке из 5 подзадач и для subtask-level метрик. |
| `current_instruction_text` | Текущий текст инструкции на естественном языке. | Нужен как семантическая цель текущей подзадачи и как вход для language-conditioned моделей. |
| `oracle_target_subtask` | Каноническая метка целевой подзадачи. | Нужна для task oracle, расчета success и сравнения с symbolic output в `L1`. |
| `action_level_id` | Текущий уровень действия `L1-L4`. | Нужен, чтобы модель знала формат ответа, а раннер знал как его валидировать и исполнять. |
| `scenario_profile_id` | Текущий baseline/stress/safety profile. | Нужен, чтобы модель и раннер понимали действующие ограничения и expected behavior. |
| `observation_profile_id` | Текущий набор доступных наблюдений. | Нужен для sensor-comparison и для проверки, что модель не использует неразрешенные каналы. |
| `active_modalities` | Человекочитаемый список активных модальностей. | Нужен для логов и отладки, чтобы было понятно, какие датчики доступны на этом шаге. |
| `active_sensor_mask` | Машиночитаемая маска доступных каналов. | Нужна для строгого сравнения сенсоров, dropout-анализа и автоматической валидации observation contract. |
| `dropped_modalities` | Список модальностей, принудительно отключенных на шаге. | Нужен для отделения исходно недоступных каналов от временно отключенных perturbation-сценарием. |
| `noise_profile` | Активный профиль шума. | Нужен, чтобы знать, какой corruption применяется к наблюдениям и сравнивать runs под одинаковым noise schedule. |
| `safety_contract` | Активные safety-ограничения на этом шаге. | Нужен, чтобы модель могла выбрать safe stop, fallback или abstain в соответствии с протоколом. |
| `event_context` | Контекст внешних вмешательств и событий. | Нужен для recovery-сценариев, blackout и других injected events, влияющих на решение модели. |
| `observation_bundle` | Сырые наблюдения, подаваемые модели. | Нужен как основной фактический вход модели: кадры, depth, state и другие разрешенные каналы. |
| `history_actions` | История предыдущих действий или символических решений. | Нужна моделям с памятью и для анализа autoregressive / closed-loop поведения. |
| `history_events` | История недавних perturbation и runtime events. | Нужна для диагностики recovery и для моделей, которые должны учитывать недавний blackout или rollback. |
| `budget_left_subtask_steps` | Оставшийся шаговый бюджет текущей подзадачи. | Нужен для fairness и для анализа, меняет ли модель поведение, когда бюджет почти исчерпан. |
| `budget_left_episode_steps` | Оставшийся шаговый бюджет эпизода. | Нужен для end-to-end планирования и для сравнения эффективности между моделями. |
| `budget_left_time_sec` | Оставшийся wall-clock budget. | Нужен, чтобы логировать и ограничивать latency-sensitive методы. |
| `active_constraints` | Дополнительные runtime-ограничения. | Нужны для явной фиксации правил вроде stop-on-blackout, forbidden action ranges или forced safe fallback. |

## 5. `model_response`

`model_response` возвращается из `predict(...)` и отражает не только действие модели, но и ее состояние, ошибки и намерения.

Обязательные поля:

- `status` (`ok|unsupported|error`)
- `action_raw`
- `action_exec`
- `action_level_id`
- `model_latency_sec`
- `error_code`
- `error_message`
- `safety_intent`
- `fallback_mode`
- `replan_requested`
- `rollback_requested`
- `tokens_in`
- `tokens_out`
- `meta`

### Расшифровка `model_response`

| Поле | Что означает | Зачем нужно |
| --- | --- | --- |
| `status` | Общий статус ответа модели. | Нужен, чтобы отделять успешный ответ от unsupported interface и runtime failure. |
| `action_raw` | Действие в том виде, как его вернула модель. | Нужно для аудита и для последующего анализа исходного решения без нормализации. |
| `action_exec` | Канонизированная форма того же действия в рамках того же action level. | Нужна для исполнения и для того, чтобы раннер работал с единым форматом без скрытой смены представления. |
| `action_level_id` | Уровень действия, в котором дан ответ. | Нужен для валидации, что модель не вернула действие другого уровня. |
| `model_latency_sec` | Время формирования ответа моделью. | Нужно для timing-метрик и отделения model latency от времени среды и executor-а. |
| `error_code` | Стандартизованный код ошибки. | Нужен для агрегирования failure cases и статистики unsupported/invalid responses. |
| `error_message` | Текстовое пояснение ошибки. | Нужно для отладки и ручного аудита отдельных сбоев. |
| `safety_intent` | Явное намерение модели в safety-ситуации. | Нужен для разделения safe stop, continue, abstain и fallback even when final action looks similar. |
| `fallback_mode` | Какой fallback режим выбрала модель. | Нужен для анализа защитного поведения и для сравнения разных семейств моделей. |
| `replan_requested` | Запрашивает ли модель перепланирование. | Нужно для recovery/replan метрик и для анализа, понимает ли модель, что текущий план испорчен. |
| `rollback_requested` | Запрашивает ли модель откат. | Нужно для оценки способности модели инициировать rollback после wrong action или drift. |
| `tokens_in` | Число входных токенов. | Нужно для LLM/VLA-метрик стоимости и для budget-aware сравнения. Для non-token моделей может быть `null`. |
| `tokens_out` | Число выходных токенов. | Нужно для анализа генеративной стоимости, latency и сравнения между LLM-based planners. |
| `meta` | Дополнительные структурированные данные. | Нужны для семейно-специфичных полей без ломки общего контракта. |

## 6. Семантика `action_raw` и `action_exec`

- `action_raw` — как действие вернула модель;
- `action_exec` — канонизированная форма этого же действия внутри того же action level.

Канонизация допустима.  
Смена action level недопустима.

## 7. Контракт по `L1-L4`

## `L1 = textual_subtasks`

### Что получает модель

- observation bundle;
- текущую инструкцию;
- историю;
- допустимый словарь canonical subtask labels.

### Что возвращает модель

Один canonical symbolic subtask id.

### Execution semantics

Исполняется через явный benchmark-side `subtask executor`.

Этот executor должен быть:

- versioned;
- логируемым;
- отделенным от native low-level CALVIN motor interface.

## `L2 = absolute_cartesian_tcp`

### Что возвращает модель

7D absolute action:

- `x, y, z`
- `euler_x, euler_y, euler_z`
- `gripper`

### Grounding

- native dataset key: `actions`

## `L3 = relative_cartesian_7d`

### Что возвращает модель

7D relative action:

- `dx, dy, dz`
- `deuler_x, deuler_y, deuler_z`
- `gripper`

### Grounding

- native dataset key: `rel_actions`

## `L4 = joint_space`

### Что возвращает модель

8D joint action:

- `joint_1 ... joint_7`
- `gripper`

### Grounding

Опирается на:

- локально заявленный joint action в CALVIN README;
- локальный пример `joint_rel`.

## 8. Что недопустимо

- молча исполнять `L1` symbolic action как `L2/L3/L4` без явного executor contract;
- принимать `L4` joint action и исполнять его как cartesian action без явного level contract;
- менять `scenario_profile_id` на лету;
- скрывать факт blackout / dropout / wrong-action intervention от logs.

## 9. Коды ошибок

| Код | Что означает | Где используется |
| --- | --- | --- |
| `unsupported_model_interface` | Модель не поддерживает требуемый интерфейс или action level. | Нужен для честной фиксации unsupported cells в benchmark matrix. |
| `empty_action` | Модель вернула пустой ответ. | Нужен для статистики fail-fast и диагностики parsing/LLM errors. |
| `invalid_action_format` | Формат ответа не соответствует контракту action level. | Нужен для action-validation metrics и для отделения reasoning fail от execution fail. |
| `action_level_mismatch` | Ответ модели относится к другому action level. | Нужен, чтобы исключить скрытое смешение `L1-L4`. |
| `scenario_profile_mismatch` | Ответ или логика модели нарушают активный scenario profile. | Нужен для контроля safety и stress-contract compliance. |
| `timeout_model` | Модель превысила разрешенное время ответа. | Нужен для timing budgets и latency-aware comparison. |
| `runtime_exception` | Во время вызова модели или adapter-а возникло исключение. | Нужен для reliability metrics и ручной диагностики. |
| `constraint_violation` | Модель предложила действие, запрещенное текущими ограничениями. | Нужен для safety, validity и contract compliance analysis. |
