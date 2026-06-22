from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

from manga_flow.schemas import ProviderConfig


DEFAULT_ENDPOINT = "https://tts.tencentcloudapi.com"
DEFAULT_ACTION = "TextToVoice"
DEFAULT_VERSION = "2019-08-23"
DEFAULT_SERVICE = "tts"


class TencentCloudTTSConfigError(RuntimeError):
    """Raised when Tencent Cloud TTS credentials are not configured."""


class TencentCloudTTSError(RuntimeError):
    """Raised when Tencent Cloud returns a TTS API error."""

    def __init__(self, code: str, message: str, request_id: str | None = None) -> None:
        suffix = f" request_id={request_id}" if request_id else ""
        super().__init__(f"Tencent Cloud TTS error {code}: {message}{suffix}")
        self.code = code
        self.message = message
        self.request_id = request_id


class TencentCloudTTSClient:
    """Minimal Tencent Cloud API 3.0 client for TextToVoice.

    The project only needs one API action, so implementing TC3 signing directly
    keeps the workflow independent from a vendor SDK install.
    """

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        endpoint: str = DEFAULT_ENDPOINT,
        region: str = "",
        timeout: float = 120.0,
    ) -> None:
        if not secret_id or not secret_key:
            raise TencentCloudTTSConfigError("Missing Tencent Cloud SecretId or SecretKey.")
        self.secret_id = secret_id
        self.secret_key = secret_key
        self.endpoint = endpoint.rstrip("/") or DEFAULT_ENDPOINT
        self.region = region
        self.timeout = timeout

    @classmethod
    def from_provider(cls, provider: ProviderConfig, env_path: Path | None = None) -> "TencentCloudTTSClient":
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()
        extra = provider.extra
        secret_id_env = str(extra.get("secret_id_env") or "TENCENTCLOUD_SECRET_ID")
        secret_key_env = str(extra.get("secret_key_env") or provider.api_key_env or "TENCENTCLOUD_SECRET_KEY")
        endpoint_env = str(extra.get("endpoint_env") or "TENCENTCLOUD_TTS_ENDPOINT")
        region_env = str(extra.get("region_env") or "TENCENTCLOUD_TTS_REGION")
        return cls(
            secret_id=os.getenv(secret_id_env, ""),
            secret_key=os.getenv(secret_key_env, ""),
            endpoint=os.getenv(endpoint_env, "") or provider.base_url or DEFAULT_ENDPOINT,
            region=os.getenv(region_env, "") or str(extra.get("region") or ""),
            timeout=float(extra.get("timeout_sec", 120.0)),
        )

    def voice_type_for(
        self,
        provider: ProviderConfig,
        speaker: str | None = None,
        character_id: str | None = None,
    ) -> int:
        extra = provider.extra
        character_map = extra.get("character_voice_map", {})
        voice_map = extra.get("voice_map", {})
        if character_id and isinstance(character_map, dict) and character_id in character_map:
            return int(character_map[character_id])
        if speaker and isinstance(voice_map, dict) and speaker in voice_map:
            return int(voice_map[speaker])
        return int(extra.get("default_voice_type", 0))

    def emotion_for(self, provider: ProviderConfig, emotion: str | None = None) -> str | None:
        if not provider.extra.get("enable_emotion", False):
            return None
        if not emotion:
            return None
        emotion_map = provider.extra.get("emotion_map", {})
        if isinstance(emotion_map, dict):
            return emotion_map.get(emotion) or emotion_map.get(str(emotion).strip())
        return None

    def create_speech(
        self,
        provider: ProviderConfig,
        text: str,
        speaker: str | None = None,
        character_id: str | None = None,
        emotion: str | None = None,
        session_id: str | None = None,
        **overrides: Any,
    ) -> bytes:
        extra = provider.extra
        emotion_category = overrides.pop("emotion_category", None) or self.emotion_for(provider, emotion)
        payload: dict[str, Any] = {
            "Text": text,
            "SessionId": session_id or f"manga-flow-{uuid.uuid4().hex}",
            "Volume": extra.get("volume", 0),
            "Speed": extra.get("speed", 0),
            "ProjectId": extra.get("project_id", 0),
            "ModelType": extra.get("model_type", 1),
            "VoiceType": overrides.pop("voice_type", None) or self.voice_type_for(provider, speaker, character_id),
            "PrimaryLanguage": extra.get("primary_language", 1),
            "SampleRate": extra.get("sample_rate", 24000),
            "Codec": extra.get("codec", "mp3"),
            "EnableSubtitle": extra.get("enable_subtitle", False),
        }
        if "segment_rate" in extra:
            payload["SegmentRate"] = extra["segment_rate"]
        if emotion_category:
            payload["EmotionCategory"] = emotion_category
            payload["EmotionIntensity"] = int(extra.get("emotion_intensity", 100))
        if extra.get("include_app_id", False):
            app_id_env = str(extra.get("app_id_env") or "TENCENTCLOUD_TTS_APP_ID")
            app_id = os.getenv(app_id_env, "")
            if app_id:
                payload["AppId"] = int(app_id)
        payload.update(_without_none(overrides))

        result = self._post(provider, payload)
        response = result.get("Response", result)
        error = response.get("Error")
        if error:
            raise TencentCloudTTSError(
                str(error.get("Code", "Unknown")),
                str(error.get("Message", "")),
                str(response.get("RequestId", "")) or None,
            )
        audio_b64 = response.get("Audio")
        if not audio_b64:
            raise TencentCloudTTSError(
                "MissingAudio",
                f"TextToVoice response did not include audio. request_id={response.get('RequestId', '')}",
                str(response.get("RequestId", "")) or None,
            )
        return base64.b64decode(audio_b64)

    def _post(self, provider: ProviderConfig, payload: dict[str, Any]) -> dict[str, Any]:
        action = provider.endpoint or DEFAULT_ACTION
        version = str(provider.extra.get("version") or DEFAULT_VERSION)
        service = str(provider.extra.get("service") or DEFAULT_SERVICE)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        timestamp = int(time.time())
        headers = self._headers(body, action=action, version=version, service=service, timestamp=timestamp)
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self.endpoint, headers=headers, content=body.encode("utf-8"))
        try:
            data = response.json()
        except json.JSONDecodeError:
            response.raise_for_status()
            raise TencentCloudTTSError("InvalidResponse", response.text[:500])
        if not response.is_success and not data.get("Response", {}).get("Error"):
            response.raise_for_status()
        return data

    def _headers(self, body: str, action: str, version: str, service: str, timestamp: int) -> dict[str, str]:
        parsed = urlparse(self.endpoint)
        host = parsed.netloc
        date = time.strftime("%Y-%m-%d", time.gmtime(timestamp))
        content_type = "application/json; charset=utf-8"
        canonical_headers = (
            f"content-type:{content_type}\n"
            f"host:{host}\n"
            f"x-tc-action:{action.lower()}\n"
        )
        signed_headers = "content-type;host;x-tc-action"
        hashed_request_payload = hashlib.sha256(body.encode("utf-8")).hexdigest()
        canonical_request = "\n".join(
            [
                "POST",
                parsed.path or "/",
                "",
                canonical_headers,
                signed_headers,
                hashed_request_payload,
            ]
        )
        credential_scope = f"{date}/{service}/tc3_request"
        string_to_sign = "\n".join(
            [
                "TC3-HMAC-SHA256",
                str(timestamp),
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signature = _sign(_sign(_sign(("TC3" + self.secret_key).encode("utf-8"), date), service), "tc3_request")
        signature_hex = hmac.new(signature, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "TC3-HMAC-SHA256 "
            f"Credential={self.secret_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature_hex}"
        )
        headers = {
            "Authorization": authorization,
            "Content-Type": content_type,
            "Host": host,
            "X-TC-Action": action,
            "X-TC-Timestamp": str(timestamp),
            "X-TC-Version": version,
        }
        if self.region:
            headers["X-TC-Region"] = self.region
        return headers


def _sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _without_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
