FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app/calvin_bench

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY estimate_scripts/docker/requirements.ui.txt /tmp/requirements.ui.txt
COPY estimate_scripts /app/calvin_bench/estimate_scripts

RUN python -m pip install --upgrade pip && \
    python -m pip install -r /tmp/requirements.ui.txt

EXPOSE 7860

CMD ["python", "estimate_scripts/runtime/gradio_benchmark_app.py", "--host", "0.0.0.0", "--port", "7860"]
