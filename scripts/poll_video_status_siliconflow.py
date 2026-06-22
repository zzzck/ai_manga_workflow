from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import httpx

from manga_flow.config import load_config
from manga_flow.providers.siliconflow import SiliconFlowClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request_id")
    parser.add_argument("--slot", default="video_t2v")
    parser.add_argument("--timeout-sec", type=int, default=900)
    args = parser.parse_args()

    root = Path.cwd()
    config = load_config(root / "config/pipeline.siliconflow.yaml")
    provider = config.providers[args.slot]
    client = SiliconFlowClient.from_provider(provider, env_path=root / ".env")
    out = root / "outputs/smoke" / datetime.now().strftime("siliconflow_video_poll_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + args.timeout_sec
    last_status = None
    while True:
        status = client.get_video_status(provider, args.request_id)
        last_status = status
        state = status.get("status")
        print(f"[POLL] status={state}", flush=True)
        if state == "Succeed":
            url = status["results"]["videos"][0]["url"]
            path = out / "video.mp4"
            with httpx.Client(timeout=120.0) as downloader:
                response = downloader.get(url)
            response.raise_for_status()
            path.write_bytes(response.content)
            print(f"[OK] video: file={path}, bytes={path.stat().st_size}", flush=True)
            (out / "report.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            return
        if state == "Failed":
            print(f"[FAIL] video: {status}", flush=True)
            (out / "report.json").write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(1)
        if time.monotonic() > deadline:
            print(f"[FAIL] timeout: {last_status}", flush=True)
            (out / "report.json").write_text(json.dumps(last_status, ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(1)
        time.sleep(10)


if __name__ == "__main__":
    main()
