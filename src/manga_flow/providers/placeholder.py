from __future__ import annotations

from itertools import cycle

from manga_flow.schemas import PipelineConfig, ProjectBrief, Shot, StoryBeat


SHOT_SIZES = ["特写", "中近景", "中景", "远景"]
CAMERA_MOVES = ["缓慢推进", "轻微横移", "快速切入", "定格后轻推"]


class PlaceholderPlanner:
    """Deterministic planner used until real model providers are selected."""

    def create_shots(self, brief: ProjectBrief, config: PipelineConfig, episode: int) -> list[Shot]:
        beats = brief.beats or _fallback_beats(brief)
        shot_duration = _resolve_shot_duration(brief, config, len(beats))
        location_by_id = {location.id: location for location in brief.locations}
        shot_sizes = cycle(SHOT_SIZES)
        camera_moves = cycle(CAMERA_MOVES)
        shots: list[Shot] = []

        for index, beat in enumerate(beats, start=1):
            location = location_by_id.get(beat.location_id or "")
            focus_characters = _pick_characters(brief, beat, index)
            base_id = f"e{episode:03d}s{index:03d}"

            shots.append(
                Shot(
                    shot_id=f"{base_id}a",
                    beat_id=beat.id,
                    scene=beat.summary,
                    location_id=location.id if location else None,
                    location_name=location.name if location else "",
                    characters=focus_characters,
                    shot_size=next(shot_sizes),
                    camera_motion=next(camera_moves),
                    duration_sec=shot_duration,
                    action=beat.action_first or f"建立情境：{beat.summary}",
                    emotion=beat.emotion,
                    dialogue=_dialogue_for_beat(brief, beat, focus_characters, first=True),
                    audio_notes=f"情绪关键词：{beat.emotion or '推进剧情'}",
                    image_prompt=_image_prompt(brief, beat, location, focus_characters, "建立镜头"),
                    video_prompt=_video_prompt(brief, beat, "细微表情变化，镜头轻微运动"),
                    production_mode=beat.production_mode_first or config.defaults.production_mode,
                )
            )

            shots.append(
                Shot(
                    shot_id=f"{base_id}b",
                    beat_id=beat.id,
                    scene=beat.summary,
                    location_id=location.id if location else None,
                    location_name=location.name if location else "",
                    characters=focus_characters,
                    shot_size=next(shot_sizes),
                    camera_motion=next(camera_moves),
                    duration_sec=shot_duration,
                    action=beat.action_second or f"制造推进或反转：{beat.summary}",
                    emotion=beat.emotion,
                    dialogue=_dialogue_for_beat(brief, beat, focus_characters, first=False),
                    audio_notes=f"音效建议：短促转场、低频冲击、环境底噪。情绪：{beat.emotion or '紧张'}",
                    image_prompt=_image_prompt(brief, beat, location, focus_characters, "情绪推进镜头"),
                    video_prompt=_video_prompt(brief, beat, "更明显的眼神、手部动作或环境变化"),
                    production_mode=beat.production_mode_second
                    or ("image_to_video" if beat.id in {"reversal", "cliffhanger"} else config.defaults.production_mode),
                )
            )

        return shots


def _fallback_beats(brief: ProjectBrief) -> list[StoryBeat]:
    return [
        StoryBeat(id="hook", summary=f"用强钩子打开：{brief.logline}", emotion="震惊"),
        StoryBeat(id="choice", summary="主角做出不可逆决定。", emotion="果断"),
        StoryBeat(id="conflict", summary="对手或障碍出现，计划受阻。", emotion="紧张"),
        StoryBeat(id="reversal", summary="主角抛出关键信息，局面反转。", emotion="压迫"),
        StoryBeat(id="cliffhanger", summary="留下下一集必须看的问题。", emotion="悬念"),
    ]


