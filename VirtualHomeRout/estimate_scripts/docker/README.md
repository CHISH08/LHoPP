# Docker + Gradio (VirtualHome benchmark UI)

## Что это

Контейнер поднимает веб-интерфейс Gradio для запуска бенчмарка `estimate_scripts`.
Тяжелые ресурсы (Unity executable, dataset, CALVIN) монтируются как volumes.

## Быстрый старт

1. Скопировать `.env.example` в `.env` и заполнить пути:

```powershell
cd VirtualHomeRout/estimate_scripts/docker
copy .env.example .env
```

2. Собрать и запустить:

```powershell
docker compose --env-file .env up --build
```

3. Открыть UI:

- http://localhost:7860

## Volume-монты

- `${VH_DATASET_VOLUME}` -> `/volumes/vh_dataset` (ro)
- `${VH_UNITY_VOLUME}` -> `/volumes/unity` (ro)
- `${CALVIN_VOLUME}` -> `/volumes/calvin` (ro)
- `estimate_scripts/runs` -> `/app/VirtualHomeRout/estimate_scripts/runs` (rw)
- `estimate_scripts/test_runs` -> `/app/VirtualHomeRout/estimate_scripts/test_runs` (rw)
- `estimate_scripts/protocol_bundle` -> `/app/VirtualHomeRout/estimate_scripts/protocol_bundle` (rw)

Логи и артефакты:

- UI job logs и ZIP пишутся в `estimate_scripts/runs/ui_jobs/...`
- step2/step4 ранны пишутся в `estimate_scripts/runs/...`
- интеграционные тесты пишутся в `estimate_scripts/test_runs/...`

## Важные замечания

- В контейнере нужен Linux Unity executable (например `VirtualHome.x86_64`).
- Исполняемый файл задается через переменную `VH_UNITY_EXE` в `.env`.
- Windows `.exe` внутри Linux-контейнера не запустится.
- UI умеет запускать модель внешне по host/port или поднять ее командой (`model launch command`).
