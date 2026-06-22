from __future__ import annotations

import base64
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from manga_flow.config import load_config
from manga_flow.providers.siliconflow import SiliconFlowClient


def main() -> None:
    root = Path.cwd()
    config = load_config(root / "config/pipeline.siliconflow.yaml")
    out = root / "outputs/smoke" / datetime.now().strftime("siliconflow_video_i2v_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    client = SiliconFlowClient.from_provider(config.providers["image_fast"], env_path=root / ".env")

    print("[STEP] generate neutral source image", flush=True)
    image_resp = client.generate_image(
        config.providers["image_fast"],
        prompt=(
            "中国古风国漫竖屏画面，安静庭院夜景，一个白色纸灯笼在雨中轻轻摇晃，"
            "石板地面反光，远处月光，冷色调，无人物，无文字，无水印。"
        ),
        image_size="720x1280",
        batch_size=1,
        num_inference_steps=8,
    )
    image_url = image_resp["images"][0]["url"]
    image_path = out / "source.png"
    with httpx.Client(timeout=120.0) as downloader:
        response = downloader.get(image_url)
    response.raise_for_status()
    image_path.write_bytes(response.content)
    print(f"[OK] source image: bytes={image_path.stat().st_size}", flush=True)

    data_url = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")
    print("[STEP] submit i2v", flush=True)
    video_provider = config.providers["video"]
    submit = client.submit_video(
        video_provider,
        prompt=(
            "A vertical ancient Chinese anime courtyard at night. The white paper lantern gently sways in the rain, "
            "moonlight reflects on wet stone, mist moves slowly across the ground, no people, slow cinematic push-in."
        ),
        image=data_url,
        image_size="720x1280",
    )
    request_id = submit["requestId"]
    print(f"[OK] submit: request_id={request_id}", flush=True)

    deadline = time.monotonic() + 900
    last_status = None
    while True:
        status = client.get_video_status(video_provider, request_id)
        last_status = status
        state = status.get("status")
        print(f"[POLL] status={state}", flush=True)
        if state == "Succeed":
            video_url = status["results"]["videos"][0]["url"]
            video_path = out / "video_i2v.mp4"
            with httpx.Client(timeout=120.0) as downloader:
                response = downloader.get(video_url)
            response.raise_for_status()
            video_path.write_bytes(response.content)
            print(f"[OK] video_i2v: bytes={video_path.stat().st_size}", flush=True)
            (out / "report.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        if state == "Failed":
            print(f"[FAIL] video_i2v: {status}", flush=True)
            (out / "report.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(1)
        if time.monotonic() > deadline:
            print(f"[FAIL] timeout: {last_status}", flush=True)
            (out / "report.json").write_text(json.dumps(last_status, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(1)
        time.sleep(10)


if __name__ == "__main__":
    main()
