# 05. Схема логирования (подробный CSV-first протокол)

## Принцип

В этом проекте первичен не набор агрегированных метрик, а полный сырой лог запуска:

- точный тег сценария;
- детальное поведение модели на каждом шаге;
- реакция среды Unity на каждое действие;
- пошаговые тайминги.

Главный артефакт: подробные CSV-файлы, пригодные для последующего анализа.

## Структура артефактов

```text
estimate_scripts/runs/
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
```

Дополнительно можно сохранять `*.jsonl`, но CSV обязателен.

## 1) `episodes.csv` (1 строка = 1 эпизод)

Обязательные колонки:

- `run_id`
- `track`
- `model_id`
- `family`
- `episode_id`
- `task_id`
- `task_title`
- `stratum` (`easy|medium|hard`)
- `seed`
- `pair_id`
- `condition_id`
- `scenario_level` (`L1..L5`)
- `scenario_variant` (например, `l4_sensor_noise`)
- `scenario_tag` (человеко-читаемый тег, например `L4:l4_sensor_noise:hard`)
- `status` (`ok|unsupported|error|invalid`)
- `max_steps`
- `max_time_sec`
- `steps_total`
- `terminate_reason`
- `decision_time_total_sec`
- `sim_exec_time_total_sec`
- `episode_wallclock_total_sec`
- `started_at_utc`
- `finished_at_utc`

Смысл: дает полный контекст запуска и итог эпизода.

## 2) `steps.csv` (1 строка = 1 шаг)

Обязательные колонки:

- `run_id`
- `episode_id`
- `step_idx`
- `timestamp_utc`
- `scenario_level`
- `scenario_variant`
- `scenario_tag`
- `active_modalities`
- `mask_size_total`
- `mask_size_allowed`
- `action_raw` (как вернула модель)
- `action_exec` (как отправлено в Unity)
- `model_status` (`ok|unsupported|error`)
- `model_error_code`
- `model_error_message`
- `sim_success_flag`
- `sim_message`
- `decision_time_step_sec`
- `sim_exec_time_step_sec`
- `episode_wallclock_step_sec` (накопленное время от старта эпизода до конца текущего шага)
- `history_size`
- `plan_revision_id` (если есть перепланирование)
- `safety_flag`
- `notes`

Смысл: полный след “наблюдение -> ответ модели -> исполнение среды -> время”.

## 3) `events.csv` (1 строка = 1 событие условия/возмущения)

Обязательные колонки:

- `run_id`
- `episode_id`
- `event_id`
- `step_idx`
- `timestamp_utc`
- `scenario_level`
- `scenario_variant`
- `event_type` (`sensor_blackout|sensor_noise|action_mask_change|injected_random_action|...`)
- `event_source` (`scenario|runtime|safety`)
- `event_payload_json`
- `model_response_before_event`
- `model_response_after_event`
- `resolved_flag`
- `resolve_step_idx`
- `resolve_latency_steps`
- `safety_reaction` (например, `continue|abort|fallback|retry`)

Смысл: фиксирует, как модель ведет себя именно в стресс-условии, а не только факт инъекции.

## Тайминги на каждом шаге (обязательно)

На уровне `steps.csv` обязательно логировать:

- `decision_time_step_sec` - время прогноза модели на этом шаге.
- `sim_exec_time_step_sec` - время исполнения действия в Unity на этом шаге.
- `episode_wallclock_step_sec` - общее накопленное время эпизода на этот шаг.

На уровне `episodes.csv` обязательно логировать:

- `decision_time_total_sec` - сумма по шагам.
- `sim_exec_time_total_sec` - сумма по шагам.
- `episode_wallclock_total_sec` - время от старта до завершения эпизода.

## Поведенческий фокус робастности

Логирование робастности должно отвечать на вопрос “как ведет себя модель”, поэтому в CSV фиксируем:

- что модель предсказала до события;
- что модель предсказала после события;
- изменилась ли стратегия (`plan_revision_id`);
- сколько шагов заняло восстановление (`resolve_latency_steps`);
- как завершился эпизод после perturbation.

## Минимальные примеры строк (CSV)

Пример `steps.csv`:

```csv
run_id,episode_id,step_idx,scenario_tag,action_raw,action_exec,model_status,sim_success_flag,decision_time_step_sec,sim_exec_time_step_sec,episode_wallclock_step_sec
vh_run_001,ep_000123,7,L4:l4_sensor_noise:hard,"[Open] <fridge> (1)","<char0> [Open] <fridge> (1)",ok,true,0.412,1.983,14.227
```

Пример `events.csv`:

```csv
run_id,episode_id,event_id,step_idx,scenario_tag,event_type,event_source,resolved_flag,resolve_step_idx,resolve_latency_steps,safety_reaction
vh_run_001,ep_000123,evt_09,8,L5:l5_base:hard,injected_random_action,scenario,true,11,3,retry
```

## Требование к воспроизводимости

Каждая строка логов должна однозначно связываться с:

- `condition_id`
- `task_id`
- `seed`
- `manifest_hash`

Без этих полей запись считается неполной для сравнения.
