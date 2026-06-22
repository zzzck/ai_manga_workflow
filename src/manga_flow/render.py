from __future__ import annotations

import base64
import hashlib
import json
import math
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

from .config import load_config
from .pipeline import run_pipeline
from .providers.siliconflow import SiliconFlowClient
from .providers.tencent_tts import TencentCloudTTSClient


FPS = 25
WIDTH = 720
HEIGHT = 1280
TTS_META_VERSION = 1
MEDIA_META_VERSION = 1
STAGE_ORDER = ["structure", "images", "voice", "videos", "compose"]


class GenerationLogger:
    def __init__(self, run_dir: Path, project_id: str, episode: int) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_dir = run_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.text_path = log_dir / f"render_{timestamp}.log"
        self.jsonl_path = log_dir / f"render_{timestamp}.jsonl"
        self.project_id = project_id
        self.episode = episode

    def event(
        self,
        stage: str,
        status: str,
        message: str,
        shot_id: str | None = None,
        detail: Any | None = None,
        level: str = "info",
    ) -> None:
        payload = {
            "time": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "project_id": self.project_id,
            "episode": self.episode,
            "stage": stage,
            "status": status,
            "shot_id": shot_id,
            "message": message,
            "detail": detail,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False) + "\n")
        shot_text = f" [{shot_id}]" if shot_id else ""
        detail_text = f" | {detail}" if detail is not None else ""
        with self.text_path.open("a", encoding="utf-8") as file:
            file.write(f"{payload['time']} {level.upper()} {stage}.{status}{shot_text} {message}{detail_text}\n")


def render_sample(
    project_path: Path,
    config_path: Path,
    episode: int | None = None,
    key_shots: list[str] | None = None,
    env_path: Path | None = None,
    video_timeout_sec: int = 900,
) -> dict[str, Any]:
    result = run_pipeline(project_path=project_path, config_path=config_path, episode=episode)
    config = load_config(config_path)
    run_dir = Path(result.run_dir)
    env_path = env_path or config_path.parent.parent / ".env"

    shot_data = _read_json(run_dir / "shot_list.json")
    key_shots = _resolve_video_shots(shot_data, key_shots)
    voice_data = _read_json(run_dir / "audio" / "voice_lines.json")
    total_duration = int(result.total_duration_sec)
    render_dir = run_dir / "render"
    clip_dir = render_dir / "clips"
    final_dir = run_dir / "final"
    logger = GenerationLogger(run_dir, result.project_id, result.episode)
    report: dict[str, Any] = {
        "project_id": result.project_id,
        "episode": result.episode,
        "run_dir": str(run_dir),
        "key_shots": key_shots,
        "log": str(logger.text_path),
        "event_log": str(logger.jsonl_path),
        "steps": [],
        "warnings": [],
    }
    logger.event(
        "render",
        "start",
        "开始渲染样片。",
        detail={"key_shots": key_shots, "shot_count": len(shot_data), "duration_sec": total_duration},
    )
    for directory in [render_dir, clip_dir, final_dir, run_dir / "assets" / "images", run_dir / "assets" / "videos"]:
        directory.mkdir(parents=True, exist_ok=True)

    media_client = SiliconFlowClient.from_provider(config.providers["image"], env_path=env_path)
    media_client.timeout = 600

    _generate_images(media_client, config, shot_data, run_dir, report, logger)
    _generate_voice(config, voice_data, shot_data, run_dir, report, env_path, logger)
    video_ready_shots = _generate_key_videos(
        media_client, config, shot_data, run_dir, key_shots, video_timeout_sec, report, logger
    )
    _render_visual_clips(shot_data, run_dir, clip_dir, key_shots, video_ready_shots, report, logger)

    visual_path = render_dir / "visual_track.mp4"
    audio_path = render_dir / "voice_mix.m4a"
    no_subs_path = render_dir / "sample_no_subs.mp4"
    final_path = final_dir / f"{result.project_id}_episode_{result.episode:03d}_sample.mp4"
    cover_path = final_dir / "cover.jpg"

    _concat_clips(shot_data, clip_dir, visual_path)
    _mix_voice_track(voice_data, run_dir, audio_path, total_duration)
    _mux_audio_video(visual_path, audio_path, no_subs_path, total_duration)
    _burn_subtitles(no_subs_path, run_dir / "audio" / "subtitles.srt", final_path, report)
    _extract_cover(final_path, cover_path)

    report["final_video"] = str(final_path)
    report["cover"] = str(cover_path)
    report["duration_sec"] = _probe_duration(final_path)
    report["files"] = {
        "visual_track": str(visual_path),
        "audio_track": str(audio_path),
        "no_subs_video": str(no_subs_path),
        "final_video": str(final_path),
        "cover": str(cover_path),
    }
    report_path = run_dir / "reports" / "render_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report"] = str(report_path)
    logger.event(
        "render",
        "complete",
        "样片渲染完成。",
        detail={"final_video": str(final_path), "duration_sec": report["duration_sec"], "warnings": report["warnings"]},
    )
    return report


