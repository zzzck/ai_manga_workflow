# 技术架构

## 总体结构

```text
项目 YAML
  -> 配置 YAML
  -> Planner
  -> Shot List
  -> Prompt Pack
  -> Asset Manifest
  -> Voice Lines
  -> Edit Plan
  -> QC Report
```

当前版本的 `Planner` 是确定性的占位实现，不调用任何远程模型。

## 核心模块

### `manga_flow.schemas`

定义数据结构：

- `ProjectBrief`
- `Character`
- `Location`
- `StoryBeat`
- `Shot`
- `VoiceLine`
- `PipelineConfig`
- `PipelineResult`

### `manga_flow.config`

负责读取 YAML：

- 项目设定：`data/projects/*.yaml`
- 流程配置：`config/*.yaml`

### `manga_flow.providers`

模型供应商抽象层。当前只有：

- `PlaceholderPlanner`

后续建议增加：

- `LlmPlanner`
- `ImageGenerator`
- `VideoGenerator`
- `VoiceGenerator`
- `MusicGenerator`

每个 provider 只负责一类模型调用，不要把业务逻辑写进 provider。

### `manga_flow.pipeline`

负责串联流程并写入产物：

- 分镜表
- 提示词
- 字幕
- 剪辑清单
- 资产清单
- 质检报告

### `manga_flow.cli`

命令行入口：

- `manga-flow check`
- `manga-flow run`
- `manga-flow init-project`

## 模型接入位置

模型选择集中在：

```text
config/pipeline.example.yaml
```

真实 API 代码建议放在：

```text
src/manga_flow/providers/
```

例如：

```text
src/manga_flow/providers/openai_llm.py
src/manga_flow/providers/openai_image.py
src/manga_flow/providers/runway_video.py
src/manga_flow/providers/elevenlabs_voice.py
```

## 数据边界

建议保持这些边界清晰：

- `ProjectBrief`：创意设定，不包含生成结果。
- `Shot`：生产计划，包含提示词和镜头要求。
- `AssetManifest`：应该生成哪些文件。
- `EditPlan`：如何把资产拼成片。
- `QCReport`：当前产物是否能进入下一步。

## 后续剪辑实现建议

第一步可以用 FFmpeg 按 `edit_plan.json` 拼接：

- 图片转视频片段
- 音频对齐
- SRT 字幕烧录
- 片头片尾
- 封面截图

如果要做更复杂的模板化动效，可以考虑 Remotion 或 MoviePy。
