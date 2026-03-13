# 02. Уровни действия `L1-L4`

Этот документ фиксирует, что именно должна делать модель на каждом `action_level_id` и как эти уровни соотносятся с CALVIN.

## Общий принцип

Во всех четырех уровнях должно оставаться одинаковым:

- `sequence_id`
- `initial_state_id`
- `subtask_list`
- `instruction_texts`
- `observation_profile_id`
- `scenario_profile_id`
- budgets
- oracle

Меняется только:

- форма model output;
- granularity принятия решений;
- execution path.

## `L1 = textual_subtasks`

## Что получает модель

- observation bundle;
- текущую текстовую инструкцию;
- историю действий и событий;
- допустимый словарь canonical subtask labels.

## Что выдает модель

Одну каноническую symbolic subtask, например:

- `open_drawer`
- `lift_red_block_table`
- `place_in_slider`
- `turn_on_led`

## Что это означает

Модель решает:

- **какую подзадачу выбрать / вызвать**

а не:

- как двигать TCP на каждом шаге;
- как двигать joints напрямую.

## Execution semantics

Для `L1` нужен явный benchmark-side executor этого уровня.

Он должен быть:

- versioned;
- логируемым;
- отделенным от native low-level CALVIN API.

Важно: это не “скрытая интерпретация” действия модели, а отдельный formal action level.

## `L2 = absolute_cartesian_tcp`

## Что выдает модель

7D absolute cartesian action:

- `x, y, z`
- `euler_x, euler_y, euler_z`
- `gripper`

## Основание в локальном CALVIN

- native dataset key: `actions`
- standard baseline path: absolute cartesian action

## Когда этот уровень полезен

- сравнение моделей, работающих с world-frame control;
- анализ стабильности абсолютного TCP target prediction;
- сравнение с более низкоуровневыми `L3/L4`.

## `L3 = relative_cartesian_7d`

## Что выдает модель

7D relative action:

- `dx, dy, dz`
- `deuler_x, deuler_y, deuler_z`
- `gripper`

## Основание в локальном CALVIN

- native dataset key: `rel_actions`
- standard baseline path: relative cartesian control

## Когда этот уровень полезен

- closed-loop control analysis;
- анализ накопления correction steps;
- анализ деградации под noise/dropout, когда модель сильно зависит от текущих наблюдений.

## `L4 = joint_space`

## Что выдает модель

Joint-space action:

- `7` joint values
- `1` gripper value

## Основание в локальном CALVIN

- `Joint action` явно заявлен в локальном CALVIN README;
- `joint_rel` показан в локальном `RL_with_CALVIN.ipynb`.

## Ограничение

Этот режим не проведен через стандартный datamodule/eval path так же явно, как `L2/L3`, поэтому в benchmark docs он описывается как supported benchmark level, а не как уже полностью стандартизованный baseline path.

## Почему `L1-L4` важны

Они позволяют измерять:

- насколько деградирует success при снижении уровня абстракции действия;
- насколько растет число corrective decisions;
- насколько растет время до успеха;
- как одинаковые perturbations по-разному бьют по semantic и motor-level моделям.

## Связь с scenario profiles

`L1-L4` не являются сценариями и не заменяют сценарные профили.

Каждый action level должен сравниваться:

- в `ideal`;
- в sensor stress;
- в safety blackout;
- в recovery scenarios.

Именно сочетание `action_level_id × scenario_profile_id` образует точку benchmark comparison.
