# 可部署版 Web 服务说明

本文档对应 `部署方案.md` 的第一阶段落地版本：在保留原有本地控制台能力的基础上，新增一个可部署的 FastAPI 服务，提供登录、管理员后台、额度表、用量流水和受保护的工作台入口。

## 本地启动

进入项目目录：

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
conda activate ai-manga-flow
```

安装或更新依赖：

```bash
python -m pip install -e .
```

启动可部署版服务：

```bash
manga-flow serve --host 127.0.0.1 --port 8000
```

如果 `manga-flow` 命令不可用，可以直接运行：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python -m manga_flow.cli serve --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

健康检查：

```bash
curl http://127.0.0.1:8000/healthz
```

## 默认账号

首次启动时，如果数据库里没有用户，系统会自动创建超级管理员：

```text
账号：admin
密码：admin123456
```

部署到服务器前必须在 `.env` 中修改：

```dotenv
AI_MANGA_SECRET_KEY=change-this-long-random-string
AI_MANGA_ADMIN_USERNAME=admin
AI_MANGA_ADMIN_PASSWORD=change-this-password
AI_MANGA_ADMIN_DISPLAY_NAME=系统管理员
AI_MANGA_ADMIN_MONTHLY_QUOTA=10000
```

如果已经启动过并创建了 SQLite 数据库，修改默认管理员环境变量不会重置现有用户。需要重置本地开发库时，停止服务后删除：

```text
data/server/ai_manga.sqlite3
data/server/ai_manga.sqlite3-wal
data/server/ai_manga.sqlite3-shm
```

## 当前已实现能力

- 登录页：账号密码登录，登录成功后写入 HttpOnly Cookie。
- 退出登录：清除会话 Cookie。
- 默认超级管理员自动初始化。
- 后台管理页 `/admin`：管理员可创建用户、修改角色、修改额度、手动增加额度、禁用/启用账号、重置密码、清零已用额度、删除失败任务记录，并查看用户额度、模型用量、统计概览和最近任务。
- SQLite 数据库：本地存储用户、额度、用量流水、任务记录和项目索引。
- 认证接口：`GET /api/auth/me`、`POST /api/auth/change-password`。
- 额度接口：`GET /api/quota/me`。
- 用量接口：`GET /api/usage/me`、`GET /api/admin/usage`。
- 项目资源接口：`GET /api/projects` 返回当前用户可见项目及元数据，`POST /api/projects` 创建并保存新项目，`GET /api/projects/{path}` 读取项目，`PUT /api/projects/{path}` 更新项目；旧版 `/api/project` 仍保留给当前网页兼容使用。
- 健康检查：`GET /healthz`，用于 Nginx、systemd 或部署脚本确认服务进程可访问。
- 模型配置检查：`GET /api/provider-status`，读取流程配置和 `.env`，返回各模型槽是否启用、模型名、端点以及所需环境变量是否已配置；不会调用外部模型接口，也不会返回密钥原文。
- 普通生成任务接口：`POST /api/jobs`、`GET /api/jobs`、`GET /api/jobs/{job_id}`、`POST /api/jobs/{job_id}/cancel`，由数据库记录任务归属、状态、日志路径和额度结算结果。
- 管理任务和统计接口：`GET /api/admin/jobs` 支持按用户、状态和任务类型筛选，`DELETE /api/admin/jobs/{job_id}` 可删除 failed/canceled 任务记录，`GET /api/admin/stats` 返回用户、额度、任务状态、失败率、按模型聚合的用量和用量摘要。
- 管理用户接口：`PATCH /api/admin/users/{user_id}` 可修改角色、状态、显示名和月额度，`POST /api/admin/users/{user_id}/quota/add` 可手动增加额度。
- 受保护控制台：`/console` 会显示当前用户和额度，并复用原有 AI 漫剧控制台。
- 受保护原接口：`/api/state`、`/api/project`、`/api/script/workshop`、`/api/script/import`、`/api/file` 等均要求登录。
- 额度预扣：AI 生成剧本、规范化导入剧本、分阶段生成会在后端检查额度。
- 普通生成任务结算：`/api/jobs` 创建任务时进入 `reserved_quota`，命令真正成功后转入 `used_quota`，命令失败后退回额度并记录失败日志。
- AI 剧本工坊结算：`/api/script/workshop` 创建任务时进入 `reserved_quota`，后台工坊任务完成后同步到数据库；成功转入 `used_quota`，失败或终止退回额度。
- 月度额度周期：`reset_cycle=monthly` 的账号会在进入新月份后自动把 `used_quota` 清零，并把 `reset_at` 更新为当前 `YYYY-MM`；运行中任务的 `reserved_quota` 不会被月度重置清掉。
- 普通用户项目隔离：项目 YAML 保存到 `data/users/<user_id>/projects/`。
- 普通用户项目索引：新旧项目接口都会把项目写入数据库索引；项目列表读取时如果遇到单个 YAML 损坏，会在对应项目上返回 `valid=false` 和错误信息，而不是影响整个列表。
- 普通用户输出隔离：生成任务使用用户专属运行时配置，输出到 `outputs/users/<user_id>/`。
- 普通用户任务隔离：只能在任务列表和任务详情中看到自己的任务；管理员可以查看全部任务。
- 普通用户文件隔离：`/api/file` 只允许访问自己的项目目录和输出目录；管理员可以访问项目根目录内文件。

## 当前本地数据库

默认 SQLite 路径：

```text
data/server/ai_manga.sqlite3
```

可以用环境变量覆盖：

```dotenv
AI_MANGA_DB_PATH=/opt/ai_manga_workflow/data/server/ai_manga.sqlite3
```

服务器多人试用初期可以继续使用 SQLite。并发用户变多后，建议按 `部署方案.md` 迁移到 PostgreSQL。

## 额度规则

第一版采用内部点数，不按真实模型成本精算：

| 操作 | 点数 |
| --- | ---: |
| AI 生成剧本 | 20 |
| 规范化导入剧本 | 5 |
| 检查项目 / 接口状态 | 0 |
| 脚本分镜 | 5 |
| 生成图片 | 80 |
| 生成配音 | 30 |
| 生成视频 | 120 |
| 合成成片 | 5 |
| 一键完整出片 | 220 |

当前普通生成任务 `/api/jobs` 和 AI 剧本工坊 `/api/script/workshop` 已按“创建时预扣、任务成功后转已用、任务失败或终止后退回”的方式结算。规范化导入是同步接口，仍是在请求完成时直接成功或退款。

AI 剧本工坊仍复用旧本地控制台里的 `WORKSHOP_JOBS` 执行逻辑，FastAPI 服务会把状态同步到数据库并在终态结算额度。服务重启后可以从数据库查到工坊任务的终态、日志和额度流水，但运行中的多角色阶段详情无法恢复；要做到完整恢复，需要继续把工坊执行器本身迁移到数据库/任务队列。

普通生成任务取消支持当前服务进程内正在运行的任务：`POST /api/jobs/{job_id}/cancel` 会向正在运行的子进程发送终止信号，并在执行线程退出后把预扣额度退回。服务重启后已经失去进程句柄的历史任务只能查看状态和日志；如果需要跨进程、跨机器可靠取消，应继续引入 Redis/Celery/RQ 等任务队列。

## 与旧本地控制台的关系

旧命令仍保留：

```bash
manga-flow web --host 127.0.0.1 --port 8765
```

它适合本机单人开发调试。

多人试用和服务器部署请使用：

```bash
manga-flow serve --host 127.0.0.1 --port 8000
```

生产环境不要把 `8765` 的旧本地控制台直接暴露到公网。

## 服务器部署要点

推荐服务器结构：

```text
Nginx 80/443
  -> 127.0.0.1:8000 FastAPI/Uvicorn
  -> data/server/ai_manga.sqlite3 或 PostgreSQL
  -> outputs/ 持久化目录
  -> .env 保存模型密钥和管理员配置
