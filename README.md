# AI 漫剧自动化工作流

这是一个可扩展的 AI 漫剧生产流程。当前版本已经提供本地网页控制台，并接入硅基流动文本/图片/视频模型与腾讯云 TTS，可分步生成或一键生成漫剧样片：

- 剧本与分镜脚本
- 图片提示词和视频提示词
- 角色、场景、镜头资产清单
- 配音台词 JSON 和 SRT 字幕
- 剪辑时间线 `edit_plan.json`
- 自动质检报告和运行日志

模型与接口配置集中在 `config/pipeline.siliconflow.yaml`，密钥从 `.env` 读取。

## 快速开始

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
conda env create -f environment.yml
conda activate ai-manga-flow
manga-flow check
manga-flow run
```

如果环境已存在：

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
conda env update -n ai-manga-flow -f environment.yml
conda activate ai-manga-flow
```

默认示例项目在：

```text
data/projects/demo_story.yaml
```

默认配置在：

```text
config/pipeline.example.yaml
```

硅基流动专用配置在：

```text
config/pipeline.siliconflow.yaml
```

运行后输出在：

```text
outputs/demo_rebirth/episode_001/
```

## 启动网页端

推荐使用网页端来编写剧本、选择角色音色、分阶段生成图片/配音/视频，并查看日志和产物。

如果要本机单人开发调试，可以继续使用旧的本地控制台：

先进入项目目录并激活环境：

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
conda activate ai-manga-flow
```

启动本地网页服务：

```bash
manga-flow web --host 127.0.0.1 --port 8765
```

如果 `manga-flow` 命令不可用，也可以直接使用当前环境里的 Python：

```bash
/opt/homebrew/Caskroom/miniforge/base/envs/ai-manga-flow/bin/python -m manga_flow.cli web --host 127.0.0.1 --port 8765
```

然后在浏览器打开：

```text
http://127.0.0.1:8765
```

如果端口 `8765` 被占用，可以换一个端口，例如：

```bash
manga-flow web --host 127.0.0.1 --port 8766
```

对应打开：

```text
http://127.0.0.1:8766
```

不要关闭运行网页服务的终端窗口。浏览器刷新不会中断后台任务，但如果停止网页服务，页面无法继续查看任务状态。

## 启动可部署版服务

如果要按 `部署方案.md` 的方向做多人试用，请启动可部署版 FastAPI 服务。它包含登录、管理员后台、用户额度、用量流水和受保护的工作台入口：

```bash
cd /Users/zzzck/Documents/yxy_html/ai_manga_workflow
conda activate ai-manga-flow
manga-flow serve --host 127.0.0.1 --port 8000
```

浏览器打开：

```text
http://127.0.0.1:8000
```

首次启动会自动创建本地超级管理员：

```text
账号：admin
密码：admin123456
```

部署到服务器前必须在 `.env` 中修改 `AI_MANGA_SECRET_KEY` 和 `AI_MANGA_ADMIN_PASSWORD`。更多说明见：

```text
docs/DEPLOYABLE_SERVER.md
```

可部署版默认限制同一账号最多 2 个排队或运行中的长任务，全站最多 8 个。可以在 `.env` 里调整：

```dotenv
AI_MANGA_MAX_ACTIVE_JOBS_PER_USER=2
AI_MANGA_MAX_ACTIVE_JOBS_TOTAL=8
```

仓库也提供了可复制到 Linux 服务器的部署模板：

```text
deploy/README.md
deploy/systemd/ai-manga.service
deploy/nginx/ai_manga_workflow.conf
```

服务启动后可以用健康检查确认 FastAPI 正常：

```bash
curl http://127.0.0.1:8000/healthz
```

管理员登录 `/admin` 后，可以在“运行状态”区块查看数据库路径、数据目录、模型配置摘要、健康检查入口和备份命令。对应 JSON 接口是：

```text
http://127.0.0.1:8000/api/admin/server-info
```

登录后可以检查模型槽位和 `.env` 是否配置完整：

```text
http://127.0.0.1:8000/api/provider-status
```

可部署版还提供项目资源接口，便于后续把网页、管理后台或外部工具接到同一套后端：

```text
GET  /api/projects
POST /api/projects
GET  /api/projects/{path}
PUT  /api/projects/{path}
```

这些接口都要求登录。普通用户只能看到和保存自己的 `data/users/<user_id>/projects/` 项目；管理员可以查看全局项目和所有用户项目。

## 网页端操作流程

### 1. 从主页选择入口

打开网页后，默认进入“主页”。主页只保留常用入口：

- AI 生成剧本：进入独立的多角色剧本工坊。
- 导入已有剧本：上传或粘贴现有剧本，再规范化为系统格式。
- 继续编辑：打开结构化剧本编辑器。
- 生成成片：进入配置、任务、日志和产物页面。

顶部导航也可以直接切换：

- 主页
- AI 生成剧本
- 导入与编辑
- 生成控制

### 2. AI 生成剧本

进入“AI 生成剧本”页面后，填写主题、类型、目标时长、画风、主角倾向、结尾类型和禁止内容。

点击：

```text
开始多角色生成
```

系统会按多角色工坊流程多次调用文本模型：创意策划、故事架构、剧情节拍、短剧台本、分镜设计、结构入库，每个阶段都有对应评审，不通过会在最大返工次数内重新生成。

生成完成后，剧本会自动保存，并跳转到“导入与编辑”页面继续检查和修改。生成到一半不想继续时，可以点击“终止生成”，当前正在进行的单次模型请求可能会先返回，但后续阶段会停止。

### 3. 导入或编辑剧本

进入“导入与编辑”页面后，可以用三种方式准备剧本：

- AI 生成剧本：从“AI 生成剧本”页面生成后自动进入编辑器。
- 导入已有剧本：上传或粘贴剧本文本，填写类型和目标时长，点击“规范化导入剧本”。
- 手动新建：点击“新建模板”，直接在结构化表单里填写角色、场景、分幕和镜头。

保存文件名由“剧本文件名”字段控制，保存路径会自动同步为：

```text
data/projects/<剧本文件名>.yaml
```

保存路径字段是只读预览；如果要改保存文件名，修改“剧本文件名”即可。

导入或编辑完成后，务必点击：

```text
保存剧本
```

保存成功后，新剧本会进入“生成控制”页面里的“项目 YAML”下拉框。

### 4. 设置角色音色

在“导入与编辑”页面的“角色”区域为每个角色设置：

- 角色性别：女、男或中性/童声。
- 腾讯云音色：可以搜索音色编号、名称或场景。
- 随机音色：按角色性别随机选择一个未被其他角色使用的音色。
- 全部随机音色：一次性给所有角色分配不重复音色。

保存剧本允许角色音色为空，方便先保存草稿。同一个剧本中已填写的角色音色不能重复。真正运行“生成配音”或“一键完整出片”前，系统会要求所有角色都补齐腾讯云音色。

### 5. 选择生成配置

进入“生成控制”页面后，先确认：

- 项目 YAML：选择要生成的剧本文件，例如 `data/projects/bie_peng_ta.yaml`。
- 流程配置：通常选择 `config/pipeline.siliconflow.yaml`。
- Env 文件：通常保持 `.env`。
- 视频超时秒数：默认 `900` 秒。
- 视频镜头：通常保持 `auto`，表示读取剧本中 `production_mode: image_to_video` 的镜头并调用视频接口。

### 6. 分步生成

第一次跑新剧本，建议按顺序分步执行，方便定位问题：

1. 检查项目：校验剧本和模型配置，不调用生成接口。
2. 脚本分镜：生成镜头列表、字幕、配音文本、图片提示词和视频提示词。
3. 生成图片：调用图片模型生成每个镜头的分镜图。
4. 生成配音：调用腾讯云 TTS，按角色音色生成语音。
5. 生成视频：优先调用图生视频接口；失败的镜头会记录日志并回退静态分镜。
6. 合成成片：把图片/视频、配音、字幕合成为最终样片。

熟悉流程后，也可以直接点击：

```text
一键完整出片
```

它会按 `脚本分镜 -> 生成图片 -> 生成配音 -> 生成视频 -> 合成成片` 顺序自动运行。

### 7. 查看日志和产物

“生成控制”页面左侧“任务”区域会显示任务状态，右侧“任务日志”会实时显示当前任务输出。

常用日志位置：

```text
outputs/web_jobs/                         # 网页按钮启动的后台任务日志
outputs/web_api/                          # AI 生成剧本、规范化导入剧本等网页接口日志
outputs/<project_id>/episode_001/logs/    # 生成流程详细日志
```

常用产物位置：

```text
outputs/<project_id>/episode_001/
```

“生成控制”页面右侧“最新产物”会显示最终视频、脚本、故事板、渲染报告、阶段报告和最新日志链接。

如果页面显示的任务状态没有及时更新，可以点页面右上角“刷新”或刷新浏览器页面。浏览器刷新不会中断已经开始的后台任务。

## 当前目录结构

```text
ai_manga_workflow/
  config/                  # 模型和流程配置
  data/projects/           # 项目设定 YAML
  docs/                    # 中文方案文档
  outputs/                 # 运行产物
  src/manga_flow/          # Python 工作流代码
