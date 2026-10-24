# AIOS Inbox Console — the read-only web front end.
#
# Build context is aios/mail/ (engine + state.py ship in the image):
#   docker buildx build --platform linux/amd64 --load \
#     -t caehomelab/aios-mail-console:0.6.0 \
#     --file aios/mail/console/Dockerfile aios/mail/
FROM python:3.12-slim

RUN pip install --no-cache-dir "fastapi==0.115.6" "uvicorn==0.32.0" \
    "pyyaml==6.0.3" "croniter==6.0.0" tzdata

COPY console/console.py /app/console.py
COPY console/static/ /app/static/
# Shared modules — the SAME files the fires run (one source of truth):
COPY engine/ /app/engine/
COPY digest/state.py /app/state.py
# model_catalog is read-only here (the console renders the cache the legs
# fetch; it holds no gateway key and makes no LLM calls).
COPY digest/model_catalog.py /app/model_catalog.py
RUN chmod 0444 /app/console.py /app/state.py /app/model_catalog.py && \
    chmod -R a+rX /app/engine /app/static

ENV DIGEST_STATE_DIR=/state
EXPOSE 8080
WORKDIR /app
CMD ["uvicorn", "console:app", "--host", "0.0.0.0", "--port", "8080"]