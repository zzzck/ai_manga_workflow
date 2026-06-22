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

当前版本已经提供两类入口：

- 命令行入口：适合脚本化运行、调试和批处理。
- 本地网页控制台：适合生成或导入剧本、编辑角色音色、分阶段运行和查看日志产物。

生成流程中，结构化脚本与分镜计划仍由本地 `Planner` 根据项目 YAML 生成；AI 生成剧本、规范化导入、图片生成、图生视频和腾讯云 TTS 会按配置调用远程接口。

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

模型供应商抽象层。当前已包含：

- `siliconflow.py`：通过硅基流动调用文本、图片和视频模型。
- `tencent_tts.py`：调用腾讯云 TTS 生成角色配音。

保留可扩展边界，后续仍可以增加：

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
- `manga-flow stage`
- `manga-flow render-sample`
- `manga-flow provider-status`
- `manga-flow web`
- `manga-flow init-project`

### `manga_flow.web`

本地网页控制台，使用 Python `ThreadingHTTPServer` 提供单页应用和 API：

- 主页：流程入口。
- AI 生成剧本：多角色撰写、评审、返工和自动保存。
- 导入与编辑：上传或粘贴剧本，规范化导入，并用结构化表单编辑项目 YAML。
- 生成控制：选择项目与配置，启动后台 CLI 任务，轮询任务状态、日志和产物。

网页启动的流程任务日志写入：

```text
outputs/web_jobs/
```

AI 生成剧本和导入剧本等网页接口日志写入：

```text
outputs/web_api/
```

## 模型接入位置

模型选择集中在：

```text
config/pipeline.siliconflow.yaml
```

示例配置仍保留在：

```text
config/pipeline.example.yaml
```

真实 API 代码放在：

```text
src/manga_flow/providers/
```

例如：

```text
src/manga_flow/providers/siliconflow.py
src/manga_flow/providers/tencent_tts.py
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
