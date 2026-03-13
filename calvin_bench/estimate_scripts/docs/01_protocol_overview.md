# 01. Обзор протокола

## Цель

Создать единый, воспроизводимый и честный протокол сравнения long-horizon моделей в `CALVIN`, где:

- первая ось задает **уровень действия**, доступного модели;
- вторая ось задает **условия исполнения** и stress-профили.

Протокол должен позволять отвечать не только на вопрос “какая модель успешнее”, но и на вопросы:

- как меняется поведение при более низкоуровневом action interface;
- как модель реагирует на шум и dropout сенсоров;
- как меняются результаты при добавлении или удалении отдельных датчиков;
- как модель ведет себя в safety-critical blackout;
- может ли модель восстановиться после wrong action;
- какие задержки связаны с моделью, а какие со средой.

## Две независимые оси benchmark'а

## Ось 1: `action_level_id`

Эта ось отвечает на вопрос: **что именно должна выдать модель как действие**.

Шкала:

- `L1 = textual_subtasks`
- `L2 = absolute_cartesian_tcp`
- `L3 = relative_cartesian_7d`
- `L4 = joint_space`

Интерпретация:

- `L1` — наиболее высокий semantic level;
- `L4` — наиболее низкий control level в этой шкале.

## Ось 2: `scenario_profile_id`

Эта ось отвечает на вопрос: **в каких условиях запускается один и тот же scenario**.

Canonical profiles:

- `ideal`
- `sensor_dropout`
- `sensor_noise`
- `safety_blackout_safe_abstain`
- `safety_blackout_best_effort`
- `recovery_wrong_action`
- `mixed_stress`

Интерпретация:

- `ideal` — baseline reference;
- остальные профили — controlled stress / ablation / safety / recovery conditions.

## Главное правило протокола

Нельзя смешивать:

- сложность action interface;
- сложность условий исполнения.

Они должны варьироваться независимо.

То есть один и тот же `sequence_id` должен быть сопоставим:

- между `L1-L4`;
- между `ideal` и stress profiles.

Отдельно протокол должен поддерживать еще один контролируемый тип сравнения:

- между парно подобранными `observation_profile_id`

при фиксированных:

- `sequence_id`
- `initial_state_id`
- `action_level_id`
- `scenario_profile_id`
- budgets
- oracle

Это не новая ось вместо существующих двух, а дополнительный comparison slice поверх уже зафиксированного scenario bundle.

## Локальные факты, на которые опирается протокол

Протокол опирается только на реальные локальные файлы:

- official sequence generator:
  - [multistep_sequences.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_models/calvin_agent/evaluation/multistep_sequences.py)
- official long-horizon eval loop:
  - [evaluate_policy.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_models/calvin_agent/evaluation/evaluate_policy.py)
- model interface:
  - [calvin_base_model.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_models/calvin_agent/models/calvin_base_model.py)
- baseline action path:
  - [calvin_env_wrapper.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_models/calvin_agent/wrappers/calvin_env_wrapper.py)
  - [play_table_env.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_env/calvin_env/envs/play_table_env.py)
  - [robot.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_env/calvin_env/robot/robot.py)
- dataset / observations / actions:
  - [dataset/README.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/dataset/README.md)
- task vocabulary and oracle:
  - [new_playtable_tasks.yaml](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_models/conf/callbacks/rollout/tasks/new_playtable_tasks.yaml)
  - [tasks.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/calvin_env/calvin_env/envs/tasks.py)
- joint-space evidence:
  - [README.md](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/README.md)
  - [RL_with_CALVIN.ipynb](/c:/Users/User/code/paper/LHoPP/calvin_bench/calvin/RL_with_CALVIN.ipynb)

## Базовые определения

## `scenario bundle`

`scenario bundle` — это один official CALVIN long-horizon episode bundle:

- `sequence_id`
- `initial_state_id`
- `subtask_list`
- `instruction_texts`
- `observation_profile_id`
- budgets
- oracle

Потом этот bundle прогоняется на разных `action_level_id` и `scenario_profile_id`.

Дополнительно тот же bundle должен быть сравним и по парам `observation_profile_id`, которые различаются ровно одним каналом наблюдения.

## `sequence_id`

Идентификатор official CALVIN sequence.

Локально подтверждено:

- `NUM_SEQUENCES = 1000`
- `seq_len = 5`
- `EP_LEN = 360`

Значит official long-horizon episode в текущем локальном CALVIN содержит:

- `5` target subtasks;
- до `360` low-level steps на одну подзадачу в reference multi-step evaluation.

## `action_level_id`

Уровень представления действия, доступного модели:

- `L1`
- `L2`
- `L3`
- `L4`

## `scenario_profile_id`

Идентификатор набора условий исполнения:

- ideal / stress / safety / recovery variants.

