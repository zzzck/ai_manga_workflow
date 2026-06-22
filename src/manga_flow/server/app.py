from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from dotenv import load_dotenv

from manga_flow import web as legacy_web

from . import auth, db


app = FastAPI(title="AI Manga Workflow Server")


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


def sync_legacy_job_status(payload: dict[str, Any]) -> None:
    legacy_status = str(payload.get("status") or "")
    status_map = {"done": "success", "failed": "failed", "running": "running", "queued": "queued"}
    db_status = status_map.get(legacy_status)
    if db_status:
        db.update_job_status(str(payload.get("id") or ""), db_status, error_message=str(payload.get("error") or ""))


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
        rows.append(
            f"<tr><td>{item['id']}</td><td>{html.escape(item['username'])}</td><td>{html.escape(item['role'])}</td>"
            f"<td>{html.escape(item['status'])}</td><td>{item.get('monthly_quota') or 0}</td>"
            f"<td>{item.get('used_quota') or 0}</td><td>{item.get('reserved_quota') or 0}</td><td>{available}</td></tr>"
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
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th, td {{ text-align:left; border-bottom:1px solid #e5e7eb; padding:8px; }}
    form {{ display:grid; grid-template-columns:repeat(5, minmax(120px, 1fr)); gap:10px; align-items:end; }}
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
      <thead><tr><th>ID</th><th>账号</th><th>角色</th><th>状态</th><th>总额</th><th>已用</th><th>预扣</th><th>可用</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
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
def api_admin_jobs(user: dict[str, Any] = Depends(auth.current_user)) -> list[dict[str, Any]]:
    auth.require_admin(user)
    return db.list_jobs()


@app.get("/api/state")
def api_state(user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    jobs = legacy_web._job_list()
    for item in jobs:
        sync_legacy_job_status(item)
    return {
        "projects": legacy_web._list_projects(),
        "configs": legacy_web._list_configs(),
        "jobs": jobs,
        "workshop_jobs": legacy_web._workshop_job_list(),
        "outputs": legacy_web._latest_outputs(),
        "deploy_user": api_me(user),
        "quota": db.quota_for_user(int(user["id"])),
    }


@app.get("/api/project")
def api_project(path: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    del user
    try:
        return legacy_web._read_project_file(path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/project")
async def api_project_save(request: Request, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    del user
    payload = await request.json()
    try:
        return legacy_web._save_project_file(payload)
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
    del user
    return legacy_web._workshop_job_detail(job_id)


@app.post("/api/script/workshop/jobs/{job_id}/cancel")
def api_workshop_cancel(job_id: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    del user
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
    event_id = 0
    try:
        event_id = db.reserve_quota(int(user["id"]), units, action_type=action)
        job = legacy_web._start_job(payload)
        db.record_job(
            job.id,
            int(user["id"]),
            action,
            project_id=str(payload.get("project") or ""),
            payload_json=json.dumps(payload, ensure_ascii=False),
            log_path=job.log_path,
            reserved_units=units,
        )
        db.finish_usage_event(event_id, "success")
        return legacy_web.asdict(job)
    except Exception as exc:
        if event_id:
            db.finish_usage_event(event_id, "refunded")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str, user: dict[str, Any] = Depends(auth.current_user)) -> dict[str, Any]:
    del user
    payload = legacy_web._job_detail(job_id)
    sync_legacy_job_status(payload)
    return payload


@app.get("/api/file")
def api_file(path: str, user: dict[str, Any] = Depends(auth.current_user)) -> Response:
    del user
    try:
        file_path = legacy_web._safe_path(path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(file_path)
