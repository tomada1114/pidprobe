# The pidprobe demo: an intentionally broken FastAPI app with pidprobe
# already installed beside it.
#
# Build from the repository root, because pidprobe is installed from this
# tree rather than from PyPI:
#
#     docker build -t pidprobe-demo -f demo/Dockerfile .
#
# One image, one interpreter: pidprobe requires the target process to run the
# same CPython major.minor as the prober, which is automatic when both are
# this image's /usr/local/bin/python.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# curl drives the demo endpoints and jq shapes the JSON pidprobe prints;
# procps provides ps, so the walkthrough can show the target's real PID.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl jq procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY demo/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# pidprobe from source: it is unreleased, and the demo has to exercise the
# code in the tree it ships with rather than a published wheel.
COPY pyproject.toml README.md LICENSE /pidprobe/
COPY src /pidprobe/src
RUN pip install /pidprobe

COPY demo/app.py /app/app.py

EXPOSE 8000

# Exec form on purpose: uvicorn becomes PID 1, so the walkthrough can always
# say `pidprobe snap 1`. No --reload and no --workers, so there is exactly
# one process to attach to.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "warning"]