def run_render_stages(
    project_path: Path,
    config_path: Path,
    stages: list[str],
    episode: int | None = None,
    key_shots: list[str] | None = None,
    env_path: Path | None = None,
    video_timeout_sec: int = 900,
) -> dict[str, Any]:
    selected_stages = _normalize_stages(stages)
    result = run_pipeline(project_path=project_path, config_path=config_path, episode=episode)
    config = load_config(config_path)
    run_dir = Path(result.run_dir)
    env_path = env_path or config_path.parent.parent / ".env"

    shot_data = _read_json(run_dir / "shot_list.json")
    voice_data = _read_json(run_dir / "audio" / "voice_lines.json")
    key_shots = _resolve_video_shots(shot_data, key_shots)
    total_duration = int(result.total_duration_sec)
    render_dir = run_dir / "render"
    clip_dir = render_dir / "clips"
    final_dir = run_dir / "final"
    logger = GenerationLogger(run_dir, result.project_id, result.episode)
    report: dict[str, Any] = {
        "project_id": result.project_id,
        "episode": result.episode,
        "run_dir": str(run_dir),
        "stages": selected_stages,
        "key_shots": key_shots,
        "log": str(logger.text_path),
        "event_log": str(logger.jsonl_path),
        "steps": [],
        "warnings": [],
        "files": dict(result.files),
    }
    logger.event(
        "stage",
        "start",
        "开始执行指定流程。",
        detail={
            "stages": selected_stages,
            "key_shots": key_shots,
            "shot_count": len(shot_data),
            "duration_sec": total_duration,
        },
    )
    for directory in [render_dir, clip_dir, final_dir, run_dir / "assets" / "images", run_dir / "assets" / "videos"]:
        directory.mkdir(parents=True, exist_ok=True)

    media_client: SiliconFlowClient | None = None
    if any(stage in selected_stages for stage in ["images", "videos"]):
        media_client = SiliconFlowClient.from_provider(config.providers["image"], env_path=env_path)
        media_client.timeout = 600

    if "structure" in selected_stages:
        _add_step(report, "structure", result.project_id, "generated", str(run_dir))
        logger.event("structure", "generated", "脚本、分镜、字幕、剪辑计划已生成。", detail={"run_dir": str(run_dir)})
    if "images" in selected_stages:
        if media_client is None:
            raise RuntimeError("Image stage requires a media client.")
        _generate_images(media_client, config, shot_data, run_dir, report, logger)
    if "voice" in selected_stages:
        _generate_voice(config, voice_data, shot_data, run_dir, report, env_path, logger)
    if "videos" in selected_stages:
        if media_client is None:
            raise RuntimeError("Video stage requires a media client.")
        _generate_key_videos(media_client, config, shot_data, run_dir, key_shots, video_timeout_sec, report, logger)
    if "compose" in selected_stages:
        video_ready_shots = _ready_video_shots(config, shot_data, run_dir, key_shots)
        _render_visual_clips(shot_data, run_dir, clip_dir, key_shots, video_ready_shots, report, logger)

        visual_path = render_dir / "visual_track.mp4"
        audio_path = render_dir / "voice_mix.m4a"
        no_subs_path = render_dir / "sample_no_subs.mp4"
        final_path = final_dir / f"{result.project_id}_episode_{result.episode:03d}_sample.mp4"
        cover_path = final_dir / "cover.jpg"
        _concat_clips(shot_data, clip_dir, visual_path)
        _mix_voice_track(voice_data, run_dir, audio_path, total_duration)
        _mux_audio_video(visual_path, audio_path, no_subs_path, total_duration)
        _burn_subtitles(no_subs_path, run_dir / "audio" / "subtitles.srt", final_path, report)
        _extract_cover(final_path, cover_path)
        report["final_video"] = str(final_path)
        report["cover"] = str(cover_path)
        report["duration_sec"] = _probe_duration(final_path)
        report["files"].update(
            {
                "visual_track": str(visual_path),
                "audio_track": str(audio_path),
                "no_subs_video": str(no_subs_path),
                "final_video": str(final_path),
                "cover": str(cover_path),
            }
        )

    report_path = run_dir / "reports" / "stage_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report"] = str(report_path)
    logger.event("stage", "complete", "指定流程执行完成。", detail={"report": str(report_path), "warnings": report["warnings"]})
    return report