def _resolve_shot_duration(brief: ProjectBrief, config: PipelineConfig, beat_count: int) -> int:
    target_duration = brief.target_duration_sec or config.defaults.target_duration_sec
    planned_shots = max(1, beat_count * 2)
    if target_duration:
        return max(3, round(target_duration / planned_shots))
    return max(3, config.defaults.shot_duration_sec)


def _pick_characters(brief: ProjectBrief, beat: StoryBeat, index: int) -> list[str]:
    if beat.characters:
        return beat.characters
    character_ids = [character.id for character in brief.characters]
    if not character_ids:
        return []
    if len(character_ids) == 1:
        return [character_ids[0]]
    if beat.id in {"hook", "decision"}:
        return [character_ids[0]]
    if beat.id == "conflict" and len(character_ids) >= 3:
        return [character_ids[0], character_ids[2]]
    if beat.id in {"reversal", "cliffhanger"}:
        return [character_ids[0], character_ids[1]]
    first = character_ids[(index - 1) % len(character_ids)]
    second = character_ids[index % len(character_ids)]
    return [first, second]


def _dialogue_for_beat(brief: ProjectBrief, beat: StoryBeat, character_ids: list[str], first: bool) -> str:
    if first and beat.dialogue_first:
        return beat.dialogue_first
    if not first and beat.dialogue_second:
        return beat.dialogue_second
    if not character_ids:
        return "旁白：命运的齿轮在这一刻重新转动。"
    protagonist = brief.characters[0].name if brief.characters else "主角"
    lead_male = brief.characters[1].name if len(brief.characters) > 1 else "对手"
    owner = brief.characters[2].name if len(brief.characters) > 2 else "业主"
    lines = {
        ("hook", True): f"{protagonist}：不对，这不是梦。",
        ("hook", False): f"{protagonist}：三年前的今天，我还有机会翻盘。",
        ("decision", True): f"{protagonist}：老街改造公告，是下周二凌晨发的。",
        ("decision", False): f"{protagonist}：这间铺子，我今天必须拿下。",
        ("conflict", True): f"{owner}：姑娘，刚才那价格我不卖了。",
        ("conflict", False): f"{protagonist}：合同我现在签，钱立刻到账。",
        ("reversal", True): f"{protagonist}：蓝色卷帘门旁边，会先拆第一栋。",
        ("reversal", False): f"{lead_male}：这份内部规划，你从哪看到的？",
        ("cliffhanger", True): f"{lead_male}：合同归你，但我只问一个问题。",
        ("cliffhanger", False): f"{lead_male}：你到底从哪里知道这件事？",
    }
    return lines.get((beat.id, first), f"{protagonist}：这一次，我要先拿到主动权。")


def _image_prompt(brief: ProjectBrief, beat: StoryBeat, location: object, character_ids: list[str], purpose: str) -> str:
    character_desc = []
    for character in brief.characters:
        if character.id in character_ids:
            locks = "，".join(character.visual_lock)
            character_desc.append(f"{character.name}：{character.appearance}，固定特征：{locks}")
    location_text = ""
    if location is not None:
        locks = "，".join(getattr(location, "visual_lock", []))
        location_text = f"场景：{getattr(location, 'name', '')}，{getattr(location, 'description', '')}，固定元素：{locks}"
    return (
        f"{brief.visual_style}\n"
        f"用途：{purpose}\n"
        f"剧情：{beat.summary}\n"
        f"情绪：{beat.emotion}\n"
        f"{location_text}\n"
        f"角色：{'；'.join(character_desc)}\n"
        f"构图：竖屏 {brief.aspect_ratio}，漫画分镜，画面清晰，无文字水印。"
    ).strip()


def _video_prompt(brief: ProjectBrief, beat: StoryBeat, motion: str) -> str:
    return (
        f"竖屏 {brief.aspect_ratio} AI 漫剧镜头。剧情：{beat.summary}。"
        f"情绪：{beat.emotion or '紧张'}。运动：{motion}。"
        "保持角色脸型、服装、发型和场景固定元素一致。"
    )
