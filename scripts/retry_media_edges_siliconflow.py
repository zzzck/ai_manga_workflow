from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from manga_flow.config import load_config
from manga_flow.providers.siliconflow import SiliconFlowClient


def main() -> None:
    root = Path.cwd()
    config = load_config(root / "config/pipeline.siliconflow.yaml")
    existing_runs = sorted((root / "outputs/smoke").glob("siliconflow_media_*"), reverse=True)
    if not existing_runs:
        raise SystemExit("No previous media smoke output found.")
    source_image = existing_runs[0] / "image_main.png"
    if not source_image.exists():
        raise SystemExit(f"Missing source image: {source_image}")

    out = root / "outputs/smoke" / datetime.now().strftime("siliconflow_media_retry_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"run_dir": str(out.resolve()), "source_image": str(source_image), "tests": []}

    def add(name: str, ok: bool, detail: object) -> None:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        report["tests"].append({"name": name, "ok": ok, "detail": str(detail)})

    def download(url: str, path: Path) -> None:
        with httpx.Client(timeout=120.0) as client:
            response = client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)

    data_url = "data:image/png;base64," + base64.b64encode(source_image.read_bytes()).decode("ascii")
    client = SiliconFlowClient.from_provider(config.providers["image"], env_path=root / ".env")

    for model in ("Qwen/Qwen-Image-Edit-2509", "Qwen/Qwen-Image-Edit"):
        try:
            resp = client._post_json(
                "/images/generations",
                {
                    "model": model,
                    "prompt": "保持人物身份、脸型、服装不变，增强蓝白灯火和雨夜冷雾氛围，不添加文字。",
                    "image": data_url,
                },
            )
            image_url = resp["images"][0]["url"]
            path = out / f"{model.split('/')[-1]}.png"
            download(image_url, path)
            add(f"image_edit:{model}", True, f"file={path.name}, bytes={path.stat().st_size}")
            break
        except Exception as exc:
            add(f"image_edit:{model}", False, repr(exc))

    try:
        submit = client._post_json(
            "/video/submit",
            {
                "model": "Wan-AI/Wan2.2-I2V-A14B",
                "prompt": (
                    "A vertical ancient Chinese anime shot. The young female coroner slowly raises a jade button under a "
                    "swaying white lantern. Blue-white ghostly lamp fire flickers, cold mist moves, slow push-in camera. "
                    "Keep character identity and costume consistent."
                ),
                "image_size": "720x1280",
                "image": data_url,
            },
        )
        request_id = submit["requestId"]
        add("video_i2v_submit_base64", True, f"request_id={request_id}")
        status = client.wait_for_video(config.providers["video"], request_id)
        if status.get("status") == "Succeed":
            video_url = status["results"]["videos"][0]["url"]
            path = out / "video_i2v_base64.mp4"
            download(video_url, path)
            add("video_i2v_base64", True, f"file={path.name}, bytes={path.stat().st_size}")
        else:
            add("video_i2v_base64", False, status)
    except Exception as exc:
        add("video_i2v_base64", False, repr(exc))

    try:
        submit = client._post_json(
            "/video/submit",
            {
                "model": "Wan-AI/Wan2.2-T2V-A14B",
                "prompt": (
                    "A vertical 9:16 ancient Chinese anime establishing shot of a rainy night mortuary. "
                    "White paper lanterns sway in the wind, cold mist slides over a wooden table, blue moonlight, suspenseful mood."
                ),
                "image_size": "720x1280",
            },
        )
        request_id = submit["requestId"]
        add("video_t2v_submit", True, f"request_id={request_id}")
        status = client.wait_for_video(config.providers["video_t2v"], request_id)
        if status.get("status") == "Succeed":
            video_url = status["results"]["videos"][0]["url"]
            path = out / "video_t2v.mp4"
            download(video_url, path)
            add("video_t2v", True, f"file={path.name}, bytes={path.stat().st_size}")
        else:
            add("video_t2v", False, status)
    except Exception as exc:
        add("video_t2v", False, repr(exc))

    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}", flush=True)
    if not any(item["ok"] for item in report["tests"] if item["name"].startswith("video_")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
