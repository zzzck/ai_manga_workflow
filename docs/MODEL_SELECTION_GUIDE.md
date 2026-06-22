# 模型选择建议

当前配置文件里所有模型都留空，等你决定供应商后再填。

## LLM

用途：

- 世界观和角色设定
- 分集大纲
- 单集剧本
- 镜头拆解
- 提示词扩写
- 质检和改写建议

选择标准：

- 中文短剧节奏理解强
- JSON 输出稳定
- 成本可控
- 支持长上下文

配置槽位：

```yaml
providers:
  llm:
    enabled: true
    provider: ""
    model: ""
    api_key_env: LLM_API_KEY
```

## 图片模型

用途：

- 角色三视图
- 表情表
- 服装表
- 场景基准图
- 分镜图
- 封面图

选择标准：

- 角色一致性
- 可参考图生成
- 可局部重绘
- 中文提示词或英文提示词稳定
- 商业授权清晰

配置槽位：

```yaml
providers:
  image:
    enabled: true
    provider: ""
    model: ""
    api_key_env: IMAGE_API_KEY
```

## 视频模型

用途：

- 关键镜头图生视频
- 动作镜头
- 情绪爆发
- 结尾悬念
- 转场

选择标准：

- 支持参考图或首尾帧
- 角色一致性较好
- 失败重试成本可接受
- API 可用
- 生成时长和比例满足竖屏需求

配置槽位：

```yaml
providers:
  video:
    enabled: true
    provider: ""
    model: ""
    api_key_env: VIDEO_API_KEY
```

## 配音模型

用途：

- 旁白
- 角色对白
- 情绪化台词
- 多角色声线

选择标准：

- 中文自然度
- 情绪控制
- 多角色管理
- API 稳定
- 声音授权清晰

配置槽位：

```yaml
providers:
  voice:
    enabled: true
    provider: ""
    model: ""
    api_key_env: VOICE_API_KEY
```

## BGM 和音效

用途：

- 情绪铺底
- 转场音
- 环境音
- 冲击音效

选择标准：

- 商业授权清晰
- 可生成短循环
- 可按情绪标签检索
- 和剪辑流程容易对接

## 推荐先选型顺序

1. LLM
2. 图片模型
3. TTS
4. 视频模型
5. BGM/音效

视频模型最贵、失败率也更高，建议最后接入。
