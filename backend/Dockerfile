# Multi-stage build · keeps the final image lean (~1.2 GB w/ all ML deps)
# Layer order is tuned so deps cache survives source-only edits.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/data/hf-cache \
    TRANSFORMERS_OFFLINE=0

# Minimal build deps · sentence-transformers / chromadb wheels exist for slim,
# but pull build-essential as a safety net for any source-only sub-deps.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. install deps first (cache layer) · we extract them from pyproject.toml
#    so this stays in sync without maintaining a parallel requirements.txt
COPY pyproject.toml ./
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" \
    > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt

# 2. copy source last · cheap edits don't bust the deps cache
COPY agent ./agent
COPY app.py ./

# 3. data dir for chroma + sqlite checkpoints + traces
#    (compose mounts a host volume here; image owns the empty default)
RUN mkdir -p /app/data/workspace /app/data/chroma /app/data/hf-cache

ENV AGENT_WORKDIR=/app/data/workspace \
    CHROMA_DIR=/app/data/chroma \
    TRACE_FILE=/app/data/traces.jsonl \
    CHECKPOINT_DB=/app/data/checkpoints.sqlite

EXPOSE 8000

# Honest healthcheck · hits the same /health endpoint the SDK uses
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
