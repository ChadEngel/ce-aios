# AIOS R0 mail digest job — python-only image, no shell tools, non-root.
# Build: docker buildx build --platform linux/amd64 --load -t caehomelab/aios-mail-digest:0.1.3 aios/mail/digest/
FROM python:3.12-slim

RUN pip install --no-cache-dir "pyjwt[crypto]==2.9.0"

COPY digest.py /app/digest.py
RUN chmod 0444 /app/digest.py

USER 65534:65534
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/digest.py"]