## `observation_profile_id`

Идентификатор observation contract, доступного модели.

Он нужен для отдельного сравнения:

- эффекта от одной дополнительной камеры;
- эффекта от одного depth channel;
- эффекта от tactile channel;
- эффекта от `scene_obs` или других state channels.

Ключевое правило: корректное сравнение по датчикам допускается только тогда, когда два `observation_profile_id` различаются ровно одним каналом наблюдения.

## `pair_id`

Идентификатор пары:

- baseline run в `ideal`
- stress run в одном из non-ideal profiles

на одном и том же scenario bundle.

`pair_id` обязателен для честного сравнения degradation и recovery.

## `baseline/stress pairing`

Каждый stress-run должен иметь парный `ideal`-run с теми же:

- `sequence_id`
- `initial_state_id`
- `observation_profile_id`
- `action_level_id`
- seed / schedule where applicable

Это правило нужно для:

- robustness comparison;
- safety deltas;
- recovery deltas;
- timing deltas.

Для сравнения по отдельным датчикам baseline/stress pairing не заменяется, а дополняется:

- сначала фиксируется пара `ideal` / stress;
- затем внутри этой пары допускается сравнение только таких `observation_profile_id`, которые различаются ровно одним каналом.

## Canonical benchmark matrix

Базовая матрица:

| scenario profile | назначение | baseline pair required |
| --- | --- | --- |
| `ideal` | reference baseline | no |
| `sensor_dropout` | robustness to missing modalities | yes |
| `sensor_noise` | robustness to corrupted perception | yes |
| `safety_blackout_safe_abstain` | strict safety evaluation | yes |
| `safety_blackout_best_effort` | diagnostic safety behavior | yes |
| `recovery_wrong_action` | recovery / rollback / replan | yes |
| `mixed_stress` | combined stress behavior | yes |

Каждый из этих профилей должен быть совместим с `L1-L4`.

## Что считается baseline

Baseline в этом протоколе — это не просто “обычный запуск”, а строго:

- `scenario_profile_id = ideal`
- те же scenario inputs;
- те же budgets;
- те же sensors;
- тот же action level;
- без noise, dropout и forced interventions.

Именно baseline является точкой отсчета для:

- success deltas;
- timing deltas;
- recovery deltas;
- safety deltas.

## Что считается stress profile

Stress profile — это любой `scenario_profile_id`, отличный от `ideal`.

Стресс может приходить из:

- сенсорных абляций;
- сенсорного шума;
- safety blackout;
- injected wrong action;
- смешанного режима.

## Что считается сравнением по отдельным датчикам

Это отдельный режим анализа, в котором:

- `action_level_id` фиксирован;
- `scenario_profile_id` фиксирован;
- меняется только `observation_profile_id`;
- при этом между двумя observation profile отличается ровно один канал.

Главный вопрос такого сравнения:

- как наличие или отсутствие конкретных сенсоров меняет success, timing, safety и recovery.

## Почему нужны оба safety режима

Протокол фиксирует два safety-профиля, потому что они проверяют разные свойства модели.

## `safety_blackout_safe_abstain`

Проверяет, умеет ли модель:

- остановиться;
- отказаться от опасного действия;
- перейти в safe fallback;
- не продолжать агрессивное управление без наблюдений.

## `safety_blackout_best_effort`

Проверяет:

- пытается ли модель продолжать выполнение по истории/внутреннему состоянию;
- может ли она продолжить поведение без грубого нарушения контракта.

Первый профиль нужен для strict safety.  
Второй — для diagnostic behavior comparison.

## Зачем нужны разные логические семейства метрик

В протоколе нет одного главного KPI.  
Есть несколько независимых аналитических срезов:

- success / chain success;
- efficiency / timing;
- safety;
- recovery / rollback / replan;
- robustness to sensor loss/noise;
- single-sensor comparison;
- action-level degradation.

Поэтому логирование строится не вокруг одной “итоговой цифры”, а вокруг полного execution trace.

## Native CALVIN vs benchmark-added behavior

## Native CALVIN

Нативно CALVIN уже дает:

- official sequences;
- task vocabulary;
- task oracle;
- language annotations;
- baseline cartesian action path;
- joint-action evidence.

## Benchmark protocol добавляет

- `action_level_id = L1-L4` как formal comparison axis;
- `scenario_profile_id` как formal conditions axis;
- `pair_id`;
- structured logging for metrics;
- safety/recovery-oriented stress profiles.

## Что не должно смешиваться

В документации и в будущем runtime нельзя смешивать:

- native CALVIN task vocabulary;
- native baseline action path;
- benchmark-side `L1` executor semantics;
- metric analysis layers.

Если в тексте не видно, к какому уровню относится сущность, документ считается недостаточно точным.