def _generate_images(
    client: SiliconFlowClient,
    config: Any,
    shots: list[dict[str, Any]],
    run_dir: Path,
    report: dict[str, Any],
    logger: GenerationLogger,
) -> None:
    image_provider = config.providers["image"]
    image_fast_provider = config.providers.get("image_fast", image_provider)
    for shot in shots:
        shot_id = shot["shot_id"]
        image_path = run_dir / "assets" / "images" / f"{shot_id}.png"
        meta_path = run_dir / "assets" / "images" / f"{shot_id}.json"
        prompt = _safe_image_prompt(shot["image_prompt"])
        expected_meta = _image_meta(image_provider, prompt)
        if _media_cache_valid(image_path, meta_path, expected_meta):
            _add_step(report, "image", shot_id, "cached", str(image_path))
            logger.event("image", "cached", "使用已缓存分镜图。", shot_id, {"file": str(image_path)})
            continue

        try:
            logger.event("image", "submit", "调用生图接口生成分镜图。", shot_id, {"model": image_provider.model})
            response = client.generate_image(
                image_provider,
                prompt=prompt,
                image_size=f"{WIDTH}x{HEIGHT}",
                batch_size=1,
                num_inference_steps=16,
            )
        except Exception as primary_exc:
            report["warnings"].append(f"{shot_id} 主生图失败，改用快速生图：{primary_exc!r}")
            logger.event(
                "image",
                "fallback",
                "主生图失败，改用快速生图。",
                shot_id,
                {"error": repr(primary_exc), "fallback_model": image_fast_provider.model},
                level="warning",
            )
            response = client.generate_image(
                image_fast_provider,
                prompt=prompt,
                image_size=f"{WIDTH}x{HEIGHT}",
                batch_size=1,
                num_inference_steps=8,
            )
        image_url = response["images"][0]["url"]
        _download(image_url, image_path)
        meta_path.write_text(json.dumps(expected_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _add_step(report, "image", shot_id, "generated", str(image_path))
        logger.event("image", "generated", "分镜图生成完成。", shot_id, {"file": str(image_path)})


def _generate_voice(
    config: Any,
    voice_lines: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    run_dir: Path,
    report: dict[str, Any],
    env_path: Path | None,
    logger: GenerationLogger,
) -> None:
    voice_provider = config.providers["voice"]
    provider_name = voice_provider.provider.lower().replace("_", "-")
    shot_by_id = {shot["shot_id"]: shot for shot in shots}
    tencent_client: TencentCloudTTSClient | None = None
    siliconflow_client: SiliconFlowClient | None = None
    if provider_name in {"tencentcloud", "tencent-cloud", "tencent"}:
        tencent_client = TencentCloudTTSClient.from_provider(voice_provider, env_path=env_path)
    else:
        siliconflow_client = SiliconFlowClient.from_provider(voice_provider, env_path=env_path)

    for line in voice_lines:
        shot_id = line["shot_id"]
        voice_path = run_dir / "audio" / f"{shot_id}.mp3"
        meta_path = run_dir / "audio" / f"{shot_id}.tts.json"
        shot = shot_by_id.get(shot_id, {})
        speaker = line.get("speaker")
        character_id = line.get("character_id")
        text = line["text"]

        if tencent_client:
            voice_type = _line_voice_type(line) or tencent_client.voice_type_for(
                voice_provider,
                speaker=speaker,
                character_id=character_id,
            )
            emotion_category = tencent_client.emotion_for(voice_provider, shot.get("emotion"))
            expected_meta = _tts_meta(
                voice_provider,
                line,
                voice_type=voice_type,
                emotion_category=emotion_category,
            )
        else:
            voice_type = voice_provider.extra.get("voice", "")
            expected_meta = _tts_meta(voice_provider, line, voice_type=voice_type)

        if _tts_cache_valid(voice_path, meta_path, expected_meta):
            _add_step(report, "tts", shot_id, "cached", f"{voice_path} speaker={speaker} voice={voice_type}")
            logger.event("tts", "cached", "使用已缓存配音。", shot_id, {"speaker": speaker, "voice": voice_type})
            continue

        if tencent_client:
            logger.event("tts", "submit", "调用腾讯云 TTS 生成配音。", shot_id, {"speaker": speaker, "voice": voice_type})
            audio = tencent_client.create_speech(
                voice_provider,
                text,
                speaker=speaker,
                character_id=character_id,
                emotion=shot.get("emotion"),
                voice_type=voice_type,
            )
        elif siliconflow_client:
            logger.event("tts", "submit", "调用 TTS 接口生成配音。", shot_id, {"speaker": speaker, "voice": voice_type})
            audio = siliconflow_client.create_speech(voice_provider, text, response_format="mp3")
        else:
            raise RuntimeError(f"Unsupported voice provider: {voice_provider.provider}")
        voice_path.write_bytes(audio)
        meta_path.write_text(json.dumps(expected_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        _add_step(report, "tts", shot_id, "generated", f"{voice_path} speaker={speaker} voice={voice_type}")
        logger.event("tts", "generated", "配音生成完成。", shot_id, {"file": str(voice_path), "speaker": speaker, "voice": voice_type})


def _line_voice_type(line: dict[str, Any]) -> int | None:
    value = line.get("voice_type")
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _generate_key_videos(
    client: SiliconFlowClient,
    config: Any,
    shots: list[dict[str, Any]],
    run_dir: Path,
    key_shots: list[str],
    timeout_sec: int,
    report: dict[str, Any],
    logger: GenerationLogger,
) -> set[str]:
    video_provider = config.providers["video"]
    shot_by_id = {shot["shot_id"]: shot for shot in shots}
    ready: set[str] = set()
    if not key_shots:
        logger.event("i2v", "skipped", "本次未指定图生视频镜头，全部使用回退渲染。")
        return ready
    for shot_id in key_shots:
        shot = shot_by_id.get(shot_id)
        if not shot:
            report["warnings"].append(f"关键镜头不存在：{shot_id}")
            logger.event("i2v", "missing_shot", "关键镜头不存在，无法调用视频接口。", shot_id, level="warning")
            continue
        video_path = run_dir / "assets" / "videos" / f"{shot_id}.mp4"
        image_path = run_dir / "assets" / "images" / f"{shot_id}.png"
        if not image_path.exists():
            report["warnings"].append(f"{shot_id} 缺少源图，跳过图生视频。")
            logger.event("i2v", "missing_image", "缺少源图，回退为静态分镜动效。", shot_id, {"fallback": "static_motion"}, "warning")
            continue
        meta_path = run_dir / "assets" / "videos" / f"{shot_id}.json"
        prompt = _safe_video_prompt(shot["video_prompt"])
        expected_meta = _video_meta(video_provider, prompt, image_path)
        if _media_cache_valid(video_path, meta_path, expected_meta):
            _add_step(report, "i2v", shot_id, "cached", str(video_path))
            ready.add(shot_id)
            logger.event("i2v", "cached", "使用已缓存且元数据匹配的 AI 视频。", shot_id, {"file": str(video_path)})
            continue
        try:
            image_data = _image_data_url(image_path)
            logger.event(
                "i2v",
                "submit",
                "调用视频生成接口。",
                shot_id,
                {
                    "model": video_provider.model,
                    "source_image": str(image_path),
                    "timeout_sec": timeout_sec,
                    "action": _short_text(shot.get("action", "")),
                    "dialogue": _short_text(shot.get("dialogue", "")),
                },
            )
            submit = client.submit_video(
                video_provider,
                prompt=prompt,
                image=image_data,
                image_size=f"{WIDTH}x{HEIGHT}",
            )
            request_id = submit["requestId"]
            logger.event("i2v", "submitted", "视频任务已提交，开始等待生成结果。", shot_id, {"request_id": request_id})
            status = _wait_for_video(client, video_provider, request_id, timeout_sec, report, shot_id, logger)
            if status.get("status") != "Succeed":
                warning = f"{shot_id} 图生视频失败，回退静态分镜动效：{status}"
                report["warnings"].append(warning)
                logger.event(
                    "i2v",
                    "failed",
                    "视频生成未成功，回退为静态分镜动效。",
                    shot_id,
                    {"status": status, "fallback": "static_motion"},
                    "warning",
                )
                continue
            video_url = status["results"]["videos"][0]["url"]
            _download(video_url, video_path)
            meta_path.write_text(json.dumps(expected_meta, ensure_ascii=False, indent=2), encoding="utf-8")
            _add_step(report, "i2v", shot_id, "generated", str(video_path))
            ready.add(shot_id)
            logger.event("i2v", "generated", "AI 视频生成并下载完成。", shot_id, {"file": str(video_path)})
        except Exception as exc:
            warning = f"{shot_id} 图生视频异常，回退静态分镜动效：{exc!r}"
            report["warnings"].append(warning)
            logger.event(
                "i2v",
                "error",
                "视频接口调用异常，回退为静态分镜动效。",
                shot_id,
                {"error": repr(exc), "fallback": "static_motion"},
                "warning",
            )
    return ready


def _render_visual_clips(
    shots: list[dict[str, Any]],
    run_dir: Path,
    clip_dir: Path,
    key_shots: list[str],
    video_ready_shots: set[str],
    report: dict[str, Any],
    logger: GenerationLogger,
) -> None:
    for index, shot in enumerate(shots):
        shot_id = shot["shot_id"]
        duration = int(shot["duration_sec"])
        clip_path = clip_dir / f"{index:03d}_{shot_id}.mp4"
        generated_video = run_dir / "assets" / "videos" / f"{shot_id}.mp4"
        if shot_id in video_ready_shots and generated_video.exists() and generated_video.stat().st_size > 0:
            _normalize_video_clip(generated_video, clip_path, duration)
            _add_step(report, "clip", shot_id, "from_i2v", str(clip_path))
            logger.event("clip", "from_i2v", "使用 AI 视频生成结果进入剪辑。", shot_id, {"file": str(clip_path)})
        else:
            image_path = run_dir / "assets" / "images" / f"{shot_id}.png"
            if shot_id in key_shots:
                logger.event("clip", "fallback", "该镜头未拿到可用 AI 视频，使用静态分镜动效回退。", shot_id, {"fallback": "static_motion"}, "warning")
            else:
                logger.event("clip", "static_motion", "非图生视频镜头，使用静态分镜动效。", shot_id)
            _render_static_clip(image_path, clip_path, duration, shot, index)
            _add_step(report, "clip", shot_id, "from_static_motion", str(clip_path))


def _safe_image_prompt(prompt: str) -> str:
    replacements = {
        "死者": "倒卧的无名信使",
        "尸": "无名信使",
        "血迹": "暗色雨痕",
        "染血": "暗色雨痕",
        "杀": "追查",
    }
    safe = prompt
    for source, target in replacements.items():
        safe = safe.replace(source, target)
    return (
        safe
        + "\n安全表达：无血腥、无恐怖画面、无伤口特写；用悬疑气氛、冷雨、灯火、雾气表达危险感。"
        + "\n画面要求：中国古风国漫，角色脸型稳定，服装和道具一致，竖屏 9:16，高清，无文字，无水印。"
    )


def _safe_video_prompt(prompt: str) -> str:
    replacements = {
        "死者": "神秘信使",
        "尸": "信使",
        "血迹": "暗色雨痕",
        "染血": "暗色雨痕",
        "杀": "追查",
        "刀光": "冷光",
    }
    safe = prompt
    for source, target in replacements.items():
        safe = safe.replace(source, target)
    return (
        safe
        + " No gore, no wounds, no horror, no text, no watermark. "
        + "Keep ancient Chinese anime style, stable character identity, slow cinematic motion, vertical 9:16."
    )


def _wait_for_video(
    client: SiliconFlowClient,
    provider: Any,
    request_id: str,
    timeout_sec: int,
    report: dict[str, Any],
    shot_id: str,
    logger: GenerationLogger,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_sec
    interval = int(provider.extra.get("poll_interval_sec", 5))
    while True:
        status = client.get_video_status(provider, request_id)
        state = status.get("status")
        _add_step(report, "i2v_poll", shot_id, state or "unknown", request_id)
        logger.event("i2v_poll", state or "unknown", "轮询视频任务状态。", shot_id, {"request_id": request_id})
        if state in {"Succeed", "Failed"}:
            return status
        if time.monotonic() >= deadline:
            logger.event(
                "i2v_poll",
                "timeout",
                "等待视频任务超时。",
                shot_id,
                {"request_id": request_id, "timeout_sec": timeout_sec, "last_status": status},
                "warning",
            )
            return {"status": "Timeout", "requestId": request_id, "last": status}
        time.sleep(interval)


def _render_static_clip(image_path: Path, clip_path: Path, duration: int, shot: dict[str, Any], index: int) -> None:
    """Render the original simple storyboard fallback from one still image."""
    motion = shot.get("camera_motion", "")
    frames = duration * FPS
    zoom_expr = "min(zoom+0.0012,1.08)"
    if "快速" in motion:
        zoom_expr = "min(zoom+0.0020,1.10)"
    elif "定格" in motion:
        zoom_expr = "min(zoom+0.0008,1.05)"
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"
    if index % 3 == 1:
        x_expr = f"(iw-iw/zoom)*(on/{frames})"
    elif index % 3 == 2:
        x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
    vf = (
        f"scale={WIDTH * 2}:{HEIGHT * 2}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH * 2}:{HEIGHT * 2},"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS},"
        "format=yuv420p"
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-t",
            str(duration),
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            str(clip_path),
        ]
    )


def _normalize_video_clip(video_path: Path, clip_path: Path, duration: int) -> None:
    vf = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p"
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(video_path),
            "-t",
            str(duration),
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            str(clip_path),
        ]
    )


def _concat_clips(shots: list[dict[str, Any]], clip_dir: Path, output_path: Path) -> None:
    concat_path = clip_dir / "concat.txt"
    lines = []
    for index, shot in enumerate(shots):
        clip_path = (clip_dir / f"{index:03d}_{shot['shot_id']}.mp4").resolve()
        lines.append(f"file '{clip_path}'")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(output_path),
        ]
    )


