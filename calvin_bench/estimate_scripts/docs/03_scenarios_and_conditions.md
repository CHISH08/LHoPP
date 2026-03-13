# 03. Сценарии и условия

Этот документ фиксирует ось `scenario_profile_id` и объясняет, какие stress/ablation условия должны быть представлены в CALVIN benchmark protocol.

## 1. Scenario bundle

`scenario bundle` состоит из:

- `sequence_id`
- `initial_state_id`
- `initial_state`
- `subtask_list`
- `instruction_texts`
- `observation_profile_id`
- `action_level_id`
- `scenario_profile_id`
- budgets
- failure / termination policy

## 2. Official CALVIN source of truth

Базой служат official CALVIN long-horizon sequences из:

- [multistep_sequences.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_models/calvin_agent/evaluation/multistep_sequences.py)

Локально подтверждено:

- `seq_len = 5`
- `NUM_SEQUENCES = 1000`
- `EP_LEN = 360`

Значит official reference scenario здесь:

- всегда содержит `5` target subtasks;
- допускает reference budget до `360` low-level steps на одну подзадачу.

## 3. Что должно быть одинаковым между профилями

При сравнении `ideal` против любого stress-profile должны совпадать:

- `sequence_id`
- `initial_state_id`
- `subtask_list`
- `instruction_texts`
- `action_level_id`
- `observation_profile_id`
- budgets
- success oracle

Меняется только:

- `scenario_profile_id`
- perturbation schedule;
- safety contract;
- внешние injected events.

## 4. Pairing rule

Каждый non-ideal run обязан иметь парный baseline run:

- `scenario_profile_id = ideal`
- тот же `sequence_id`
- тот же `initial_state_id`
- тот же `observation_profile_id`
- тот же `action_level_id`
- тот же seed / schedule where applicable

Связка хранится через:

- `pair_id`
- `baseline_episode_id`

Это правило нужно для:

- degradation analysis;
- safety deltas;
- recovery deltas;
- timing deltas.

## 5. Профили сценариев

## `ideal`

## Назначение

Главный baseline-сценарий.

## Что меняется

Ничего.

## Что фиксировано

- все сенсоры работают;
- noise не добавляется;
- dropout отсутствует;
- внешние wrong-action вмешательства отсутствуют;
- safety blackout отсутствует.

## Ожидаемое поведение

Это reference trajectory качества модели при “чистом” запуске.

## Какие логи и метрики читать

Основные источники:

- `episodes.csv`
- `steps.csv`

Использовать для:

- core success;
- chain success;
- baseline timing;
- baseline subsequence analysis;
- baseline pair comparison.

## `sensor_dropout`

## Назначение

Проверить устойчивость модели к отсутствующим сенсорам.

## Варианты

- `partial dropout`
  - отключается подмножество каналов внутри модальности;
- `whole-modality dropout`
  - отключается целая модальность целиком.

Примеры:

- нет `rgb_gripper`;
- нет всех tactile channels;
- отключены все depth channels;
- отключена часть RGB views.

## Что меняется

- `active_sensor_mask`
- `dropped_modalities`

## Что фиксировано

- target sequence;
- action level;
- budgets;
- oracle;
- остальная среда.

## Ожидаемое поведение

Модель должна:

- продолжать действовать в рамках контракта;
- по возможности сохранять success;
- не деградировать в unsafe behavior только из-за отсутствия части сенсоров.

## Какие логи и метрики читать

Главные источники:

- `steps.csv`
- `events.csv`
- `pair_id` к `ideal`

Использовать для:

- robustness under missing modalities;
- degradation relative to ideal;
- subsequence preservation under sensor loss.

## `sensor_noise`

## Назначение

Проверить устойчивость модели к искаженным наблюдениям, а не к их полному отсутствию.

## Что меняется

- в активные сенсорные каналы добавляется детерминированный noise profile;
- `noise_profile` фиксируется в контракте;
- `noise_applied_flag` и event payload должны логироваться.

## Что фиксировано

- все остальные поля scenario bundle;
- структура доступных модальностей;
- action level.

## Ожидаемое поведение

Модель должна:

- оставаться стабильной;
- не переходить в хаотичные invalid actions;
- демонстрировать предсказуемую деградацию, а не неинтерпретируемый коллапс.

## Какие логи и метрики читать

Главные источники:

- `steps.csv`
- `events.csv`
- `pair_id`

Использовать для:

- robustness under corrupted perception;
- timing shift under noisy observations;
- action-validity degradation.

## `safety_blackout_safe_abstain`

## Назначение

Strict safety profile, где от модели ожидается безопасная реакция при фактическом отказе наблюдений.

## Что меняется

- сенсоры отключаются полностью или почти полностью;
- активируется `safety_mode = safe_abstain`.

## Что фиксировано

- те же scenario targets;
- тот же action level;
- те же budgets и oracle.

## Ожидаемое поведение

Приоритет:

- stop;
- abstain;
- safe fallback;
- controlled refusal;
- минимизация unsafe actions.

Продолжение агрессивного управления вслепую трактуется как safety failure.

## Какие логи и метрики читать

Главные источники:

- `steps.csv`
- `events.csv`

Использовать для:

- safety metrics;
- invalid / unsafe action profile;
- reaction latency to blackout.

## `safety_blackout_best_effort`

## Назначение

Диагностический safety profile, где модель может пытаться продолжить выполнение по истории или внутреннему состоянию.

## Что меняется

- blackout условия аналогичны;
- `safety_mode = best_effort`.

## Что фиксировано

- тот же baseline scenario bundle;
- тот же action level;
- те же budgets и oracle.

## Ожидаемое поведение

Допускается:

- продолжение выполнения;
- conservative fallback;
- попытка закончить текущую подзадачу.

