from __future__ import annotations

from typing import Protocol

from manga_flow.schemas import PipelineConfig, ProjectBrief, Shot


class StoryPlanner(Protocol):
    def create_shots(self, brief: ProjectBrief, config: PipelineConfig, episode: int) -> list[Shot]:
        """Create a shot list for one episode."""
