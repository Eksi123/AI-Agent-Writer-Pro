# syntax=docker/dockerfile:1
# AI Writer Pro —— 多阶段构建（手册 M7：Dockerfile + docker-compose）
#
# 阶段 1：Node 构建前端（Vue3 + Vite → dist/）
# 阶段 2：Python 运行时（uv 安装依赖 + 后端 + 前端产物单体部署）
#
# 构建：docker build -t ai-writer-pro .
# 运行：docker compose up -d   （推荐，含 data 卷与 healthcheck）

# ---------------------------------------------------------------------------
# 阶段 1：前端构建
# ---------------------------------------------------------------------------
FROM node:24-alpine AS webbuild

WORKDIR /web

# 先拷贝依赖清单，利用层缓存
COPY src/web/package.json src/web/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY src/web/ ./
RUN npm run build

# ---------------------------------------------------------------------------
# 阶段 2：Python 运行时
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# uv（锁定版本，与 pyproject build-system requires 对应）
COPY --from=ghcr.io/astral-sh/uv:0.7 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    DATA_ROOT=/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000

WORKDIR /srv/app

# 先装依赖（利用层缓存）
COPY pyproject.toml uv.lock ./
COPY src/app ./src/app
RUN uv sync --frozen --no-dev --no-install-project

# 拷贝其余源码并安装项目本身
COPY alembic.ini README.md ./
COPY doc ./doc
RUN uv sync --frozen --no-dev

# 前端产物（单体部署：FastAPI 挂载 dist/，SPA history 回落）
COPY --from=webbuild /web/dist ./src/web/dist

# 数据卷（app.db / agent_state.db / workspaces / skills / exports / trash / logs）
RUN mkdir -p /data
VOLUME /data

EXPOSE 8000

# 健康检查：/api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3).status==200 else 1)"

# 启动：迁移 + 服务
CMD ["sh", "-c", "uv run writer migrate && uv run writer dev --host 0.0.0.0 --port 8000 --no-reload"]
