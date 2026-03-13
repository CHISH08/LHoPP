# Docker + Gradio UI (CALVIN benchmark)

Этот docker-пакет поднимает Gradio UI для `calvin_bench/estimate_scripts`.

Ключевое требование выполнено через volumes:

- heavy CALVIN repo в volume (`CALVIN_REPO_VOLUME`)
- CALVIN dataset в volume (`CALVIN_DATASET_VOLUME`)
- protocol/contracts в volume (`CALVIN_PROTOCOL_VOLUME`)
- runs/logs в volume (`CALVIN_RUNS_VOLUME`)
- test runs в volume (`CALVIN_TEST_RUNS_VOLUME`)
- запускаемые benchmark-скрипты (`estimate_scripts`) монтируются как volume в контейнер

## Быстрый старт

1. Подготовить `.env`:

```powershell
cd calvin_bench/estimate_scripts/docker
copy .env.example .env
```

2. Отредактировать пути в `.env`.

3. Собрать и запустить:

```powershell
docker compose --env-file .env up --build
```

4. Открыть UI:

- http://localhost:7860

## Что важно

- `CALVIN_REPO_VOLUME` должен указывать на корень CALVIN-репозитория (где есть `calvin_models` и `calvin_env`).
- `CALVIN_DATASET_VOLUME` должен указывать на dataset root для step3 (`task_D_D` или другой).
- Если используете `model-backend=http`, можно запускать model server на хосте или через `model launch command` в UI.
- Реальный step3 c CALVIN env требует установленных runtime-зависимостей в контейнере (они включены в `requirements.ui.txt`).
