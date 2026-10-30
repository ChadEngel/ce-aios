# AIOS NOC agent (hire #1) — R0 read-only triage.
#
# Build context is the REPO ROOT (ce-aios), so the path below is aios/noc/noc.py:
#   docker buildx build --platform linux/amd64 --load \
#     -t caehomelab/aios-noc:0.1.1 --file aios/noc/Dockerfile .
#
# History lesson (2026-10-30): the first build of this image was made with the
# source living NOWHERE but inside the image (/app/noc.py, no repo). That made
# the running agent unmaintainable — there was no source of truth to patch, and
# recovering noc.py required exporting the image from a k3s node. This
# Dockerfile + aios/noc/noc.py put the source in git, matching aios/manager/.
FROM python:3.12-slim

# pyjwt[crypto] for the GitHub App RS256 JWT; tzdata for correct local stamps.
RUN pip install --no-cache-dir "pyjwt[crypto]==2.9.0" tzdata

COPY aios/noc/noc.py /app/noc.py
RUN chmod 0444 /app/noc.py

USER 65534:65534
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/app/noc.py"]
