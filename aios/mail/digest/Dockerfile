# AIOS R0 mail digest job — python-only image, no shell tools, non-root.
# Build: docker buildx build --platform linux/amd64 --load -t caehomelab/aios-mail-digest:0.4.0 aios/mail/digest/
FROM python:3.12-slim

RUN pip install --no-cache-dir "pyjwt[crypto]==2.9.0" "pyyaml==6.0.3" tzdata

# BUILD CONTEXT IS aios/mail/ (NOT aios/mail/digest/): the extracted engine
# lives at aios/mail/engine/, so build like
#   docker buildx build --platform linux/amd64 --load -t <tag> --file aios/mail/digest/Dockerfile aios/mail/
COPY digest/digest.py /app/digest.py
COPY digest/filer.py digest/state.py /app/
COPY engine/ /app/engine/
RUN chmod 0444 /app/digest.py /app/filer.py /app/state.py && \
    chmod -R a+rX /app/engine

USER 65534:65534
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/digest.py"]