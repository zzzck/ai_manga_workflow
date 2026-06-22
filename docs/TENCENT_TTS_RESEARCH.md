# 腾讯云 TTS 音色调研

调研日期：2026-06-20

## 结论

腾讯云语音合成可以满足“不同角色使用不同声音”的需求。

核心能力：

- 通过 `VoiceType` 指定音色 ID。
- 同时提供男声、女声、童声、新闻、阅读、聊天、解说、客服等多类音色。
- 支持 `mp3`、`wav`、`pcm` 输出。
- 支持 `8000`、`16000`、`24000` 采样率，具体取决于音色。
- 支持 `Speed`、`Volume` 等基础控制。
- 部分多情感音色支持 `EmotionCategory` 和 `EmotionIntensity`。
- 基础语音合成接口为 `TextToVoice`，请求域名 `tts.tencentcloudapi.com`。

官方文档：

- 音色列表：https://cloud.tencent.com/document/product/1073/92668
- 基础语音合成：https://cloud.tencent.com/document/api/1073/37995
- 实时语音合成：https://cloud.tencent.com/document/product/1073/34093
- 长文本语音合成：https://cloud.tencent.com/document/product/1073/57373

## 当前工程接入状态

已接入：

- `src/manga_flow/providers/tencent_tts.py`：腾讯云 `TextToVoice` 调用，内置 TC3-HMAC-SHA256 签名。
- `config/pipeline.siliconflow.yaml`：`voice` 槽已切换为 `tencentcloud`。
- `scripts/smoke_tencent_tts.py`：三角色 TTS 烟测。
- 渲染流程只把 `voice_lines.json` 中的 `text` 送去 TTS，不会把 `沈照夜：`、`谢凌舟：`、`旁白：` 读出来。
- 每条音频会写入 `audio/{shot_id}.tts.json`，当 provider、文本、角色或音色变化时自动重新合成。

2026-06-20 实测：

- 推荐的大模型音色 `502005 / 501004 / 501006` 已能完成鉴权和请求，但当前腾讯云账号返回 `UnsupportedOperation.PkgExhausted`，表示对应资源包余量耗尽。
- 精品音色 `101013 / 101001 / 101030` 已跑通，可用于当前标准版样片。

当前标准版音色：

```yaml
narrator: 101013  # 智辉，新闻男声
shen_zhaoye: 101001  # 智瑜，情感女声
xie_lingzhou: 101030  # 智柯，通用男声
sample_rate: 16000
```

后续如果开通或充值大模型音色资源包，可切回下方“推荐接入策略”的 `502005 / 501004 / 501006`，并把采样率改回 `24000`。

## 接口参数要点

基础语音合成 `TextToVoice` 典型参数：

```json
{
  "Text": "这枚玉扣，是我哥哥的。",
  "SessionId": "unique-session-id",
  "Volume": 0,
  "Speed": 0,
  "ModelType": 1,
  "VoiceType": 502003,
  "PrimaryLanguage": 1,
  "SampleRate": 24000,
  "Codec": "mp3",
  "EnableSubtitle": false,
  "EmotionCategory": "neutral",
  "EmotionIntensity": 100
}
```

注意：

- `Text` 里只放要读出来的台词，不要放 `沈照夜：` 这种说话人标签。
- 说话人标签只保留在字幕里。
- 多角色配音需要按角色选择不同 `VoiceType`。

## 适合《月下借命灯》的音色建议

### 旁白

推荐：

- `502005 智小解`：解说男声，适合旁白、悬疑铺陈。
- `501000 智斌`：阅读男声，适合稳重旁白。
- `501003 智宇`：阅读男声，适合故事叙述。

建议默认：`502005 智小解`

### 沈照夜

角色：年轻女仵作，冷静、克制、清冷。

推荐：

- `502001 智小柔`：聊天女声，超自然大模型音色。
- `502003 智小敏`：聊天女声，超自然大模型音色。
- `501002 智菊`：阅读女声，偏稳。
- `501004 月华`：聊天女声，名字和古风氛围比较贴。
- `601009 爱小芊`：聊天女声，支持多情感。
- `601010 爱小娇`：聊天女声，支持多情感。

建议默认：`501004 月华` 或 `502001 智小柔`

### 谢凌舟

角色：少年将军，冷静、有压迫感。

推荐：

- `502006 智小悟`：聊天男声，超自然大模型音色。
- `502005 智小解`：解说男声，清晰稳定。
- `501005 飞镜`：聊天男声，名字和古风气质贴。
- `501006 千嶂`：聊天男声，名字和古风气质贴。
- `601008 爱小豪`：聊天男声，支持多情感。
- `601011 爱小川`：聊天男声。

建议默认：`501006 千嶂` 或 `502006 智小悟`

### 无名信使

无台词，不需要音色。

## 多情感参数

腾讯云基础语音合成文档中 `EmotionCategory` 支持：

```text
neutral, sad, happy, angry, fear, news, story, radio, poetry, call,
sajiao, disgusted, amaze, peaceful, exciting, aojiao, jieshuo
```

`EmotionIntensity` 取值范围：

```text
50-200
```

但只有支持多情感的音色才生效。音色列表里例如：

- `601008 爱小豪`
- `601009 爱小芊`
- `601010 爱小娇`

这些显示支持多种情绪，可用于后续精修。

## 推荐接入策略

第一阶段：

```yaml
narrator: 502005
shen_zhaoye: 501004
xie_lingzhou: 501006
```

第二阶段：

若要更强情绪：

```yaml
narrator: 502005
shen_zhaoye: 601009
xie_lingzhou: 601008
```

并按镜头情绪设置：

```yaml
惊疑: amaze
决绝: peaceful
压迫: fear
对峙: angry
悬念: story
```

## 需要你准备的腾讯云信息

如果要接入腾讯云 TTS，需要：

```bash
TENCENTCLOUD_SECRET_ID=
TENCENTCLOUD_SECRET_KEY=
TENCENTCLOUD_TTS_REGION=ap-guangzhou
TENCENTCLOUD_TTS_APP_ID=
```

其中 SecretId/SecretKey 用于 API 鉴权，AppId 需要在腾讯云语音合成控制台开通服务后获取。