def _mix_voice_track(voice_lines: list[dict[str, Any]], run_dir: Path, output_path: Path, duration_sec: int) -> None:
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, line in enumerate(voice_lines):
        audio_path = run_dir / "audio" / f"{line['shot_id']}.mp3"
        if not audio_path.exists():
            continue
        inputs.extend(["-i", str(audio_path)])
        delay = int(math.floor(float(line["start_sec"]) * 1000))
        label = f"a{index}"
        filters.append(f"[{index}:a]adelay={delay}:all=1[{label}]")
        labels.append(f"[{label}]")
    if not labels:
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t",
                str(duration_sec),
                "-c:a",
                "aac",
                str(output_path),
            ]
        )
        return
    filter_complex = (
        ";".join(filters)
        + ";"
        + "".join(labels)
        + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0,"
        + f"apad=whole_dur={duration_sec},atrim=0:{duration_sec},alimiter=limit=0.95[aout]"
    )
    _run(
        [
            "ffmpeg",
            "-y",
            *inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(output_path),
        ]
    )


def _mux_audio_video(visual_path: Path, audio_path: Path, output_path: Path, duration_sec: int) -> None:
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(visual_path),
            "-i",
            str(audio_path),
            "-t",
            str(duration_sec),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output_path),
        ]
    )