```

systemd 可以使用：

```ini
[Unit]
Description=AI Manga Workflow Web Server
After=network.target

[Service]
WorkingDirectory=/opt/ai_manga_workflow
EnvironmentFile=/opt/ai_manga_workflow/.env
ExecStart=/opt/ai_manga_workflow/.venv/bin/uvicorn manga_flow.server.app:app --host 127.0.0.1 --port 8000
Restart=always
User=www-data

[Install]
WantedBy=multi-user.target
```

仓库中已经提供可直接复制和按路径调整的模板：

```text
deploy/README.md
deploy/systemd/ai-manga.service
deploy/nginx/ai_manga_workflow.conf
```

## 尚未完成的服务器化改造

这些是 `部署方案.md` 中还需要继续推进的部分：

- 用数据库任务表或任务队列完全替代旧版 `web.py` 的 `WORKSHOP_JOBS`，让 AI 剧本工坊运行中的阶段详情也支持重启后恢复。
- AI 剧本工坊按更细的实际执行阶段结算额度；当前第一版按整个工坊任务成功或失败结算。
- 管理后台已经支持按用户筛选任务、查看失败率、删除失败任务和按模型聚合用量。后续如果要精确到每一次真实 API 调用，需要让各 provider 回写 token、图片、视频等真实成本。
- PostgreSQL 迁移和 Alembic 迁移脚本。

## 本地验证清单

当前本地已经验证：

- `manga-flow serve --host 127.0.0.1 --port 8000` 可以启动服务。
- `/healthz` 返回 `{"status":"ok"}`。
- `/api/provider-status` 登录后可查看模型槽位和必需环境变量是否配置。
- 未登录访问 `/api/state` 返回 `401`。
- 默认管理员可以登录 `/console` 和 `/admin`。
- 管理员可以创建普通用户。
- 管理员可以修改普通用户额度、清零用量、禁用/启用账号、重置密码。
- 普通用户登录后只能看到自己的项目列表。
- 普通用户项目会初始化到 `data/users/<user_id>/projects/`。
- 普通用户无法通过 `/api/file` 下载全局 `data/projects/` 文件。
- 普通用户可以下载自己的 `data/users/<user_id>/projects/` 文件。
- 普通用户运行 `structure` 阶段时，输出目录为 `outputs/users/<user_id>/...`。
- 普通用户运行 `structure` 阶段会先预扣对应额度，成功后转为已用。
- 普通用户运行失败的普通生成任务会自动退回预扣额度，并在 `/api/jobs/{job_id}` 返回失败日志。
- 服务重启后，普通生成任务的历史状态、日志路径和额度流水仍可从数据库查询。
- 普通用户可以通过 `POST /api/jobs/{job_id}/cancel` 终止当前进程内正在运行的普通生成任务。
- 管理员可以通过 `/admin` 查看统计概览、最近任务、最近用量，也可以通过 `/api/admin/stats` 查看统计 JSON。
- AI 剧本工坊成功会把预扣额度转为已用；失败或终止会退回预扣额度，并把终态同步到数据库任务表。
- 管理员可以修改用户角色、手动增加额度，并删除 failed/canceled 任务记录。
- 登录用户可以通过 `POST /api/auth/change-password` 修改自己的密码。
- 管理员可以在 `/admin` 和 `/api/admin/stats` 查看基于额度流水聚合的模型用量。
- 月度额度自动重置会清零已用额度并保留运行中预扣额度，后台用户表会显示当前额度周期。
