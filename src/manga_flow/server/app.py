from __future__ import annotations

import html
import json
import os
from pathlib import Path
import subprocess
import threading
from typing import Any
from urllib.parse import parse_qs

import yaml
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from dotenv import load_dotenv

from manga_flow import web as legacy_web

from . import auth, db


app = FastAPI(title="AI Manga Workflow Server")

ROOT = Path.cwd().resolve()
GLOBAL_PROJECT_DIR = ROOT / "data" / "projects"
USER_DATA_DIR = ROOT / "data" / "users"
USER_OUTPUT_DIR = ROOT / "outputs" / "users"
RUNNING_PROCESSES: dict[str, subprocess.Popen[str]] = {}
RUNNING_PROCESSES_LOCK = threading.Lock()


@app.on_event("startup")
def startup() -> None:
    load_dotenv(".env")
    db.init_db()
    auth.bootstrap_admin()


def json_error(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


def quota_units(action: str, stages: str = "") -> int:
    if action == "script_workshop":
        return 20
    if action == "script_import":
        return 5
    if action == "check" or action == "provider_status":
        return 0
    if action != "stage":
        return 0
    parts = {item.strip() for item in stages.replace("，", ",").split(",") if item.strip()}
    if not parts or "all" in parts:
        return 220
    cost = 0
    if "structure" in parts:
        cost += 5
    if "images" in parts:
        cost += 80
    if "voice" in parts:
        cost += 30
    if "videos" in parts:
        cost += 120
    if "compose" in parts:
        cost += 5
    return cost


def public_job_status(status_value: str) -> str:
    return {
        "success": "done",
        "failed": "failed",
        "canceled": "canceled",
        "running": "running",
        "queued": "queued",
    }.get(status_value, status_value)


def job_label(job: dict[str, Any]) -> str:
    try:
        payload = json.loads(job.get("input_payload_json") or "{}")
    except Exception:
        payload = {}
    job_type = str(job.get("job_type") or payload.get("action") or "")
    if job_type == "check":
        return "检查项目"
    if job_type == "provider_status":
        return "接口状态"
    if job_type == "stage":
        return f"运行流程：{payload.get('stages') or 'all'}"
    if job_type == "script_workshop":
        return "AI 剧本工坊"
    return job_type or "任务"


def db_job_payload(job: dict[str, Any], include_log: bool = False) -> dict[str, Any]:
    payload = {
        **job,
        "action": job.get("job_type"),
        "label": job_label(job),
        "status": public_job_status(str(job.get("status") or "")),
        "return_code": 0 if job.get("status") == "success" else (1 if job.get("status") in {"failed", "canceled"} else None),
        "error": job.get("error_message") or "",
    }
    if include_log:
        log_path = Path(str(job.get("log_path") or ""))
        payload["log"] = log_path.read_text(encoding="utf-8", errors="replace")[-40000:] if log_path.exists() else ""
    return payload


def db_jobs_for_user(user: dict[str, Any]) -> list[dict[str, Any]]:
    rows = db.list_jobs(None if is_admin(user) else int(user["id"]), limit=50)
    return [db_job_payload(row) for row in rows]


def cancel_db_job(job_id: str) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job:
        raise ValueError("任务不存在。")
    if str(job.get("job_type") or "") == "script_workshop":
        raise ValueError("AI 剧本工坊任务请使用 /api/script/workshop/jobs/{job_id}/cancel 终止。")
    status_value = str(job.get("status") or "")
    if status_value in {"success", "failed", "canceled"}:
        return db_job_payload(job, include_log=True)
    db.update_job_status(job_id, "canceled", error_message="用户请求终止任务。")
    with RUNNING_PROCESSES_LOCK:
        process = RUNNING_PROCESSES.get(job_id)
    if process and process.poll() is None:
        process.terminate()
    updated = db.get_job(job_id)
    assert updated is not None
    return db_job_payload(updated, include_log=True)


def start_db_job(user: dict[str, Any], payload: dict[str, Any], units: int) -> dict[str, Any]:
    job_id = db.next_job_hint()
    prepared = prepare_user_stage_payload(user, payload, job_id_hint=job_id)
    label, command = legacy_web._build_command(prepared)
    log_dir = user_output_dir(user) / "web_jobs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{job_id}.log"
    event_id = 0
    try:
        event_id = db.reserve_quota(int(user["id"]), units, action_type=str(payload.get("action") or "stage"), job_id=job_id)
        db.record_job(
            job_id,
            int(user["id"]),
            str(payload.get("action") or "stage"),
            project_id=str(prepared.get("project") or ""),
            payload_json=json.dumps({**prepared, "label": label}, ensure_ascii=False),
            log_path=str(log_path),
            reserved_units=units,
        )
        thread = threading.Thread(target=run_db_job, args=(job_id, command, log_path, event_id, units), daemon=True)
        thread.start()
    except Exception:
        if event_id:
            db.finish_usage_event(event_id, "refunded")
        raise
    job = db.get_job(job_id)
    assert job is not None
    return db_job_payload(job)


def run_db_job(job_id: str, command: list[str], log_path: Path, event_id: int, units: int) -> None:
    if not db.mark_job_running(job_id):
        latest = db.get_job(job_id) or {}
        if latest.get("status") == "canceled":
            db.finish_usage_event(event_id, "refunded")
            db.finish_job(job_id, "canceled", actual_units=0, error_message=latest.get("error_message") or "用户请求终止任务。")
        return
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    return_code = 1
    error_message = ""
    try:
        with log_path.open("w", encoding="utf-8") as log:
            log.write("$ " + " ".join(command) + "\n\n")
            log.flush()
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with RUNNING_PROCESSES_LOCK:
                RUNNING_PROCESSES[job_id] = process
            if (db.get_job(job_id) or {}).get("status") == "canceled" and process.poll() is None:
                process.terminate()
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
            return_code = process.wait()
    except Exception as exc:
        error_message = repr(exc)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\nERROR: {error_message}\n")
    finally:
        with RUNNING_PROCESSES_LOCK:
            RUNNING_PROCESSES.pop(job_id, None)
    latest = db.get_job(job_id) or {}
    if latest.get("status") == "canceled":
        db.finish_usage_event(event_id, "refunded")
        db.finish_job(job_id, "canceled", actual_units=0, error_message=latest.get("error_message") or "用户请求终止任务。")
    elif return_code == 0:
        db.finish_usage_event(event_id, "success", actual_units=units)
        db.finish_job(job_id, "success", actual_units=units)
    else:
        db.finish_usage_event(event_id, "refunded")
        db.finish_job(job_id, "failed", actual_units=0, error_message=error_message or f"return_code={return_code}")


def is_admin(user: dict[str, Any]) -> bool:
    return user["role"] in {"super_admin", "admin"}


def user_project_dir(user: dict[str, Any]) -> Path:
    return USER_DATA_DIR / str(user["id"]) / "projects"


def user_config_dir(user: dict[str, Any]) -> Path:
    return USER_DATA_DIR / str(user["id"]) / "configs"


def user_output_dir(user: dict[str, Any]) -> Path:
    return USER_OUTPUT_DIR / str(user["id"])


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def ensure_user_workspace(user: dict[str, Any]) -> None:
    project_dir = user_project_dir(user)
    project_dir.mkdir(parents=True, exist_ok=True)
    if any(project_dir.glob("*.yaml")):
        return
    for source in [GLOBAL_PROJECT_DIR / "demo_story.yaml", GLOBAL_PROJECT_DIR / "ancient_short.yaml"]:
        if source.exists():
            target = project_dir / source.name
            if not target.exists():
                target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def list_user_projects(user: dict[str, Any]) -> list[str]:
    if is_admin(user):
        projects = legacy_web._list_projects()
        for user_dir in sorted(USER_DATA_DIR.glob("*/projects")) if USER_DATA_DIR.exists() else []:
            projects.extend(relative(path) for path in sorted(user_dir.glob("*.yaml")))
        return sorted(dict.fromkeys(projects))
    ensure_user_workspace(user)
    return [relative(path) for path in sorted(user_project_dir(user).glob("*.yaml"))]


def user_safe_project_path(user: dict[str, Any], rel_path: str, *, for_write: bool = False) -> str:
    raw_name = Path(str(rel_path or "new_manga_project.yaml")).name
    if not raw_name.endswith((".yaml", ".yml")):
        raw_name = f"{raw_name}.yaml"
    if is_admin(user) and not for_write:
        path = (ROOT / rel_path).resolve()
        if path == ROOT or ROOT not in path.parents:
            raise ValueError("项目路径越界。")
        return str(path.relative_to(ROOT))
    project_dir = user_project_dir(user)
    project_dir.mkdir(parents=True, exist_ok=True)
    return relative(project_dir / raw_name)


def read_project_for_user(user: dict[str, Any], rel_path: str) -> dict[str, Any]:
    safe_path = user_safe_project_path(user, rel_path, for_write=False)
    path = (ROOT / safe_path).resolve()
    if not path.exists():
        raise ValueError(f"Project file does not exist: {safe_path}")
    content = path.read_text(encoding="utf-8")
    project = legacy_web._project_from_content(content)
    return legacy_web._project_response(path, project, content)


def save_project_for_user(user: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    safe_path = user_safe_project_path(user, str(payload.get("path") or ""), for_write=True)
    path = (ROOT / safe_path).resolve()
    if isinstance(payload.get("data"), dict):
        project = legacy_web.ProjectBrief.model_validate(payload["data"])
        legacy_web._validate_unique_character_voices(project)
        content = legacy_web._project_to_yaml(project)
    else:
        content = str(payload.get("content") or "")
        if not content.strip():
            raise ValueError("Project YAML content is empty.")
        project = legacy_web._project_from_content(content)
        legacy_web._validate_unique_character_voices(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    response = legacy_web._project_response(path, project, content)
    response["projects"] = list_user_projects(user)
    db.upsert_project(int(user["id"]), project.title or project.project_id, response["path"], output_dir=relative(user_output_dir(user)))
    return response


def prepare_user_stage_payload(user: dict[str, Any], payload: dict[str, Any], job_id_hint: str) -> dict[str, Any]:
    next_payload = dict(payload)
    next_payload["project"] = user_safe_project_path(user, str(payload.get("project") or ""), for_write=False)
    source_config = (ROOT / str(payload.get("config") or "config/pipeline.siliconflow.yaml")).resolve()
    if source_config == ROOT or ROOT not in source_config.parents or not source_config.exists():
        raise ValueError(f"流程配置不存在或越界：{payload.get('config')}")
    with source_config.open("r", encoding="utf-8") as file:
        config_data = yaml.safe_load(file) or {}
    if not isinstance(config_data, dict):
        raise ValueError(f"流程配置格式错误：{source_config}")
    config_data.setdefault("project", {})
    config_data["project"]["output_dir"] = relative(user_output_dir(user))
    config_dir = user_config_dir(user)
    config_dir.mkdir(parents=True, exist_ok=True)
    runtime_config = config_dir / f"runtime_{job_id_hint}.yaml"
    runtime_config.write_text(yaml.safe_dump(config_data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    next_payload["config"] = relative(runtime_config)
    return next_payload


def latest_outputs_for_user(user: dict[str, Any]) -> dict[str, str]:
    if is_admin(user):
        return legacy_web._latest_outputs()
    output_root = user_output_dir(user)
    if not output_root.exists():
        return {}
    run_dirs = [path for path in output_root.glob("*/episode_*") if path.is_dir()]
    if not run_dirs:
        return {}
    run_dir = max(run_dirs, key=legacy_web._run_dir_mtime)
    project_id = run_dir.parent.name
    episode_text = run_dir.name.removeprefix("episode_")
    candidates = {
        "final_video": run_dir / "final" / f"{project_id}_episode_{episode_text}_sample.mp4",
        "script": run_dir / "script.md",
        "storyboard": run_dir / "storyboard.html",
        "render_report": run_dir / "reports" / "render_report.json",
        "stage_report": run_dir / "reports" / "stage_report.json",
    }
    outputs = {name: relative(path) for name, path in candidates.items() if path.exists()}
    logs = sorted((run_dir / "logs").glob("render_*.log"), key=lambda item: item.stat().st_mtime, reverse=True) if (run_dir / "logs").exists() else []
    if logs:
        outputs["latest_log"] = relative(logs[0])
    return outputs


def user_can_access_file(user: dict[str, Any], file_path: Path) -> bool:
    path = file_path.resolve()
    if is_admin(user):
        return path == ROOT or ROOT in path.parents
    allowed_roots = [user_project_dir(user).resolve(), user_output_dir(user).resolve()]
    return any(path == root or root in path.parents for root in allowed_roots)


def require_can_manage_user(operator: dict[str, Any], target_user_id: int) -> dict[str, Any]:
    auth.require_admin(operator)
    target = db.get_user_by_id(target_user_id)
    if not target:
        raise HTTPException(status_code=404, detail="用户不存在。")
    if operator["role"] != "super_admin" and target["role"] == "super_admin":
        raise HTTPException(status_code=403, detail="admin 不能修改 super_admin。")
    return target


def filter_jobs_for_user(user: dict[str, Any], jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if is_admin(user):
        return jobs
    allowed = db.user_job_ids(int(user["id"]))
    return [job for job in jobs if str(job.get("id") or "") in allowed]


def require_job_access(user: dict[str, Any], job_id: str) -> None:
    if is_admin(user):
        return
    job = db.get_job(job_id)
    if not job or int(job["user_id"]) != int(user["id"]):
        raise HTTPException(status_code=403, detail="没有权限访问该任务。")


def login_page(error: str = "") -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI 漫剧工作台登录</title>
  <style>
    body {{ margin: 0; min-height: 100vh; display: grid; place-items: center; background: #f6f7f9; color: #17202a; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ width: min(420px, calc(100vw - 32px)); background: #fff; border: 1px solid #d8dde6; border-radius: 8px; padding: 24px; }}
    h1 {{ font-size: 20px; margin: 0 0 16px; }}
    label {{ display: block; margin: 12px 0 6px; color: #667085; font-size: 13px; }}
    input {{ width: 100%; height: 38px; border: 1px solid #d8dde6; border-radius: 6px; padding: 0 10px; box-sizing: border-box; }}
    button {{ width: 100%; margin-top: 18px; height: 40px; border: 0; border-radius: 6px; background: #2563eb; color: white; font-weight: 700; cursor: pointer; }}
    .error {{ color: #c2410c; font-size: 13px; margin-bottom: 10px; }}
    .hint {{ color: #667085; font-size: 12px; line-height: 1.5; margin-top: 12px; }}
  </style>
</head>
<body>
  <main>
    <h1>AI 漫剧工作台登录</h1>
    {f'<div class="error">{html.escape(error)}</div>' if error else ''}
    <form method="post" action="/login">
      <label>账号</label>
      <input name="username" autocomplete="username" required autofocus>
      <label>密码</label>
      <input name="password" type="password" autocomplete="current-password" required>
      <button type="submit">登录</button>
    </form>
    <div class="hint">本地默认管理员：admin / admin123456。部署服务器前请用环境变量 AI_MANGA_ADMIN_PASSWORD 修改初始密码。</div>
  </main>
</body>
</html>"""


def console_page(user: dict[str, Any]) -> str:
    quota = db.quota_for_user(int(user["id"]))
    admin_link = '<a href="/admin">后台管理</a>' if user["role"] in {"super_admin", "admin"} else ""
    banner = f"""
  <div class="deploy-banner">
    <span>当前用户：{html.escape(user["username"])}（{html.escape(user["role"])})</span>
    <span>额度：可用 {quota["available_quota"]} / 总额 {quota["monthly_quota"]}，已用 {quota["used_quota"]}，预扣 {quota["reserved_quota"]}</span>
    {admin_link}
    <a href="/api/quota/me" target="_blank">额度详情</a>
    <form method="post" action="/logout"><button type="submit">退出</button></form>
  </div>
  <style>
    .deploy-banner {{ display:flex; flex-wrap:wrap; align-items:center; gap:12px; padding:10px 24px; background:#0f172a; color:#e5e7eb; font-size:13px; }}
    .deploy-banner a {{ color:#bfdbfe; text-decoration:none; }}
    .deploy-banner form {{ margin:0; }}
    .deploy-banner button {{ min-height:28px; border:1px solid #475569; border-radius:6px; background:#1e293b; color:#e5e7eb; cursor:pointer; padding:0 10px; }}
  </style>
"""
    return legacy_web.INDEX_HTML.replace("<body>", f"<body>{banner}", 1)


def admin_page(user: dict[str, Any]) -> str:
    rows = []
    for item in db.list_users():
        available = (item.get("monthly_quota") or 0) - (item.get("used_quota") or 0) - (item.get("reserved_quota") or 0)
        disabled_selected = "selected" if item["status"] == "disabled" else ""
        active_selected = "selected" if item["status"] == "active" else ""
        rows.append(
            f"<tr><td>{item['id']}</td><td>{html.escape(item['username'])}</td><td>{html.escape(item['role'])}</td>"
            f"<td>{html.escape(item['status'])}</td><td>{item.get('monthly_quota') or 0}</td>"
            f"<td>{item.get('used_quota') or 0}</td><td>{item.get('reserved_quota') or 0}</td><td>{available}</td>"
            f"<td><form method='post' action='/admin/users/{item['id']}/quota' class='inline-form'>"
            f"<input name='monthly_quota' type='number' value='{item.get('monthly_quota') or 0}'><button>改额度</button></form>"
            f"<form method='post' action='/admin/users/{item['id']}/status' class='inline-form'>"
            f"<select name='status'><option value='active' {active_selected}>active</option><option value='disabled' {disabled_selected}>disabled</option></select><button>改状态</button></form>"
            f"<form method='post' action='/admin/users/{item['id']}/password' class='inline-form'>"
            f"<input name='password' placeholder='新密码'><button>重置密码</button></form>"
            f"<form method='post' action='/admin/users/{item['id']}/quota/reset' class='inline-form'><button>清零用量</button></form></td></tr>"
        )
    stats = db.admin_stats()
    quota_stats = stats.get("quotas", {})
    job_stats = stats.get("jobs", {})
    user_stats = stats.get("users", {})
    job_rows = []
    for item in db.list_jobs(limit=30):
        job_rows.append(
            f"<tr><td>{html.escape(str(item.get('id') or ''))}</td>"
            f"<td>{html.escape(str(item.get('username') or ''))}</td>"
            f"<td>{html.escape(str(item.get('job_type') or ''))}</td>"
            f"<td>{html.escape(str(item.get('status') or ''))}</td>"
            f"<td>{item.get('reserved_units') or 0}</td><td>{item.get('actual_units') or 0}</td>"
            f"<td>{html.escape(str(item.get('created_at') or ''))}</td></tr>"
        )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>后台管理</title>
  <style>
    body {{ margin:0; padding:24px; background:#f6f7f9; color:#17202a; font-family:-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:16px; }}
    section {{ background:#fff; border:1px solid #d8dde6; border-radius:8px; padding:16px; margin-bottom:16px; }}
    h1, h2 {{ margin:0 0 12px; }}
    .stat-grid {{ display:grid; grid-template-columns:repeat(4, minmax(140px, 1fr)); gap:12px; }}
    .stat-card {{ border:1px solid #e5e7eb; border-radius:8px; padding:12px; background:#f8fafc; }}
    .stat-card strong {{ display:block; font-size:22px; margin-bottom:4px; }}
    .stat-card span {{ color:#667085; font-size:12px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ text-align:left; border-bottom:1px solid #e5e7eb; padding:8px; }}
    form {{ display:grid; grid-template-columns:repeat(5, minmax(120px, 1fr)); gap:10px; align-items:end; }}
    .inline-form {{ display:flex; grid-template-columns:none; gap:6px; margin:0 0 6px; align-items:center; }}
    .inline-form input, .inline-form select {{ width:120px; }}
    .inline-form button {{ min-width:72px; padding:0 8px; }}
    label {{ display:block; color:#667085; font-size:12px; margin-bottom:5px; }}
    input, select {{ width:100%; height:34px; border:1px solid #d8dde6; border-radius:6px; padding:0 8px; box-sizing:border-box; }}
    button {{ height:34px; border:0; border-radius:6px; background:#2563eb; color:#fff; font-weight:700; cursor:pointer; }}
    a {{ color:#2563eb; text-decoration:none; }}
  </style>
</head>
<body>
  <header>
    <h1>后台管理</h1>
    <div><a href="/console">返回工作台</a></div>
  </header>
  <section>
    <h2>统计概览</h2>
    <div class="stat-grid">
      <div class="stat-card"><strong>{user_stats.get('total_users') or 0}</strong><span>用户总数</span></div>
      <div class="stat-card"><strong>{quota_stats.get('used_quota') or 0}</strong><span>已用额度</span></div>
      <div class="stat-card"><strong>{quota_stats.get('reserved_quota') or 0}</strong><span>预扣额度</span></div>
      <div class="stat-card"><strong>{round(float(job_stats.get('failure_rate') or 0) * 100, 1)}%</strong><span>任务失败/终止率</span></div>
    </div>
  </section>
  <section>
    <h2>创建用户</h2>
    <form method="post" action="/admin/users">
      <div><label>账号</label><input name="username" required></div>
      <div><label>初始密码</label><input name="password" required></div>
      <div><label>角色</label><select name="role"><option value="user">user</option><option value="admin">admin</option></select></div>
      <div><label>月额度</label><input name="monthly_quota" type="number" value="500"></div>
      <button type="submit">创建</button>
    </form>
  </section>
  <section>
    <h2>用户与额度</h2>
    <table>
      <thead><tr><th>ID</th><th>账号</th><th>角色</th><th>状态</th><th>总额</th><th>已用</th><th>预扣</th><th>可用</th><th>操作</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </section>
  <section>
    <h2>最近任务</h2>
    <table>
      <thead><tr><th>ID</th><th>用户</th><th>类型</th><th>状态</th><th>预扣</th><th>实际</th><th>创建时间</th></tr></thead>
      <tbody>{''.join(job_rows)}</tbody>
    </table>
    <p><a href="/api/admin/jobs" target="_blank">查看任务 JSON</a> · <a href="/api/admin/stats" target="_blank">查看统计 JSON</a></p>
  </section>
  <section>
    <h2>最近用量</h2>
    <pre>{html.escape(json.dumps(db.list_usage(limit=30), ensure_ascii=False, indent=2))}</pre>
  </section>
</body>
</html>"""


async def form_data(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    return {key: values[-1] for key, values in parse_qs(body).items()}


@app.get("/", response_class=HTMLResponse)
def root(request: Request) -> Response:
    if auth.optional_current_user(request):
        return RedirectResponse("/console", status_code=302)
    return RedirectResponse("/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request) -> Response:
    if auth.optional_current_user(request):
        return RedirectResponse("/console", status_code=302)
    return HTMLResponse(login_page())


@app.post("/login")
async def login_post(request: Request) -> Response:
    form = await form_data(request)
    username = form.get("username", "").strip()
    password = form.get("password", "")
    user = db.get_user_by_username(username)
    if not user or user["status"] != "active" or not auth.verify_password(password, user["password_hash"]):
        return HTMLResponse(login_page("账号或密码错误，或账号已禁用。"), status_code=401)
    with db.connect() as conn:
        conn.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
    response = RedirectResponse("/console", status_code=302)
    response.set_cookie(auth.COOKIE_NAME, auth.create_token(int(user["id"])), httponly=True, samesite="lax")
    return response


@app.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@app.get("/console", response_class=HTMLResponse)
def console(user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    return HTMLResponse(console_page(user))


@app.get("/admin", response_class=HTMLResponse)
def admin(user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    auth.require_admin(user)
    return HTMLResponse(admin_page(user))


@app.post("/admin/users")
async def admin_create_user(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    auth.require_admin(user)
    form = await form_data(request)
    try:
        db.create_user(
            username=form.get("username", "").strip(),
            password_hash=auth.hash_password(form.get("password", "")),
            role=form.get("role", "user"),
            monthly_quota=int(form.get("monthly_quota") or 500),
        )
    except Exception as exc:
        return HTMLResponse(f"创建用户失败：{html.escape(str(exc))}<br><a href='/admin'>返回</a>", status_code=400)
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/users/{user_id}/quota")
async def admin_update_quota(user_id: int, request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    require_can_manage_user(user, user_id)
    form = await form_data(request)
    db.update_user(user_id, monthly_quota=int(form.get("monthly_quota") or 0))
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/users/{user_id}/status")
async def admin_update_status(user_id: int, request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    target = require_can_manage_user(user, user_id)
    form = await form_data(request)
    status_value = form.get("status", "active")
    if status_value not in {"active", "disabled"}:
        raise HTTPException(status_code=400, detail="状态无效。")
    if int(target["id"]) == int(user["id"]) and status_value == "disabled":
        raise HTTPException(status_code=400, detail="不能禁用当前登录账号。")
    db.update_user(user_id, status=status_value)
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/users/{user_id}/password")
async def admin_reset_password(user_id: int, request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    require_can_manage_user(user, user_id)
    form = await form_data(request)
    password = form.get("password", "")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位。")
    db.update_user(user_id, password_hash=auth.hash_password(password))
    return RedirectResponse("/admin", status_code=302)


@app.post("/admin/users/{user_id}/quota/reset")
def admin_reset_quota(user_id: int, user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    require_can_manage_user(user, user_id)
    db.reset_user_quota(user_id)
    return RedirectResponse("/admin", status_code=302)


@app.get("/api/auth/me")
def api_me(user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    return {key: user[key] for key in ["id", "username", "role", "status", "display_name", "created_at", "last_login_at"]}


@app.get("/api/quota/me")
def api_quota_me(user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    return db.quota_for_user(int(user["id"]))


@app.get("/api/usage/me")
def api_usage_me(user: dict[str, Any] = Depends(auth.current_user)) -> list[dict[str, Any]]:
    return db.list_usage(int(user["id"]))


@app.get("/api/admin/users")
def api_admin_users(user: dict[str, Any] = Depends(auth.current_user)) -> list[dict[str, Any]]:
    auth.require_admin(user)
    return db.list_users()


@app.get("/api/admin/usage")
def api_admin_usage(user: dict[str, Any] = Depends(auth.current_user)) -> list[dict[str, Any]]:
    auth.require_admin(user)
    return db.list_usage()


@app.get("/api/admin/jobs")
def api_admin_jobs(
    user_id: int | None = None,
    status: str = "",
    job_type: str = "",
    limit: int = 50,
    user: dict[str, Any] = Depends(auth.current_user),
) -> list[dict[str, Any]]:
    auth.require_admin(user)
    return db.list_jobs(user_id=user_id, status=status, job_type=job_type, limit=limit)


@app.get("/api/admin/stats")
def api_admin_stats(user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    auth.require_admin(user)
    return db.admin_stats()


@app.get("/api/state")
def api_state(user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    return {
        "projects": list_user_projects(user),
        "configs": legacy_web._list_configs(),
        "jobs": db_jobs_for_user(user),
        "workshop_jobs": filter_jobs_for_user(user, legacy_web._workshop_job_list()),
        "outputs": latest_outputs_for_user(user),
        "deploy_user": api_me(user),
        "quota": db.quota_for_user(int(user["id"])),
    }


@app.get("/api/project")
def api_project(path: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    try:
        return read_project_for_user(user, path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/project")
async def api_project_save(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    payload = await request.json()
    try:
        return save_project_for_user(user, payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/project/preview")
async def api_project_preview(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    del user
    payload = await request.json()
    try:
        return legacy_web._preview_project_file(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/script/import")
async def api_script_import(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    payload = await request.json()
    units = quota_units("script_import")
    event_id = 0
    try:
        event_id = db.reserve_quota(int(user["id"]), units, "script_import")
        result = legacy_web._import_script(payload)
        legacy_web._write_web_api_log("script_import", payload, result=result)
        db.finish_usage_event(event_id, "success")
        return result
    except Exception as exc:
        if event_id:
            db.finish_usage_event(event_id, "refunded")
        log_path = legacy_web._write_web_api_log("script_import", payload, error=exc)
        raise HTTPException(status_code=400, detail=f"{exc}；日志：{log_path}") from exc


@app.post("/api/script/workshop")
async def api_script_workshop(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    payload = await request.json()
    units = quota_units("script_workshop")
    event_id = 0
    try:
        event_id = db.reserve_quota(int(user["id"]), units, "script_workshop")
        payload["_projects_dir"] = relative(user_project_dir(user))
        job = legacy_web._start_workshop_job(payload)
        db.record_job(
            str(job["id"]),
            int(user["id"]),
            "script_workshop",
            payload_json=json.dumps(payload, ensure_ascii=False),
            log_path=str(job.get("log_path") or ""),
            reserved_units=units,
        )
        db.finish_usage_event(event_id, "success")
        return job
    except Exception as exc:
        if event_id:
            db.finish_usage_event(event_id, "refunded")
        log_path = legacy_web._write_web_api_log("script_workshop", payload, error=exc)
        raise HTTPException(status_code=400, detail=f"{exc}；日志：{log_path}") from exc


@app.post("/api/script/file")
async def api_script_file(file: UploadFile, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    del user
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File is too large. Please upload a file under 8MB.")
    try:
        content = legacy_web._extract_uploaded_text(Path(file.filename or "script.txt").name, data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"filename": file.filename, "content": content, "chars": len(content)}


@app.get("/api/script/workshop/jobs/{job_id}")
def api_workshop_job(job_id: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    require_job_access(user, job_id)
    return legacy_web._workshop_job_detail(job_id)


@app.post("/api/script/workshop/jobs/{job_id}/cancel")
def api_workshop_cancel(job_id: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    require_job_access(user, job_id)
    try:
        return legacy_web._cancel_workshop_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/jobs")
async def api_jobs_start(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    payload = await request.json()
    action = str(payload.get("action") or "stage")
    stages = str(payload.get("stages") or "")
    units = quota_units(action, stages)
    try:
        return start_db_job(user, payload, units)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs")
def api_jobs_list(user: dict[str, Any] = Depends(auth.current_user)) -> list[dict[str, Any]]:
    return db_jobs_for_user(user)


@app.post("/api/jobs/{job_id}/cancel")
def api_job_cancel(job_id: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    require_job_access(user, job_id)
    try:
        return cancel_db_job(job_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    require_job_access(user, job_id)
    job = db.get_job(job_id)
    if not job:
        return {"id": job_id, "status": "missing", "log": ""}
    return db_job_payload(job, include_log=True)


@app.get("/api/file")
def api_file(path: str, user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    try:
        file_path = legacy_web._safe_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    if not user_can_access_file(user, file_path):
        raise HTTPException(status_code=403, detail="没有权限访问该文件。")
    return FileResponse(file_path)
