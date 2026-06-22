from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

from fastapi import Cookie, HTTPException, Request, status

from . import db


COOKIE_NAME = "ai_manga_session"
TOKEN_TTL_SEC = 60 * 60 * 24 * 7


def secret_key() -> str:
    return os.getenv("AI_MANGA_SECRET_KEY") or "local-dev-change-me"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    return f"pbkdf2_sha256${salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt, expected = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 240_000)
    actual = base64.b64encode(digest).decode("ascii")
    return hmac.compare_digest(actual, expected)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def create_token(user_id: int) -> str:
    payload = {
        "uid": user_id,
        "exp": int(time.time()) + TOKEN_TTL_SEC,
        "nonce": secrets.token_hex(8),
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret_key().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64(signature)}"


def parse_token(token: str) -> dict[str, Any] | None:
    try:
        body, signature = token.split(".", 1)
    except ValueError:
        return None
    expected = _b64(hmac.new(secret_key().encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if int(payload.get("exp") or 0) < int(time.time()):
        return None
    return payload


def current_user(ai_manga_session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> dict[str, Any]:
    if not ai_manga_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")
    payload = parse_token(ai_manga_session)
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session.")
    user = db.get_user_by_id(int(payload["uid"]))
    if not user or user["status"] != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User is disabled or missing.")
    return user


def optional_current_user(request: Request) -> dict[str, Any] | None:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    payload = parse_token(token)
    if not payload:
        return None
    user = db.get_user_by_id(int(payload["uid"]))
    if not user or user["status"] != "active":
        return None
    return user


def require_admin(user: dict[str, Any]) -> dict[str, Any]:
    if user["role"] not in {"super_admin", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin permission required.")
    return user


def bootstrap_admin() -> None:
    username = os.getenv("AI_MANGA_ADMIN_USERNAME", "admin")
    password = os.getenv("AI_MANGA_ADMIN_PASSWORD", "admin123456")
    display_name = os.getenv("AI_MANGA_ADMIN_DISPLAY_NAME", "系统管理员")
    monthly_quota = int(os.getenv("AI_MANGA_ADMIN_MONTHLY_QUOTA", "10000"))
    with db.connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        if row and int(row["count"]) > 0:
            return
        cursor = conn.execute(
            """
            INSERT INTO users (username, password_hash, role, status, display_name)
            VALUES (?, ?, 'super_admin', 'active', ?)
            """,
            (username, hash_password(password), display_name),
        )
        db.ensure_quota(conn, int(cursor.lastrowid), monthly_quota=monthly_quota)

