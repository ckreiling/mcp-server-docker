FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

COPY uv.lock pyproject.toml /app/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev --no-editable

COPY ./src /app/src
COPY README.md ./README.md
COPY LICENSE ./LICENSE
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm

WORKDIR /app
 
COPY --from=uv --chown=app:app /app/.venv /app/.venv

# Ensure executables in the venv take precedence over system executables
ENV PATH="/app/.venv/bin:$PATH"

# when running the container, add --db-path and a bind mount to the host's db file
ENTRYPOINT ["mcp-server-docker"]
