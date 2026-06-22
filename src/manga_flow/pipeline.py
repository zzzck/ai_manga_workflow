from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape

from .config import load_config, load_project
from .providers.placeholder import PlaceholderPlanner
from .schemas import PipelineResult, ProjectBrief, Shot, VoiceLine


def run_pipeline(project_path: Path, config_path: Path, episode: int | None = None) -> PipelineResult:
    project_path = project_path.resolve()
    config_path = config_path.resolve()
    brief = load_project(project_path)
    config = load_config(config_path)
    episode_number = episode or int(config.project.get("default_episode", 1))

    root_dir = _infer_root_dir(config_path)
    output_dir = Path(config.project.get("output_dir", "outputs"))
    if not output_dir.is_absolute():
        output_dir = root_dir / output_dir
    run_dir = output_dir / brief.project_id / f"episode_{episode_number:03d}"
    _create_run_dirs(run_dir)

    planner = PlaceholderPlanner()
    shots = planner.create_shots(brief, config, episode_number)
    voice_lines = _build_voice_lines(brief, shots)
    total_duration = sum(shot.duration_sec for shot in shots)

    files: dict[str, str] = {}
    files["shot_list_json"] = _write_json(run_dir / "shot_list.json", [shot.model_dump(mode="json") for shot in shots])
    files["shot_list_csv"] = _write_shot_csv(run_dir / "shot_list.csv", shots)
    files["script"] = _write_script_markdown(run_dir / "script.md", brief, shots)
    files["storyboard_html"] = _write_storyboard_html(run_dir / "storyboard.html", brief, shots)
    files["subtitles"] = _write_srt(run_dir / "audio" / "subtitles.srt", voice_lines)
    files["voice_lines"] = _write_json(run_dir / "audio" / "voice_lines.json", [line.model_dump(mode="json") for line in voice_lines])
    files["edit_plan"] = _write_json(run_dir / "edit" / "edit_plan.json", _build_edit_plan(brief, shots, voice_lines, config.providers))
    files["asset_manifest"] = _write_json(run_dir / "assets" / "asset_manifest.json", _build_asset_manifest(brief, shots))
    files.update(_write_prompts(run_dir, shots))
    warnings = _collect_warnings(brief, shots, total_duration, config)
    files["qc_report"] = _write_qc_report(run_dir / "reports" / "qc_report.md", brief, shots, total_duration, warnings)
    files["run_manifest"] = str(run_dir / "run_manifest.json")

    result = PipelineResult(
        project_id=brief.project_id,
        episode=episode_number,
        run_dir=str(run_dir),
        total_duration_sec=total_duration,
        shot_count=len(shots),
        files=files,
        warnings=warnings,
    )
    _write_json(run_dir / "run_manifest.json", result.model_dump(mode="json"))
    return result


def _infer_root_dir(config_path: Path) -> Path:
    if config_path.parent.name == "config":
        return config_path.parent.parent
    return Path.cwd()


def _create_run_dirs(run_dir: Path) -> None:
    for child in [
        run_dir,
        run_dir / "prompts" / "image",
        run_dir / "prompts" / "video",
        run_dir / "audio",
        run_dir / "assets" / "characters",
        run_dir / "assets" / "locations",
        run_dir / "assets" / "images",
        run_dir / "assets" / "videos",
        run_dir / "edit",
        run_dir / "reports",
    ]:
        child.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, data: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    return str(path)


