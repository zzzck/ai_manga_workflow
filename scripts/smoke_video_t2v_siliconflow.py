from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from manga_flow.config import load_config
from manga_flow.providers.siliconflow import SiliconFlowClient


def main() -> None:
    root = Path.cwd()
    config = load_config(root / "config/pipeline.siliconflow.yaml")
    provider = config.providers["video_t2v"]
    out = root / "outputs/smoke" / datetime.now().strftime("siliconflow_video_t2v_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {"run_dir": str(out.resolve()), "tests": []}

    def add(name: str, ok: bool, detail: object) -> None:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        report["tests"].append({"name": name, "ok": ok, "detail": str(detail)})

    def download(url: str, path: Path) -> None:
        with httpx.Client(timeout=120.0) as client:
            response = client.get(url)
        response.raise_for_status()
        path.write_bytes(response.content)

    client = SiliconFlowClient.from_provider(provider, env_path=root / ".env")
    submit = client.submit_video(
        provider,
        prompt=(
            "A vertical 9:16 ancient Chinese anime establishing shot of a quiet courtyard at night. "
            "A white paper lantern gently sways in the wind, soft moonlight, light rain, no people, calm cinematic mood."
        ),
        image_size="720x1280",
    )
    request_id = submit["requestId"]
    add("video_t2v_submit", True, f"request_id={request_id}")

    deadline = time.monotonic() + 600
    last_status = None
    while True:
        status = client.get_video_status(provider, request_id)
        last_status = status
        state = status.get("status")
        print(f"[POLL] status={state}", flush=True)
        if state == "Succeed":
            url = status["results"]["videos"][0]["url"]
            path = out / "video_t2v.mp4"
            download(url, path)
            add("video_t2v", True, f"file={path.name}, bytes={path.stat().st_size}")
            break
        if state == "Failed":
            add("video_t2v", False, status)
            break
        if time.monotonic() > deadline:
            add("video_t2v", False, f"timeout, last_status={last_status}")
            break
        time.sleep(5)

    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}", flush=True)
    if not all(item["ok"] for item in report["tests"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
