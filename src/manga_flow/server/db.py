from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any


ROOT = Path.cwd().resolve()
DEFAULT_DB_PATH = ROOT / "data" / "server" / "ai_manga.sqlite3"


def database_path() -> Path:
    return Path(os.getenv("AI_MANGA_DB_PATH", str(DEFAULT_DB_PATH))).expanduser().resolve()


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('super_admin', 'admin', 'user')),
                status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'disabled')),
                display_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_login_at TEXT
            );

            CREATE TABLE IF NOT EXISTS user_quotas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
                monthly_quota INTEGER NOT NULL DEFAULT 500,
                used_quota INTEGER NOT NULL DEFAULT 0,
                reserved_quota INTEGER NOT NULL DEFAULT 0,
                reset_cycle TEXT NOT NULL DEFAULT 'monthly',
                reset_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                job_id TEXT,
                provider TEXT NOT NULL DEFAULT '',
                model_name TEXT NOT NULL DEFAULT '',
                action_type TEXT NOT NULL,
                estimated_units INTEGER NOT NULL,
                actual_units INTEGER NOT NULL DEFAULT 0,
                raw_cost REAL,
                status TEXT NOT NULL CHECK(status IN ('reserved', 'success', 'failed', 'refunded')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                project_id TEXT NOT NULL DEFAULT '',
                input_payload_json TEXT NOT NULL DEFAULT '{}',
                output_path TEXT NOT NULL DEFAULT '',
                log_path TEXT NOT NULL DEFAULT '',
                reserved_units INTEGER NOT NULL DEFAULT 0,
                actual_units INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT,
                finished_at TEXT
            );

            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                yaml_path TEXT NOT NULL,
                output_dir TEXT NOT NULL DEFAULT '',
                visibility TEXT NOT NULL DEFAULT 'private',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, yaml_path)
            );
            """
        )


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())


def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        return row_to_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def ensure_quota(conn: sqlite3.Connection, user_id: int, monthly_quota: int = 500) -> None:
    conn.execute(
        """
        INSERT INTO user_quotas (user_id, monthly_quota)
        VALUES (?, ?)
        ON CONFLICT(user_id) DO NOTHING
        """,
        (user_id, monthly_quota),
    )


def quota_for_user(user_id: int) -> dict[str, Any]:
    with connect() as conn:
        ensure_quota(conn, user_id)
        row = conn.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,)).fetchone()
        assert row is not None
        payload = dict(row)
    payload["available_quota"] = payload["monthly_quota"] - payload["used_quota"] - payload["reserved_quota"]
    return payload


def list_users() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT users.id, users.username, users.role, users.status, users.display_name,
                   users.created_at, users.last_login_at,
                   user_quotas.monthly_quota, user_quotas.used_quota, user_quotas.reserved_quota
            FROM users
            LEFT JOIN user_quotas ON user_quotas.user_id = users.id
            ORDER BY users.id
            """
        ).fetchall()
        return [dict(row) for row in rows]


def create_user(
    username: str,
    password_hash: str,
    role: str = "user",
    display_name: str = "",
    monthly_quota: int = 500,
) -> dict[str, Any]:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO users (username, password_hash, role, status, display_name)
            VALUES (?, ?, ?, 'active', ?)
            """,
            (username, password_hash, role, display_name),
        )
        user_id = int(cursor.lastrowid)
        ensure_quota(conn, user_id, monthly_quota=monthly_quota)
    user = get_user_by_id(user_id)
    assert user is not None
    return user


def update_user(
    user_id: int,
    *,
    role: str | None = None,
    status: str | None = None,
    display_name: str | None = None,
    password_hash: str | None = None,
    monthly_quota: int | None = None,
) -> dict[str, Any]:
    fields: list[str] = []
    values: list[Any] = []
    if role is not None:
        fields.append("role = ?")
        values.append(role)
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if display_name is not None:
        fields.append("display_name = ?")
        values.append(display_name)
    if password_hash is not None:
        fields.append("password_hash = ?")
        values.append(password_hash)
    with connect() as conn:
        if fields:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", (*values, user_id))
        if monthly_quota is not None:
            ensure_quota(conn, user_id, monthly_quota=monthly_quota)
            conn.execute(
                "UPDATE user_quotas SET monthly_quota = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (monthly_quota, user_id),
            )
    user = get_user_by_id(user_id)
    if not user:
        raise ValueError(f"User does not exist: {user_id}")
    return user


def reset_user_quota(user_id: int) -> None:
    with connect() as conn:
        ensure_quota(conn, user_id)
        conn.execute(
            """
            UPDATE user_quotas
            SET used_quota = 0,
                reserved_quota = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (user_id,),
        )