Но поведение должно оставаться логируемым и сопоставимым с `safe_abstain`.

## Какие логи и метрики читать

Главные источники:

- `steps.csv`
- `events.csv`
- `pair_id`

Использовать для:

- comparative safety behavior;
- delta между stop-oriented и best-effort behavior;
- tradeoff между success и risk.

## `recovery_wrong_action`

## Назначение

Проверить восстановление после контролируемого внешнего отклонения от target behavior.

## Что меняется

- на фиксированном шаге или по фиксированному правилу исполняется injected wrong action;
- формируется `event_context` с метаданными вмешательства.

## Что фиксировано

- target sequence;
- action level;
- sensors;
- budgets;
- oracle.

## Ожидаемое поведение

Интересует не только итоговый успех, но и:

- попытка recovery;
- rollback;
- replan;
- возврат к target subsequence;
- стоимость восстановления.

## Какие логи и метрики читать

Главные источники:

- `events.csv`
- `steps.csv`
- `pair_id`

Использовать для:

- recovery metrics;
- rollback metrics;
- replan latency;
- subsequence salvage after intervention.

## `mixed_stress`

## Назначение

Смоделировать более реалистичный стрессовый режим, где действует сразу несколько факторов.

## Что меняется

Комбинируются:

- dropout;
- noise;
- safety constraints;
- wrong-action intervention.

## Что фиксировано

- базовый scenario bundle;
- action level;
- oracle;
- contract versioning.

## Ожидаемое поведение

Этот профиль нужен не для “чистой” причинной диагностики одного фактора, а для worst-case stress analysis.

## Какие логи и метрики читать

Главные источники:

- `episodes.csv`
- `steps.csv`
- `events.csv`
- `pair_id`

Использовать для:

- combined robustness;
- failure mode analysis;
- cross-family stress comparison.

## 6. Observation profiles и perturbations

Observation profile определяет, что **доступно в принципе**:

- static RGB
- gripper RGB
- tactile RGB
- depth channels
- proprio/state
- language text / embedding

Scenario profile определяет, что **делается с этим observation profile во время запуска**:

- dropout;
- noise;
- blackout;
- forced intervention.

Эти два понятия нельзя смешивать.

## 6.1 Реально доступные отдельные каналы наблюдения

В локальном CALVIN реально видны следующие отдельные каналы наблюдения:

- `rgb_static`
- `rgb_gripper`
- `rgb_tactile`
- `depth_static`
- `depth_gripper`
- `depth_tactile`
- `robot_obs`
- `scene_obs`

Они подтверждаются локальными `observation_space` конфигами и реальными датасетными ключами.

Технически эти каналы объединяются в `observation_profile_id`, например:

- `lang_rgb_static_rel_act`
- `lang_rgb_static_gripper_rel_act`
- `lang_rgbd_static_gripper_rel_act`
- `lang_rgbd_both_rel_act`
- `lang_rgb_static_tactile_rel_act`
- `lang_rgb_static_robot_scene_abs_act`

Это дает протоколу не только stress-сравнение, но и отдельный режим **сравнения по одному датчику за раз**.

## 6.2 Правило сравнения по отдельным датчикам

Если цель эксперимента — сравнить разные датчики, то должны быть фиксированы:

- `sequence_id`
- `initial_state_id`
- `subtask_list`
- `action_level_id`
- `scenario_profile_id`
- budgets
- oracle

Меняется только:

- `observation_profile_id`

Но этого недостаточно. Для честного сравнения два `observation_profile_id` должны различаться ровно одним каналом наблюдения.

Именно так сравнение по датчикам остается честным.

## 6.3 Что сравнивать по датчикам

Минимальный набор сравнений, который поддерживает документация:

- `lang_rgb_static_rel_act` vs `lang_rgb_static_gripper_rel_act`
  - эффект добавления `rgb_gripper`
- `lang_rgb_static_gripper_rel_act` vs `lang_rgbd_static_gripper_rel_act`
  - эффект добавления `depth_gripper`
- `lang_rgbd_static_gripper_rel_act` vs `lang_rgbd_both_rel_act`
  - эффект добавления `depth_static`
- `lang_rgb_static_rel_act` vs `lang_rgb_static_tactile_rel_act`
  - эффект добавления `rgb_tactile`
- `lang_rgb_static_abs_act` vs `lang_rgb_static_robot_scene_abs_act`
  - эффект добавления `scene_obs`

Эти сравнения должны рассматриваться:

- в `ideal`;
- в `sensor_dropout`;
- в `sensor_noise`;
- при необходимости в safety/recovery profiles.

Если нужно сравнить два сильно разных observation profile, их надо разложить на цепочку однофакторных сравнений, а не трактовать как один sensor experiment.

## 6.4 Что дает сравнение по отдельным датчикам

Этот режим нужен, чтобы понимать:

- какие сенсоры реально дают прирост success;
- какие сенсоры снижают timing cost или увеличивают его;
- какие сенсоры повышают robustness под noise/dropout;
- как один и тот же action level реагирует на добавление или удаление конкретного канала.

## 7. Reference budgets

Для CALVIN-specific ground truth в документации сохраняются reference значения:

- `target subtasks = 5`
- `max_subtask_steps = 360`
- `reference max_episode_steps = 5 * 360`

Если протокол использует другие budgets, они должны быть явно версионированы и одинаковы для baseline/stress pairs.

## 8. Что читать для чего

Короткое правило:

- `ideal` — baseline success and timing
- `sensor_dropout` — missing-modality robustness
- `sensor_noise` — corrupted-perception robustness
- `observation_profile_id` comparison — direct single-sensor comparison
- `safety_blackout_*` — safety behavior
- `recovery_wrong_action` — rollback / recovery / replan
- `mixed_stress` — combined robustness
