# Rackphone CLI / Prometheus bridge.
#
# adb is in the image but the container does not own the USB device. Point
# ADB_SERVER_SOCKET at an adb server on the VM host instead: the daemon keeps
# the USB handle across container restarts, which matters because reclaiming a
# phone after a restart otherwise needs a replug.

# ===== Stage 1: Assembler =====
FROM python:3.13-slim AS builder

# Base python runtime env + uv settings + venv in /opt/venv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install uv binary (fast deps resolver/installer)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy only dependency manifests first (better layer cache)
COPY pyproject.toml uv.lock README.md ./

# Create venv + install prod deps (locked, without dev)
RUN uv venv /opt/venv \
    && uv sync --frozen --no-dev --no-install-project

# Copy source code after deps to keep caching efficient
COPY src ./src

# Install the package itself into the venv (so the `rackphone` script exists)
RUN uv sync --frozen --no-dev

# ===== Stage 2: Final =====
FROM python:3.13-slim AS final

# Runtime python env; use the prebuilt venv binaries
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    RACKPHONE_REPO=/repo

# Runtime-only deps: adb speaks to the server that owns the USB handle
RUN apt-get update && apt-get install --no-install-recommends -y \
      android-tools-adb \
      curl \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged user (fixed UID/GID for k8s-friendly perms)
RUN groupadd -g 10000 rackphone && \
    useradd -m -u 10000 -g rackphone rackphone

WORKDIR /app

# Bring in venv + app from builder stage
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app

# Fix ownership so non-root user can read/run everything
RUN chown -R rackphone:rackphone /app /opt/venv

USER rackphone

EXPOSE 9105

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
  CMD curl --fail http://localhost:9105/metrics || exit 1

# Prometheus bridge by default; any other subcommand can be passed as CMD
ENTRYPOINT ["rackphone"]
CMD ["serve", "--host", "0.0.0.0", "--port", "9105"]