def _burn_subtitles(input_path: Path, subtitle_path: Path, output_path: Path, report: dict[str, Any]) -> None:
    overlays = _write_subtitle_overlays(subtitle_path, subtitle_path.parent / "subtitle_overlays")
    if not overlays:
        _run(["ffmpeg", "-y", "-i", str(input_path), "-c", "copy", str(output_path)])
        return
    duration = str(_probe_duration(input_path))
    inputs: list[str] = []
    filters: list[str] = []
    previous = "[0:v]"
    for index, overlay in enumerate(overlays, start=1):
        inputs.extend(["-loop", "1", "-i", str(overlay["path"])])
        output_label = f"v{index}"
        filters.append(
            f"{previous}[{index}:v]overlay=0:0:enable='between(t,{overlay['start']},{overlay['end']})'[{output_label}]"
        )
        previous = f"[{output_label}]"
    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                *inputs,
                "-filter_complex",
                ";".join(filters),
                "-map",
                previous,
                "-map",
                "0:a:0",
                "-t",
                duration,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "copy",
                str(output_path),
            ]
        )
    except RuntimeError as exc:
        report["warnings"].append(f"字幕烧录失败，使用无字幕版本：{exc}")
        _run(["ffmpeg", "-y", "-i", str(input_path), "-c", "copy", str(output_path)])


