from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


ProductionMode = Literal["static_motion", "image_to_video", "manual_review"]


class Character(BaseModel):
    id: str
    name: str
    role: str
    appearance: str
    personality: str = ""
    gender: str = ""
    voice_style: str = ""
    voice_type: int | None = None
    visual_lock: list[str] = Field(default_factory=list)


class Location(BaseModel):
    id: str
    name: str
    description: str
    visual_lock: list[str] = Field(default_factory=list)


class StoryBeat(BaseModel):
    id: str
    summary: str
    emotion: str = ""
    location_id: str | None = None
    characters: list[str] = Field(default_factory=list)
    dialogue_first: str = ""
    dialogue_second: str = ""
    action_first: str = ""
    action_second: str = ""
    production_mode_first: ProductionMode | None = None
    production_mode_second: ProductionMode | None = None


class ProjectBrief(BaseModel):
    project_id: str
    title: str
    genre: str = ""
    format: str = "vertical_dynamic_comic"
    aspect_ratio: str = "9:16"
    target_duration_sec: int = 75
    audience: str = ""
    logline: str
    visual_style: str = ""
    tone: str = ""
    characters: list[Character] = Field(default_factory=list)
    locations: list[Location] = Field(default_factory=list)
    beats: list[StoryBeat] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_character_voice_types(self) -> "ProjectBrief":
        assigned: dict[int, str] = {}
        for character in self.characters:
            if character.voice_type is None:
                continue
            if character.voice_type in assigned:
                raise ValueError(
                    f"角色音色不能重复：{assigned[character.voice_type]} 和 {character.name} "
                    f"都使用 VoiceType {character.voice_type}。"
                )
            assigned[character.voice_type] = character.name or character.id
        return self


class ProviderConfig(BaseModel):
    enabled: bool = False
    provider: str = ""
    model: str = ""
    api_key_env: str = ""
    base_url: str = ""
    endpoint: str = ""
    notes: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class PipelineDefaults(BaseModel):
    language: str = "zh-CN"
    format: str = "vertical_dynamic_comic"
    aspect_ratio: str = "9:16"
    target_duration_sec: int = 75
    shot_duration_sec: int = 5
    production_mode: ProductionMode = "static_motion"
    subtitle_style: str = "mobile_drama"


class QualityGates(BaseModel):
    max_target_duration_drift_sec: int = 12
    require_character_visual_locks: bool = True
    require_dialogue_or_audio_note: bool = True
    require_prompt_per_shot: bool = True


class PipelineConfig(BaseModel):
    project: dict[str, Any] = Field(default_factory=dict)
    defaults: PipelineDefaults = Field(default_factory=PipelineDefaults)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    quality_gates: QualityGates = Field(default_factory=QualityGates)


class Shot(BaseModel):
    shot_id: str
    beat_id: str
    scene: str
    location_id: str | None = None
    location_name: str = ""
    characters: list[str] = Field(default_factory=list)
    shot_size: str
    camera_motion: str
    duration_sec: int
    action: str
    emotion: str = ""
    dialogue: str = ""
    audio_notes: str = ""
    image_prompt: str
    video_prompt: str
    production_mode: ProductionMode = "static_motion"


class VoiceLine(BaseModel):
    line_id: str
    shot_id: str
    character_id: str | None = None
    speaker: str
    text: str
    start_sec: float
    end_sec: float
    voice_style: str = ""
    voice_type: int | None = None


class PipelineResult(BaseModel):
    project_id: str
    episode: int
    run_dir: str
    total_duration_sec: int
    shot_count: int
    files: dict[str, str]
    warnings: list[str] = Field(default_factory=list)
