# 网页控制台使用说明

本项目已经提供一个本地网页控制台，用来分步运行或一键运行 AI 漫剧生成流程。

## 启动网页

在项目根目录运行：

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python -m manga_flow.cli web --host 127.0.0.1 --port 8765
```

浏览器打开：

```text
http://127.0.0.1:8765
```

如果端口被占用，可以换一个端口：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python -m manga_flow.cli web --host 127.0.0.1 --port 8766
```

## 页面配置项

- 项目 YAML：选择要生成的项目剧本，例如 `data/projects/ancient_short.yaml`。
- 流程配置：选择模型和流程配置，例如 `config/pipeline.siliconflow.yaml`。
- Env 文件：默认 `.env`，用于读取硅基流动和腾讯云密钥。
- 视频超时秒数：单个图生视频任务最多等待多久，默认 900 秒。
- 视频镜头：默认 `auto`，会读取项目 YAML 中 `production_mode: image_to_video` 的镜头；填空则不调用视频接口；也可以填写逗号分隔的镜头 ID。

## 编写新剧本

网页里的“剧本编辑”区域默认是结构化表单，不需要手动调整 YAML 缩进。

### AI 生成粗略大纲

1. 在“AI 写大纲”里输入主题。
2. 选择类型和目标时长。
3. 点击“AI 生成粗略大纲”。
4. 等待模型生成项目草稿。
5. 生成结果会自动填入下面的结构化表单。
6. 检查角色、场景、分幕和镜头后，点击“保存剧本”。

AI 大纲调用当前“流程配置”里的 `llm` 槽位，例如 `config/pipeline.siliconflow.yaml` 中的 GLM-5.2。

如果模型接口返回权限或额度错误，页面会明确提示失败原因，并先填入一个本地粗略草稿，方便继续编辑；这不是模型真实生成结果。

### 导入已有剧本

如果你已经有一个不符合本系统格式的剧本，可以使用“导入已有剧本”：

1. 点击“上传剧本文件”，选择剧本文件；也可以直接粘贴到“粘贴任意格式剧本”。
2. 支持直接读取 `txt`、`md`、`markdown`、`json`、`yaml`、`yml`、`srt`、`csv`、`tsv`、`log` 和 `docx`。
3. 文件读取后会自动填入文本框，可以先检查和手动修正。
4. 填写类型和目标时长。
5. 点击“规范化导入剧本”。
6. 系统会调用当前 `llm` 模型，把剧本整理成基础信息、角色、场景、分幕和镜头。
7. 导入结果会自动填入结构化表单。
8. 检查并修改角色、场景、分幕和镜头。
9. 点击“保存剧本”。
10. 后续可以继续“检查项目”或“一键完整出片”。

如果模型导入失败，系统会使用本地段落拆分生成一个可编辑草稿，并在页面中提示失败原因。长剧本会优先导入前 12000 字，超出部分建议分段导入或手动补充。PDF 暂不直接读取，建议先转成文本或 docx。

### 角色音色

角色区域已经接入腾讯云 `TextToVoice` 的 `VoiceType` 音色 ID：

1. 先给每个角色选择“角色性别”：女、男或中性/童声。
2. 在“腾讯云音色”里搜索音色编号、名称、推荐场景或音色类型。
3. 也可以点击单个角色的“随机音色”，系统会按角色性别随机选择一个未被其他角色使用的音色。
4. 点击“全部随机音色”可以一次性给所有角色分配不重复音色。
5. 一个项目中角色音色不能重复；如果手动编辑 YAML 写了重复 `voice_type`，保存和检查时会报错。

保存后，角色会带上：

```yaml
gender: female
voice_type: 101001
voice_style: 101001 智瑜 / 情感女声 / 精品音色 / 8k/16k
```

配音阶段会优先使用角色上的 `voice_type`。如果某个角色没有选择音色，会回退到流程配置里的 `character_voice_map`、`voice_map` 或 `default_voice_type`。

### 手动编辑

1. 点击“新建模板”。
2. 修改“保存路径”，例如 `data/projects/my_new_story.yaml`。
3. 在“基础信息”里填写项目 ID、剧名、一句话故事、固定画风和节奏要求。
4. 在“角色”里添加角色，填写角色 ID、角色名、外貌、性别、腾讯云音色和视觉锁定。
5. 在“场景”里添加主要场景，填写场景 ID、名称、描述和视觉锁定。
6. 在“剧情分幕”里添加分幕。左侧切换第几幕，右侧编辑当前幕。
7. 在当前幕里切换“镜头 1 / 镜头 2”，分别填写动作、台词/旁白和生成方式。
8. 点击“保存剧本”。系统会自动生成符合流程要求的 YAML。
9. 保存成功后，新文件会进入“项目 YAML”下拉框。
10. 点击“检查项目”，确认项目可运行。
11. 点击“一键完整出片”，或按阶段依次运行。

每个分幕有两个镜头。生成方式可以选：

- 优先图生视频：保存为 `image_to_video`，视频阶段会优先调用视频接口。
- 静态分镜回退：保存为 `static_motion`，该镜头不主动调用视频接口。
- 人工复核：保存为 `manual_review`，用于后续人工处理。

“高级：查看或直接编辑 YAML”用于调试或批量复制。普通使用时不需要打开它。

## 功能按钮

- 检查项目：校验项目 YAML 和模型配置，不调用远程生成接口。
- 接口状态：查看各模型槽位和 API Key 是否已配置，不调用远程生成接口。
- 脚本分镜：生成结构化脚本、分镜、字幕和剪辑计划。
- 生成图片：调用图片接口生成每个镜头的分镜图。
- 生成配音：调用腾讯云 TTS，按角色音色生成台词音频。
- 生成视频：优先调用图生视频接口；失败时只记录失败，不强行生成本地动态视频。
- 合成成片：把图片、可用 AI 视频、配音和字幕合成为最终样片。
- 一键完整出片：按 `脚本分镜 -> 图片 -> 配音 -> 视频 -> 合成` 顺序完整执行。

## 日志和产物

网页左侧会显示最近任务，右侧会实时显示任务日志。

网页任务日志保存到：

```text
outputs/web_jobs/
```

每次渲染流程自己的详细日志保存到对应项目目录：

```text
outputs/<project_id>/episode_<episode>/logs/
```

常用产物会在页面“最新产物”区域显示，包括：

- 最终视频
- 脚本 Markdown
- 故事板 HTML
- 渲染报告 JSON
- 阶段报告 JSON
- 最新运行日志

## 手动命令等价写法

网页每个按钮本质上是在后台运行 CLI 命令。例如一键完整出片等价于：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python -m manga_flow.cli stage \
  --stages all \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml \
  --env-file .env \
  --key-shots auto \
  --video-timeout-sec 900
```

只生成视频阶段：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python -m manga_flow.cli stage \
  --stages videos \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml \
  --env-file .env \
  --key-shots auto
```

只合成成片：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python -m manga_flow.cli stage \
  --stages compose \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml \
  --env-file .env \
  --key-shots auto
```

单独运行某个阶段时，需要保证它依赖的前置产物已经存在。例如合成阶段需要图片、配音和字幕文件。