def _write_subtitle_overlays(srt_path: Path, overlay_dir: Path) -> list[dict[str, Any]]:
    overlay_dir.mkdir(parents=True, exist_ok=True)
    for old_file in overlay_dir.glob("*.png"):
        old_file.unlink()
    srt = srt_path.read_text(encoding="utf-8").strip()
    font = _load_subtitle_font(48)
    overlays: list[dict[str, Any]] = []
    for index, block in enumerate(re_split_srt_blocks(srt), start=1):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = [item.strip() for item in lines[1].split("-->")]
        text = " ".join(line.strip() for line in lines[2:])
        image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        wrapped = _wrap_subtitle_text(draw, text, font, WIDTH - 96)
        line_gap = 8
        line_heights = []
        line_widths = []
        for line in wrapped:
            box = draw.textbbox((0, 0), line, font=font, stroke_width=2)
            line_widths.append(box[2] - box[0])
            line_heights.append(box[3] - box[1])
        text_height = sum(line_heights) + max(0, len(wrapped) - 1) * line_gap
        box_width = min(WIDTH - 64, max(line_widths) + 56)
        box_height = text_height + 34
        box_x0 = (WIDTH - box_width) // 2
        box_y0 = HEIGHT - box_height - 92
        draw.rounded_rectangle(
            [box_x0, box_y0, box_x0 + box_width, box_y0 + box_height],
            radius=18,
            fill=(0, 0, 0, 132),
        )
        y = box_y0 + 17
        for line, line_width, line_height in zip(wrapped, line_widths, line_heights):
            x = (WIDTH - line_width) // 2
            draw.text(
                (x, y),
                line,
                font=font,
                fill=(255, 255, 255, 255),
                stroke_width=2,
                stroke_fill=(0, 0, 0, 220),
            )
            y += line_height + line_gap
        path = overlay_dir / f"subtitle_{index:03d}.png"
        image.save(path)
        overlays.append({"path": path, "start": _seconds_from_srt(start), "end": _seconds_from_srt(end)})
    return overlays


