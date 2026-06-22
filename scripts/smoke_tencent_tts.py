from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from manga_flow.config import load_config
from manga_flow.providers.tencent_tts import TencentCloudTTSClient


def main() -> None:
    root = Path.cwd()
    config = load_config(root / "config/pipeline.siliconflow.yaml")
    provider = config.providers["voice"]
    client = TencentCloudTTSClient.from_provider(provider, env_path=root / ".env")
    out = root / "outputs/smoke" / datetime.now().strftime("tencent_tts_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)

    cases = [
        {
            "name": "narrator",
            "speaker": "旁白",
            "character_id": None,
            "text": "子时，城外义庄又送来一具无名信使。",
        },
        {
            "name": "shen_zhaoye",
            "speaker": "沈照夜",
            "character_id": "shen_zhaoye",
            "text": "这枚玉扣，是我哥哥的。",
        },
        {
            "name": "xie_lingzhou",
            "speaker": "谢凌舟",
            "character_id": "xie_lingzhou",
            "text": "这盏灯，不能再亮第二次。",
        },
    ]
    report = {"run_dir": str(out.resolve()), "tests": []}
    for case in cases:
        voice_type = client.voice_type_for(provider, case["speaker"], case["character_id"])
        path = out / f"{case['name']}_{voice_type}.mp3"
        audio = client.create_speech(
            provider,
            case["text"],
            speaker=case["speaker"],
            character_id=case["character_id"],
        )
        path.write_bytes(audio)
        item = {
            "name": case["name"],
            "speaker": case["speaker"],
            "voice_type": voice_type,
            "bytes": len(audio),
            "file": str(path),
        }
        report["tests"].append(item)
        print(f"[OK] {case['name']}: voice={voice_type}, bytes={len(audio)}, file={path}", flush=True)

    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}", flush=True)


if __name__ == "__main__":
    main()
