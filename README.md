# AI Agent Writer Pro

大纲驱动的 AI 小说写作工作台 —— **单用户应用**，无需注册登录，你的小说全部保存在自己的机器上。

先写大纲，再逐章成文：既可以自己动手写，也可以让 AI 按大纲生成正文（生成前后自动做衔接检查与要点核对）。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://www.python.org/)
[![Vue](https://img.shields.io/badge/Vue-3-42b883.svg)](https://vuejs.org/)

> 面向开发 / 维护者的架构与实现细节见 [`Project-Develop.md`](./Project-Develop.md)。

---

## 一、功能介绍

打开应用就是一个**三栏工作台**：左边是你的小说项目和一只陪你写作的小宠物（带城市天气），中间是写作区，右边是 AI 写作助手。另有「通用设置」和「AI 设置」两个独立页面。

### 写一本书的完整流程

1. **建项目**：起个书名、选 1–3 个题材标签、写一段简介，左栏就会出现这本书。
2. **写大纲**：一份 Markdown 文档，`# 卷` → `## 章` → 每章简介。它是全书的骨架，也是 AI 生成正文时的依据；保存时会自动校验格式并提示问题。
3. **搭卷章**：在目录上「往前 / 往后插入卷或章节」，插到第几位就排在第几位，后面的自动顺延；把一卷的章节删空，这一卷会一并消失。
4. **写正文**：富文本编辑器，边写边自动保存、实时字数统计；写错了可以**回退编辑**（最多 5 次，和 Word 的撤销一样）；每次手动保存都会留一份**修订快照**，随时可以回滚到旧版本。
5. **读成书**：阅读模式有目录抽屉、上一章 / 下一章翻页、记住你读到哪一行、独立的夜间模式；写完还能一键**导出成书**（Markdown / TXT / EPUB / MOBI 四种格式打包下载）。

### 让 AI 帮你写

- **AI 写作助手**（右栏）：多会话、按项目隔离，回复像聊天一样逐字流出来；它调用了什么工具、列了什么写作计划，都实时展示给你看。
- **一个发送键，三种形态**：空闲时是发送箭头；AI 思考中且你没打字时变成红色停止方块；你打了字则变成「追加消息」，可以选 *停止后发送 / 添加到队列 / 作为引导插入*，不打断它当前的工作。
- **按大纲生成/修改正文（四道关卡）**：在对话里说「按大纲某章的章简介生成/修改正文」，AI 会自动走四道关卡——先检查大纲里有没有这章的简介、再做与上一章的衔接预检、然后生成正文、最后核对剧情要点；要点不符会自动重写（最多两次）。无需点任何按钮，意图由后端识别。
- **选区 AI 润色**：在正文里选中一段话，选区上方浮出「✨ AI 润色」按钮；点一下，这段文字连同它所在的章节、段落范围、前后文一起进入对话，AI 就知道该改哪一段、只改这一段；具体怎么改，你在对话里描述即可。
- **AI 改稿时不打架**：AI 正在修改某一章时，该章进入只读，工具栏「自动保存」右侧显示灰色提示「AI编辑中，请稍后再试……」，手动与自动保存都会被拒绝，AI 写完后自动解锁。
- **该你拍板时会问你**：AI 要做大范围改写、覆盖已有正文等高危操作前，先弹审批卡片（批准 / 改参数后批准 / 拒绝 / 补充说明）；拿不准的设定问题会弹咨询卡片问你。
- **出错说人话**：任何报错都是简短中文提示 + 该怎么做的建议，不会甩一串英文异常给你。
- **子代理与技能**：内置策划、撰写、润色、连贯性审查、资料检索 5 个子代理和 8 项写作技能，都可以自建、改提示词、换模型。

### 后悔药：回收站

误删的章节不会立刻消失。顶部工具栏「回收站」里保留**最近删除过章节的 20 本小说**，点进去能看到「卷 → 被删章节」：

- **回收**：章节还原回原来的卷（卷若已被删空会按快照重建）；如果整本小说都被删了，回收其中任意一章会**连书带大纲一起找回来**；
- **删除**：红色警示的彻底删除，不可恢复。某本书的被删章节全部删干净后，这本书也会从回收站彻底移除。

### 写作之外的陪伴与掌控

- **宠物与天气**：左栏一只生肖宠物，你写作时它敲键盘、久不动笔它打瞌睡；旁边是所在城市实时天气与空气质量。
- **三种主题**：浅色 / 深色 / 护眼，主题色、字号、字体、页宽都能调，改完即时生效。
- **全文检索**：整本书搜一句话，中文也能搜得准。
- **模型自由接**：OpenAI / Anthropic / Google / Azure / OpenRouter 以及任何 OpenAI 兼容服务都能接；可以测连接、设默认模型、配降级链；密钥存在你本机的系统级环境变量里（界面填写、变量名随便起），界面上只显示掩码。
- **审计日志**：AI 的每一次文件操作都有据可查（只读）。
- **数据在你手里**：正文就是一份份本地 Markdown 文件，整个数据目录复制走就是备份。

---

## 二、启动与部署

### 2.1 本地一键启动（推荐日常使用）

环境要求：Python 3.12+；Node.js 20+（仅构建前端需要）；推荐装 [uv](https://docs.astral.sh/uv/)；calibre 可选（不装则 MOBI 导出置灰）。

```powershell
python main.py                 # 同时拉起后端(8000) + 前端 Vite(5173)，Ctrl+C 一起停
python main.py --prod          # 单体模式：构建前端后只开后端 8000
python main.py --backend-only  # 只启动后端
python main.py --frontend-only # 只启动前端（需后端已在 8000）
python main.py --port 9000     # 自定义后端端口（前端代理自动跟随）
```

访问 `http://localhost:5173`（开发模式，前端热更新）或 `http://127.0.0.1:8000`（单体模式）。
脚本会自动安装依赖、构建前端、等待端口就绪；没有 uv 时自动回落为直跑。

### 2.2 本地手动启动

```powershell
# 1. 安装后端依赖（自动创建 .venv）
uv sync

# 2. 初始化 / 迁移数据库
uv run writer migrate

# 3. 启动后端（默认 http://127.0.0.1:8000）
uv run writer dev

# 4. 另开一个终端，启动前端开发服务器
cd src/web
npm install
npm run dev
```

**首次使用**：左下角「AI 设置」→「大模型 API」添加 Provider 与模型（密钥写入系统级环境变量，变量名随便起），之后就能在右栏和 AI 对话；想让 AI 按大纲写某一章，直接在对话里说「按大纲《某章》的章简介生成正文」即可。

**可选配置**：应用配置（端口 / 数据目录 / 日志级别等）全部有默认值，无需任何配置文件。
若想接 Langfuse 可观测，编辑根目录 `config.ini` 的 `[langfuse]` 段（`public_key` / `secret_key` / `host`）；
三项全空或不建该文件时，追踪自动降级为本地结构化日志，功能不受影响。

### 2.3 服务器部署（Linux + Nginx，从零到上线）

以 Ubuntu 22.04 / Debian 12 为例，全程约 10 分钟。

**① 装基础软件**

```bash
sudo apt update && sudo apt install -y git curl nginx build-essential
# Python 3.12（系统自带版本过低时用 deadsnakes 源）
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y python3.12 python3.12-venv
# uv（依赖管理）
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
# Node.js 20（仅构建前端需要，构建完可留可不留）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

**② 取代码并装后端依赖**

```bash
sudo mkdir -p /srv && sudo chown $USER /srv
git clone <你的仓库地址> /srv/ai-writer-pro && cd /srv/ai-writer-pro
uv sync                      # 创建 .venv 并安装依赖
# 可选：编辑 config.ini 的 [langfuse] 段接入 Langfuse（不配则追踪降级为本地日志）
uv run writer migrate        # 建表 / 迁移
```

**③ 构建前端**

```bash
cd src/web && npm ci && npm run build && cd ../..
# 产出 src/web/dist/，后端会自动挂载它（单页应用 history 回落）
```

**④ 用 systemd 托管后端**

```bash
sudo tee /etc/systemd/system/ai-writer.service > /dev/null <<'EOF'
[Unit]
Description=AI Agent Writer Pro backend
After=network.target

[Service]
WorkingDirectory=/srv/ai-writer-pro
Environment=DATA_ROOT=/srv/ai-writer-pro/data
ExecStart=/srv/ai-writer-pro/.venv/bin/python -m uvicorn app.main:app \
          --host 127.0.0.1 --port 8000 --app-dir src --no-reload
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now ai-writer
systemctl status ai-writer --no-pager      # 应看到 active (running)
curl -s http://127.0.0.1:8000/api/v1/health # 应返回 {"code":0,...}
```

**⑤ Nginx 反向代理（含 SSE 长连接调优）**

```bash
sudo tee /etc/nginx/sites-available/ai-writer > /dev/null <<'EOF'
server {
    listen 80;
    server_name _;                 # 有域名就换成你的域名

    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE 流式回复必需：关缓冲、放宽超时、禁用缓存
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/ai-writer /etc/nginx/sites-enabled/ai-writer
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

**⑥ 放行防火墙并验收**

```bash
sudo ufw allow 80/tcp && sudo ufw reload     # 用云安全组的记得同步放行
curl -sI http://127.0.0.1/ | head -1         # HTTP/1.1 200 OK
```

浏览器打开 `http://服务器IP/` 即可使用。要上 HTTPS 用 `certbot --nginx` 一条命令搞定。
数据默认落在 `/srv/ai-writer-pro/data/`，备份 = 停服后整目录打包。

### 2.4 Docker 部署

仓库自带多阶段构建（node 构建前端 → python-slim 运行）与 compose 编排：

```bash
docker compose up -d            # 构建并启动，容器内自动 migrate
docker compose logs -f app      # 看日志
docker compose down             # 停止（数据卷保留）
```

数据持久化在命名卷 `writer-data`（容器内 `/data`）；默认只绑 `127.0.0.1:8000`，
局域网访问把 `docker-compose.yml` 里端口改成 `"8000:8000"`（或前面再套一层 Nginx，SSE 调优同 2.3 ⑤）。
备份示例：

```bash
docker run --rm -v writer-data:/data -v "${PWD}:/backup" alpine tar czf /backup/writer-data.tgz -C /data .
```

### 2.5 怎么测试

```powershell
uv run python -m pytest -q     # 全量测试（497 用例，假模型驱动，不触网 / 不打真实 LLM）
uv run ruff check src tests    # 后端 lint
cd src/web; npm run build      # 前端类型检查 + 构建
uv run writer doctor           # 环境自检（FTS5 / 二元切分 / calibre / Langfuse / 依赖版本）
```

手工冒烟建议顺序：启动 → 「AI 设置」加一个模型并测连接 → 新建小说 → 插入卷章 → 写两段正文（看自动保存与回退编辑）→ 选中一段点「AI 润色」→ 右栏发一句话看流式回复 → 删一章去回收站找回 → 导出成书。

---

## 三、项目设计总览

### 3.1 项目模块与大纲

```
ai-agent-writer-pro/
├─ main.py                  本地一键启动脚本（后端 + 前端 Vite 一起拉）
├─ pyproject.toml / uv.lock 依赖唯一事实源；writer CLI（dev/migrate/rebuild-index/doctor）
├─ Dockerfile / docker-compose.yml   多阶段镜像与编排
├─ src/
│  ├─ app/                  后端（Python / FastAPI）
│  │  ├─ api/               路由层：15 个模块 / 89 个端点，只做协议转换，不写业务
│  │  ├─ services/          业务编排层：17 个模块（项目/卷章/正文/大纲/检索/导出/回收站/天气…）
│  │  ├─ agent/             AI Agent（本项目技术核心）：主循环 / 四阶段门禁 / 子代理 / 技能 /
│  │  │                     MCP / 记忆（消息树）/ 沙箱与权限 / LLM 工厂 / 可观测
│  │  ├─ core/ db/ schemas/ 基础设施：配置 / 错误码 / 路径 / 密钥、ORM 与迁移、DTO
│  └─ web/                  前端（Vue 3 + TS + Vite 单页应用）
│     ├─ views/ components/ 三栏工作台 + 通用设置 + AI 设置；回收站 / 导出 / 审批卡片等组件
│     ├─ stores/            Pinia：project / editor / reader / agent / settings
│     ├─ api/ i18n/ styles/ 类型化接口封装与 SSE 客户端、中文文案集中管理、主题变量
├─ tests/                   pytest 21 个文件 / 497 用例（httpx 直连 ASGI + 假模型）
└─ data/                    运行时数据（见 3.3）
```

分层铁律：`api → services` 是唯一取数路径；`services` 不碰 HTTP 概念；Agent 与写作业务
通过文件工具和 REST 两条受控通道交互；所有文件写入收敛到 `WorkspaceGuard` 一个入口
（路径白名单 + 越权拦截 + 审计埋点 + 派生索引同步）。

### 3.2 项目运行逻辑框架

```
浏览器(Vue SPA)
   │  REST：/api/v1/**（取数 / 写数）          SSE：/api/v1/agent/runs/{id}/stream
   ▼
FastAPI（api/ 路由层，拆信封 {code,message,data}）
   ▼
services/（业务编排：校验 → 事务 → 文件/DB 写入 → 审计 → 派生索引同步）
   │                                  │
   ▼                                  ▼
Markdown 文件（内容事实源）        SQLite（登记 / 快照 / 会话 / 消息树 / 审计）
```

- **启动**：进程起来 → 建表/打迁移戳 → 探测 FTS5 → 挂载前端 `dist/`（单页应用回落）→ 就绪。
- **写作链路**：前端编辑 → `PUT /chapters/{id}/content` 携 `base_version` → 乐观锁校验
  （冲突回 409 让前端弹合并横幅）→ 按策略留修订快照 → 写文件 + 更新索引。
- **AI 链路**：发消息 → 后端立刻回 `202 + run_id` → 后台跑 Agent 主循环 → 事件经 SSE 推给前端
  （token 增量 / 工具调用 / 泳道 / 计划 / 门禁阶段 / 用量）→ run 结束消息树落库、前端重拉合并。
  生成正文走四阶段门禁（G0 简介存在 → G1 衔接预检 → G2 生成 → G3 要点核对），
  高危写入触发 interrupt 审批，拿不准触发 consult 咨询，都在右栏卡片里闭环。
- **可观测**：Langfuse 配置齐全时全链路上报；全空自动降级为本地结构化日志，功能不受影响。

### 3.3 数据存储

```
data/
├─ app.db                     SQLite：项目登记 / 修订快照 / 会话 / 消息树 / 运行 / 中断 / 审计 / 配置 / 派生索引
├─ agent_state.db             LangGraph checkpoint（Agent 运行态，跨库不建外键）
├─ workspaces/{project_id}/   ★内容事实源：novel.yaml / volumes.yaml / chapters/**/*.md / outline/ / summaries/
├─ skills/                    用户自建技能（Markdown 物化）
├─ session-skills/{sid}/      会话选中技能的物化副本
├─ exports/                   导出任务产物（zip）
├─ trash/{project_id}-{ts}/   整本小说被删除时的工作区归档
├─ recycle/{project_id}/      回收站：index.yaml + chapters/（被删章节，最多 20 本）
└─ logs/                      app / audit / error 三类 JSONL 日志
```

设计要点：**正文与知识库以 Markdown 文件为唯一事实源**，数据库只是登记、快照与可重建的索引
（`writer rebuild-index` 可全量重建）；派生索引写失败不阻断写入，只标脏待重建。
删掉 `data/` 即完全清空，整目录复制即备份。

---

## 四、开源协议

本项目基于 **MIT License** 开源，完整条款见 [`LICENSE`](./LICENSE)。

你可以自由地使用、复制、修改、合并、发布、分发本软件，甚至出售副本，只需在所有副本中保留
版权声明与许可声明。本软件按「原样」提供，不附带任何明示或暗示的担保。