def _load_subtitle_font(size: int) -> ImageFont.FreeTypeFont:
    for font_path in [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]:
        path = Path(font_path)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def _wrap_subtitle_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        box = draw.textbbox((0, 0), candidate, font=font, stroke_width=2)
        if box[2] - box[0] <= max_width or not current:
            current = candidate
            continue
        lines.append(current)
        current = char
    if current:
        lines.append(current)
    return lines[-2:] if len(lines) > 2 else lines


def _seconds_from_srt(srt_time: str) -> float:
    hours, minutes, rest = srt_time.replace(",", ".").split(":")
    seconds, millis = rest.split(".")
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis[:3].ljust(3, "0")) / 1000


def _write_ass_from_srt(srt_path: Path, ass_path: Path) -> None:
    srt = srt_path.read_text(encoding="utf-8").strip()
    events: list[str] = []
    for block in re_split_srt_blocks(srt):
        lines = [line for line in block.splitlines() if line.strip()]
        if len(lines) < 3 or "-->" not in lines[1]:
            continue
        start, end = [item.strip() for item in lines[1].split("-->")]
        text = r"\N".join(_escape_ass_text(line.strip()) for line in lines[2:])
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}")
    ass = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {WIDTH}",
            f"PlayResY: {HEIGHT}",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,PingFang SC,48,&H00FFFFFF,&H000000FF,&H90000000,&H90000000,0,0,0,0,100,100,0,0,1,3,1,2,40,40,108,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *events,
            "",
        ]
    )
    ass_path.write_text(ass, encoding="utf-8")


def re_split_srt_blocks(srt: str) -> list[str]:
    return [block.strip() for block in srt.replace("\r\n", "\n").split("\n\n") if block.strip()]


def _ass_time(srt_time: str) -> str:
    hours, minutes, rest = srt_time.replace(",", ".").split(":")
    seconds, centis = rest.split(".")
    return f"{int(hours)}:{minutes}:{seconds}.{centis[:2]}"


def _escape_ass_text(text: str) -> str:
    return text.replace("{", "（").replace("}", "）")


def _extract_cover(video_path: Path, cover_path: Path) -> None:
    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:03",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(cover_path),
        ]
    )


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return round(float(result.stdout.strip()), 3)


def _download(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=180.0) as client:
        response = client.get(url)
    response.raise_for_status()
    path.write_bytes(response.content)


