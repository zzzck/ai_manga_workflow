from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from manga_flow.schemas import ProviderConfig


DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"


class ProviderNotConfiguredError(RuntimeError):
    """Raised when a provider slot is missing credentials or endpoint config."""


class SiliconFlowClient:
    """Small REST client for SiliconFlow provider slots.

    The workflow keeps vendor details in YAML. This client intentionally stays
    thin so each production step can call the exact SiliconFlow endpoint it needs.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 120.0,
    ) -> None:
        if not api_key:
            raise ProviderNotConfiguredError("Missing SiliconFlow API key.")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @classmethod
    def from_provider(cls, provider: ProviderConfig, env_path: Path | None = None) -> "SiliconFlowClient":
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()
        api_key = os.getenv(provider.api_key_env or "SILICONFLOW_API_KEY", "")
        base_url = os.getenv("SILICONFLOW_BASE_URL") or provider.base_url or DEFAULT_BASE_URL
        return cls(api_key=api_key, base_url=base_url)

    @staticmethod
    def model_name(provider: ProviderConfig) -> str:
        model_env = provider.extra.get("model_env")
        if isinstance(model_env, str) and model_env:
            return os.getenv(model_env, "") or provider.model
        return provider.model

    def chat_completion(
        self,
        provider: ProviderConfig,
        messages: list[dict[str, Any]],
        **overrides: Any,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model_name(provider),
            "messages": messages,
            "temperature": provider.extra.get("temperature", 0.7),
            "top_p": provider.extra.get("top_p", 0.9),
            "max_tokens": provider.extra.get("max_tokens", 4096),
        }
        response_format = provider.extra.get("response_format")
        if response_format:
            payload["response_format"] = response_format
        payload.update(_without_none(overrides))
        return self._post_json(provider.endpoint or "/chat/completions", payload)

    def vision_completion(
        self,
        provider: ProviderConfig,
        image_url: str,
        prompt: str,
        detail: str | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        image_detail = detail or provider.extra.get("image_detail", "high")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url, "detail": image_detail}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        return self.chat_completion(provider, messages=messages, **overrides)

    def generate_image(
        self,
        provider: ProviderConfig,
        prompt: str,
        image: str | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name(provider),
            "prompt": prompt,
            "negative_prompt": provider.extra.get("negative_prompt"),
            "image_size": provider.extra.get("image_size"),
            "batch_size": provider.extra.get("batch_size"),
            "num_inference_steps": provider.extra.get("num_inference_steps"),
            "guidance_scale": provider.extra.get("guidance_scale"),
        }
        if image:
            payload["image"] = image
        payload.update(_without_none(overrides))
        return self._post_json(provider.endpoint or "/images/generations", _without_none(payload))

    def submit_video(
        self,
        provider: ProviderConfig,
        prompt: str,
        image: str | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model_name(provider),
            "prompt": prompt,
            "image_size": provider.extra.get("image_size", "720x1280"),
            "negative_prompt": provider.extra.get("negative_prompt"),
        }
        if image:
            payload["image"] = image
        payload.update(_without_none(overrides))
        return self._post_json(provider.endpoint or "/video/submit", _without_none(payload))

    def get_video_status(self, provider: ProviderConfig, request_id: str) -> dict[str, Any]:
        endpoint = provider.extra.get("poll_endpoint", "/video/status")
        return self._post_json(str(endpoint), {"requestId": request_id})

    def wait_for_video(self, provider: ProviderConfig, request_id: str) -> dict[str, Any]:
        interval = int(provider.extra.get("poll_interval_sec", 5))
        timeout = int(provider.extra.get("poll_timeout_sec", 600))
        deadline = time.monotonic() + timeout
        while True:
            result = self.get_video_status(provider, request_id)
            status = result.get("status")
            if status in {"Succeed", "Failed"}:
                return result
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for video request {request_id}")
            time.sleep(interval)

    def create_speech(
        self,
        provider: ProviderConfig,
        text: str,
        voice: str | None = None,
        **overrides: Any,
    ) -> bytes:
        payload = {
            "model": self.model_name(provider),
            "input": text,
            "voice": voice or provider.extra.get("voice"),
            "response_format": provider.extra.get("response_format", "mp3"),
            "speed": provider.extra.get("speed", 1.0),
            "gain": provider.extra.get("gain", 0),
        }
        payload.update(_without_none(overrides))
        return self._post_bytes(provider.endpoint or "/audio/speech", _without_none(payload))

    def create_transcription(self, provider: ProviderConfig, audio_path: Path) -> dict[str, Any]:
        url = self._url(provider.endpoint or "/audio/transcriptions")
        headers = {"Authorization": f"Bearer {self.api_key}"}
        with audio_path.open("rb") as audio:
            files = {"file": (audio_path.name, audio)}
            data = {"model": self.model_name(provider)}
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=headers, files=files, data=data)
        response.raise_for_status()
        return response.json()

    def create_embeddings(self, provider: ProviderConfig, input_data: Any, **overrides: Any) -> dict[str, Any]:
        payload = {
            "model": self.model_name(provider),
            "input": input_data,
            "dimensions": provider.extra.get("dimensions"),
            "encoding_format": provider.extra.get("encoding_format", "float"),
        }
        payload.update(_without_none(overrides))
        return self._post_json(provider.endpoint or "/embeddings", _without_none(payload))

    def rerank(
        self,
        provider: ProviderConfig,
        query: str,
        documents: list[str],
        **overrides: Any,
    ) -> dict[str, Any]:
        payload = {
            "model": self.model_name(provider),
            "query": query,
            "documents": documents,
            "top_n": provider.extra.get("top_n"),
            "return_documents": provider.extra.get("return_documents", True),
        }
        payload.update(_without_none(overrides))
        return self._post_json(provider.endpoint or "/rerank", _without_none(payload))

    def _post_json(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self._url(endpoint), headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    def _post_bytes(self, endpoint: str, payload: dict[str, Any]) -> bytes:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(self._url(endpoint), headers=headers, json=payload)
        response.raise_for_status()
        return response.content

    def _url(self, endpoint: str) -> str:
        return f"{self.base_url}/{endpoint.lstrip('/')}"


def _without_none(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value is not None}
