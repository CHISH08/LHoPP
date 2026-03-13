FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV OPENCV_IO_ENABLE_OPENEXR=1

WORKDIR /app/VirtualHomeRout

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Copy only code/dependencies. Heavy datasets and Unity binaries are mounted as volumes.
COPY requirements.txt /app/VirtualHomeRout/requirements.txt
COPY virtualhome /app/VirtualHomeRout/virtualhome
COPY estimate_scripts /app/VirtualHomeRout/estimate_scripts

RUN python -m pip install --upgrade pip && \
    python -m pip install -r /app/VirtualHomeRout/requirements.txt && \
    python -m pip install "gradio>=5.0,<6.0"

EXPOSE 7860

CMD ["python", "estimate_scripts/runtime/gradio_benchmark_app.py", "--host", "0.0.0.0", "--port", "7860"]
