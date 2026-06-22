from __future__ import annotations

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
    out = root / "outputs/smoke" / datetime.now().strftime("siliconflow_media_%Y%m%d_%H%M%S")
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

    client = SiliconFlowClient.from_provider(config.providers["image"], env_path=root / ".env")

    image_url = ""
    try:
        resp = client.generate_image(
            config.providers["image"],
            prompt=(
                "中国古风国漫竖屏分镜，雨夜义庄，白灯笼摇晃，年轻女仵作沈照夜，"
                "乌发半挽，青色窄袖长衫，腰间银针囊，手持旧玉扣，冷月色调，"
                "强情绪特写，画面清晰，无文字水印。"
            ),
            image_size="720x1280",
            batch_size=1,
            num_inference_steps=12,
        )
        image_url = resp["images"][0]["url"]
        image_path = out / "image_main.png"
        download(image_url, image_path)
        add("image_generation", True, f"file={image_path.name}, bytes={image_path.stat().st_size}")
    except Exception as exc:
        add("image_generation", False, repr(exc))

    if image_url:
        try:
            resp = client.vision_completion(
                config.providers["llm_vision"],
                image_url=image_url,
                prompt=(
                    "请用 JSON 返回：画面是否是中国古风国漫、是否有女性角色、是否适合雨夜悬疑漫剧。"
                    "字段为 style_ok, has_female_character, drama_fit, notes。"
                ),
                max_tokens=512,
                temperature=0,
            )
            content = resp["choices"][0]["message"]["content"]
            add("vision_check", True, content[:300])
        except Exception as exc:
            add("vision_check", False, repr(exc))
    else:
        add("vision_check", False, "skipped because image generation failed")

    edited_url = ""
    if image_url:
        try:
            resp = client.generate_image(
                config.providers["image_edit"],
                prompt="保持角色身份、脸型、服装不变，增强蓝白借命灯火焰和雨夜冷雾氛围，不添加文字。",
                image=image_url,
            )
            edited_url = resp["images"][0]["url"]
            edited_path = out / "image_edit.png"
            download(edited_url, edited_path)
            add("image_edit", True, f"file={edited_path.name}, bytes={edited_path.stat().st_size}")
        except Exception as exc:
            add("image_edit", False, repr(exc))
    else:
        add("image_edit", False, "skipped because image generation failed")

    video_url = ""
    if image_url:
        try:
            submit = client.submit_video(
                config.providers["video"],
                prompt=(
                    "The young ancient Chinese female coroner slowly raises the jade button under a swaying white lantern. "
                    "Cold rain falls outside the morgue, blue-white ghostly lamp fire flickers, mist moves across the floor. "
                    "Keep the character face, hairstyle, blue robe, silver needle pouch, and ancient Chinese anime style consistent. "
                    "Vertical 9:16 cinematic shot, slow push-in, suspenseful mood."
                ),
                image=image_url,
                image_size="720x1280",
            )
            request_id = submit["requestId"]
            add("video_submit", True, f"request_id={request_id}")
            status = client.wait_for_video(config.providers["video"], request_id)
            status_name = status.get("status")
            if status_name == "Succeed":
                video_url = status["results"]["videos"][0]["url"]
                video_path = out / "video_i2v.mp4"
                download(video_url, video_path)
                add("video_i2v", True, f"file={video_path.name}, bytes={video_path.stat().st_size}")
            else:
                add("video_i2v", False, status)
        except Exception as exc:
            add("video_i2v", False, repr(exc))
    else:
        add("video_i2v", False, "skipped because image generation failed")

    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}", flush=True)
    if not all(item["ok"] for item in report["tests"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
