# Production image for the backend. Build from the repository root (the
# agent reads shared/tools.json):
#   docker build -f backend/Dockerfile -t browser-agent-backend .

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/ ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --system --uid 10001 --home-dir /srv agent
WORKDIR /srv/backend
COPY --from=build --chown=agent:agent /srv/backend /srv/backend
COPY --chown=agent:agent shared/ /srv/shared/
ENV PATH=/srv/backend/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ENVIRONMENT=production \
    HOST=0.0.0.0 \
    PORT=8787 \
    LOG_JSON=true \
    SHARED_TOOLS_PATH=/srv/shared/tools.json \
    DATA_DIR=/srv/data
USER agent
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/readyz' % os.environ['PORT'])"
CMD ["python", "-m", "app"]
