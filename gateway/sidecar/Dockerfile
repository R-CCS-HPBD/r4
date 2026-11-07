# =============================================================================
# R4 Platform Gateway - Auth Sidecar Image
# =============================================================================
# Lightweight authentication service for NGINX X-Accel-Redirect pattern.
# =============================================================================

FROM python:3.11-slim-trixie

LABEL maintainer="R4 Platform Team"
LABEL description="Auth sidecar for R4 Platform Gateway"

# Install system dependencies (xmlsec1 required by python3-saml)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    pkg-config libxml2-dev libxmlsec1-dev libxmlsec1-openssl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python dependencies: only the compiled lock, --no-deps (ADR-100).
# requirements.txt is the input; `python scripts/deps/lockctl.py export`
# compiles requirements.lock.txt from it with the release cooldown.
COPY requirements.lock.txt .
RUN pip install --no-cache-dir --no-deps -r requirements.lock.txt \
    && pip check

# Copy application
COPY main.py .
COPY saml_sp.py .
COPY otel.py .

# Environment defaults
ENV UMRS_URL=http://umrs:8000
ENV JWT_ALGORITHM=HS256
ENV S3_ENDPOINT=http://minio:9000
ENV S3_ACCESS_KEY=minioadmin
ENV S3_SECRET_KEY=minioadmin
ENV S3_BUCKET=r4-data
ENV DATABASE_URL=postgresql://r4:r4-dev@postgres:5432/r4
ENV PRESIGNED_TTL_SECONDS=3600

# Non-root user
RUN useradd -m -u 1000 sidecar
USER sidecar

EXPOSE 8081

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8081/health || exit 1

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