def _write_shot_csv(path: Path, shots: list[Shot]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "shot_id",
        "beat_id",
        "duration_sec",
        "location_name",
        "characters",
        "shot_size",
        "camera_motion",
        "production_mode",
        "action",
        "dialogue",
        "image_prompt",
        "video_prompt",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for shot in shots:
            row = shot.model_dump(mode="json")
            row["characters"] = ",".join(shot.characters)
            writer.writerow({field: row.get(field, "") for field in fields})
    return str(path)


def _write_script_markdown(path: Path, brief: ProjectBrief, shots: list[Shot]) -> str:
    character_name_by_id = {character.id: character.name for character in brief.characters}
    lines = [
        f"# {brief.title}",
        "",
        f"- 项目 ID：`{brief.project_id}`",
        f"- 类型：{brief.genre}",
        f"- 形式：{brief.format}",
        f"- 画幅：{brief.aspect_ratio}",
        f"- 目标时长：{brief.target_duration_sec} 秒",
        "",
        "## 一句话故事",
        "",
        brief.logline,
        "",
        "## 角色锁定",
        "",
    ]
    for character in brief.characters:
        locks = "，".join(character.visual_lock) or "待补充"
        lines.append(f"- {character.name}（{character.role}）：{character.appearance}；固定特征：{locks}")
    lines.extend(["", "## 分镜脚本", ""])
    for shot in shots:
        characters = "、".join(character_name_by_id.get(character_id, character_id) for character_id in shot.characters) or "无"
        lines.extend(
            [
                f"### {shot.shot_id}",
                "",
                f"- 场景：{shot.location_name or shot.location_id or '待定'}",
                f"- 角色：{characters}",
                f"- 景别/运动：{shot.shot_size}，{shot.camera_motion}",
                f"- 时长：{shot.duration_sec} 秒",
                f"- 动作：{shot.action}",
                f"- 台词：{shot.dialogue or '无'}",
                f"- 生产模式：`{shot.production_mode}`",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _write_storyboard_html(path: Path, brief: ProjectBrief, shots: list[Shot]) -> str:
    env = Environment(autoescape=select_autoescape(default=True))
    template = env.from_string(
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ brief.title }} - Storyboard</title>
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f7f4; color: #1f2328; }
    header { padding: 28px 24px; background: #ffffff; border-bottom: 1px solid #deded8; }
    h1 { margin: 0 0 8px; font-size: 28px; }
    main { padding: 24px; display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }
    article { background: #ffffff; border: 1px solid #deded8; border-radius: 8px; overflow: hidden; }
    .frame { aspect-ratio: 9 / 16; background: linear-gradient(160deg, #e6e2d8, #f7f7f4); display: grid; place-items: center; padding: 18px; text-align: center; }
    .frame strong { font-size: 22px; }
    .body { padding: 16px; }
    p { margin: 0 0 10px; line-height: 1.55; }
    pre { white-space: pre-wrap; font-size: 12px; background: #f2f2ee; border-radius: 6px; padding: 10px; overflow-wrap: anywhere; }
    .meta { color: #60666c; font-size: 13px; }
  </style>
</head>
<body>
  <header>
    <h1>{{ brief.title }}</h1>
    <div class="meta">{{ brief.genre }} / {{ brief.aspect_ratio }} / {{ brief.target_duration_sec }} 秒目标时长</div>
  </header>
  <main>
    {% for shot in shots %}
    <article>
      <div class="frame">
        <div>
          <strong>{{ shot.shot_id }}</strong>
          <p>{{ shot.shot_size }} · {{ shot.camera_motion }}</p>
          <p>{{ shot.location_name or "场景待定" }}</p>
        </div>
      </div>
      <div class="body">
        <p><strong>动作：</strong>{{ shot.action }}</p>
        <p><strong>台词：</strong>{{ shot.dialogue or "无" }}</p>
        <p><strong>模式：</strong>{{ shot.production_mode }}，{{ shot.duration_sec }} 秒</p>
        <pre>{{ shot.image_prompt }}</pre>
      </div>
    </article>
    {% endfor %}
  </main>
</body>
</html>
"""
    )
    path.write_text(template.render(brief=brief, shots=shots), encoding="utf-8")
    return str(path)


def _write_prompts(run_dir: Path, shots: list[Shot]) -> dict[str, str]:
    files: dict[str, str] = {}
    for shot in shots:
        image_path = run_dir / "prompts" / "image" / f"{shot.shot_id}.txt"
        video_path = run_dir / "prompts" / "video" / f"{shot.shot_id}.txt"
        image_path.write_text(shot.image_prompt + "\n", encoding="utf-8")
        video_path.write_text(shot.video_prompt + "\n", encoding="utf-8")
    files["image_prompts_dir"] = str(run_dir / "prompts" / "image")
    files["video_prompts_dir"] = str(run_dir / "prompts" / "video")
    return files


def _build_voice_lines(brief: ProjectBrief, shots: list[Shot]) -> list[VoiceLine]:
    character_by_name = {character.name: character for character in brief.characters}
    character_by_name.update({character.id: character for character in brief.characters})
    lines: list[VoiceLine] = []
    cursor = 0.0
    for shot in shots:
        start = cursor
        end = cursor + shot.duration_sec
        cursor = end
        if not shot.dialogue:
            continue
        speaker, text = _split_dialogue(shot.dialogue)
        character = character_by_name.get(speaker)
        lines.append(
            VoiceLine(
                line_id=f"line_{shot.shot_id}",
                shot_id=shot.shot_id,
                character_id=character.id if character else None,
                speaker=speaker,
                text=text,
                start_sec=start,
                end_sec=end,
                voice_style=character.voice_style if character else "旁白或待定声音",
                voice_type=character.voice_type if character else None,
            )
        )
    return lines


def _split_dialogue(dialogue: str) -> tuple[str, str]:
    parts = re.split(r"[:：]", dialogue, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "旁白", dialogue.strip()


def _write_srt(path: Path, voice_lines: list[VoiceLine]) -> str:
    entries: list[str] = []
    for index, line in enumerate(voice_lines, start=1):
        entries.extend(
            [
                str(index),
                f"{_srt_time(line.start_sec)} --> {_srt_time(line.end_sec)}",
                f"{line.speaker}：{line.text}",
                "",
            ]
        )
    path.write_text("\n".join(entries), encoding="utf-8")
    return str(path)


def _srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _build_edit_plan(
    brief: ProjectBrief,
    shots: list[Shot],
    voice_lines: list[VoiceLine],
    providers: dict[str, Any],
) -> dict[str, Any]:
    voice_by_shot = {line.shot_id: line for line in voice_lines}
    timeline = []
    cursor = 0
    for shot in shots:
        voice = voice_by_shot.get(shot.shot_id)
        timeline.append(
            {
                "shot_id": shot.shot_id,
                "start_sec": cursor,
                "end_sec": cursor + shot.duration_sec,
                "duration_sec": shot.duration_sec,
                "mode": shot.production_mode,
                "image_asset": f"assets/images/{shot.shot_id}.png",
                "video_asset": f"assets/videos/{shot.shot_id}.mp4",
                "voice_asset": f"audio/{shot.shot_id}.wav" if voice else "",
                "subtitle": f"{voice.speaker}：{voice.text}" if voice else "",
                "camera_motion": shot.camera_motion,
                "transition": "cut",
            }
        )
        cursor += shot.duration_sec
    return {
        "project_id": brief.project_id,
        "title": brief.title,
        "aspect_ratio": brief.aspect_ratio,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider_slots": {name: provider.model_dump(mode="json") for name, provider in providers.items()},
        "timeline": timeline,
    }


def _build_asset_manifest(brief: ProjectBrief, shots: list[Shot]) -> dict[str, Any]:
    return {
        "characters": [character.model_dump(mode="json") for character in brief.characters],
        "locations": [location.model_dump(mode="json") for location in brief.locations],
        "required_images": [f"assets/images/{shot.shot_id}.png" for shot in shots],
        "required_videos": [
            f"assets/videos/{shot.shot_id}.mp4" for shot in shots if shot.production_mode == "image_to_video"
        ],
        "notes": "当前为资产清单占位。接入真实图片/视频模型后由 provider 写入实际文件。",
    }


def _collect_warnings(brief: ProjectBrief, shots: list[Shot], total_duration: int, config: Any) -> list[str]:
    warnings: list[str] = []
    target = brief.target_duration_sec or config.defaults.target_duration_sec
    drift = abs(total_duration - target)
    if drift > config.quality_gates.max_target_duration_drift_sec:
        warnings.append(f"总时长 {total_duration}s 与目标 {target}s 相差 {drift}s，超过阈值。")
    if config.quality_gates.require_character_visual_locks:
        for character in brief.characters:
            if not character.visual_lock:
                warnings.append(f"角色 {character.name} 缺少 visual_lock，后续容易变脸或变装。")
    if config.quality_gates.require_prompt_per_shot:
        for shot in shots:
            if not shot.image_prompt or not shot.video_prompt:
                warnings.append(f"镜头 {shot.shot_id} 缺少图片或视频提示词。")
    if config.quality_gates.require_dialogue_or_audio_note:
        for shot in shots:
            if not shot.dialogue and not shot.audio_notes:
                warnings.append(f"镜头 {shot.shot_id} 缺少台词和音频说明。")
    if any(shot.production_mode == "image_to_video" for shot in shots):
        video_provider = config.providers.get("video")
        if not video_provider or not video_provider.enabled:
            warnings.append("存在 image_to_video 镜头，但 video provider 仍未启用；当前只生成占位清单。")
    return warnings


def _write_qc_report(path: Path, brief: ProjectBrief, shots: list[Shot], total_duration: int, warnings: list[str]) -> str:
    lines = [
        f"# QC Report - {brief.title}",
        "",
        f"- 镜头数：{len(shots)}",
        f"- 总时长：{total_duration} 秒",
        f"- 目标时长：{brief.target_duration_sec} 秒",
        "",
        "## 自动检查",
        "",
    ]
    if warnings:
        lines.extend(f"- 警告：{warning}" for warning in warnings)
    else:
        lines.append("- 未发现阻塞性问题。")
    lines.extend(
        [
            "",
            "## 人工复核建议",
            "",
            "- 检查主角脸型、发型、服装是否跨镜头一致。",
            "- 检查字幕是否符合竖屏短剧节奏，单句不要过长。",
            "- 检查关键钩子是否出现在前 3 秒和结尾 5 秒。",
            "- 接入真实模型后补充黑屏、静音、音画同步检测。",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
