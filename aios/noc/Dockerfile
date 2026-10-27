# AIOS NOC agent (hire #1) — python-only image, no shell tools, non-root.
# Build (context MUST be the repo root so aios/noc/ is copied in):
#   docker buildx build --platform linux/amd64 --load \
#     -t caehomelab/aios-noc:0.1.0 --file aios/noc/Dockerfile .
FROM python:3.12-slim

RUN pip install --no-cache-dir "pyjwt[crypto]==2.9.0"

COPY aios/noc/noc.py /app/noc.py
RUN chmod 0444 /app/noc.py

USER 65534:65534
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/noc.py"]
