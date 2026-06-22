from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from manga_flow.config import load_config
from manga_flow.providers.siliconflow import SiliconFlowClient
from manga_flow.providers.tencent_tts import TencentCloudTTSClient


def main() -> None:
    root = Path.cwd()
    config = load_config(root / "config/pipeline.siliconflow.yaml")
    out = root / "outputs/smoke" / datetime.now().strftime("siliconflow_low_cost_%Y%m%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    report = {"run_dir": str(out.resolve()), "tests": []}

    def add(name: str, ok: bool, detail: object) -> None:
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}", flush=True)
        report["tests"].append({"name": name, "ok": ok, "detail": str(detail)})

    client = SiliconFlowClient.from_provider(config.providers["llm"], env_path=root / ".env")

    try:
        resp = client.chat_completion(
            config.providers["llm_fast"],
            messages=[
                {"role": "system", "content": "你是接口烟测助手，只输出 JSON。"},
                {"role": "user", "content": '返回 {"ok": true, "task": "manga_flow_smoke"}'},
            ],
            temperature=0,
            max_tokens=128,
        )
        content = resp["choices"][0]["message"]["content"]
        add("llm_fast_chat", True, content[:200])
    except Exception as exc:
        add("llm_fast_chat", False, repr(exc))

    try:
        resp = client.create_embeddings(
            config.providers["embedding"],
            ["沈照夜是女仵作", "谢凌舟是少年将军"],
            dimensions=1024,
        )
        dims = len(resp["data"][0]["embedding"])
        add("embedding", True, f"vectors={len(resp['data'])}, dims={dims}")
    except Exception as exc:
        add("embedding", False, repr(exc))

    try:
        resp = client.rerank(
            config.providers["rerank"],
            query="谁手持雁翎刀？",
            documents=["沈照夜腰间有银针囊。", "谢凌舟腰悬雁翎刀。", "无名信使手握旧玉扣。"],
            top_n=2,
            return_documents=True,
        )
        add("rerank", True, f"results={len(resp.get('results', []))}")
    except Exception as exc:
        add("rerank", False, repr(exc))

    speech_path = out / "tts_test.mp3"
    try:
        voice_provider = config.providers["voice"]
        if voice_provider.provider.lower().replace("_", "-") in {"tencentcloud", "tencent-cloud", "tencent"}:
            tts_client = TencentCloudTTSClient.from_provider(voice_provider, env_path=root / ".env")
            audio = tts_client.create_speech(voice_provider, "子时，城外义庄又送来一具无名信使。", speaker="旁白")
        else:
            audio = client.create_speech(
                voice_provider,
                "子时，城外义庄又送来一具无名尸。",
                response_format="mp3",
            )
        speech_path.write_bytes(audio)
        add("tts", True, f"bytes={len(audio)}, file={speech_path.name}")
    except Exception as exc:
        add("tts", False, repr(exc))

    if speech_path.exists() and speech_path.stat().st_size > 0:
        try:
            resp = client.create_transcription(config.providers["stt"], speech_path)
            add("stt", True, resp.get("text", "")[:200])
        except Exception as exc:
            add("stt", False, repr(exc))
    else:
        add("stt", False, "skipped because tts failed")

    report_path = out / "report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"REPORT={report_path}", flush=True)
    if not all(item["ok"] for item in report["tests"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
