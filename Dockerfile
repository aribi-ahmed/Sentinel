# SENTINEL API image.
#
# Objective M-11 is accepted on "fresh machine to running system in under ten
# minutes", so the image is built to remove the two things that would otherwise
# dominate a cold start:
#
#   1. The MiniLM embedding model (~90 MB) is downloaded at build time, not on
#      the first investigation.
#   2. The compliance index is built into the image from ./docs, so the
#      container needs no post-start ingestion step.
#
# Multi-stage: the build stage carries compilers and caches, the runtime stage
# carries only what serves traffic.

# ---------------------------------------------------------------- build stage
FROM python:3.14-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# uv resolves and installs far faster than pip, and honours the lock file.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Dependency layer first: it changes far less often than the source, so edits to
# the application do not invalidate the (slow) install layer.
COPY pyproject.toml uv.lock* ./
# torch is installed first, from PyTorch's CPU index. The default PyPI wheel
# bundles the CUDA runtime (~4.6 GB of nvidia/triton libraries) that this
# container can never use: the embedding model runs on CPU.
RUN uv venv /opt/venv \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache \
      --index-url https://download.pytorch.org/whl/cpu torch \
 && VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r pyproject.toml

ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv

# Bake the embedding model into the image so the first request is not delayed by
# a 90 MB download — and so the container works on an air-gapped host.
ENV HF_HOME=/opt/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/models \
    TOKENIZERS_PARALLELISM=false
RUN python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')"

# Build the compliance index from the regulatory corpus that ships in ./docs.
COPY src/ ./src/
COPY docs/ ./docs/
COPY ingest_compliance.py ./
RUN python ingest_compliance.py

# -------------------------------------------------------------- runtime stage
FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv \
    HF_HOME=/opt/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/models \
    TOKENIZERS_PARALLELISM=false \
    PYTHONPATH=/app/src

# curl is here for the container health check and nothing else.
RUN apt-get update \
 && apt-get install --no-install-recommends -y curl \
 && rm -rf /var/lib/apt/lists/*

# Never run the API as root.
RUN useradd --create-home --uid 1000 sentinel

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/models /opt/models

WORKDIR /app
COPY --chown=sentinel:sentinel src/ ./src/
COPY --chown=sentinel:sentinel scripts/ ./scripts/
COPY --chown=sentinel:sentinel ingest_compliance.py ./
# The OFAC lists the sanctions screen reads at startup.
COPY --chown=sentinel:sentinel datasets/sdn.csv datasets/alt.csv ./datasets/
# The regulatory corpus is served by the reference-library endpoint, so the
# source PDFs are needed at runtime as well as at index-build time.
COPY --chown=sentinel:sentinel docs/corpus/ ./docs/corpus/
COPY --chown=sentinel:sentinel --from=builder /build/chroma_compliance ./chroma_compliance

USER sentinel
EXPOSE 8000

# Reports the durability and provider state the instance actually has, so an
# orchestrator sees "unhealthy" if Postgres or the model provider is unreachable.
HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/system || exit 1

CMD ["uvicorn", "sentinel.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
