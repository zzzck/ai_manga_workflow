from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from manga_flow.cli import app as cli_app


def load_server(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_MANGA_DB_PATH", str(tmp_path / "server.sqlite3"))
    monkeypatch.setenv("AI_MANGA_SECRET_KEY", "test-secret-key-for-server-tests")
    monkeypatch.setenv("AI_MANGA_ADMIN_PASSWORD", "adminpass123")
    monkeypatch.setenv("SILICONFLOW_API_KEY", "sk-test-should-not-leak")

    for module_name in [
        "manga_flow.web",
        "manga_flow.server.db",
        "manga_flow.server.auth",
        "manga_flow.server.app",
    ]:
        if module_name in sys.modules:
            importlib.reload(sys.modules[module_name])
        else:
            importlib.import_module(module_name)
    return sys.modules["manga_flow.server.app"], sys.modules["manga_flow.server.db"]


def login_admin(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"username": "admin", "password": "adminpass123"},
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}


def test_project_resource_api_uses_user_workspace(tmp_path: Path, monkeypatch) -> None:
    app_mod, _ = load_server(tmp_path, monkeypatch)

    with TestClient(app_mod.app) as client:
        assert client.get("/api/projects").status_code == 401
        login_admin(client)

        created = client.post("/api/projects", json={"name": "部署测试剧本"})
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["path"].startswith("data/users/1/projects/")
        assert body["data"]["title"] == "部署测试剧本"

        listed = client.get("/api/projects")
        assert listed.status_code == 200, listed.text
        assert any(item["path"] == body["path"] and item["valid"] for item in listed.json()["projects"])

        updated = client.put(
            f"/api/projects/{body['path']}",
            json={"data": {**body["data"], "logline": "更新后的故事。"}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["logline"] == "更新后的故事。"


def test_workshop_db_result_recovers_from_project_yaml(tmp_path: Path, monkeypatch) -> None:
    app_mod, db = load_server(tmp_path, monkeypatch)

    with TestClient(app_mod.app) as client:
        login_admin(client)
        created = client.post("/api/projects", json={"name": "工坊恢复测试"})
        assert created.status_code == 200, created.text
        project_path = created.json()["path"]

        job_id = "workshop_recovery_test"
        log_path = tmp_path / "outputs" / "users" / "1" / "web_api" / "script_workshop_test" / "generation.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("DONE saved draft\n", encoding="utf-8")
        db.record_job(
            job_id,
            1,
            "script_workshop",
            payload_json=json.dumps({"theme": "恢复测试"}, ensure_ascii=False),
            log_path=str(log_path),
            reserved_units=20,
        )
        db.finish_job(job_id, "success", actual_units=20, output_path=project_path)

        state = client.get("/api/state")
        assert state.status_code == 200, state.text
        recovered = [job for job in state.json()["workshop_jobs"] if job["id"] == job_id]
        assert recovered and recovered[0]["status"] == "done"
        assert recovered[0]["has_result"] is True

        detail = client.get(f"/api/script/workshop/jobs/{job_id}")
        assert detail.status_code == 200, detail.text
        payload = detail.json()
        assert payload["result"]["data"]["title"] == "工坊恢复测试"
        assert "DONE saved draft" in payload["log"]


def test_job_capacity_rejects_without_quota_reservation(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AI_MANGA_MAX_ACTIVE_JOBS_PER_USER", "1")
    monkeypatch.setenv("AI_MANGA_MAX_ACTIVE_JOBS_TOTAL", "1")
    app_mod, db = load_server(tmp_path, monkeypatch)

    with TestClient(app_mod.app) as client:
        login_admin(client)
        db.record_job("active_job", 1, "stage", payload_json="{}", reserved_units=5)
        db.mark_job_running("active_job")
        before = db.quota_for_user(1)

        response = client.post("/api/script/workshop", json={"theme": "并发限制测试"})
        assert response.status_code == 400, response.text
        assert "上限为 1" in response.text
        after = db.quota_for_user(1)
        assert after["reserved_quota"] == before["reserved_quota"]


def test_admin_server_info_does_not_leak_api_keys(tmp_path: Path, monkeypatch) -> None:
    app_mod, _ = load_server(tmp_path, monkeypatch)

    with TestClient(app_mod.app) as client:
        assert client.get("/api/admin/server-info").status_code == 401
        login_admin(client)

        response = client.get("/api/admin/server-info")
        assert response.status_code == 200, response.text
        assert "sk-test-should-not-leak" not in response.text
        body = response.json()
        assert body["healthz"] == "/healthz"
        assert "backup-server" in body["commands"]["backup"]

        admin_page = client.get("/admin")
        assert admin_page.status_code == 200
        assert "运行状态" in admin_page.text
        assert "sk-test-should-not-leak" not in admin_page.text


def test_backup_restore_requires_explicit_apply_and_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AI_MANGA_DB_PATH", str(tmp_path / "data" / "server" / "ai_manga.sqlite3"))

    db_path = tmp_path / "data" / "server" / "ai_manga.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE smoke (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO smoke (name) VALUES ('backup')")
        conn.commit()
    finally:
        conn.close()

    (tmp_path / "data" / "projects").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "projects" / "demo.yaml").write_text("title: backup\n", encoding="utf-8")
    (tmp_path / "data" / "users" / "1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "users" / "1" / "note.txt").write_text("user backup\n", encoding="utf-8")
    (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "outputs" / "result.txt").write_text("output backup\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=backup\n", encoding="utf-8")

    runner = CliRunner()
    backup_result = runner.invoke(cli_app, ["backup-server", "--output", "backups", "--include-env"])
    assert backup_result.exit_code == 0, backup_result.output
    backup_zip = next((tmp_path / "backups").glob("ai_manga_backup_*.zip"))

    (tmp_path / "data" / "projects" / "demo.yaml").write_text("title: changed\n", encoding="utf-8")
    preview = runner.invoke(cli_app, ["restore-server-backup", str(backup_zip), "--include-env"])
    assert preview.exit_code == 1, preview.output
    assert "Conflicts" in preview.output
    assert (tmp_path / "data" / "projects" / "demo.yaml").read_text(encoding="utf-8") == "title: changed\n"

    restored = runner.invoke(cli_app, ["restore-server-backup", str(backup_zip), "--apply", "--force", "--include-env"])
    assert restored.exit_code == 0, restored.output
    assert (tmp_path / "data" / "projects" / "demo.yaml").read_text(encoding="utf-8") == "title: backup\n"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=backup\n"
