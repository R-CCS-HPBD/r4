# r4-client: Lightweight container with r4 CLI + umrs CLI
# ---------------------------------------------------------------
# Build:  docker build -t r4-client -f r4/Dockerfile .
# Run:    docker run --rm r4-client --help
#         docker run --rm -e R4_GATEWAY_URL=http://gw:8080 r4-client chat "hi"
#         docker run --rm -it -v ~/.config/r4:/home/r4user/.config/r4 r4-client
#
# NOTE: Build context must be the repository root (not r4/).

# ============================================================
# Stage 1 — builder
# ============================================================
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# -- Third-party packages: only from the image lock (ADR-100) --
#    r4/docker/requirements.lock.txt is exported from the workspace uv.lock
#    (root extra `r4-worker-image`) by `python scripts/deps/lockctl.py export`.
#    --no-deps everywhere: pip resolves nothing on its own.
COPY r4/docker/requirements.lock.txt /build/requirements.lock.txt
RUN pip install --no-cache-dir --no-deps -r /build/requirements.lock.txt

# -- The unpublished siblings r4 depends on (registry-free), no deps --
#    r4-contracts (stdlib-only) → r4-teamengine-spi (ADR-020 SPI) → science.
COPY r4-contracts/pyproject.toml r4-contracts/README.md /build/r4-contracts/
COPY r4-contracts/src/ /build/r4-contracts/src/
RUN pip install --no-cache-dir --no-deps /build/r4-contracts
COPY packages/r4-teamengine-spi/ /build/r4-teamengine-spi/
RUN pip install --no-cache-dir --no-deps /build/r4-teamengine-spi
COPY science/ /build/science/
RUN pip install --no-cache-dir --no-deps /build/science

# -- r4 (extras chat + companion-lite: rich, in the lock) --
COPY r4/pyproject.toml r4/README.md /build/r4/
COPY r4/src/ /build/r4/src/
RUN pip install --no-cache-dir --no-deps /build/r4

# -- umrs (client path + shared/core runtime only; no ML stack). Its client
#    extra, diskcache and python-jose[cryptography] are in the lock. --
COPY umrs/src/pyproject.toml /build/umrs/
COPY umrs/src/umrs/ /build/umrs/umrs/
COPY umrs/src/client_umrs.py /build/umrs/
RUN pip install --no-cache-dir --no-deps /build/umrs \
    && pip check

# ============================================================
# Stage 2 — runtime
# ============================================================
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="R4 Client"
LABEL org.opencontainers.image.description="R4 Platform CLI (r4 + umrs)"
LABEL org.opencontainers.image.vendor="RCCS"
LABEL org.opencontainers.image.version="0.1.0"

# Non-root user
RUN groupadd -g 1000 r4user && \
    useradd -m -u 1000 -g r4user -s /bin/bash r4user && \
    mkdir -p /home/r4user/.config/r4 && \
    chown -R r4user:r4user /home/r4user

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER r4user
WORKDIR /home/r4user

ENTRYPOINT ["r4"]
CMD []
