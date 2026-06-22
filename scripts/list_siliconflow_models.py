from __future__ import annotations

import os

import httpx
from dotenv import load_dotenv


def main() -> None:
    load_dotenv(".env")
    key = os.getenv("SILICONFLOW_API_KEY", "")
    headers = {"Authorization": f"Bearer {key}"}
    needles = [
        "Z-Image",
        "Qwen-Image",
        "Wan2.2",
        "GLM-5.2",
        "GLM-4.5V",
        "CosyVoice",
        "SenseVoice",
    ]
    for params in ({"type": "image"}, {"type": "video"}, {"type": "text"}, {"type": "audio"}):
        response = httpx.get(
            "https://api.siliconflow.cn/v1/models",
            headers=headers,
            params=params,
            timeout=60,
        )
        print("PARAMS", params, "STATUS", response.status_code)
        response.raise_for_status()
        data = response.json()
        items = data.get("data", data if isinstance(data, list) else [])
        for item in items:
            name = item.get("id") or item.get("model") or item.get("name")
            if name and any(needle in name for needle in needles):
                print(name)


if __name__ == "__main__":
    main()