def _image_data_url(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_video_shots(shots: list[dict[str, Any]], requested: list[str] | None) -> list[str]:
    if requested is not None:
        return requested
    return [shot["shot_id"] for shot in shots if shot.get("production_mode") == "image_to_video"]


def _normalize_stages(stages: list[str]) -> list[str]:
    normalized: list[str] = []
    for stage in stages:
        for item in str(stage).split(","):
            name = item.strip().lower()
            if not name:
                continue
            if name == "all":
                for default_stage in STAGE_ORDER:
                    if default_stage not in normalized:
                        normalized.append(default_stage)
                continue
            if name not in STAGE_ORDER:
                raise ValueError(f"Unsupported stage: {name}. Expected one of: {', '.join(STAGE_ORDER)}, all")
            if name not in normalized:
                normalized.append(name)
    return normalized or list(STAGE_ORDER)


def _ready_video_shots(config: Any, shots: list[dict[str, Any]], run_dir: Path, key_shots: list[str]) -> set[str]:
    video_provider = config.providers["video"]
    ready: set[str] = set()
    shot_by_id = {shot["shot_id"]: shot for shot in shots}
    for shot_id in key_shots:
        shot = shot_by_id.get(shot_id)
        if not shot:
            continue
        video_path = run_dir / "assets" / "videos" / f"{shot_id}.mp4"
        image_path = run_dir / "assets" / "images" / f"{shot_id}.png"
        meta_path = run_dir / "assets" / "videos" / f"{shot_id}.json"
        if not image_path.exists():
            continue
        prompt = _safe_video_prompt(shot["video_prompt"])
        expected_meta = _video_meta(video_provider, prompt, image_path)
        if _media_cache_valid(video_path, meta_path, expected_meta):
            ready.add(shot_id)
    return ready


def _short_text(text: Any, limit: int = 80) -> str:
    value = str(text or "").replace("\n", " ").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _media_cache_valid(asset_path: Path, meta_path: Path, expected_meta: dict[str, Any]) -> bool:
    if not asset_path.exists() or asset_path.stat().st_size <= 0 or not meta_path.exists():
        return False
    try:
        current_meta = _read_json(meta_path)
    except (OSError, json.JSONDecodeError):
        return False
    return current_meta == expected_meta


def _image_meta(provider: Any, prompt: str) -> dict[str, Any]:
    extra = provider.extra
    return {
        "version": MEDIA_META_VERSION,
        "type": "image",
        "provider": provider.provider,
        "model": provider.model,
        "endpoint": provider.endpoint,
        "prompt": prompt,
        "image_size": f"{WIDTH}x{HEIGHT}",
        "negative_prompt": extra.get("negative_prompt"),
        "batch_size": extra.get("batch_size"),
        "num_inference_steps": extra.get("num_inference_steps"),
        "guidance_scale": extra.get("guidance_scale"),
    }


def _video_meta(provider: Any, prompt: str, image_path: Path) -> dict[str, Any]:
    extra = provider.extra
    return {
        "version": MEDIA_META_VERSION,
        "type": "image_to_video",
        "provider": provider.provider,
        "model": provider.model,
        "endpoint": provider.endpoint,
        "prompt": prompt,
        "image_size": f"{WIDTH}x{HEIGHT}",
        "negative_prompt": extra.get("negative_prompt"),
        "source_image": image_path.name,
        "source_image_sha256": _file_sha256(image_path),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tts_cache_valid(voice_path: Path, meta_path: Path, expected_meta: dict[str, Any]) -> bool:
    if not voice_path.exists() or voice_path.stat().st_size <= 0 or not meta_path.exists():
        return False
    try:
        current_meta = _read_json(meta_path)
    except (OSError, json.JSONDecodeError):
        return False
    return current_meta == expected_meta


def _tts_meta(
    provider: Any,
    line: dict[str, Any],
    voice_type: str | int | None = None,
    emotion_category: str | None = None,
) -> dict[str, Any]:
    extra = provider.extra
    codec = extra.get("codec") or extra.get("response_format", "mp3")
    return {
        "version": TTS_META_VERSION,
        "provider": provider.provider,
        "model": provider.model,
        "endpoint": provider.endpoint,
        "speaker": line.get("speaker"),
        "character_id": line.get("character_id"),
        "text": line.get("text"),
        "voice_type": voice_type,
        "emotion_category": emotion_category,
        "codec": codec,
        "sample_rate": extra.get("sample_rate"),
        "speed": extra.get("speed"),
        "volume": extra.get("volume"),
    }


def _add_step(report: dict[str, Any], step: str, item: str, status: str, detail: str) -> None:
    report["steps"].append({"step": step, "item": item, "status": status, "detail": detail})


def _run(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(args)}\nSTDOUT:\n{result.stdout[-2000:]}\nSTDERR:\n{result.stderr[-4000:]}"
        )
