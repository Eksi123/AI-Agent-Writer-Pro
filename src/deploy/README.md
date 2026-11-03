# AI Writer Pro 部署配置

本目录包含 Docker 容器化和部署相关的配置文件。

## 文件说明

- `Dockerfile` - 多阶段构建镜像（Node.js 构建前端 + Python 运行后端）
- `docker-compose.yml` - 容器编排配置（含数据卷和健康检查）
- `nginx.conf` - Nginx 反向代理配置（含 SSE 长连接优化）

## 快速开始

### Docker 部署

```bash
# 从项目根目录执行
cd src/deploy
docker compose up -d

# 查看日志
docker compose logs -f app

# 停止服务
docker compose down
```

### 数据持久化

数据存储在 `writer-data` 卷中（容器内 `/data`），包含：
- 数据库文件（app.db, agent_state.db）
- 小说工作区（workspaces/）
- 技能配置（skills/）
- 导出文件（exports/）
- 日志（logs/）

### 备份数据

```bash
docker run --rm -v writer-data:/data -v "${PWD}:/backup" alpine tar czf /backup/writer-data.tgz -C /data .
```

## Nginx 反向代理

如需使用 Nginx 作为反向代理（支持 SSE 长连接），可参考 `nginx.conf` 配置。

```bash
# 复制配置到 Nginx
sudo cp src/deploy/nginx.conf /etc/nginx/sites-available/ai-writer
sudo ln -s /etc/nginx/sites-available/ai-writer /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 环境变量

可通过 `.env` 文件（项目根目录）配置：

```env
DATA_ROOT=/data
APP_HOST=0.0.0.0
APP_PORT=8000
LOG_LEVEL=INFO
# LLM API Keys
OPENAI_API_KEY=sk-xxx
ANTHROPIC_API_KEY=sk-xxx
```

## 健康检查

容器内置健康检查端点：`http://127.0.0.1:8000/api/v1/health`
