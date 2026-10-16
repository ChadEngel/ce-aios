# aios-manager image
#
# Slim python:3.12-slim base + our deps. ~50 MB final image, no build tools
# needed at runtime (we just need the python packages and our script).
#
# Build:
#   docker build -t caehomelab/aios-manager:<tag> aios/manager/
#   docker push caehomelab/aios-manager:<tag>            # if using a registry
#
# Or, for the k3s lab (no external registry), use the local registry or
# `k3s ctr image import` after `docker save`.

FROM python:3.12-slim

# Don't write .pyc files, don't buffer stdout (so Loki/promtail sees logs
# in real time), run as non-root.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aios_manager.py .

# Run as a non-root user for defense in depth. The python:3.12-slim image
# already has a `nonroot` user (uid 65532) — use it.
USER 65532:65532

CMD ["python", "/app/aios_manager.py"]
