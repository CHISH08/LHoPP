# 09. Docker + UI

Этот документ описывает запуск CALVIN benchmark UI в контейнере.

## Что добавлено

- Gradio UI: [gradio_benchmark_app.py](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/runtime/gradio_benchmark_app.py)
- Dockerfile: [Dockerfile](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/Dockerfile)
- Compose: [docker-compose.yml](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/docker-compose.yml)
- Пример env: [.env.example](/c:/Users/User/code/paper/LHoPP/calvin_bench/estimate_scripts/docker/.env.example)

## Volume-политика (тяжелые данные)

В compose вынесено в volumes:

- `CALVIN_REPO_VOLUME` -> `/volumes/calvin_repo` (repo с `calvin_models`/`calvin_env`)
- `CALVIN_DATASET_VOLUME` -> `/volumes/calvin_dataset` (dataset root)
- `CALVIN_PROTOCOL_VOLUME` -> `/volumes/calvin_protocol_bundle` (step1/step2 artifacts)
- `CALVIN_RUNS_VOLUME` -> `/volumes/calvin_runs` (step3 runs, logs, frames)
- `CALVIN_TEST_RUNS_VOLUME` -> `/volumes/calvin_test_runs` (smoke/integration runs)
- `CALVIN_MODEL_SCRIPTS_VOLUME` -> `/volumes/model_scripts` (custom model scripts)
- `estimate_scripts` монтируется как volume в `/app/calvin_bench/estimate_scripts`

Это означает, что тяжелые файлы и все логи не хранятся внутри слоя образа.

## Быстрый запуск

```powershell
cd calvin_bench/estimate_scripts/docker
copy .env.example .env
# отредактировать пути в .env

docker compose --env-file .env up --build
```

UI:

- http://localhost:7860

## Что делает UI

UI запускает pipeline:

1. Step 1 (optional): deterministic sample generation
2. Step 2 (optional): scenario contracts generation
3. Step 3 (optional): benchmark runtime with model backend (`mock_random` / `http` / `python`)

Во время работы UI:

- показывает live logs,
- обновляет frame preview,
- формирует preview videos,
- отдает ZIP с артефактами запуска.
