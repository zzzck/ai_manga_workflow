from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

import httpx

from manga_flow.providers.siliconflow import SiliconFlowClient


def main() -> None:
    root = Path.cwd()
    source_images = sorted((root / "outputs/smoke").glob("siliconflow_media_*/image_main.png"), reverse=True)
    if not source_images:
        raise SystemExit("No media image found.")
    source_image = source_images[0]
    out = root / "outputs/smoke" / datetime.now().strftime("siliconflow_image_edit_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    data_url = "data:image/png;base64," + base64.b64encode(source_image.read_bytes()).decode("ascii")
    client = SiliconFlowClient.from_provider(
        provider=type("Provider", (), {"api_key_env": "SILICONFLOW_API_KEY", "base_url": "https://api.siliconflow.cn/v1"})(),
        env_path=root / ".env",
    )
    client.timeout = 600

    report = {"source_image": str(source_image), "tests": []}
    for model in ["Qwen/Qwen-Image-Edit", "Qwen/Qwen-Image-Edit-2509"]:
        print(f"[TRY] {model}", flush=True)
        try:
            resp = client._post_json(
                "/images/generations",
                {
                    "model": model,
                    "prompt": "保持人物身份、构图、服装不变，只增强蓝白灯火、月光和雨夜冷雾氛围，不添加文字。",
                    "image": data_url,
                },
            )
            url = resp["images"][0]["url"]
            path = out / f"{model.split('/')[-1]}.png"
            with httpx.Client(timeout=120.0) as downloader:
                image_resp = downloader.get(url)
            image_resp.raise_for_status()
            path.write_bytes(image_resp.content)
            detail = f"file={path.name}, bytes={path.stat().st_size}"
            print(f"[OK] {model}: {detail}", flush=True)
            report["tests"].append({"model": model, "ok": True, "detail": detail})
            break
        except Exception as exc:
            print(f"[FAIL] {model}: {exc!r}", flush=True)
            report["tests"].append({"model": model, "ok": False, "detail": repr(exc)})
    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}", flush=True)
    if not any(item["ok"] for item in report["tests"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