def reserve_quota(user_id: int, units: int, action_type: str, job_id: str | None = None) -> int:
    if units <= 0:
        return 0
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ensure_quota(conn, user_id)
        quota = conn.execute("SELECT * FROM user_quotas WHERE user_id = ?", (user_id,)).fetchone()
        assert quota is not None
        available = int(quota["monthly_quota"]) - int(quota["used_quota"]) - int(quota["reserved_quota"])
        if available < units:
            conn.execute("ROLLBACK")
            raise ValueError(f"额度不足：需要 {units} 点，当前可用 {available} 点。")
        conn.execute(
            """
            UPDATE user_quotas
            SET reserved_quota = reserved_quota + ?, updated_at = CURRENT_TIMESTAMP
            WHERE user_id = ?
            """,
            (units, user_id),
        )
        cursor = conn.execute(
            """
            INSERT INTO usage_events (user_id, job_id, action_type, estimated_units, status)
            VALUES (?, ?, ?, ?, 'reserved')
            """,
            (user_id, job_id or "", action_type, units),
        )
        event_id = int(cursor.lastrowid)
        conn.execute("COMMIT")
        return event_id


def finish_usage_event(event_id: int, status: str, actual_units: int | None = None) -> None:
    if not event_id:
        return
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        event = conn.execute("SELECT * FROM usage_events WHERE id = ?", (event_id,)).fetchone()
        if not event or event["status"] != "reserved":
            conn.execute("ROLLBACK")
            return
        estimated = int(event["estimated_units"])
        actual = estimated if actual_units is None else max(0, int(actual_units))
        user_id = int(event["user_id"])
        if status == "success":
            conn.execute(
                """
                UPDATE user_quotas
                SET reserved_quota = MAX(0, reserved_quota - ?),
                    used_quota = used_quota + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (estimated, actual, user_id),
            )
        else:
            conn.execute(
                """
                UPDATE user_quotas
                SET reserved_quota = MAX(0, reserved_quota - ?),
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (estimated, user_id),
            )
        conn.execute(
            """
            UPDATE usage_events
            SET status = ?, actual_units = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, actual if status == "success" else 0, event_id),
        )
        conn.execute("COMMIT")


def record_job(
    job_id: str,
    user_id: int,
    job_type: str,
    *,
    project_id: str = "",
    payload_json: str = "{}",
    log_path: str = "",
    reserved_units: int = 0,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO jobs
                (id, user_id, job_type, status, project_id, input_payload_json, log_path, reserved_units)
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            """,
            (job_id, user_id, job_type, project_id, payload_json, log_path, reserved_units),
        )


def update_job_status(job_id: str, status: str, *, error_message: str = "", output_path: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?,
                error_message = ?,
                output_path = COALESCE(NULLIF(?, ''), output_path),
                finished_at = CASE WHEN ? IN ('success', 'failed', 'canceled') THEN CURRENT_TIMESTAMP ELSE finished_at END
            WHERE id = ?
            """,
            (status, error_message, output_path, status, job_id),
        )


def list_usage(user_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT usage_events.*, users.username
                FROM usage_events
                JOIN users ON users.id = usage_events.user_id
                ORDER BY usage_events.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT usage_events.*, users.username
                FROM usage_events
                JOIN users ON users.id = usage_events.user_id
                WHERE usage_events.user_id = ?
                ORDER BY usage_events.id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]


def list_jobs(user_id: int | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT jobs.*, users.username
                FROM jobs
                JOIN users ON users.id = jobs.user_id
                ORDER BY jobs.created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT jobs.*, users.username
                FROM jobs
                JOIN users ON users.id = jobs.user_id
                WHERE jobs.user_id = ?
                ORDER BY jobs.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]


def get_job(job_id: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT jobs.*, users.username
            FROM jobs
            JOIN users ON users.id = jobs.user_id
            WHERE jobs.id = ?
            """,
            (job_id,),
        ).fetchone()
        return row_to_dict(row)


def user_job_ids(user_id: int) -> set[str]:
    with connect() as conn:
        rows = conn.execute("SELECT id FROM jobs WHERE user_id = ?", (user_id,)).fetchall()
        return {str(row["id"]) for row in rows}


def upsert_project(user_id: int, name: str, yaml_path: str, output_dir: str = "", visibility: str = "private") -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO projects (user_id, name, yaml_path, output_dir, visibility)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, yaml_path) DO UPDATE SET
                name = excluded.name,
                output_dir = excluded.output_dir,
                visibility = excluded.visibility,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, name, yaml_path, output_dir, visibility),
        )


def list_projects(user_id: int | None = None) -> list[dict[str, Any]]:
    with connect() as conn:
        if user_id is None:
            rows = conn.execute(
                """
                SELECT projects.*, users.username
                FROM projects
                JOIN users ON users.id = projects.user_id
                ORDER BY projects.updated_at DESC
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT projects.*, users.username
                FROM projects
                JOIN users ON users.id = projects.user_id
                WHERE projects.user_id = ?
                ORDER BY projects.updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def next_job_hint() -> str:
    return uuid.uuid4().hex[:12]
