# Multi-stage build using uv (NFR-3). Builds one image used by api/worker/mcp.
FROM python:3.12-slim AS builder

# Bring in the uv binary from the official image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# The project depends on the sibling `llm-structured-output` library via a uv path
# source (../structured_output). It lives outside this build context, so it is
# provided as a named additional build context and copied to the matching path.
# (compose: build.additional_contexts.structured_output=../structured_output)
COPY --from=structured_output pyproject.toml README.md /structured_output/
COPY --from=structured_output src /structured_output/src

# Install dependencies first (cached layer) using only the lock metadata.
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev || uv sync --no-install-project --no-dev

# Now copy the source and install the project itself. README.md and LICENSE are
# referenced by the package metadata, so they must be present for the build.
COPY README.md LICENSE ./
COPY src ./src
COPY config ./config
COPY prompts ./prompts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 docstore

WORKDIR /app
# Bring the editable-installed local library along so its install resolves.
COPY --from=builder --chown=docstore:docstore /structured_output /structured_output
COPY --from=builder --chown=docstore:docstore /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

USER docstore

# Default command; overridden per service in docker-compose (api/worker/mcp).
CMD ["docstore-api"]
