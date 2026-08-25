# Rackphone CLI / Prometheus bridge.
#
# adb is in the image but the container does not own the USB device. Point
# ADB_SERVER_SOCKET at an adb server on the VM host instead: the daemon keeps
# the USB handle across container restarts, which matters because reclaiming a
# phone after a restart otherwise needs a replug.
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends android-tools-adb \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY rackphone ./rackphone

RUN uv pip install --system --no-cache .

ENV PYTHONUNBUFFERED=1 \
    RACKPHONE_REPO=/repo

EXPOSE 9105
ENTRYPOINT ["rackphone"]
CMD ["serve", "--host", "0.0.0.0", "--port", "9105"]