```

## 常用命令

检查配置和项目：

```bash
manga-flow check \
  --config config/pipeline.example.yaml \
  --project data/projects/demo_story.yaml
```

运行基础结构流程：

```bash
manga-flow run \
  --config config/pipeline.example.yaml \
  --project data/projects/demo_story.yaml \
  --episode 1
```

生成可观看样片：

```bash
manga-flow render-sample \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml \
  --key-shots e001s002b,e001s005b
```

创建新项目模板：

```bash
manga-flow init-project urban_rebirth_001 "重生后我只想搞钱"
```

查看硅基流动模型槽位和 API Key 状态：

```bash
manga-flow provider-status --config config/pipeline.siliconflow.yaml
```

检查可部署版服务上线前配置：

```bash
manga-flow deploy-check --config config/pipeline.siliconflow.yaml --env-file .env
```

备份可部署版服务数据：

```bash
manga-flow backup-server --output backups
```

默认会打包 SQLite 快照、`data/users`、`data/projects` 和 `outputs`，不会包含 `.env`。如果确实要把 `.env` 一起放进备份包，需要显式加 `--include-env`，并妥善保存备份文件。

启动本地网页控制台：

```bash
manga-flow web --host 127.0.0.1 --port 8765
```

分阶段运行生成流程：

```bash
manga-flow stage \
  --stages images,voice,videos,compose \
  --config config/pipeline.siliconflow.yaml \
  --project data/projects/ancient_short.yaml \
  --key-shots auto
```

## 下一步

1. 在 `data/projects/*.yaml` 固定角色、场景、视觉风格和剧情节拍。
2. 在 `config/pipeline.siliconflow.yaml` 或 `config/pipeline.example.yaml` 选择模型供应商和模型名。
3. 在 `src/manga_flow/providers/` 增加真实 provider，例如 `openai_image.py`、`runway_video.py`、`elevenlabs_voice.py`。
4. 让 provider 写入真实图片、视频、音频文件，再由剪辑模块生成成片。

硅基流动接入说明见：

```text
docs/SILICONFLOW_SETUP.md
```

更详细的网页控制台说明见：

```text
docs/WEB_CONSOLE.md
```
