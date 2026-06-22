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
- 后台管理页 `/admin`：管理员可创建用户，并查看用户额度。
- SQLite 数据库：本地存储用户、额度、用量流水、任务记录和项目索引。
- 额度接口：`GET /api/quota/me`。
- 用量接口：`GET /api/usage/me`、`GET /api/admin/usage`。
- 任务接口：`POST /api/jobs`、`GET /api/jobs/{job_id}`，并记录任务归属用户。
- 受保护控制台：`/console` 会显示当前用户和额度，并复用原有 AI 漫剧控制台。
- 受保护原接口：`/api/state`、`/api/project`、`/api/script/workshop`、`/api/script/import`、`/api/file` 等均要求登录。
- 额度预扣：AI 生成剧本、规范化导入剧本、分阶段生成会在后端检查额度。

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

当前实现是在任务创建前预扣或检查额度，任务成功接受后记为已用。后续需要进一步细化为“任务真正成功后再从 reserved 转 used，失败自动退款”，并把旧内存任务执行器完全迁移到数据库任务执行器。

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

## 尚未完成的服务器化改造

这些是 `部署方案.md` 中还需要继续推进的部分：

- 按用户隔离项目 YAML：`data/users/<user_id>/projects/`。
- 按用户隔离输出：`outputs/users/<user_id>/...`。
- 文件下载接口按 user_id 做严格路径授权。
- 用数据库任务表完全替代旧版 `web.py` 的内存 `JOBS` / `WORKSHOP_JOBS`。
- 任务失败时按实际执行情况自动退款。
- 管理后台支持禁用/启用用户、重置密码、修改额度、查看失败率和模型统计。
- PostgreSQL 迁移和 Alembic 迁移脚本。

