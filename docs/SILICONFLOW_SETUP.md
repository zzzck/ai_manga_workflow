# 硅基流动模型接入配置

本项目已新增硅基流动专用配置：

```text
config/pipeline.siliconflow.yaml
```

你后续只需要复制 `.env.example` 为 `.env`，填入：

```bash
SILICONFLOW_API_KEY=你的硅基流动 API Key
```

模型名已经先按当前需求填好。

## 模型选择

### 文本编剧 / 分镜

```yaml
llm:
  model: zai-org/GLM-5.2
```

用途：

- 选题扩写
- 世界观和角色设定
- 分集大纲
- 单集剧本
- 分镜 JSON
- 图片/视频提示词扩写

选择原因：你指定文本类使用 GLM，硅基流动模型中心显示 `zai-org/GLM-5.2` 是 2026-06-17 发布的 GLM 最新旗舰模型，支持长上下文和工具调用。

### 快速文本任务

```yaml
llm_fast:
  model: zai-org/GLM-5.2
```

用途：

- 标题/简介
- 批量改格式
- 提示词补齐
- 低成本质检

当前先按你的要求同样使用 GLM-5.2。后面如果要降成本，可以再换成较便宜的 GLM 或其他模型。

### 视觉理解 / 画面质检

```yaml
llm_vision:
  model: zai-org/GLM-4.5V
```

用途：

- 检查分镜图是否符合镜头
- 检查角色是否变脸
- 检查服装、道具、场景固定元素是否一致

说明：硅基流动文档里多模态模型统一通过 `/chat/completions` 调用，GLM 系列支持视觉输入，但当前 `GLM-5.2` 在模型中心显示为对话模型，所以视觉槽位先配置为 `GLM-4.5V`。

### 生图

```yaml
image:
  model: Tongyi-MAI/Z-Image
```

用途：

- 角色卡
- 场景图
- 分镜图
- 封面

选择原因：模型中心说明 `Z-Image` 更偏高质量、稳定多样性、风格覆盖和提示词遵循，适合古风漫剧这种风格化资产。

快速预览备选：

```yaml
image_fast:
  model: Tongyi-MAI/Z-Image-Turbo
```

用途：批量草图、快速试分镜。

图像编辑：

```yaml
image_edit:
  model: Qwen/Qwen-Image-Edit
```

用途：修表情、修道具、改服装、局部重绘、保持角色一致。

### 视频

```yaml
video:
  model: Wan-AI/Wan2.2-I2V-A14B
```

用途：关键镜头图生视频。

选择原因：漫剧需要角色一致性，先生成稳定分镜图，再图生视频，比直接文生视频更可控。硅基流动文档当前支持 `Wan-AI/Wan2.2-I2V-A14B`，并说明 9:16 图生视频匹配 `720x1280`。

文生视频备选：

```yaml
video_t2v:
  model: Wan-AI/Wan2.2-T2V-A14B
```

用途：空镜、氛围镜头、转场，不依赖固定角色脸的镜头。

### 语音

```yaml
voice:
  model: FunAudioLLM/CosyVoice2-0.5B
```

用途：旁白和角色对白。

默认音色先用：

```yaml
voice: FunAudioLLM/CosyVoice2-0.5B:claire
```

后续如果要做固定角色声线，可以用硅基流动的参考音频上传接口，但必须使用有授权的声音。

### 语音转文本

```yaml
stt:
  model: FunAudioLLM/SenseVoiceSmall
```

用途：配音回听校对、自动字幕核验。

### 检索

```yaml
embedding:
  model: Qwen/Qwen3-Embedding-8B
rerank:
  model: BAAI/bge-reranker-v2-m3
```

用途：后续做 IP 设定库、历史剧情记忆、角色一致性设定检索。

## API 端点

硅基流动统一 base URL：

```text
https://api.siliconflow.cn/v1
```

当前已封装的端点：

```text
/chat/completions      文本和视觉理解
/images/generations    生图和图像编辑
/video/submit          创建视频任务
/video/status          查询视频结果
/audio/speech          文本转语音
/audio/transcriptions  语音转文本
/embeddings            向量
/rerank                重排序
```

## 检查配置

不调用远程 API，只检查配置和 key 是否存在：

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
conda activate ai-manga-flow
manga-flow provider-status --config config/pipeline.siliconflow.yaml
```

如果 `.env` 里还没填 key，会显示：

```text
missing: SILICONFLOW_API_KEY
```

## 运行现有流程

当前 `manga-flow run` 仍然会先生成结构化生产包，不会直接扣费调用模型：

```bash
manga-flow run \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml
```

下一步接真实生成时，会把 `src/manga_flow/providers/siliconflow.py` 里的调用接到剧本生成、生图、视频、TTS 节点。
