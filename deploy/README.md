# 服务器部署模板

这些文件用于把可部署版 FastAPI 服务放到一台 Linux 服务器上试用。

默认假设项目目录为：

```text
/opt/ai_manga_workflow
```

如果你的服务器路径不同，需要同步修改：

- `deploy/systemd/ai-manga.service`
- `deploy/nginx/ai_manga_workflow.conf`
- `.env` 里的 `AI_MANGA_DB_PATH`

## 1. 准备项目和环境

```bash
cd /opt/ai_manga_workflow
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

复制 `.env.example` 为 `.env`，填写模型密钥，并至少修改：

```dotenv
AI_MANGA_SECRET_KEY=请换成长随机字符串
AI_MANGA_ADMIN_PASSWORD=请换成强密码
AI_MANGA_DB_PATH=/opt/ai_manga_workflow/data/server/ai_manga.sqlite3
AI_MANGA_MAX_ACTIVE_JOBS_PER_USER=2
AI_MANGA_MAX_ACTIVE_JOBS_TOTAL=8
```

`AI_MANGA_MAX_ACTIVE_JOBS_PER_USER` 和 `AI_MANGA_MAX_ACTIVE_JOBS_TOTAL` 用于限制排队/运行中的长任务数量，防止试用服务器被任务堆满；设为 `0` 表示不限制。

确保运行用户可以写入数据目录：

```bash
sudo mkdir -p /opt/ai_manga_workflow/data/server /opt/ai_manga_workflow/data/users /opt/ai_manga_workflow/outputs
sudo chown -R www-data:www-data /opt/ai_manga_workflow/data /opt/ai_manga_workflow/outputs
```

## 2. 安装 systemd 服务

如果这是从旧版本升级，先备份数据：

```bash
manga-flow backup-server --output backups
```

服务启动时会自动检查 SQLite 表结构并补齐缺失列。升级前备份仍然是必要步骤。

```bash
sudo cp deploy/systemd/ai-manga.service /etc/systemd/system/ai-manga.service
sudo systemctl daemon-reload
sudo systemctl enable --now ai-manga
sudo systemctl status ai-manga
```

检查本机服务：

```bash
curl http://127.0.0.1:8000/healthz
```

应该返回：

```json
{"status":"ok"}
```

## 3. 安装 Nginx 反向代理

```bash
sudo cp deploy/nginx/ai_manga_workflow.conf /etc/nginx/sites-available/ai_manga_workflow.conf
sudo ln -sf /etc/nginx/sites-available/ai_manga_workflow.conf /etc/nginx/sites-enabled/ai_manga_workflow.conf
sudo nginx -t
sudo systemctl reload nginx
```

然后访问：

```text
http://服务器IP/
```

## 4. 初始账号

首次启动时会创建 `.env` 中配置的管理员账号：

```dotenv
AI_MANGA_ADMIN_USERNAME=admin
AI_MANGA_ADMIN_PASSWORD=...
```

如果 SQLite 数据库已经存在，修改这些环境变量不会覆盖已有账号。需要重建开发库时，先停止服务，再删除：

```text
data/server/ai_manga.sqlite3
data/server/ai_manga.sqlite3-wal
data/server/ai_manga.sqlite3-shm
```

## 5. 常用运维命令

查看服务日志：

```bash
sudo journalctl -u ai-manga -f
```

重启服务：

```bash
sudo systemctl restart ai-manga
```

备份 SQLite、项目和产物：

```bash
cd /opt/ai_manga_workflow
. .venv/bin/activate
manga-flow backup-server --output backups
```

备份命令会用 SQLite backup API 生成一致性数据库快照，并打包 `data/users`、`data/projects` 和 `outputs`。默认不包含 `.env`，避免把模型密钥和管理员配置放进备份包；如果确实需要连同 `.env` 备份，使用 `--include-env` 并限制备份文件访问权限。
