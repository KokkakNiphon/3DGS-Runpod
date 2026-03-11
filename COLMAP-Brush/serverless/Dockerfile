# ─────────────────────────────────────────────────────────────
# COLMAP + Brush — RunPod Serverless
# ─────────────────────────────────────────────────────────────
FROM runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    colmap \
    wget \
    xz-utils \
    libgl1 \
    libglib2.0-0 \
    libvulkan1 \
    vulkan-tools \
    mesa-vulkan-drivers \
    xvfb && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN wget -q https://github.com/ArthurBrussee/brush/releases/download/v0.3.0/brush-app-x86_64-unknown-linux-gnu.tar.xz -O /tmp/brush.tar.xz && \
    tar -xf /tmp/brush.tar.xz -C / && \
    rm /tmp/brush.tar.xz

RUN pip install --no-cache-dir runpod requests

WORKDIR /app
COPY runpod_serverless.py .

CMD ["python", "-u", "runpod_serverless.py"]
