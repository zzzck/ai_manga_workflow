# AI 漫剧本地生成运行手册

适用项目：`月下借命灯`

最后更新：2026-06-21

## 1. 进入项目

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
```

推荐直接使用已创建好的 conda 环境 Python：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python --version
```

也可以手动激活环境：

```bash
conda activate ai-manga-flow
```

## 2. 检查模型接口配置

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python \
  -m manga_flow.cli provider-status \
  --config config/pipeline.siliconflow.yaml \
  --env-file .env
```

确认关键项显示为 `set`：

- `llm`
- `image`
- `video`
- `voice`
- `stt`

密钥放在 `.env`，不要写进文档或提交仓库。

## 3. 修改剧本

当前剧本源文件：

```text
data/projects/ancient_short.yaml
```

常改字段：

- `logline`：一句话故事
- `characters`：角色设定和视觉锁定
- `locations`：场景设定和固定元素
- `beats`：剧情节点、动作、台词

每个 `beat` 会自动拆成两个镜头：

- `action_first` + `dialogue_first`
- `action_second` + `dialogue_second`

当前新版脚本是 6 个 `beat`，自动生成 12 个镜头，总时长 60 秒。

## 4. 只生成脚本、分镜、字幕和剪辑计划

这个命令不调用生图、视频、TTS，不消耗模型媒体额度：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python \
  -m manga_flow.cli run \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml
```

输出目录：

```text
outputs/ancient_moon_lamp/episode_001/
```

重点产物：

```text
script.md
shot_list.json
shot_list.csv
storyboard.html
audio/subtitles.srt
audio/voice_lines.json
edit/edit_plan.json
reports/qc_report.md
```

## 5. 完整生成样片

标准命令：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python \
  -m manga_flow.cli render-sample \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml \
  --key-shots auto \
  --video-timeout-sec 900
```

说明：

- 先生成结构化产物。
- 再生成 12 张分镜图。
- 再生成 12 条腾讯云 TTS 配音。
- `--key-shots auto` 会自动读取 `production_mode: image_to_video` 的镜头并优先调用视频生成接口。
- 其他镜头会用最早版本的静态分镜动效，只做简单推拉/横移，不额外添加雨线、灯光、震动、口型等本地动作。
- 如果某个视频镜头生成失败，日志会记录具体 `shot_id`、错误信息和回退方案，然后该镜头使用静态分镜动效。
- 最后自动混音、封装、烧录字幕。

最终样片：

```text
outputs/ancient_moon_lamp/episode_001/final/ancient_moon_lamp_episode_001_sample.mp4
```

渲染报告：

```text
outputs/ancient_moon_lamp/episode_001/reports/render_report.json
```

每次运行还会生成独立日志：

```text
outputs/ancient_moon_lamp/episode_001/logs/render_YYYYMMDD_HHMMSS.log
outputs/ancient_moon_lamp/episode_001/logs/render_YYYYMMDD_HHMMSS.jsonl
```

看人类可读日志：

```bash
tail -n 80 outputs/ancient_moon_lamp/episode_001/logs/render_*.log
```

只看视频生成失败和回退：

```bash
grep -E "i2v\\.(failed|error)|clip\\.fallback" \
  outputs/ancient_moon_lamp/episode_001/logs/render_*.log
```

## 6. 稳定静态分镜版生成

如果视频接口返回 `403 Forbidden`，或暂时不想等待图生视频，可以关闭关键视频镜头：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python \
  -m manga_flow.cli render-sample \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml \
  --key-shots "" \
  --video-timeout-sec 900
```

这会生成完整 60 秒样片，但所有画面使用静态分镜动效，不依赖远程视频模型。

当前静态分镜动效只包含：

- 基于单张分镜图的慢速推拉。
- 少量横移。
- 不添加本地雨线、雾气、灯光、震动或口型效果。

## 7. 检查最终视频

```bash
ffprobe -v error \
  -show_entries format=duration,size:stream=codec_type,codec_name \
  -of json \
  outputs/ancient_moon_lamp/episode_001/final/ancient_moon_lamp_episode_001_sample.mp4
```

正常应看到：

- `duration` 接近 `60.000000`
- 一个 `video` stream
- 一个 `audio` stream

## 8. 缓存机制

现在图片、视频、TTS 都有缓存元数据：

```text
assets/images/{shot_id}.json
assets/videos/{shot_id}.json
audio/{shot_id}.tts.json
```

当剧本、提示词、音色、provider 或源图变化时，会自动重新生成对应资源。

如果想强制重新生成全部资源，可以删除对应缓存和媒体文件后再运行。常见位置：

```text
outputs/ancient_moon_lamp/episode_001/assets/images/
outputs/ancient_moon_lamp/episode_001/assets/videos/
outputs/ancient_moon_lamp/episode_001/audio/e001s*.mp3
```

## 9. 当前 TTS 配置

当前使用腾讯云 TTS，角色音色：

```yaml
旁白: 101013
沈照夜: 101001
谢凌舟: 101030
sample_rate: 16000
```

配置位置：

```text
config/pipeline.siliconflow.yaml
```

如果要切回更高质量的大模型音色，可以把 `voice_map` 和 `character_voice_map` 改为：

```yaml
旁白: 502005
沈照夜: 501004
谢凌舟: 501006
```

同时建议把：

```yaml
sample_rate: 24000
```

## 10. 常见问题

### 腾讯云 TTS 报 `UnsupportedOperation.PkgExhausted`

含义通常是当前音色对应资源包、调用类型或短时间额度不可用。

处理方式：

- 稍后重试。
- 临时切回 `101013 / 101001 / 101030`。
- 检查腾讯云控制台中对应语音合成类型的资源包，而不仅是账号总余量。

### 硅基视频接口报 `403 Forbidden`

当前渲染器会自动回退为静态分镜动效，最终样片仍能生成。

处理方式：

- 查看 `outputs/ancient_moon_lamp/episode_001/logs/render_*.log`，确认失败的 `shot_id` 和接口错误。
- 需要稳定出片时，用 `--key-shots ""` 生成静态分镜版。
- 检查硅基流动控制台中视频模型权限和余额。
- 换用已开通的视频模型后，更新 `config/pipeline.siliconflow.yaml` 的 `providers.video.model`。

### 修改剧本后画面还是旧的

正常情况下不会。系统会根据提示词元数据自动判断是否重新生成。

如果仍看到旧画面，删除对应镜头的图片和 `.json` 元数据后重跑：

```text
outputs/ancient_moon_lamp/episode_001/assets/images/{shot_id}.png
outputs/ancient_moon_lamp/episode_001/assets/images/{shot_id}.json
```
