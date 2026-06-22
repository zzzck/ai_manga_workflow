from __future__ import annotations

import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree import ElementTree

import cgi

import yaml

from .config import load_config
from .providers.siliconflow import SiliconFlowClient
from .schemas import ProjectBrief


ROOT = Path.cwd().resolve()
JOBS: dict[str, "Job"] = {}
JOB_LOCK = threading.Lock()
WORKSHOP_JOBS: dict[str, "WorkshopJob"] = {}
WORKSHOP_JOB_LOCK = threading.Lock()


class WorkshopCancelled(RuntimeError):
    """Raised when the user requests cancellation between model calls."""


@dataclass
class Job:
    id: str
    action: str
    label: str
    command: list[str]
    log_path: str
    status: str = "queued"
    return_code: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    error: str = ""


@dataclass
class WorkshopJob:
    id: str
    label: str
    log_path: str
    log_dir: str
    status: str = "queued"
    current_stage: str = ""
    current_stage_name: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    result: dict[str, Any] | None = None
    project_path: str = ""
    source: str = ""
    model: str = ""
    warning: str = ""
    error: str = ""
    cancel_requested: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI 漫剧工作流控制台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d8dde6;
      --text: #17202a;
      --muted: #667085;
      --blue: #2563eb;
      --green: #138a43;
      --red: #c2410c;
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }
    header { padding: 16px 24px; border-bottom: 1px solid var(--line); background: var(--panel); display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    h1 { margin: 0; font-size: 20px; }
    .sub { color: var(--muted); font-size: 13px; margin-top: 4px; }
    main { padding: 18px 24px 28px; }
    .page-tabs { display: flex; gap: 8px; padding: 12px 24px 0; background: var(--bg); flex-wrap: wrap; }
    .page-tabs button { min-height: 34px; padding: 0 13px; }
    .page-tabs button.active { background: var(--blue); border-color: var(--blue); color: #fff; }
    .page { display: none; }
    .page.active { display: block; }
    .home-grid { display: grid; grid-template-columns: repeat(2, minmax(260px, 1fr)); gap: 16px; align-items: stretch; }
    .entry-card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 18px; display: grid; gap: 12px; align-content: start; }
    .entry-card h2 { font-size: 18px; margin: 0; }
    .entry-card p { margin: 0; color: var(--muted); font-size: 13px; line-height: 1.55; }
    .entry-card button { justify-self: start; min-width: 140px; }
    .production-layout { display: grid; gap: 16px; grid-template-columns: minmax(260px, 340px) minmax(0, 1fr); align-items: start; }
    .editor-layout { display: grid; gap: 16px; grid-template-columns: minmax(300px, 0.56fr) minmax(0, 1fr); align-items: start; }
    .sidebar, .workspace { display: grid; gap: 16px; align-content: start; }
    .runtime-grid { display: grid; gap: 16px; grid-template-columns: minmax(0, 1fr) minmax(320px, 0.72fr); align-items: start; }
    section { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; }
    h2 { margin: 0 0 12px; font-size: 15px; }
    label { display: block; font-size: 12px; color: var(--muted); margin: 12px 0 5px; }
    input, select, textarea { width: 100%; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--text); }
    input, select { height: 36px; padding: 0 10px; }
    textarea { min-height: 92px; padding: 10px; resize: vertical; font: 13px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
    textarea.yaml { min-height: 300px; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .field-full { grid-column: 1 / -1; }
    .story-form { display: grid; gap: 12px; }
    .group-title { display: flex; justify-content: space-between; gap: 10px; align-items: center; margin-top: 10px; }
    .group-title h3 { margin: 0; font-size: 14px; }
    .item-card { border: 1px solid var(--line); border-radius: 6px; padding: 10px; margin-top: 8px; background: #fbfcfe; }
    .card-head { display: flex; justify-content: space-between; gap: 8px; align-items: center; margin-bottom: 8px; }
    .card-head strong { font-size: 13px; }
    .beat-editor { display: grid; grid-template-columns: minmax(180px, 240px) minmax(0, 1fr); gap: 12px; margin-top: 8px; }
    .beat-list { display: grid; gap: 6px; align-content: start; }
    .beat-pill { text-align: left; min-height: 34px; font-size: 12px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .beat-pill.active { border-color: var(--blue); color: var(--blue); background: #eff6ff; }
    .shot-tabs { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin: 8px 0 10px; }
    .shot-tabs button.active { border-color: var(--blue); color: #fff; background: var(--blue); }
    .switch-row { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 8px; }
    .small-btn { min-height: 30px; padding: 0 10px; font-size: 12px; }
    button.danger { color: var(--red); border-color: #fed7aa; }
    details.advanced { margin-top: 10px; border: 1px dashed var(--line); border-radius: 6px; padding: 10px; }
    details.advanced summary { cursor: pointer; color: var(--muted); font-size: 13px; font-weight: 600; }
    .buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .sidebar .buttons { grid-template-columns: 1fr; }
    .editor-actions { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; margin-top: 8px; }
    button { min-height: 38px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--text); cursor: pointer; font-weight: 600; }
    button.primary { background: var(--blue); border-color: var(--blue); color: #fff; }
    button:hover { filter: brightness(0.98); }
    button:disabled { cursor: not-allowed; opacity: 0.58; filter: none; }
    button.primary:disabled { background: #94a3b8; border-color: #94a3b8; }
    .hint { font-size: 12px; color: var(--muted); line-height: 1.5; margin: 10px 0 0; }
    .group-actions { display: flex; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
    .voice-picker { grid-column: 1 / -1; border: 1px dashed var(--line); border-radius: 6px; padding: 10px; background: #fff; }
    .voice-picker label:first-child { margin-top: 0; }
    .voice-controls { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; align-items: end; }
    .voice-controls button { min-height: 36px; white-space: nowrap; }
    .voice-label { margin-top: 6px; font-size: 12px; color: var(--muted); line-height: 1.45; }
    .voice-links { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 7px; font-size: 12px; }
    .voice-links a { color: var(--blue); text-decoration: none; }
    .workshop-shell { display: grid; gap: 12px; }
    .workshop-request { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.9fr); gap: 12px; }
    .workshop-actions { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .workshop-actions button { min-height: 34px; padding: 0 12px; }
    .workshop-grid { display: grid; grid-template-columns: minmax(180px, 240px) minmax(0, 1fr) minmax(260px, 340px); gap: 12px; align-items: start; }
    .stage-list, .role-list { display: grid; gap: 6px; align-content: start; }
    .stage-card, .role-card { text-align: left; border: 1px solid var(--line); border-radius: 6px; padding: 9px; background: #fff; cursor: pointer; }
    .stage-card.active, .role-card.active { border-color: var(--blue); background: #eff6ff; color: #1d4ed8; }
    .stage-card.queued { background: #f8fafc; color: #64748b; }
    .stage-card.running { border-color: var(--blue); background: #eff6ff; box-shadow: inset 3px 0 0 var(--blue); }
    .stage-card.done { border-color: #86efac; background: #f0fdf4; }
    .stage-card.failed { border-color: #fdba74; background: #fff7ed; }
    .stage-card strong, .role-card strong { display: block; font-size: 12px; }
    .stage-card span, .role-card span { display: block; margin-top: 4px; font-size: 11px; color: var(--muted); line-height: 1.35; }
    .artifact-panel { display: grid; gap: 8px; }
    .artifact-meta { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; color: var(--muted); font-size: 12px; }
    .workshop-status { flex: 1 1 260px; min-height: 34px; display: flex; align-items: center; padding: 7px 10px; border-radius: 6px; border: 1px solid #dbeafe; background: #eff6ff; color: #1d4ed8; font-size: 12px; line-height: 1.45; }
    .workshop-status.idle { border-color: var(--line); background: #fff; color: var(--muted); }
    .workshop-status.running { border-color: #bfdbfe; background: #eff6ff; color: #1d4ed8; }
    .workshop-status.error { border-color: #fed7aa; background: #fff7ed; color: var(--red); }
    .prompt-editor { border: 1px solid var(--line); border-radius: 6px; padding: 10px; background: #fbfcfe; }
    .prompt-editor textarea { min-height: 140px; font-size: 12px; }
    .prompt-editor .grid2 input, .prompt-editor .grid2 select { height: 32px; }
    .prompt-editor label { margin-top: 8px; }
    .job { border: 1px solid var(--line); border-radius: 6px; padding: 8px; margin-bottom: 8px; cursor: pointer; }
    .job.active { outline: 2px solid rgba(37, 99, 235, 0.25); }
    .row { display: flex; justify-content: space-between; gap: 12px; align-items: center; }
    .badge { font-size: 12px; padding: 2px 7px; border-radius: 999px; background: #eef2ff; color: #3730a3; }
    .badge.running { background: #e0f2fe; color: #075985; }
    .badge.done { background: #dcfce7; color: #166534; }
    .badge.failed { background: #ffedd5; color: #9a3412; }
    pre { margin: 0; white-space: pre-wrap; overflow: auto; max-height: 360px; background: #111827; color: #e5e7eb; border-radius: 6px; padding: 12px; font-size: 12px; line-height: 1.45; }
    .links { display: grid; gap: 8px; }
    .links a { color: var(--blue); text-decoration: none; overflow-wrap: anywhere; }
    video { width: 100%; max-height: 540px; border: 1px solid var(--line); border-radius: 6px; background: #000; }
    .muted { color: var(--muted); }
    @media (max-width: 1100px) { .workshop-grid, .workshop-request { grid-template-columns: 1fr; } }
    @media (max-width: 960px) { main { padding: 12px; } .home-grid, .production-layout, .editor-layout, .runtime-grid, .beat-editor { grid-template-columns: 1fr; } header { padding: 14px 12px; } .page-tabs { padding: 10px 12px 0; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AI 漫剧工作流控制台</h1>
      <div class="sub">分步运行、完整出片、日志追踪和产物预览</div>
    </div>
    <button onclick="refreshState()">刷新</button>
  </header>
  <nav class="page-tabs">
    <button id="tab-home" class="active" onclick="showPage('home')">入口</button>
    <button id="tab-workshop" onclick="showPage('workshop')">AI 生成剧本</button>
    <button id="tab-editor" onclick="showPage('editor')">导入与编辑</button>
    <button id="tab-production" onclick="showPage('production')">生成控制</button>
  </nav>
  <main>
    <div id="page-home" class="page active">
      <div class="home-grid">
        <div class="entry-card">
          <h2>AI 生成剧本</h2>
          <p>输入主题、类型和风格，由多角色工坊逐层生成剧本，并自动保存为可编辑项目。</p>
          <button class="primary" onclick="showPage('workshop')">进入 AI 工坊</button>
        </div>
        <div class="entry-card">
          <h2>导入已有剧本</h2>
          <p>上传或粘贴现有剧本，规范化为角色、场景、分幕和镜头，再继续出片流程。</p>
          <button class="primary" onclick="showPage('editor'); document.getElementById('importScript')?.focus();">导入剧本</button>
        </div>
        <div class="entry-card">
          <h2>继续编辑</h2>
          <p>加载项目 YAML，修改角色音色、分幕、对白、镜头动作和保存文件名。</p>
          <button onclick="showPage('editor')">打开编辑器</button>
        </div>
        <div class="entry-card">
          <h2>生成成片</h2>
          <p>检查项目，分步生成脚本、图片、配音、视频，或一键完整出片。</p>
          <button onclick="showPage('production')">进入生成控制</button>
        </div>
      </div>
    </div>

    <div id="page-production" class="page">
      <div class="production-layout">
        <div class="sidebar">
      <section>
        <h2>配置</h2>
        <label>项目 YAML</label>
        <select id="project" onchange="loadProjectEditor(this.value)"></select>
        <label>流程配置</label>
        <select id="config"></select>
        <div class="grid2">
          <div>
            <label>Env 文件</label>
            <input id="envFile" value=".env">
          </div>
          <div>
            <label>视频超时秒数</label>
            <input id="timeout" type="number" value="900">
          </div>
        </div>
        <label>视频镜头</label>
        <input id="keyShots" value="auto">
        <div class="hint">`auto` 会读取 `production_mode: image_to_video`；空字符串会关闭视频接口。</div>
      </section>

      <section>
        <h2>流程运行</h2>
        <div class="buttons">
          <button onclick="startJob('check')">检查项目</button>
          <button onclick="startJob('provider_status')">接口状态</button>
          <button onclick="startJob('stage','structure')">脚本分镜</button>
          <button onclick="startJob('stage','images')">生成图片</button>
          <button onclick="startJob('stage','voice')">生成配音</button>
          <button onclick="startJob('stage','videos')">生成视频</button>
          <button onclick="startJob('stage','compose')">合成成片</button>
          <button class="primary" onclick="startJob('stage','all')">一键完整出片</button>
        </div>
        <div class="hint">单独运行视频阶段前，请先确保图片阶段已经完成。单独合成前，请确保图片和配音已经完成。</div>
      </section>

      <section>
        <h2>任务</h2>
        <div id="jobs" class="muted">暂无任务</div>
      </section>
        </div>

        <div class="workspace">
          <div class="runtime-grid">
            <section>
              <h2>任务日志</h2>
              <pre id="log">等待任务开始...</pre>
            </section>
            <section>
              <h2>最新产物</h2>
              <div id="outputs" class="links"></div>
              <div id="preview" style="margin-top: 12px;"></div>
            </section>
          </div>
        </div>
      </div>
    </div>

    <div id="page-workshop" class="page">
      <section>
        <h2>AI 生成剧本</h2>
        <div class="item-card workshop-shell">
          <div class="card-head">
            <strong>AI 剧本工坊</strong>
            <span class="muted" style="font-size:12px;">多角色撰写 + 评审返工 + 结构化导入</span>
          </div>
          <div class="workshop-request">
            <div>
              <label>主题</label>
              <textarea id="outlineTheme" placeholder="例如：古风女仵作在雨夜义庄发现失踪兄长线索，点燃禁灯后引出白塔狱阴谋。"></textarea>
              <div class="grid2">
                <div>
                  <label>类型</label>
                  <input id="outlineGenre" value="古风悬疑">
                </div>
                <div>
                  <label>目标时长（秒）</label>
                  <input id="outlineDuration" type="number" value="60">
                </div>
              </div>
              <label>画风和质感</label>
              <input id="outlineStyle" value="中国古风国漫，竖屏 9:16，悬疑压迫感，角色脸型稳定，服装道具一致">
            </div>
            <div>
              <div class="grid2">
                <div>
                  <label>生成模式</label>
                  <select id="outlineMode" onchange="syncWorkshopMode()">
                    <option value="standard" selected>标准模式</option>
                    <option value="fast">快速模式</option>
                    <option value="strict">严格模式</option>
                  </select>
                </div>
                <div>
                  <label>最大返工次数</label>
                  <input id="outlineMaxRevisions" type="number" min="0" max="4" value="2">
                </div>
              </div>
              <label>主角倾向</label>
              <input id="outlineProtagonist" placeholder="例如：冷静女仵作 / 少年术士 / 群像">
              <label>自动保存文件名（可选）</label>
              <input id="outlineSaveName" placeholder="留空时使用 AI 根据剧情生成的默认文件名">
              <label>结尾类型</label>
              <select id="outlineEnding">
                <option value="悬念钩子" selected>悬念钩子</option>
                <option value="强反转">强反转</option>
                <option value="爽点收束">爽点收束</option>
                <option value="续集引子">续集引子</option>
              </select>
              <label>禁止内容</label>
              <textarea id="outlineForbidden" placeholder="例如：不要现代穿越，不要血腥细节，不要新增太多角色。"></textarea>
            </div>
          </div>
          <div class="workshop-actions">
            <button id="workshopRunBtn" class="primary" onclick="generateOutline()">开始多角色生成</button>
            <button id="workshopCancelBtn" class="danger" style="display:none;" onclick="cancelWorkshopJob()">终止生成</button>
            <button onclick="restoreWorkshopDefaults()">恢复默认提示词</button>
            <div id="outlineStatus" class="workshop-status idle">生成后会自动填入下面的结构化表单，你可以继续手动调整。</div>
          </div>
          <div class="workshop-grid">
            <div>
              <div class="card-head"><strong>生成阶段</strong></div>
              <div id="workshopStages" class="stage-list"></div>
            </div>
            <div class="artifact-panel">
              <div class="card-head"><strong>阶段产物 / 评审</strong></div>
              <div id="workshopArtifactMeta" class="artifact-meta"></div>
              <pre id="workshopArtifact">等待生成。左侧选择阶段后可以查看该阶段的撰写产物、评审意见和返工记录。</pre>
            </div>
            <div>
              <div class="card-head"><strong>角色提示词</strong></div>
              <div id="workshopRoles" class="role-list"></div>
              <div class="prompt-editor" style="margin-top:8px;">
                <div class="grid2">
                  <div>
                    <label>模型槽</label>
                    <select id="workshopRoleModelSlot">
                      <option value="llm">llm</option>
                      <option value="llm_fast">llm_fast</option>
                    </select>
                  </div>
                  <div>
                    <label>温度</label>
                    <input id="workshopRoleTemperature" type="number" min="0" max="1.2" step="0.05">
                  </div>
                </div>
                <label>系统提示词</label>
                <textarea id="workshopRoleSystemPrompt"></textarea>
                <label>任务提示词</label>
                <textarea id="workshopRoleUserPrompt"></textarea>
                <button class="small-btn" style="width:100%;margin-top:8px;" onclick="saveCurrentWorkshopRole(); renderWorkshopRoles(); setOutlineStatus('当前角色提示词已暂存，生成时会随请求提交。')">暂存当前角色提示词</button>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <div id="page-editor" class="page">
      <div class="editor-layout">
        <section>
          <h2>导入已有剧本</h2>
          <div class="item-card">
          <div class="card-head"><strong>导入已有剧本</strong></div>
          <div class="form-grid">
            <div class="field-full">
              <label>上传剧本文件</label>
              <input id="importFile" type="file" accept=".txt,.md,.markdown,.json,.yaml,.yml,.srt,.csv,.tsv,.log,.docx" onchange="loadImportFile()">
              <div class="hint">支持 txt、md、json、yaml、srt、csv 和 docx。读取后会填入下方文本框。</div>
            </div>
            <div class="field-full">
              <label>粘贴任意格式剧本</label>
              <textarea id="importScript" placeholder="可以粘贴散文式剧情、传统剧本、分场大纲、对白稿，系统会尝试规范化为角色、场景、分幕和镜头。"></textarea>
            </div>
            <div>
              <label>类型</label>
              <input id="importGenre" value="古风悬疑">
            </div>
            <div>
              <label>目标时长（秒）</label>
              <input id="importDuration" type="number" value="60">
            </div>
          </div>
          <button class="primary" style="width:100%;margin-top:8px;" onclick="importScriptDraft()">规范化导入剧本</button>
          <div id="importStatus" class="hint">导入后会自动填入结构化表单，保存后即可继续生成图片、配音、视频和成片。</div>
          </div>
        </section>
        <section>
          <h2>剧本编辑</h2>
        <div class="grid2">
          <div>
            <label>剧本文件名</label>
            <input id="projectFileName" value="new_manga_project" oninput="syncProjectPathFromFileName()">
          </div>
          <div>
            <label>保存路径</label>
            <input id="projectPath" value="data/projects/new_manga_project.yaml" readonly>
          </div>
        </div>
        <div id="storyForm" class="story-form"></div>
        <div class="editor-actions">
          <button onclick="loadProjectEditor(document.getElementById('project').value)">加载当前</button>
          <button onclick="newProjectTemplate()">新建模板</button>
          <button class="primary" onclick="saveProjectEditor()">保存剧本</button>
        </div>
        <details class="advanced">
          <summary>高级：查看或直接编辑 YAML</summary>
          <label>项目 YAML</label>
          <textarea id="projectEditor" class="yaml" spellcheck="false"></textarea>
          <div class="editor-actions">
            <button onclick="saveRawYaml()">按 YAML 保存</button>
            <button onclick="loadProjectEditor(document.getElementById('projectPath').value)">重新加载</button>
            <button onclick="syncYamlPreview()">刷新预览</button>
          </div>
        </details>
        <div id="editorStatus" class="hint">保存后会自动进入项目下拉框，可继续运行检查或一键完整出片。</div>
        </section>
      </div>
    </div>
  </main>
<script>
let selectedJob = null;
let projectOptionsSignature = '';
let configOptionsSignature = '';
let jobsSignature = '';
let outputsSignature = '';
let lastLogJobId = null;
let lastLogText = null;
let editorLoadedPath = '';
let currentProject = null;
let selectedBeatIndex = 0;
let selectedShotSide = 'first';
let currentPage = 'home';

function showPage(page) {
  const nextPage = document.getElementById(`page-${page}`) ? page : 'home';
  currentPage = nextPage;
  document.querySelectorAll('.page').forEach(el => {
    el.classList.toggle('active', el.id === `page-${nextPage}`);
  });
  document.querySelectorAll('.page-tabs button').forEach(button => {
    button.classList.toggle('active', button.id === `tab-${nextPage}`);
  });
  if (nextPage === 'production') {
    refreshJob().catch(err => console.warn(err));
  }
}

const TENCENT_VOICES = [
  {id: 101013, name: '智辉', gender: 'male', scene: '新闻男声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101030, name: '智柯', gender: 'male', scene: '通用男声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101054, name: '智友', gender: 'male', scene: '通用男声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101004, name: '智云', gender: 'male', scene: '通用男声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101021, name: '智瑞', gender: 'male', scene: '新闻男声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101015, name: '智萌', gender: 'male', scene: '男童声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101001, name: '智瑜', gender: 'female', scene: '情感女声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101055, name: '智付', gender: 'female', scene: '通用女声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101027, name: '智梅', gender: 'female', scene: '通用女声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101026, name: '智希', gender: 'female', scene: '通用女声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101011, name: '智燕', gender: 'female', scene: '新闻女声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101016, name: '智甜', gender: 'female', scene: '女童声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 101019, name: '智彤', gender: 'female', scene: '粤语女声', tier: '精品音色', sample_rate: '8k/16k'},
  {id: 501000, name: '智斌', gender: 'male', scene: '阅读男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 501003, name: '智宇', gender: 'male', scene: '阅读男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 501005, name: '飞镜', gender: 'male', scene: '聊天男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 501006, name: '千嶂', gender: 'male', scene: '聊天男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 501007, name: '浅草', gender: 'male', scene: '聊天男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 601008, name: '爱小豪', gender: 'male', scene: '聊天男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 601011, name: '爱小川', gender: 'male', scene: '聊天男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 601014, name: '爱小简', gender: 'male', scene: '聊天男声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 501001, name: '智兰', gender: 'female', scene: '资讯女声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 501002, name: '智菊', gender: 'female', scene: '阅读女声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 501004, name: '月华', gender: 'female', scene: '聊天女声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 601009, name: '爱小芊', gender: 'female', scene: '聊天女声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 601010, name: '爱小娇', gender: 'female', scene: '聊天女声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 601012, name: '爱小璟', gender: 'female', scene: '特色女声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 601013, name: '爱小伊', gender: 'female', scene: '阅读女声', tier: '大模型音色', sample_rate: '8k/16k/24k'},
  {id: 502006, name: '智小悟', gender: 'male', scene: '聊天男声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 502005, name: '智小解', gender: 'male', scene: '解说男声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 602004, name: '暖心阿灿', gender: 'male', scene: '聊天男声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 603000, name: '懂事少年', gender: 'male', scene: '特色男声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 603003, name: '随和老李', gender: 'male', scene: '聊天男声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 603005, name: '知心大林', gender: 'male', scene: '聊天男声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 603006, name: '沉稳青叔', gender: 'male', scene: '聊天男声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 502004, name: '智小满', gender: 'female', scene: '营销女声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 502003, name: '智小敏', gender: 'female', scene: '聊天女声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 502001, name: '智小柔', gender: 'female', scene: '聊天女声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 602005, name: '专业梓欣', gender: 'female', scene: '聊天女声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 603001, name: '潇湘妹妹', gender: 'female', scene: '特色女声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 603004, name: '温柔小柠', gender: 'female', scene: '聊天女声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 603007, name: '邻家女孩', gender: 'female', scene: '聊天女声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'},
  {id: 502007, name: '智小虎', gender: 'neutral', scene: '聊天童声', tier: '超自然大模型音色', sample_rate: '8k/16k/24k'}
];

const DEFAULT_WORKSHOP_STAGES = [
  {id: 'ideas', name: '创意扩展', writer: 'creative_planner', reviewer: 'commercial_reviewer', output: 'ideas.json'},
  {id: 'story_bible', name: '故事圣经', writer: 'story_architect', reviewer: 'character_reviewer', output: 'story_bible.json'},
  {id: 'beats', name: '剧情节拍', writer: 'beat_designer', reviewer: 'rhythm_reviewer', output: 'beats.json'},
  {id: 'script', name: '短剧台本', writer: 'drama_writer', reviewer: 'dialogue_reviewer', output: 'script.json'},
  {id: 'storyboard', name: '分镜设计', writer: 'storyboard_director', reviewer: 'production_reviewer', output: 'storyboard_beats.json'},
  {id: 'final_project', name: '结构入库', writer: 'formatter', reviewer: 'structure_reviewer', output: 'final_project.json'}
];

const DEFAULT_SCRIPT_ROLES = [
  {
    id: 'creative_planner',
    name: '创意策划师',
    type: 'writer',
    model_slot: 'llm',
    temperature: 0.85,
    max_tokens: 1600,
    system_prompt: '你是短漫剧创意策划师，擅长把一句主题扩展成适合 60 秒竖屏漫剧的强钩子故事方向。只输出 JSON。',
    user_prompt: '生成 3 个候选故事方向。每个候选必须包含 title、logline、core_conflict、hook、reversal、ending_hook、risk。不要写成长篇设定。'
  },
  {
    id: 'commercial_reviewer',
    name: '商业爽点评审员',
    type: 'reviewer',
    model_slot: 'llm_fast',
    temperature: 0.35,
    max_tokens: 900,
    system_prompt: '你是短剧商业爽点评审员，只检查候选是否有强钩子、明确冲突、反转和可视化价值。只输出评审 JSON。',
    user_prompt: '检查候选故事是否适合短漫剧自动生成。重点看前三秒钩子、主角目标、阻碍、反转和结尾钩子。'
  },
  {
    id: 'story_architect',
    name: '故事架构师',
    type: 'writer',
    model_slot: 'llm',
    temperature: 0.72,
    max_tokens: 2200,
    system_prompt: '你是故事架构师和人物关系设计师，负责锁定主线、人物动机、秘密、场景和世界规则。只输出 JSON。',
    user_prompt: '从已通过的候选中选择最适合的一条，生成 story_bible。必须包含 selected_idea、story_spine、characters、locations、rules、must_keep、must_avoid。角色控制在 2-4 个，场景控制在 1-3 个。'
  },
  {
    id: 'character_reviewer',
    name: '人设一致性评审员',
    type: 'reviewer',
    model_slot: 'llm_fast',
    temperature: 0.3,
    max_tokens: 900,
    system_prompt: '你是人设一致性评审员，只检查角色是否必要、动机是否清晰、视觉锁定是否可复用。只输出评审 JSON。',
    user_prompt: '检查 story_bible 中的角色、场景、规则是否够少、够清晰、适合后续生成图片和视频。'
  },
  {
    id: 'beat_designer',
    name: '剧情节拍设计师',
    type: 'writer',
    model_slot: 'llm',
    temperature: 0.7,
    max_tokens: 2100,
    system_prompt: '你是剧情节拍设计师，擅长把故事拆成竖屏短漫剧的 5-6 个高信息密度节拍。只输出 JSON。',
    user_prompt: '根据 story_bible 生成 beats。每个 beat 必须包含 id、summary、turning_point、emotion、location_id、characters、visual_moment、cliff_or_payoff。'
  },
  {
    id: 'rhythm_reviewer',
    name: '节奏评审员',
    type: 'reviewer',
    model_slot: 'llm_fast',
    temperature: 0.3,
    max_tokens: 900,
    system_prompt: '你是节奏评审员，只判断剧情节拍是否有开场钩子、升级、反转、收束或结尾钩子。只输出评审 JSON。',
    user_prompt: '检查 beats 是否每一幕都有剧情变化，是否适合目标时长，是否避免流水账。'
  },
  {
    id: 'drama_writer',
    name: '短剧编剧',
    type: 'writer',
    model_slot: 'llm',
    temperature: 0.72,
    max_tokens: 2600,
    system_prompt: '你是短剧编剧，负责把剧情节拍扩写成动作和短台词。只输出 JSON。',
    user_prompt: '根据 beats 写 script_beats。每一幕输出 summary、emotion、location_id、characters、action_first、dialogue_first、action_second、dialogue_second。台词要短，角色名前缀只用于字幕，不要在朗读正文里重复角色名。'
  },
  {
    id: 'dialogue_reviewer',
    name: '台词评审员',
    type: 'reviewer',
    model_slot: 'llm_fast',
    temperature: 0.3,
    max_tokens: 900,
    system_prompt: '你是台词评审员，只检查台词是否短、口语化、推动剧情，并且旁白和角色台词区分清楚。只输出评审 JSON。',
    user_prompt: '检查 script_beats 中每句台词是否有用、是否太长、是否和动作重复。'
  },
  {
    id: 'storyboard_director',
    name: '分镜导演',
    type: 'writer',
    model_slot: 'llm',
    temperature: 0.64,
    max_tokens: 2600,
    system_prompt: '你是分镜导演，负责把台本改写成当前系统需要的一幕两镜头结构。只输出 JSON。',
    user_prompt: '生成 storyboard_beats。每个 beat 必须包含 id、summary、emotion、location_id、characters、action_first、dialogue_first、action_second、dialogue_second、production_mode_first、production_mode_second。production_mode 默认 image_to_video。'
  },
  {
    id: 'production_reviewer',
    name: '生产可行性评审员',
    type: 'reviewer',
    model_slot: 'llm_fast',
    temperature: 0.25,
    max_tokens: 900,
    system_prompt: '你是 AI 漫剧生产可行性评审员，只检查镜头是否可画、可视频化、角色和场景引用是否有效。只输出评审 JSON。',
    user_prompt: '检查 storyboard_beats 是否每幕两个镜头、角色和场景引用有效、动作可视化、视频提示可执行。'
  },
  {
    id: 'formatter',
    name: '格式化工程师',
    type: 'writer',
    model_slot: 'llm',
    temperature: 0.28,
    max_tokens: 3200,
    system_prompt: '你是格式化工程师，负责把所有阶段产物转换成本系统 ProjectBrief JSON。只输出 JSON，不要输出 Markdown。',
    user_prompt: '输出最终 project JSON，字段必须包含 project_id、title、genre、format、aspect_ratio、target_duration_sec、audience、logline、visual_style、tone、characters、locations、beats。project_id 会作为默认保存文件名主体，必须根据剧情生成简短英文或拼音风格名称，不要使用 ai_story/new_story 等通用名。characters 必须包含 gender；voice_type 可以为 null，后续真正生成配音或一键出片前由系统检查并要求用户选择腾讯云音色。beats 必须使用 storyboard_beats 的一幕两镜头结构。'
  },
  {
    id: 'structure_reviewer',
    name: '结构校验员',
    type: 'reviewer',
    model_slot: 'llm_fast',
    temperature: 0.2,
    max_tokens: 900,
    system_prompt: '你是结构校验员，只检查最终 ProjectBrief JSON 的字段、引用、时长和生产模式。只输出评审 JSON。',
    user_prompt: '检查最终 project JSON 是否能被系统直接保存：字段齐全、角色音色可以为空但不能重复、location_id 和 characters 引用有效、production_mode 使用 image_to_video。不要因为 voice_type 为 null 判失败；音色必填检查发生在真正生成配音或一键出片前。'
  }
];

let workshopRoles = cloneValue(DEFAULT_SCRIPT_ROLES);
let workshopTimeline = [];
let selectedWorkshopRoleId = 'creative_planner';
let selectedWorkshopStageIndex = 0;
let workshopRunning = false;
let currentWorkshopJobId = localStorage.getItem('aiMangaWorkshopJobId') || '';
let workshopResultAppliedJobId = '';

function cloneValue(value) {
  return JSON.parse(JSON.stringify(value));
}

function syncWorkshopMode() {
  const defaults = {fast: 1, standard: 2, strict: 3};
  const mode = valueOf('outlineMode') || 'standard';
  const input = document.getElementById('outlineMaxRevisions');
  if (input) input.value = defaults[mode] ?? 2;
}

function restoreWorkshopDefaults() {
  if (workshopRunning) {
    setOutlineStatus('AI 剧本工坊正在生成中，当前请求结束前不能重置提示词。', true);
    return;
  }
  workshopRoles = cloneValue(DEFAULT_SCRIPT_ROLES);
  selectedWorkshopRoleId = 'creative_planner';
  renderWorkshop();
  setOutlineStatus('已恢复默认角色提示词。');
}

function renderWorkshop() {
  renderWorkshopStages();
  renderWorkshopRoles();
  renderWorkshopRoleEditor();
  renderWorkshopArtifact();
}

function renderWorkshopStages() {
  const box = document.getElementById('workshopStages');
  if (!box) return;
  box.innerHTML = DEFAULT_WORKSHOP_STAGES.map((stage, index) => {
    const entry = workshopTimeline.find(item => item.stage_id === stage.id);
    const stageState = entry?.status || (entry ? (entry.passed ? 'done' : 'failed') : '');
    const status = workshopStageStatusText(entry);
    const attempts = entry ? (entry.attempts ? `尝试 ${entry.attempts} 次` : (entry.note || '等待后端返回')) : stage.output;
    return `<div class="stage-card ${index === selectedWorkshopStageIndex ? 'active' : ''} ${stageState}" onclick="selectWorkshopStage(${index})">
      <strong>${index + 1}. ${esc(stage.name)}</strong>
      <span>${esc(status)} · ${esc(attempts)}</span>
    </div>`;
  }).join('');
}

function workshopStageStatusText(entry) {
  if (!entry) return '待运行';
  if (entry.status === 'running') return '已提交';
  if (entry.status === 'queued') return '等待结果';
  if (entry.status === 'canceled') return '已终止';
  if (entry.status === 'failed') return '失败';
  return entry.passed ? '通过' : '未完全通过';
}

function selectWorkshopStage(index) {
  selectedWorkshopStageIndex = Math.max(0, Math.min(index, DEFAULT_WORKSHOP_STAGES.length - 1));
  renderWorkshopStages();
  renderWorkshopArtifact();
}

function renderWorkshopRoles() {
  const box = document.getElementById('workshopRoles');
  if (!box) return;
  box.innerHTML = workshopRoles.map(role => {
    const typeLabel = role.type === 'reviewer' ? '评审' : '撰写';
    return `<div class="role-card ${role.id === selectedWorkshopRoleId ? 'active' : ''}" onclick="selectWorkshopRole('${role.id}')">
      <strong>${esc(role.name)}</strong>
      <span>${esc(typeLabel)} · ${esc(role.model_slot || 'llm')}</span>
    </div>`;
  }).join('');
}

function selectWorkshopRole(roleId) {
  saveCurrentWorkshopRole();
  selectedWorkshopRoleId = roleId;
  renderWorkshopRoles();
  renderWorkshopRoleEditor();
}

function renderWorkshopRoleEditor() {
  const role = workshopRoles.find(item => item.id === selectedWorkshopRoleId) || workshopRoles[0];
  if (!role) return;
  selectedWorkshopRoleId = role.id;
  const slot = document.getElementById('workshopRoleModelSlot');
  const temperature = document.getElementById('workshopRoleTemperature');
  const systemPrompt = document.getElementById('workshopRoleSystemPrompt');
  const userPrompt = document.getElementById('workshopRoleUserPrompt');
  if (!slot || !temperature || !systemPrompt || !userPrompt) return;
  slot.value = role.model_slot || 'llm';
  temperature.value = role.temperature ?? 0.7;
  systemPrompt.value = role.system_prompt || '';
  userPrompt.value = role.user_prompt || '';
}

function saveCurrentWorkshopRole() {
  if (workshopRunning) return;
  const role = workshopRoles.find(item => item.id === selectedWorkshopRoleId);
  if (!role || !document.getElementById('workshopRoleSystemPrompt')) return;
  role.model_slot = valueOf('workshopRoleModelSlot') || 'llm';
  role.temperature = Number(valueOf('workshopRoleTemperature') || role.temperature || 0.7);
  role.system_prompt = valueOf('workshopRoleSystemPrompt');
  role.user_prompt = valueOf('workshopRoleUserPrompt');
}

function renderWorkshopArtifact() {
  const meta = document.getElementById('workshopArtifactMeta');
  const box = document.getElementById('workshopArtifact');
  if (!meta || !box) return;
  const stage = DEFAULT_WORKSHOP_STAGES[selectedWorkshopStageIndex] || DEFAULT_WORKSHOP_STAGES[0];
  const entry = workshopTimeline.find(item => item.stage_id === stage.id);
  meta.innerHTML = `<span>阶段：${esc(stage.name)}</span><span>撰写：${esc(roleName(stage.writer))}</span><span>评审：${esc(roleName(stage.reviewer))}</span>`;
  if (!entry) {
    box.textContent = '等待生成。运行后这里会显示该阶段的 writer artifact、review result、返工次数和日志文件。';
    return;
  }
  if (entry.status === 'running' || entry.status === 'queued') {
    box.textContent = JSON.stringify({
      status: entry.status === 'running' ? '请求已提交' : '等待后端返回',
      note: 'AI 剧本工坊接口正在运行。当前版本是长请求模式，模型返回前不会逐段刷新真实产物；请求完成后会一次性显示所有阶段产物和评审结果。',
      stage: entry.stage_name,
      writer: entry.writer,
      reviewer: entry.reviewer
    }, null, 2);
    return;
  }
  box.textContent = JSON.stringify(entry, null, 2);
}

function roleName(roleId) {
  return workshopRoles.find(item => item.id === roleId)?.name || roleId;
}

function collectWorkshopPayload() {
  saveCurrentWorkshopRole();
  return {
    theme: valueOf('outlineTheme'),
    genre: valueOf('outlineGenre'),
    target_duration_sec: Number(valueOf('outlineDuration') || 60),
    style: valueOf('outlineStyle'),
    protagonist: valueOf('outlineProtagonist'),
    save_name: valueOf('outlineSaveName'),
    ending_type: valueOf('outlineEnding'),
    forbidden: valueOf('outlineForbidden'),
    mode: valueOf('outlineMode') || 'standard',
    max_revision_attempts: Number(valueOf('outlineMaxRevisions') || 2),
    roles: cloneValue(workshopRoles),
    stages: cloneValue(DEFAULT_WORKSHOP_STAGES),
    config: document.getElementById('config').value,
    env_file: document.getElementById('envFile').value
  };
}

function setWorkshopRunning(running, cancelRequested = false) {
  workshopRunning = running;
  const runButton = document.getElementById('workshopRunBtn');
  if (runButton) {
    runButton.disabled = running;
    runButton.textContent = running ? '生成中，请勿重复点击' : '开始多角色生成';
    runButton.setAttribute('aria-busy', running ? 'true' : 'false');
  }
  const cancelButton = document.getElementById('workshopCancelBtn');
  if (cancelButton) {
    cancelButton.style.display = running ? '' : 'none';
    cancelButton.disabled = !running || cancelRequested;
    cancelButton.textContent = cancelRequested ? '正在终止...' : '终止生成';
  }
}

function markWorkshopSubmitted() {
  workshopTimeline = DEFAULT_WORKSHOP_STAGES.map((stage, index) => ({
    stage_id: stage.id,
    stage_name: stage.name,
    writer: roleName(stage.writer),
    reviewer: roleName(stage.reviewer),
    status: index === 0 ? 'running' : 'queued',
    passed: false,
    attempts: 0,
    note: index === 0 ? '请求已提交' : '等待后端返回'
  }));
  selectedWorkshopStageIndex = 0;
  renderWorkshopStages();
  renderWorkshopArtifact();
}

function markWorkshopFailed(message) {
  workshopTimeline = DEFAULT_WORKSHOP_STAGES.map((stage, index) => {
    const existing = workshopTimeline.find(item => item.stage_id === stage.id) || {};
    return {
      ...existing,
      stage_id: stage.id,
      stage_name: stage.name,
      writer: roleName(stage.writer),
      reviewer: roleName(stage.reviewer),
      status: index === 0 ? 'failed' : (existing.status || 'queued'),
      passed: false,
      attempts: existing.attempts || 0,
      note: index === 0 ? message : (existing.note || '未执行')
    };
  });
  selectedWorkshopStageIndex = 0;
  renderWorkshopStages();
  renderWorkshopArtifact();
}

function nextPaint() {
  return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

function formPayload(action, stages) {
  return {
    action,
    stages,
    project: document.getElementById('project').value,
    config: document.getElementById('config').value,
    env_file: document.getElementById('envFile').value,
    key_shots: document.getElementById('keyShots').value,
    video_timeout_sec: Number(document.getElementById('timeout').value || 900)
  };
}

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const text = await res.text();
    try {
      const payload = JSON.parse(text);
      throw new Error(payload.error || text);
    } catch (err) {
      if (err instanceof SyntaxError) throw new Error(text);
      throw err;
    }
  }
  return await res.json();
}

async function refreshState() {
  const state = await api('/api/state');
  syncWorkshopJobFromState(state.workshop_jobs || []);
  fillSelect('project', state.projects, 'data/projects/ancient_short.yaml');
  fillSelect('config', state.configs, 'config/pipeline.siliconflow.yaml');
  renderJobs(state.jobs);
  renderOutputs(state.outputs);
  if (!editorLoadedPath && document.getElementById('project').value) {
    await loadProjectEditor(document.getElementById('project').value);
  }
  await refreshWorkshopJob();
}

function syncWorkshopJobFromState(jobs) {
  if (!Array.isArray(jobs) || !jobs.length) return;
  const active = jobs.find(job => ['queued', 'running'].includes(job.status));
  if (!currentWorkshopJobId && active) {
    currentWorkshopJobId = active.id;
    localStorage.setItem('aiMangaWorkshopJobId', currentWorkshopJobId);
  }
  const current = jobs.find(job => job.id === currentWorkshopJobId);
  if (!current) return;
  if (Array.isArray(current.artifacts) && current.artifacts.length) {
    workshopTimeline = current.artifacts;
    renderWorkshopStages();
    renderWorkshopArtifact();
  }
  if (['queued', 'running'].includes(current.status)) {
    setWorkshopRunning(true, Boolean(current.cancel_requested));
    setOutlineStatus(`后台任务运行中：${current.id}${current.current_stage_name ? `，当前阶段：${current.current_stage_name}` : ''}`, false, 'running');
  }
}

function fillSelect(id, values, preferred) {
  const el = document.getElementById(id);
  const signature = JSON.stringify(values);
  const signatureName = id === 'project' ? 'projectOptionsSignature' : 'configOptionsSignature';
  if ((id === 'project' && projectOptionsSignature === signature) || (id === 'config' && configOptionsSignature === signature)) {
    return;
  }
  const current = el.value || preferred;
  el.innerHTML = values.map(v => `<option value="${v}">${v}</option>`).join('');
  if (values.includes(current)) el.value = current;
  else if (values.includes(preferred)) el.value = preferred;
  if (signatureName === 'projectOptionsSignature') projectOptionsSignature = signature;
  if (signatureName === 'configOptionsSignature') configOptionsSignature = signature;
}

async function startJob(action, stages) {
  if (action === 'stage' || action === 'check') {
    try {
      await saveProjectEditor({silent: true});
      if (action === 'stage' && stageRequiresVoices(stages || '')) {
        const project = collectProjectForm();
        validateRequiredCharacterVoices(project);
      }
    } catch (err) {
      setEditorStatus(`未启动流程：${String(err.message || err)}`, true);
      showPage('editor');
      return;
    }
  }
  const payload = formPayload(action, stages || '');
  const job = await api('/api/jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  selectedJob = job.id;
  showPage('production');
  await refreshState();
  await refreshJob();
}

function stageRequiresVoices(stages) {
  const value = String(stages || '').trim();
  if (!value) return false;
  const parts = value.split(/[,，\s]+/).map(item => item.trim()).filter(Boolean);
  return parts.includes('voice') || parts.includes('all');
}

function setProjectPath(path) {
  const safePath = path || 'data/projects/new_manga_project.yaml';
  const pathInput = document.getElementById('projectPath');
  const nameInput = document.getElementById('projectFileName');
  if (pathInput) pathInput.value = safePath;
  if (nameInput) nameInput.value = projectFileNameFromPath(safePath);
}

function syncProjectPathFromFileName() {
  const nameInput = document.getElementById('projectFileName');
  const pathInput = document.getElementById('projectPath');
  if (!nameInput || !pathInput) return;
  const fileName = normalizeProjectFileName(nameInput.value, 'new_manga_project');
  pathInput.value = `data/projects/${fileName}.yaml`;
}

function projectFileNameFromPath(path) {
  const name = String(path || '').split('/').pop() || 'new_manga_project.yaml';
  return name.replace(/\.(ya?ml)$/i, '');
}

function normalizeProjectFileName(value, fallback) {
  let name = String(value || '').trim().replace(/\.(ya?ml)$/i, '');
  name = name.replace(/[\\/:*?"<>|]+/g, '_').replace(/\s+/g, '_').replace(/_+/g, '_').replace(/^_+|_+$/g, '');
  return name || fallback;
}

async function loadProjectEditor(path) {
  if (!path) return;
  const data = await api(`/api/project?path=${encodeURIComponent(path)}`);
  setProjectPath(data.path);
  document.getElementById('projectEditor').value = data.content;
  currentProject = normalizeProject(data.data);
  selectedBeatIndex = 0;
  selectedShotSide = 'first';
  renderProjectForm(currentProject);
  editorLoadedPath = data.path;
  setEditorStatus(`已加载：${data.path}`);
}

function newProjectTemplate() {
  const slug = `new_story_${new Date().toISOString().slice(0, 10).replaceAll('-', '')}`;
  const path = `data/projects/${slug}.yaml`;
  setProjectPath(path);
  currentProject = defaultProject(slug);
  selectedBeatIndex = 0;
  selectedShotSide = 'first';
  renderProjectForm(currentProject);
  document.getElementById('projectEditor').value = '';
  editorLoadedPath = '';
  setEditorStatus('已创建新模板，修改后点击“保存剧本”。');
}

async function saveProjectEditor(options = {}) {
  syncProjectPathFromFileName();
  const path = document.getElementById('projectPath').value.trim();
  const data = collectProjectForm();
  try {
    validateUniqueCharacterVoices(data);
  } catch (err) {
    if (!options.silent) setEditorStatus(String(err.message || err), true);
    throw err;
  }
  if (!path || !data.project_id || !data.title || !data.logline) {
    if (!options.silent) setEditorStatus('保存失败：保存路径、项目 ID、剧名和一句话故事不能为空。', true);
    throw new Error('Path, project_id, title and logline are required.');
  }
  const result = await api('/api/project', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path, data})
  });
  currentProject = normalizeProject(result.data);
  renderProjectForm(currentProject);
  document.getElementById('projectEditor').value = result.content;
  editorLoadedPath = result.path;
  projectOptionsSignature = '';
  await refreshState();
  document.getElementById('project').value = result.path;
  if (!options.silent) setEditorStatus(`保存成功：${result.path}`);
  return result;
}

async function saveRawYaml() {
  syncProjectPathFromFileName();
  const path = document.getElementById('projectPath').value.trim();
  const content = document.getElementById('projectEditor').value;
  const result = await api('/api/project', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path, content})
  });
  currentProject = normalizeProject(result.data);
  renderProjectForm(currentProject);
  document.getElementById('projectEditor').value = result.content;
  editorLoadedPath = result.path;
  projectOptionsSignature = '';
  await refreshState();
  document.getElementById('project').value = result.path;
  setEditorStatus(`YAML 保存成功：${result.path}`);
}

async function syncYamlPreview() {
  const data = collectProjectForm();
  try {
    validateUniqueCharacterVoices(data);
  } catch (err) {
    setEditorStatus(String(err.message || err), true);
    throw err;
  }
  const result = await api('/api/project/preview', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({data})
  });
  document.getElementById('projectEditor').value = result.content;
  setEditorStatus('YAML 预览已按表单刷新，尚未保存。');
}

async function generateOutline() {
  if (workshopRunning) {
    setOutlineStatus('AI 剧本工坊正在生成中，请等待当前请求完成，不要重复点击。', false, 'running');
    return;
  }
  const theme = valueOf('outlineTheme');
  if (!theme) {
    setOutlineStatus('请先填写主题。', true);
    return;
  }
  const payload = collectWorkshopPayload();
  setWorkshopRunning(true);
  currentWorkshopJobId = '';
  workshopResultAppliedJobId = '';
  selectedJob = null;
  jobsSignature = '';
  lastLogJobId = null;
  lastLogText = null;
  markWorkshopSubmitted();
  setOutlineStatus('正在创建后台任务，请稍等。创建成功后可以刷新页面，任务仍会继续运行。', false, 'running');
  await nextPaint();
  try {
    const job = await api('/api/script/workshop', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    currentWorkshopJobId = job.id;
    localStorage.setItem('aiMangaWorkshopJobId', currentWorkshopJobId);
    applyWorkshopJob(job);
  } catch (err) {
    const message = String(err.message || err);
    markWorkshopFailed(message);
    setOutlineStatus(`生成失败：${message}`, true);
    setWorkshopRunning(false);
  }
}

async function refreshWorkshopJob() {
  if (!currentWorkshopJobId) return;
  const job = await api(`/api/script/workshop/jobs/${currentWorkshopJobId}`);
  applyWorkshopJob(job);
}

function applyWorkshopJob(job) {
  if (!job || job.status === 'missing') {
    setWorkshopRunning(false);
    localStorage.removeItem('aiMangaWorkshopJobId');
    return;
  }
  currentWorkshopJobId = job.id;
  localStorage.setItem('aiMangaWorkshopJobId', currentWorkshopJobId);
  if (Array.isArray(job.artifacts) && job.artifacts.length) {
    workshopTimeline = job.artifacts;
  }
  renderWorkshopJobLog(job);
  renderWorkshopStages();
  renderWorkshopArtifact();
  const active = ['queued', 'running'].includes(job.status);
  setWorkshopRunning(active, Boolean(job.cancel_requested));
  if (job.status === 'queued') {
    setOutlineStatus(`后台任务已创建：${job.id}，等待开始。`, false, 'running');
  } else if (job.status === 'running') {
    const cancelText = job.cancel_requested ? '，已请求终止，当前模型调用结束后会停止后续阶段' : '';
    setOutlineStatus(`后台任务运行中：${job.id}${job.current_stage_name ? `，当前阶段：${job.current_stage_name}` : ''}${cancelText}`, false, 'running');
  } else if (job.status === 'done') {
    applyWorkshopResult(job);
  } else if (job.status === 'canceled') {
    setOutlineStatus(`已终止：${job.error || '后续阶段已停止。'} 日志：${job.log_dir || ''}`, true);
  } else if (job.status === 'failed') {
    markWorkshopFailed(job.error || '后台任务失败。');
    setOutlineStatus(`生成失败：${job.error || '后台任务失败。'} 日志：${job.log_dir || ''}`, true);
  }
}

function renderWorkshopJobLog(job) {
  const box = document.getElementById('log');
  if (!box || selectedJob) return;
  const nextLog = job.log || `(暂无日志)\n\n工坊任务：${job.id}\n状态：${job.status}`;
  if (lastLogJobId === `workshop:${job.id}` && lastLogText === nextLog) return;
  const shouldStickToBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  box.textContent = nextLog;
  if (shouldStickToBottom) box.scrollTop = box.scrollHeight;
  lastLogJobId = `workshop:${job.id}`;
  lastLogText = nextLog;
}

function applyWorkshopResult(job) {
  if (workshopResultAppliedJobId === job.id) {
    const title = job.result?.title || job.result?.project_id || '剧本';
    setOutlineStatus(`已生成并自动保存：${title}。保存路径：${job.project_path || job.result?.path || ''}`);
    return;
  }
  const result = job.result || {};
  if (!result.data) {
    setOutlineStatus(`后台任务已完成，但没有返回剧本数据。日志：${job.log_dir || ''}`, true);
    return;
  }
  currentProject = normalizeProject(result.data);
  selectedBeatIndex = 0;
  selectedShotSide = 'first';
  renderProjectForm(currentProject);
  const savedPath = job.project_path || result.path || `data/projects/${currentProject.project_id || 'ai_outline'}.yaml`;
  setProjectPath(savedPath);
  document.getElementById('projectEditor').value = result.content || '';
  editorLoadedPath = savedPath;
  projectOptionsSignature = '';
  if (Array.isArray(result.projects)) {
    fillSelect('project', result.projects, savedPath);
    document.getElementById('project').value = savedPath;
  }
  workshopResultAppliedJobId = job.id;
  if (result.warning) {
    setOutlineStatus(`${result.warning} 已自动保存草稿：${savedPath}。日志：${result.log_dir || job.log_dir || ''}`, true);
  } else {
    setOutlineStatus(`已生成并自动保存：${currentProject.title || currentProject.project_id}。保存路径：${savedPath}。日志：${result.log_dir || job.log_dir || ''}`);
  }
  showPage('editor');
}

async function cancelWorkshopJob() {
  if (!currentWorkshopJobId) {
    setOutlineStatus('当前没有可终止的 AI 剧本工坊任务。', true);
    return;
  }
  const cancelButton = document.getElementById('workshopCancelBtn');
  if (cancelButton) {
    cancelButton.disabled = true;
    cancelButton.textContent = '正在终止...';
  }
  try {
    const job = await api(`/api/script/workshop/jobs/${currentWorkshopJobId}/cancel`, {method: 'POST'});
    applyWorkshopJob(job);
    setOutlineStatus('已请求终止。当前正在进行的单次模型调用可能会先返回，但后续阶段会停止。', false, 'running');
  } catch (err) {
    setOutlineStatus(`终止失败：${String(err.message || err)}`, true);
  }
}

async function loadImportFile() {
  const input = document.getElementById('importFile');
  const file = input.files && input.files[0];
  if (!file) return;
  const ext = file.name.split('.').pop().toLowerCase();
  setImportStatus(`正在读取文件：${file.name}`);
  try {
    let text = '';
    if (ext === 'pdf') {
      throw new Error('暂不支持直接读取 PDF，请先转成 txt、md 或 docx。');
    } else if (ext === 'docx') {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/script/file', { method: 'POST', body: formData });
      if (!res.ok) {
        const errorText = await res.text();
        try {
          const payload = JSON.parse(errorText);
          throw new Error(payload.error || errorText);
        } catch (err) {
          if (err instanceof SyntaxError) throw new Error(errorText);
          throw err;
        }
      }
      const payload = await res.json();
      text = payload.content || '';
    } else {
      text = await file.text();
    }
    document.getElementById('importScript').value = text;
    setImportStatus(`已读取文件：${file.name}，共 ${text.length} 字符。确认内容后点击“规范化导入剧本”。`);
  } catch (err) {
    setImportStatus(`读取文件失败：${String(err.message || err)}`, true);
  }
}

async function importScriptDraft() {
  const script = valueOf('importScript');
  if (!script) {
    setImportStatus('请先粘贴要导入的剧本。', true);
    return;
  }
  setImportStatus('正在规范化剧本，请稍等...');
  try {
    const result = await api('/api/script/import', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        script,
        genre: valueOf('importGenre'),
        target_duration_sec: Number(valueOf('importDuration') || 60),
        config: document.getElementById('config').value,
        env_file: document.getElementById('envFile').value
      })
    });
    currentProject = normalizeProject(result.data);
    selectedBeatIndex = 0;
    selectedShotSide = 'first';
    renderProjectForm(currentProject);
    setProjectPath(`data/projects/${currentProject.project_id || 'imported_script'}.yaml`);
    document.getElementById('projectEditor').value = result.content;
    if (result.warning) {
      setImportStatus(`${result.warning} 请检查后点击“保存剧本”。`, true);
    } else {
      setImportStatus(`已导入：${currentProject.title || currentProject.project_id}。请检查后点击“保存剧本”。`);
    }
  } catch (err) {
    setImportStatus(`导入失败：${String(err.message || err)}`, true);
  }
}

function setOutlineStatus(message, isError = false, state = 'idle') {
  const box = document.getElementById('outlineStatus');
  box.textContent = message;
  box.className = `workshop-status ${isError ? 'error' : state}`;
}

function setImportStatus(message, isError = false) {
  const box = document.getElementById('importStatus');
  box.textContent = message;
  box.style.color = isError ? 'var(--red)' : 'var(--muted)';
}

function setEditorStatus(message, isError = false) {
  const box = document.getElementById('editorStatus');
  box.textContent = message;
  box.style.color = isError ? 'var(--red)' : 'var(--muted)';
}

function renderProjectForm(project) {
  currentProject = normalizeProject(project);
  const box = document.getElementById('storyForm');
  box.innerHTML = `
    <div class="group-title"><h3>基础信息</h3></div>
    <div class="form-grid">
      ${textInput('p_project_id', '项目 ID', currentProject.project_id)}
      ${textInput('p_title', '剧名', currentProject.title)}
      ${textInput('p_genre', '类型', currentProject.genre)}
      ${numberInput('p_target_duration_sec', '目标时长（秒）', currentProject.target_duration_sec || 60)}
      ${textInput('p_audience', '目标受众', currentProject.audience, 'field-full')}
      ${textArea('p_logline', '一句话故事', currentProject.logline, 'field-full')}
      ${textArea('p_visual_style', '固定画风', currentProject.visual_style, 'field-full')}
      ${textArea('p_tone', '节奏和情绪要求', currentProject.tone, 'field-full')}
    </div>
    ${renderCharacterCards(currentProject.characters)}
    ${renderLocationCards(currentProject.locations)}
    ${renderBeatCards(currentProject.beats)}
  `;
  renderAllVoiceSelects();
}

function renderCharacterCards(characters) {
  return `
    <div>
      <div class="group-title">
        <h3>角色</h3>
        <div class="group-actions">
          <button class="small-btn" onclick="randomAllVoices()">全部随机音色</button>
          <button class="small-btn" onclick="addCharacter()">添加角色</button>
        </div>
      </div>
      ${characters.map((item, index) => `
        <div class="item-card" data-character-index="${index}">
          <div class="card-head">
            <strong>角色 ${index + 1}</strong>
            <button class="small-btn danger" onclick="removeCharacter(${index})">删除</button>
          </div>
          <div class="form-grid">
            ${textInput(`char_${index}_id`, '角色 ID', item.id)}
            ${textInput(`char_${index}_name`, '角色名', item.name)}
            ${textInput(`char_${index}_role`, '定位', item.role)}
            ${genderSelectInput(`char_${index}_gender`, '角色性别', item.gender || '', index)}
            ${voicePickerInput(index, item)}
            ${textArea(`char_${index}_appearance`, '外貌设定', item.appearance, 'field-full')}
            ${textArea(`char_${index}_personality`, '性格', item.personality, 'field-full')}
            ${textArea(`char_${index}_visual_lock`, '视觉锁定（每行一个）', listToText(item.visual_lock), 'field-full')}
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function renderLocationCards(locations) {
  return `
    <div>
      <div class="group-title">
        <h3>场景</h3>
        <button class="small-btn" onclick="addLocation()">添加场景</button>
      </div>
      ${locations.map((item, index) => `
        <div class="item-card" data-location-index="${index}">
          <div class="card-head">
            <strong>场景 ${index + 1}</strong>
            <button class="small-btn danger" onclick="removeLocation(${index})">删除</button>
          </div>
          <div class="form-grid">
            ${textInput(`loc_${index}_id`, '场景 ID', item.id)}
            ${textInput(`loc_${index}_name`, '场景名', item.name)}
            ${textArea(`loc_${index}_description`, '场景描述', item.description, 'field-full')}
            ${textArea(`loc_${index}_visual_lock`, '视觉锁定（每行一个）', listToText(item.visual_lock), 'field-full')}
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

function renderBeatCards(beats) {
  if (!beats.length) {
    selectedBeatIndex = 0;
    return `
      <div>
        <div class="group-title">
          <h3>剧情分幕</h3>
          <button class="small-btn" onclick="addBeat()">添加分幕</button>
        </div>
        <div class="hint">暂无分幕，点击“添加分幕”开始。</div>
      </div>
    `;
  }
  selectedBeatIndex = Math.min(Math.max(selectedBeatIndex, 0), beats.length - 1);
  const item = beats[selectedBeatIndex];
  const shotLabel = selectedShotSide === 'first' ? '镜头 1' : '镜头 2';
  const actionKey = selectedShotSide === 'first' ? 'action_first' : 'action_second';
  const dialogueKey = selectedShotSide === 'first' ? 'dialogue_first' : 'dialogue_second';
  const modeKey = selectedShotSide === 'first' ? 'production_mode_first' : 'production_mode_second';
  return `
    <div>
      <div class="group-title">
        <h3>剧情分幕</h3>
        <button class="small-btn" onclick="addBeat()">添加分幕</button>
      </div>
      <div class="beat-editor">
        <div class="beat-list">
          ${beats.map((beat, index) => `
            <button class="beat-pill ${index === selectedBeatIndex ? 'active' : ''}" onclick="selectBeat(${index})" title="${esc(beat.summary)}">
              ${index + 1}. ${esc(beat.id || beat.summary || '未命名分幕')}
            </button>
          `).join('')}
          <div class="switch-row">
            <button class="small-btn" onclick="moveBeat(-1)">上一幕</button>
            <button class="small-btn" onclick="moveBeat(1)">下一幕</button>
          </div>
        </div>
        <div class="item-card" data-beat-index="${selectedBeatIndex}">
          <div class="card-head">
            <strong>分幕 ${selectedBeatIndex + 1} / ${beats.length}</strong>
            <button class="small-btn danger" onclick="removeBeat(${selectedBeatIndex})">删除本幕</button>
          </div>
          <div class="form-grid">
            ${textInput(`beat_${selectedBeatIndex}_id`, '分幕 ID', item.id)}
            ${textInput(`beat_${selectedBeatIndex}_emotion`, '情绪', item.emotion)}
            ${selectInput(`beat_${selectedBeatIndex}_location_id`, '场景', item.location_id || '', currentProject.locations.map(loc => [loc.id, `${loc.name || loc.id} (${loc.id})`]), true)}
            ${textInput(`beat_${selectedBeatIndex}_characters`, '出场角色 ID（逗号分隔）', (item.characters || []).join(', '))}
            ${textArea(`beat_${selectedBeatIndex}_summary`, '剧情摘要', item.summary, 'field-full')}
          </div>
          <div class="shot-tabs">
            <button class="${selectedShotSide === 'first' ? 'active' : ''}" onclick="selectShotSide('first')">镜头 1</button>
            <button class="${selectedShotSide === 'second' ? 'active' : ''}" onclick="selectShotSide('second')">镜头 2</button>
          </div>
          <div class="form-grid">
            ${textArea(`beat_${selectedBeatIndex}_${actionKey}`, `${shotLabel} 动作`, item[actionKey], 'field-full')}
            ${textArea(`beat_${selectedBeatIndex}_${dialogueKey}`, `${shotLabel} 台词/旁白`, item[dialogueKey], 'field-full')}
            ${selectInput(`beat_${selectedBeatIndex}_${modeKey}`, `${shotLabel} 生成方式`, item[modeKey] || 'image_to_video', productionModeOptions())}
          </div>
        </div>
      </div>
    </div>
  `;
}

function textInput(id, label, value, cls = '') {
  return `<div class="${cls}"><label>${label}</label><input id="${id}" value="${esc(value)}"></div>`;
}

function numberInput(id, label, value) {
  return `<div><label>${label}</label><input id="${id}" type="number" min="10" value="${esc(value)}"></div>`;
}

function textArea(id, label, value, cls = '') {
  return `<div class="${cls}"><label>${label}</label><textarea id="${id}">${esc(value)}</textarea></div>`;
}

function selectInput(id, label, value, options, allowEmpty = false) {
  const items = allowEmpty ? [['', '未指定']] : [];
  return `<div><label>${label}</label><select id="${id}">${
    items.concat(options).map(([optionValue, optionLabel]) =>
      `<option value="${esc(optionValue)}" ${String(value || '') === String(optionValue) ? 'selected' : ''}>${esc(optionLabel)}</option>`
    ).join('')
  }</select></div>`;
}

function genderSelectInput(id, label, value, index) {
  const options = [
    ['', '未指定'],
    ['female', '女'],
    ['male', '男'],
    ['neutral', '中性/童声']
  ];
  return `<div><label>${label}</label><select id="${id}" onchange="handleCharacterGenderChange(${index})">${
    options.map(([optionValue, optionLabel]) =>
      `<option value="${esc(optionValue)}" ${String(value || '') === String(optionValue) ? 'selected' : ''}>${esc(optionLabel)}</option>`
    ).join('')
  }</select></div>`;
}

function voicePickerInput(index, item) {
  const voiceType = item.voice_type || '';
  return `
    <div class="voice-picker">
      <label>腾讯云音色</label>
      <input id="char_${index}_voice_search" placeholder="搜索编号、名称、场景或音色类型" oninput="renderVoiceSelect(${index})">
      <div class="voice-controls">
        <select id="char_${index}_voice_type" data-current="${esc(voiceType)}" onchange="syncVoiceStyle(${index}); renderAllVoiceSelects();"></select>
        <button class="small-btn" onclick="randomVoice(${index})">随机音色</button>
      </div>
      <input id="char_${index}_voice_style" type="hidden" value="${esc(item.voice_style || '')}" data-original-style="${esc(item.voice_style || '')}" data-original-voice-type="${esc(voiceType)}">
      <div id="char_${index}_voice_label" class="voice-label">${esc(item.voice_style || '未选择音色，将使用流程配置里的默认音色。')}</div>
      <div class="voice-links">
        <a href="https://console.cloud.tencent.com/tts/complexaudio" target="_blank" rel="noopener">打开腾讯云试听音色</a>
        <a href="https://cloud.tencent.com/document/product/1073/56353" target="_blank" rel="noopener">查看腾讯云合成音频说明</a>
      </div>
    </div>
  `;
}

function productionModeOptions() {
  return [
    ['image_to_video', '优先图生视频'],
    ['static_motion', '静态分镜回退'],
    ['manual_review', '人工复核']
  ];
}

function renderAllVoiceSelects() {
  Array.from(document.querySelectorAll('[data-character-index]')).forEach(card => {
    renderVoiceSelect(Number(card.dataset.characterIndex));
  });
}

function renderVoiceSelect(index) {
  const select = document.getElementById(`char_${index}_voice_type`);
  if (!select) return;
  const current = select.value || select.dataset.current || '';
  const search = valueOf(`char_${index}_voice_search`).toLowerCase();
  const gender = valueOf(`char_${index}_gender`);
  const used = usedVoiceTypes(index);
  const filteredOptions = TENCENT_VOICES.filter(voice => {
    const haystack = `${voice.id} ${voice.name} ${voice.scene} ${voice.tier} ${voice.sample_rate}`.toLowerCase();
    return voiceGenderMatches(voice, gender) && (!search || haystack.includes(search));
  });
  const currentVoice = findVoice(current);
  const options = currentVoice && !filteredOptions.some(voice => String(voice.id) === String(current))
    ? [currentVoice].concat(filteredOptions)
    : filteredOptions;
  const rows = [`<option value="">未选择，使用默认音色</option>`].concat(
    options.map(voice => {
      const value = String(voice.id);
      const disabled = used.has(value) && value !== String(current);
      const suffix = disabled ? '（已被其他角色使用）' : '';
      return `<option value="${value}" ${disabled ? 'disabled' : ''}>${esc(voiceOptionText(voice) + suffix)}</option>`;
    })
  );
  select.innerHTML = rows.join('');
  if (current && Array.from(select.options).some(option => option.value === String(current))) {
    select.value = String(current);
  } else {
    select.value = '';
  }
  select.dataset.current = select.value;
  setVoiceLabel(index, select.value);
}

function handleCharacterGenderChange(index) {
  const select = document.getElementById(`char_${index}_voice_type`);
  if (select) {
    select.value = '';
    select.dataset.current = '';
  }
  setVoiceLabel(index, '');
  renderAllVoiceSelects();
}

function syncVoiceStyle(index) {
  const select = document.getElementById(`char_${index}_voice_type`);
  if (!select) return;
  select.dataset.current = select.value || '';
  setVoiceLabel(index, select.value);
}

function randomVoice(index, options = {}) {
  const select = document.getElementById(`char_${index}_voice_type`);
  if (!select) return false;
  const gender = valueOf(`char_${index}_gender`);
  const used = usedVoiceTypes(index);
  const candidates = TENCENT_VOICES.filter(voice => voiceGenderMatches(voice, gender) && !used.has(String(voice.id)));
  const exactGender = candidates.filter(voice => gender && voice.gender === gender);
  const exactStandard = exactGender.filter(voice => voice.tier === '精品音色');
  const standard = candidates.filter(voice => voice.tier === '精品音色');
  const pool = exactStandard.length ? exactStandard : (exactGender.length ? exactGender : (standard.length ? standard : candidates));
  if (!pool.length) {
    if (!options.quiet) setEditorStatus('没有可用的不重复音色，请减少角色数量或手动调整性别。', true);
    return false;
  }
  const voice = pool[Math.floor(Math.random() * pool.length)];
  select.dataset.current = String(voice.id);
  renderVoiceSelect(index);
  select.value = String(voice.id);
  syncVoiceStyle(index);
  renderAllVoiceSelects();
  if (!options.quiet) setEditorStatus(`已为角色 ${Number(index) + 1} 随机选择：${voiceOptionText(voice)}`);
  return true;
}

function randomAllVoices() {
  Array.from(document.querySelectorAll('[data-character-index]')).forEach(card => {
    const index = Number(card.dataset.characterIndex);
    const select = document.getElementById(`char_${index}_voice_type`);
    if (select) {
      select.value = '';
      select.dataset.current = '';
    }
  });
  let ok = true;
  Array.from(document.querySelectorAll('[data-character-index]')).forEach(card => {
    ok = randomVoice(Number(card.dataset.characterIndex), {quiet: true}) && ok;
  });
  renderAllVoiceSelects();
  setEditorStatus(ok ? '已为所有角色随机选择不重复音色。' : '部分角色没有可用的不重复音色，请手动检查。', !ok);
}

function usedVoiceTypes(exceptIndex = null) {
  const used = new Set();
  Array.from(document.querySelectorAll('[data-character-index]')).forEach(card => {
    const index = Number(card.dataset.characterIndex);
    if (exceptIndex !== null && index === Number(exceptIndex)) return;
    const value = voiceValueForIndex(index);
    if (value) used.add(String(value));
  });
  return used;
}

function voiceValueForIndex(index) {
  const select = document.getElementById(`char_${index}_voice_type`);
  if (select && (select.value || select.dataset.current)) {
    return select.value || select.dataset.current;
  }
  return currentProject?.characters?.[index]?.voice_type || '';
}

function voiceGenderMatches(voice, gender) {
  if (!gender) return true;
  if (gender === 'neutral') return voice.gender === 'neutral';
  return voice.gender === gender || voice.gender === 'neutral';
}

function setVoiceLabel(index, voiceId) {
  const hidden = document.getElementById(`char_${index}_voice_style`);
  const label = document.getElementById(`char_${index}_voice_label`);
  const voice = findVoice(voiceId);
  const originalStyle = hidden?.dataset.originalStyle || '';
  const originalVoiceType = hidden?.dataset.originalVoiceType || '';
  const fallbackText = originalStyle && !originalVoiceType
    ? `未选择音色，将使用流程配置里的默认音色。原声音备注：${originalStyle}`
    : '未选择音色，将使用流程配置里的默认音色。';
  const text = voice ? voiceOptionText(voice) : fallbackText;
  if (hidden) hidden.value = voice ? text : (originalVoiceType ? '' : originalStyle);
  if (label) label.textContent = text;
}

function findVoice(voiceId) {
  return TENCENT_VOICES.find(voice => String(voice.id) === String(voiceId || ''));
}

function voiceOptionText(voice) {
  return `${voice.id} ${voice.name} / ${voice.scene} / ${voice.tier} / ${voice.sample_rate}`;
}

function validateUniqueCharacterVoices(project) {
  const assigned = new Map();
  for (const character of project.characters || []) {
    if (character.voice_type === null || character.voice_type === undefined || character.voice_type === '') continue;
    const key = String(character.voice_type);
    const name = character.name || character.id || '未命名角色';
    if (assigned.has(key)) {
      throw new Error(`角色音色不能重复：${assigned.get(key)} 和 ${name} 都选择了 VoiceType ${key}。`);
    }
    assigned.set(key, name);
  }
}

function validateRequiredCharacterVoices(project) {
  validateUniqueCharacterVoices(project);
  const missing = (project.characters || []).filter(character =>
    character.voice_type === null || character.voice_type === undefined || character.voice_type === ''
  );
  if (!missing.length) return;
  const names = missing.map(character => character.name || character.id || '未命名角色').join('、');
  throw new Error(`生成配音或一键出片前，请先为所有角色选择腾讯云音色。未选择：${names}。保存剧本允许音色为空，但生成阶段必须补齐。`);
}

function collectProjectForm() {
  const beats = (currentProject?.beats || []).map(beat => ({...beat}));
  if (beats.length && document.querySelector('[data-beat-index]')) {
    beats[selectedBeatIndex] = collectBeat(selectedBeatIndex, beats[selectedBeatIndex]);
  }
  return {
    project_id: valueOf('p_project_id'),
    title: valueOf('p_title'),
    genre: valueOf('p_genre'),
    format: 'vertical_dynamic_comic',
    aspect_ratio: '9:16',
    target_duration_sec: Number(valueOf('p_target_duration_sec') || 60),
    audience: valueOf('p_audience'),
    logline: valueOf('p_logline'),
    visual_style: valueOf('p_visual_style'),
    tone: valueOf('p_tone'),
    characters: Array.from(document.querySelectorAll('[data-character-index]')).map(card => collectCharacter(card.dataset.characterIndex)),
    locations: Array.from(document.querySelectorAll('[data-location-index]')).map(card => collectLocation(card.dataset.locationIndex)),
    beats
  };
}

function collectCharacter(index) {
  const voiceType = valueOf(`char_${index}_voice_type`);
  return {
    id: valueOf(`char_${index}_id`),
    name: valueOf(`char_${index}_name`),
    role: valueOf(`char_${index}_role`),
    appearance: valueOf(`char_${index}_appearance`),
    personality: valueOf(`char_${index}_personality`),
    gender: valueOf(`char_${index}_gender`),
    voice_style: valueOf(`char_${index}_voice_style`),
    voice_type: voiceType ? Number(voiceType) : null,
    visual_lock: linesOf(`char_${index}_visual_lock`)
  };
}

function collectLocation(index) {
  return {
    id: valueOf(`loc_${index}_id`),
    name: valueOf(`loc_${index}_name`),
    description: valueOf(`loc_${index}_description`),
    visual_lock: linesOf(`loc_${index}_visual_lock`)
  };
}

function collectBeat(index, existing = {}) {
  const beat = {...existing};
  beat.id = valueOf(`beat_${index}_id`) || beat.id || `beat_${Number(index) + 1}`;
  beat.summary = valueOf(`beat_${index}_summary`) || beat.summary || '';
  beat.emotion = valueOf(`beat_${index}_emotion`) || beat.emotion || '';
  beat.location_id = valueOf(`beat_${index}_location_id`) || null;
  beat.characters = csvOf(`beat_${index}_characters`);
  if (selectedShotSide === 'first') {
    beat.action_first = valueOf(`beat_${index}_action_first`);
    beat.dialogue_first = valueOf(`beat_${index}_dialogue_first`);
    beat.production_mode_first = valueOf(`beat_${index}_production_mode_first`) || 'image_to_video';
  } else {
    beat.action_second = valueOf(`beat_${index}_action_second`);
    beat.dialogue_second = valueOf(`beat_${index}_dialogue_second`);
    beat.production_mode_second = valueOf(`beat_${index}_production_mode_second`) || 'image_to_video';
  }
  return beat;
}

function addCharacter() {
  const project = collectProjectForm();
  project.characters.push(defaultCharacter(project.characters.length + 1));
  renderProjectForm(project);
}

function removeCharacter(index) {
  const project = collectProjectForm();
  project.characters.splice(index, 1);
  renderProjectForm(project);
}

function addLocation() {
  const project = collectProjectForm();
  project.locations.push(defaultLocation(project.locations.length + 1));
  renderProjectForm(project);
}

function removeLocation(index) {
  const project = collectProjectForm();
  project.locations.splice(index, 1);
  renderProjectForm(project);
}

function addBeat() {
  const project = collectProjectForm();
  project.beats.push(defaultBeat(project.beats.length + 1, project));
  selectedBeatIndex = project.beats.length - 1;
  selectedShotSide = 'first';
  renderProjectForm(project);
}

function removeBeat(index) {
  const project = collectProjectForm();
  project.beats.splice(index, 1);
  selectedBeatIndex = Math.max(0, Math.min(index, project.beats.length - 1));
  selectedShotSide = 'first';
  renderProjectForm(project);
}

function selectBeat(index) {
  const project = collectProjectForm();
  selectedBeatIndex = index;
  selectedShotSide = 'first';
  renderProjectForm(project);
}

function moveBeat(delta) {
  const project = collectProjectForm();
  selectedBeatIndex = Math.max(0, Math.min(selectedBeatIndex + delta, project.beats.length - 1));
  selectedShotSide = 'first';
  renderProjectForm(project);
}

function selectShotSide(side) {
  const project = collectProjectForm();
  selectedShotSide = side;
  renderProjectForm(project);
}

function normalizeProject(project) {
  const normalized = project || defaultProject('new_story');
  normalized.characters = normalized.characters || [];
  normalized.locations = normalized.locations || [];
  normalized.beats = normalized.beats || [];
  normalized.target_duration_sec = normalized.target_duration_sec || 60;
  return normalized;
}

function defaultProject(slug) {
  return {
    project_id: slug,
    title: '新漫剧项目',
    genre: '古风悬疑',
    format: 'vertical_dynamic_comic',
    aspect_ratio: '9:16',
    target_duration_sec: 60,
    audience: '短剧用户',
    logline: '一句话写清楚主角、冲突、反转和钩子。',
    visual_style: '中国古风国漫，竖屏 9:16，角色脸型稳定，服装和道具一致，高清，无文字，无水印。',
    tone: '强钩子、快节奏、信息逐层翻转，台词短促。',
    characters: [defaultCharacter(1)],
    locations: [defaultLocation(1)],
    beats: [defaultBeat(1), defaultBeat(2)]
  };
}

function defaultCharacter(index) {
  return {
    id: index === 1 ? 'protagonist' : `character_${index}`,
    name: index === 1 ? '主角名' : `角色${index}`,
    role: index === 1 ? '主角' : '配角',
    appearance: '年龄、发型、服装、标志物、气质。',
    personality: '冷静、执着、行动力强。',
    gender: index === 1 ? 'female' : '',
    voice_style: '',
    voice_type: null,
    visual_lock: ['固定发型', '固定服装', '固定道具']
  };
}

function defaultLocation(index) {
  return {
    id: index === 1 ? 'main_location' : `location_${index}`,
    name: index === 1 ? '主场景' : `场景${index}`,
    description: '场景空间、光线、时代感、关键物件。',
    visual_lock: ['固定场景元素']
  };
}

function defaultBeat(index, project = currentProject || {}) {
  const isFirst = index === 1;
  return {
    id: isFirst ? 'hook' : (index === 2 ? 'cliffhanger' : `beat_${index}`),
    summary: isFirst ? '开场钩子：一个异常事件打破平静。' : '结尾反转：主角发现更大的危险。',
    emotion: isFirst ? '惊疑' : '悬念',
    location_id: (project.locations || [])[0]?.id || 'main_location',
    characters: [(project.characters || [])[0]?.id || 'protagonist'],
    action_first: isFirst ? '第一个镜头动作。' : '反转前的动作。',
    action_second: isFirst ? '第二个镜头动作。' : '钩子画面。',
    dialogue_first: isFirst ? '旁白：一句短钩子。' : '主角名：一句推进真相的台词。',
    dialogue_second: isFirst ? '主角名：一句角色台词。' : '旁白：结尾悬念。',
    production_mode_first: 'image_to_video',
    production_mode_second: 'image_to_video'
  };
}

function valueOf(id) {
  return (document.getElementById(id)?.value || '').trim();
}

function linesOf(id) {
  return valueOf(id).split('\n').map(item => item.trim()).filter(Boolean);
}

function csvOf(id) {
  return valueOf(id).split(/[,，\n]/).map(item => item.trim()).filter(Boolean);
}

function listToText(value) {
  return Array.isArray(value) ? value.join('\n') : '';
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[char]));
}

function renderJobs(jobs) {
  const box = document.getElementById('jobs');
  const signature = JSON.stringify(jobs.map(job => ({
    id: job.id,
    label: job.label,
    status: job.status,
    return_code: job.return_code,
    active: job.id === selectedJob
  })));
  if (jobsSignature === signature) return;
  jobsSignature = signature;
  if (!jobs.length) {
    box.textContent = '暂无任务';
    return;
  }
  box.innerHTML = jobs.map(job => {
    const cls = job.status === 'running' ? 'running' : (job.status === 'done' ? 'done' : (job.status === 'failed' ? 'failed' : ''));
    const active = job.id === selectedJob ? ' active' : '';
    return `<div class="job${active}" onclick="selectJob('${job.id}')">
      <div class="row"><strong>${job.label}</strong><span class="badge ${cls}">${job.status}</span></div>
      <div class="muted" style="font-size:12px;margin-top:4px;">${job.id}</div>
    </div>`;
  }).join('');
  if (!selectedJob && !workshopRunning && jobs[0]) selectedJob = jobs[0].id;
}

async function selectJob(id) {
  selectedJob = id;
  jobsSignature = '';
  lastLogJobId = null;
  lastLogText = null;
  await refreshState();
  await refreshJob();
}

async function refreshJob() {
  if (!selectedJob) return;
  const job = await api(`/api/jobs/${selectedJob}`);
  const nextLog = job.log || '(暂无日志)';
  if (lastLogJobId === selectedJob && lastLogText === nextLog) return;
  const box = document.getElementById('log');
  const shouldStickToBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 24;
  box.textContent = nextLog;
  if (shouldStickToBottom) box.scrollTop = box.scrollHeight;
  lastLogJobId = selectedJob;
  lastLogText = nextLog;
}

function link(path, label) {
  return `<a href="/api/file?path=${encodeURIComponent(path)}" target="_blank">${label}: ${path}</a>`;
}

function renderOutputs(outputs) {
  const signature = JSON.stringify(outputs);
  if (outputsSignature === signature) return;
  outputsSignature = signature;
  const box = document.getElementById('outputs');
  const preview = document.getElementById('preview');
  const links = [];
  if (outputs.final_video) links.push(link(outputs.final_video, '最终视频'));
  if (outputs.script) links.push(link(outputs.script, '脚本'));
  if (outputs.storyboard) links.push(link(outputs.storyboard, '故事板'));
  if (outputs.render_report) links.push(link(outputs.render_report, '渲染报告'));
  if (outputs.stage_report) links.push(link(outputs.stage_report, '阶段报告'));
  if (outputs.latest_log) links.push(link(outputs.latest_log, '最新日志'));
  if (outputs.latest_web_api_log) links.push(link(outputs.latest_web_api_log, '网页接口日志'));
  box.innerHTML = links.length ? links.join('') : '<span class="muted">暂无产物</span>';
  if (!outputs.final_video) {
    preview.innerHTML = '';
    preview.dataset.videoPath = '';
    return;
  }
  if (preview.dataset.videoPath !== outputs.final_video) {
    preview.dataset.videoPath = outputs.final_video;
    preview.innerHTML = `<video controls src="/api/file?path=${encodeURIComponent(outputs.final_video)}"></video>`;
  }
}

setInterval(async () => {
  try {
    await refreshState();
    await refreshJob();
    await refreshWorkshopJob();
  } catch (err) {
    console.warn(err);
  }
}, 2500);
renderWorkshop();
refreshState().catch(err => document.getElementById('log').textContent = String(err));
</script>
</body>
</html>
"""


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"AI manga workflow web console: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web console.")
    finally:
        server.server_close()


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_head("text/html; charset=utf-8", len(INDEX_HTML.encode("utf-8")))
            return
        if parsed.path == "/api/file":
            params = parse_qs(parsed.query)
            rel_path = unquote(params.get("path", [""])[0])
            self._send_file(rel_path, head_only=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/state":
            self._send_json({
                "projects": _list_projects(),
                "configs": _list_configs(),
                "jobs": _job_list(),
                "workshop_jobs": _workshop_job_list(),
                "outputs": _latest_outputs(),
            })
            return
        if parsed.path == "/api/project":
            params = parse_qs(parsed.query)
            rel_path = unquote(params.get("path", [""])[0])
            try:
                self._send_json(_read_project_file(rel_path))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            self._send_json(_job_detail(job_id))
            return
        if parsed.path.startswith("/api/script/workshop/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            self._send_json(_workshop_job_detail(job_id))
            return
        if parsed.path == "/api/file":
            params = parse_qs(parsed.query)
            rel_path = unquote(params.get("path", [""])[0])
            self._send_file(rel_path)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/script/workshop/jobs/") and parsed.path.endswith("/cancel"):
            job_id = parsed.path.split("/")[-2]
            try:
                self._send_json(_cancel_workshop_job(job_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        if parsed.path not in {
            "/api/jobs",
            "/api/project",
            "/api/project/preview",
            "/api/outline",
            "/api/script/workshop",
            "/api/script/import",
            "/api/script/file",
        }:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/script/file":
            try:
                self._send_json(_read_uploaded_script_file(self))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if parsed.path == "/api/script/workshop":
            try:
                job = _start_workshop_job(payload)
                self._send_json(job)
            except Exception as exc:
                log_path = _write_web_api_log("script_workshop", payload, error=exc)
                self._send_json({"error": f"{exc}；日志：{log_path}"}, status=400)
            return
        if parsed.path == "/api/script/import":
            try:
                result = _import_script(payload)
                _write_web_api_log("script_import", payload, result=result)
                self._send_json(result)
            except Exception as exc:
                log_path = _write_web_api_log("script_import", payload, error=exc)
                self._send_json({"error": f"{exc}；日志：{log_path}"}, status=400)
            return
        if parsed.path == "/api/outline":
            try:
                result = _generate_outline(payload)
                _write_web_api_log("outline", payload, result=result)
                self._send_json(result)
            except Exception as exc:
                log_path = _write_web_api_log("outline", payload, error=exc)
                self._send_json({"error": f"{exc}；日志：{log_path}"}, status=400)
            return
        if parsed.path == "/api/project/preview":
            try:
                self._send_json(_preview_project_file(payload))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/project":
            try:
                self._send_json(_save_project_file(payload))
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=400)
            return
        try:
            job = _start_job(payload)
        except Exception as exc:
            self._send_json({"error": str(exc)}, status=400)
            return
        self._send_json(asdict(job))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self._send_head(content_type, len(data))
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_head(self, content_type: str, content_length: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.end_headers()

    def _send_file(self, rel_path: str, head_only: bool = False) -> None:
        try:
            path = _safe_path(rel_path)
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        file_size = path.stat().st_size
        byte_range = _parse_byte_range(self.headers.get("Range", ""), file_size)

        if byte_range:
            start, end = byte_range
            length = end - start + 1
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Type", content_type)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if not head_only:
                self._stream_file(path, start, length)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(file_size))
        self.end_headers()
        if not head_only:
            self._stream_file(path, 0, file_size)

    def _stream_file(self, path: Path, start: int, length: int) -> None:
        remaining = length
        try:
            with path.open("rb") as file:
                file.seek(start)
                while remaining > 0:
                    chunk = file.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            return


def _start_job(payload: dict[str, Any]) -> Job:
    job_id = uuid.uuid4().hex[:12]
    jobs_dir = ROOT / "outputs" / "web_jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    action = str(payload.get("action") or "stage")
    label, command = _build_command(payload)
    log_path = jobs_dir / f"{job_id}.log"
    job = Job(id=job_id, action=action, label=label, command=command, log_path=str(log_path))
    with JOB_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(target=_run_job, args=(job,), daemon=True)
    thread.start()
    return job


def _build_command(payload: dict[str, Any]) -> tuple[str, list[str]]:
    action = str(payload.get("action") or "stage")
    config = str(payload.get("config") or "config/pipeline.siliconflow.yaml")
    project = str(payload.get("project") or "data/projects/ancient_short.yaml")
    env_file = str(payload.get("env_file") or ".env")
    key_shots = str(payload.get("key_shots", "auto"))
    timeout = str(int(payload.get("video_timeout_sec") or 900))
    python = sys.executable

    if action == "check":
        return "检查项目", [python, "-m", "manga_flow.cli", "check", "--config", config, "--project", project]
    if action == "provider_status":
        return "接口状态", [python, "-m", "manga_flow.cli", "provider-status", "--config", config, "--env-file", env_file]
    if action == "stage":
        stages = str(payload.get("stages") or "all")
        return f"运行流程：{stages}", [
            python,
            "-m",
            "manga_flow.cli",
            "stage",
            "--stages",
            stages,
            "--config",
            config,
            "--project",
            project,
            "--env-file",
            env_file,
            "--key-shots",
            key_shots,
            "--video-timeout-sec",
            timeout,
        ]
    raise ValueError(f"Unsupported action: {action}")


def _run_job(job: Job) -> None:
    with JOB_LOCK:
        job.status = "running"
        job.started_at = time.time()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    with Path(job.log_path).open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(job.command) + "\n\n")
        log.flush()
        try:
            process = subprocess.Popen(
                job.command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                log.write(line)
                log.flush()
            return_code = process.wait()
            with JOB_LOCK:
                job.return_code = return_code
                job.status = "done" if return_code == 0 else "failed"
                job.finished_at = time.time()
        except Exception as exc:
            log.write(f"\nERROR: {exc!r}\n")
            with JOB_LOCK:
                job.status = "failed"
                job.error = repr(exc)
                job.finished_at = time.time()


def _job_list() -> list[dict[str, Any]]:
    with JOB_LOCK:
        jobs = sorted(JOBS.values(), key=lambda item: item.started_at, reverse=True)
        return [asdict(job) for job in jobs[:20]]


def _job_detail(job_id: str) -> dict[str, Any]:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return {"id": job_id, "status": "missing", "log": ""}
        payload = asdict(job)
    path = Path(job.log_path)
    payload["log"] = path.read_text(encoding="utf-8", errors="replace")[-40000:] if path.exists() else ""
    return payload


def _start_workshop_job(payload: dict[str, Any]) -> dict[str, Any]:
    theme = str(payload.get("theme") or "").strip()
    if not theme:
        raise ValueError("Theme is required.")
    job_id = uuid.uuid4().hex[:12]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = ROOT / "outputs" / "web_api" / f"script_workshop_{timestamp}_{job_id}"
    log_dir.mkdir(parents=True, exist_ok=True)
    job = WorkshopJob(
        id=job_id,
        label="AI 剧本工坊",
        log_path=str(log_dir / "generation.log"),
        log_dir=str(log_dir),
    )
    with WORKSHOP_JOB_LOCK:
        WORKSHOP_JOBS[job_id] = job
    thread = threading.Thread(target=_run_workshop_job, args=(job, payload), daemon=True)
    thread.start()
    return _workshop_job_detail(job_id)


def _run_workshop_job(job: WorkshopJob, payload: dict[str, Any]) -> None:
    _update_workshop_job(job, status="running", started_at=time.time())
    _append_workshop_log(Path(job.log_dir), f"START workshop job {job.id}")
    try:
        result = _generate_script_workshop(payload, job=job)
        if job.cancel_requested:
            raise WorkshopCancelled("用户已请求终止，已停止后续保存。")
        saved_path = _autosave_workshop_project(result, job.id, save_name=payload.get("save_name"))
        result["path"] = saved_path
        result["projects"] = _list_projects()
        _write_web_api_log("script_workshop", payload, result=result)
        _update_workshop_job(
            job,
            status="done",
            result=result,
            project_path=saved_path,
            source=str(result.get("source") or ""),
            model=str(result.get("model") or ""),
            warning=str(result.get("warning") or ""),
            current_stage="",
            current_stage_name="",
            finished_at=time.time(),
        )
        _append_workshop_log(Path(job.log_dir), f"DONE saved draft: {saved_path}")
    except WorkshopCancelled as exc:
        _update_workshop_job(
            job,
            status="canceled",
            error=str(exc),
            current_stage="",
            current_stage_name="",
            artifacts=_mark_workshop_artifacts_terminal(job, "canceled", str(exc)),
            finished_at=time.time(),
        )
        _append_workshop_log(Path(job.log_dir), f"CANCELED: {exc}")
    except Exception as exc:
        _write_web_api_log("script_workshop", payload, error=exc)
        _update_workshop_job(
            job,
            status="failed",
            error=str(exc),
            current_stage="",
            current_stage_name="",
            artifacts=_mark_workshop_artifacts_terminal(job, "failed", str(exc)),
            finished_at=time.time(),
        )
        _append_workshop_log(Path(job.log_dir), f"FAILED: {exc!r}")


def _update_workshop_job(job: WorkshopJob, **fields: Any) -> None:
    with WORKSHOP_JOB_LOCK:
        for key, value in fields.items():
            setattr(job, key, value)


def _workshop_job_list() -> list[dict[str, Any]]:
    with WORKSHOP_JOB_LOCK:
        jobs = sorted(WORKSHOP_JOBS.values(), key=lambda item: item.started_at, reverse=True)
        return [_workshop_job_payload(job, include_log=False, include_result=False) for job in jobs[:12]]


def _workshop_job_detail(job_id: str) -> dict[str, Any]:
    with WORKSHOP_JOB_LOCK:
        job = WORKSHOP_JOBS.get(job_id)
        if not job:
            return {"id": job_id, "status": "missing", "log": ""}
        payload = _workshop_job_payload(job, include_log=False)
    path = Path(payload.get("log_path") or "")
    payload["log"] = path.read_text(encoding="utf-8", errors="replace")[-60000:] if path.exists() else ""
    return payload


def _workshop_job_payload(job: WorkshopJob, include_log: bool = False, include_result: bool = True) -> dict[str, Any]:
    payload = asdict(job)
    if not include_log:
        payload.pop("log", None)
    if not include_result:
        payload["has_result"] = bool(payload.get("result"))
        payload.pop("result", None)
        payload["artifacts"] = [_workshop_artifact_summary(item) for item in payload.get("artifacts") or []]
    try:
        payload["log_dir"] = str(Path(job.log_dir).relative_to(ROOT))
    except ValueError:
        payload["log_dir"] = job.log_dir
    try:
        payload["log_path"] = str(Path(job.log_path).relative_to(ROOT))
    except ValueError:
        payload["log_path"] = job.log_path
    return payload


def _workshop_artifact_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ["stage_id", "stage_name", "status", "writer", "reviewer", "passed", "attempts", "note"]
        if key in item
    }


def _cancel_workshop_job(job_id: str) -> dict[str, Any]:
    with WORKSHOP_JOB_LOCK:
        job = WORKSHOP_JOBS.get(job_id)
        if not job:
            raise ValueError(f"Workshop job does not exist: {job_id}")
        if job.status in {"done", "failed", "canceled"}:
            return _workshop_job_payload(job)
        job.cancel_requested = True
    _append_workshop_log(Path(job.log_dir), "CANCEL REQUESTED by user. Active model call may finish before cancellation takes effect.")
    return _workshop_job_detail(job_id)


def _autosave_workshop_project(result: dict[str, Any], job_id: str, save_name: Any = None) -> str:
    project = ProjectBrief.model_validate(result.get("data") or {})
    _validate_unique_character_voices(project)
    content = str(result.get("content") or "") or _project_to_yaml(project)
    projects_dir = ROOT / "data" / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    if str(save_name or "").strip():
        base_name = _safe_project_file_stem(str(save_name), fallback=project.project_id or f"ai_workshop_{timestamp}")
    else:
        base_name = _safe_project_file_stem(f"ai_workshop_{project.project_id}_{timestamp}_{job_id[:6]}", fallback=f"ai_workshop_{timestamp}")
    path = projects_dir / f"{base_name}.yaml"
    counter = 2
    while path.exists():
        path = projects_dir / f"{base_name}_{counter}.yaml"
        counter += 1
    path.write_text(content, encoding="utf-8")
    return str(path.relative_to(ROOT))


def _check_workshop_cancel(job: WorkshopJob | None) -> None:
    if job is not None and job.cancel_requested:
        raise WorkshopCancelled("用户已终止 AI 剧本工坊任务。")


def _mark_workshop_artifacts_terminal(job: WorkshopJob, status: str, note: str) -> list[dict[str, Any]]:
    with WORKSHOP_JOB_LOCK:
        artifacts = [dict(item) for item in job.artifacts]
    if not artifacts:
        return artifacts
    for item in artifacts:
        if item.get("status") in {"running", "queued"}:
            item["status"] = status
            item["note"] = note
    return artifacts


def _write_web_api_log(
    action: str,
    payload: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: Exception | None = None,
) -> str:
    log_dir = ROOT / "outputs" / "web_api"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{action}_{timestamp}_{uuid.uuid4().hex[:8]}.log"
    status = "failed" if error else ("warning" if result and result.get("warning") else "ok")
    lines = [
        f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"action: {action}",
        f"status: {status}",
        "",
        "request:",
        json.dumps(_sanitize_web_api_payload(payload), ensure_ascii=False, indent=2),
    ]
    if result is not None:
        lines.extend(
            [
                "",
                "result:",
                json.dumps(_summarize_web_api_result(result), ensure_ascii=False, indent=2),
            ]
        )
    if error is not None:
        lines.extend(
            [
                "",
                "error:",
                str(error),
                "",
                "traceback:",
                "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            ]
        )
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(log_path.relative_to(ROOT))


def _sanitize_web_api_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in payload.items():
        if key in {"script", "theme"}:
            text = str(value or "")
            sanitized[key] = {
                "chars": len(text),
                "preview": text[:2000],
                "truncated": len(text) > 2000,
            }
        elif key == "roles" and isinstance(value, list):
            sanitized[key] = [
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "type": item.get("type"),
                    "model_slot": item.get("model_slot"),
                    "system_prompt_chars": len(str(item.get("system_prompt") or "")),
                    "user_prompt_chars": len(str(item.get("user_prompt") or "")),
                }
                for item in value
                if isinstance(item, dict)
            ]
        else:
            sanitized[key] = value
    return sanitized


def _summarize_web_api_result(result: dict[str, Any]) -> dict[str, Any]:
    summary_keys = ["source", "model", "project_id", "title", "warning", "log_dir"]
    summary = {key: result.get(key) for key in summary_keys if key in result}
    if isinstance(result.get("artifacts"), list):
        summary["artifact_count"] = len(result["artifacts"])
    data = result.get("data")
    if isinstance(data, dict):
        summary["counts"] = {
            "characters": len(data.get("characters") or []),
            "locations": len(data.get("locations") or []),
            "beats": len(data.get("beats") or []),
        }
    return summary


def _list_projects() -> list[str]:
    return _relative_files(ROOT / "data" / "projects", "*.yaml")


def _list_configs() -> list[str]:
    return _relative_files(ROOT / "config", "*.yaml")


def _relative_files(directory: Path, pattern: str) -> list[str]:
    if not directory.exists():
        return []
    return [str(path.relative_to(ROOT)) for path in sorted(directory.glob(pattern))]


def _read_uploaded_script_file(handler: Handler) -> dict[str, Any]:
    form = cgi.FieldStorage(
        fp=handler.rfile,
        headers=handler.headers,
        environ={
            "REQUEST_METHOD": "POST",
            "CONTENT_TYPE": handler.headers.get("Content-Type", ""),
        },
    )
    field = form["file"] if "file" in form else None
    if isinstance(field, list):
        field = field[0] if field else None
    if field is None or not getattr(field, "filename", ""):
        raise ValueError("No uploaded file found.")
    filename = Path(str(field.filename)).name
    data = field.file.read()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("File is too large. Please upload a file under 8MB.")
    content = _extract_uploaded_text(filename, data)
    if not content.strip():
        raise ValueError("No readable text was extracted from the file.")
    return {"filename": filename, "content": content, "chars": len(content)}


def _extract_uploaded_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        return _extract_docx_text(data)
    if suffix == ".pdf":
        raise ValueError("PDF upload is not supported yet. Please convert it to txt, md, or docx.")
    return _decode_text_file(data)


def _decode_text_file(data: bytes) -> str:
    for encoding in ["utf-8-sig", "utf-8", "gb18030", "big5"]:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            document_xml = archive.read("word/document.xml")
    except Exception as exc:
        raise ValueError(f"Could not read docx document text: {exc}") from exc
    root = ElementTree.fromstring(document_xml)
    paragraph_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"
    text_tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"
    paragraphs: list[str] = []
    for paragraph in root.iter(paragraph_tag):
        texts = [node.text or "" for node in paragraph.iter(text_tag)]
        line = "".join(texts).strip()
        if line:
            paragraphs.append(line)
    return "\n".join(paragraphs)


def _read_project_file(rel_path: str) -> dict[str, Any]:
    path = _project_file_path(rel_path)
    if not path.exists():
        raise ValueError(f"Project file does not exist: {rel_path}")
    content = path.read_text(encoding="utf-8")
    project = _project_from_content(content)
    return _project_response(path, project, content)


def _save_project_file(payload: dict[str, Any]) -> dict[str, Any]:
    rel_path = str(payload.get("path") or "").strip()
    path = _project_file_path(rel_path)
    if isinstance(payload.get("data"), dict):
        project = ProjectBrief.model_validate(payload["data"])
        _validate_unique_character_voices(project)
        content = _project_to_yaml(project)
    else:
        content = str(payload.get("content") or "")
        if not content.strip():
            raise ValueError("Project YAML content is empty.")
        project = _project_from_content(content)
        _validate_unique_character_voices(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    response = _project_response(path, project, content)
    response["projects"] = _list_projects()
    return response


def _preview_project_file(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload.get("data"), dict):
        raise ValueError("Project preview requires structured project data.")
    project = ProjectBrief.model_validate(payload["data"])
    _validate_unique_character_voices(project)
    content = _project_to_yaml(project)
    return {
        "content": content,
        "data": _project_data(project),
        "project_id": project.project_id,
        "title": project.title,
    }


def _generate_outline(payload: dict[str, Any]) -> dict[str, Any]:
    theme = str(payload.get("theme") or "").strip()
    if not theme:
        raise ValueError("Theme is required.")
    genre = str(payload.get("genre") or "古风悬疑").strip()
    target_duration_sec = int(payload.get("target_duration_sec") or 60)
    config_path = _safe_path(str(payload.get("config") or "config/pipeline.siliconflow.yaml"))
    env_path = _safe_path(str(payload.get("env_file") or ".env"))
    config = load_config(config_path)
    provider = config.providers.get("llm")
    if not provider or not provider.enabled:
        raise ValueError("LLM provider slot is not enabled in config.")

    client = SiliconFlowClient.from_provider(provider, env_path=env_path)
    client.timeout = 360
    messages = [
        {
            "role": "system",
            "content": (
                "你是短漫剧编剧和分镜策划。只输出 JSON 对象，不要输出 Markdown。"
                "JSON 必须能被后续自动化流程直接使用。"
            ),
        },
        {
            "role": "user",
            "content": _outline_prompt(theme, genre, target_duration_sec),
        },
    ]
    try:
        response = client.chat_completion(
            provider,
            messages=messages,
            temperature=0.78,
            top_p=0.9,
            max_tokens=2600,
            response_format={"type": "json_object"},
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        raw_data = _extract_json_object(content)
        data = raw_data.get("project", raw_data)
        if not isinstance(data, dict):
            raise ValueError("LLM response did not contain a project object.")
        data = _normalize_outline_data(data, genre, target_duration_sec)
        project = ProjectBrief.model_validate(data)
        warning = ""
        source = "llm"
    except Exception as exc:
        project = _fallback_outline_project(theme, genre, target_duration_sec)
        warning = f"AI 模型调用失败，已填入本地草稿：{exc}"
        source = "fallback"
    yaml_content = _project_to_yaml(project)
    return {
        "content": yaml_content,
        "data": _project_data(project),
        "project_id": project.project_id,
        "title": project.title,
        "model": SiliconFlowClient.model_name(provider),
        "source": source,
        "warning": warning,
    }


def _generate_script_workshop(payload: dict[str, Any], job: WorkshopJob | None = None) -> dict[str, Any]:
    theme = str(payload.get("theme") or "").strip()
    if not theme:
        raise ValueError("Theme is required.")
    genre = str(payload.get("genre") or "古风悬疑").strip()
    target_duration_sec = int(payload.get("target_duration_sec") or 60)
    config_path = _safe_path(str(payload.get("config") or "config/pipeline.siliconflow.yaml"))
    env_path = _safe_path(str(payload.get("env_file") or ".env"))
    config = load_config(config_path)
    if not _enabled_provider(config, "llm"):
        raise ValueError("LLM provider slot is not enabled in config.")

    log_dir = Path(job.log_dir) if job is not None else ROOT / "outputs" / "web_api" / f"script_workshop_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    log_dir.mkdir(parents=True, exist_ok=True)
    roles = _workshop_roles_by_id(payload.get("roles"))
    stages = _workshop_stages(payload.get("stages"))
    max_revision_attempts = _workshop_revision_attempts(payload)
    story_request = {
        "theme": theme,
        "genre": genre,
        "target_duration_sec": target_duration_sec,
        "style": str(payload.get("style") or "中国古风国漫，竖屏 9:16，角色稳定，高清，无文字，无水印。").strip(),
        "protagonist": str(payload.get("protagonist") or "").strip(),
        "ending_type": str(payload.get("ending_type") or "悬念钩子").strip(),
        "forbidden": str(payload.get("forbidden") or "").strip(),
        "mode": str(payload.get("mode") or "standard").strip(),
        "max_revision_attempts": max_revision_attempts,
    }
    _write_workshop_json(log_dir, "00_request.json", {"story_request": story_request, "stages": stages, "roles": list(roles.values())})

    warnings: list[str] = []
    context: dict[str, Any] = {}
    artifacts: list[dict[str, Any]] = []
    used_models: list[str] = []
    source = "workshop"

    try:
        for stage in stages:
            _check_workshop_cancel(job)
            _append_workshop_log(log_dir, f"STAGE START {stage['id']} {stage['name']}")
            _update_workshop_job(
                job,
                current_stage=stage["id"],
                current_stage_name=stage["name"],
                artifacts=artifacts + [_pending_workshop_stage(stage, roles)],
            ) if job is not None else None
            stage_result = _run_workshop_stage(
                config=config,
                env_path=env_path,
                roles=roles,
                stage=stage,
                story_request=story_request,
                context=context,
                max_revision_attempts=max_revision_attempts,
                log_dir=log_dir,
                warnings=warnings,
                used_models=used_models,
                job=job,
            )
            context[stage["id"]] = stage_result["artifact"]
            artifacts.append(_summarize_workshop_stage(stage_result))
            _update_workshop_job(job, artifacts=artifacts) if job is not None else None
            _append_workshop_log(log_dir, f"STAGE DONE {stage['id']} passed={stage_result['passed']}")
            _check_workshop_cancel(job)

        data = _extract_workshop_project_data(context, story_request)
        project = ProjectBrief.model_validate(data)
        _validate_unique_character_voices(project)
    except WorkshopCancelled:
        raise
    except Exception as exc:
        project = _fallback_outline_project(theme, genre, target_duration_sec)
        source = "fallback"
        warnings.append(f"AI 剧本工坊失败，已填入本地草稿：{exc}")
        _update_workshop_job(job, artifacts=_mark_workshop_artifacts_terminal(job, "failed", str(exc))) if job is not None else None
        _append_workshop_log(log_dir, f"FALLBACK: {exc!r}")

    yaml_content = _project_to_yaml(project)
    _write_workshop_json(log_dir, "final_project.json", _project_data(project))
    return {
        "content": yaml_content,
        "data": _project_data(project),
        "project_id": project.project_id,
        "title": project.title,
        "model": ", ".join(dict.fromkeys(used_models)) or "",
        "source": source,
        "warning": "；".join(warnings),
        "artifacts": artifacts,
        "log_dir": str(log_dir.relative_to(ROOT)),
    }


def _enabled_provider(config: Any, slot: str) -> Any | None:
    provider = config.providers.get(slot)
    if provider and provider.enabled:
        return provider
    return None


def _workshop_roles_by_id(raw_roles: Any) -> dict[str, dict[str, Any]]:
    default_roles = {role["id"]: dict(role) for role in _default_workshop_roles()}
    if isinstance(raw_roles, list):
        for item in raw_roles:
            if not isinstance(item, dict):
                continue
            role_id = str(item.get("id") or "").strip()
            if not role_id:
                continue
            role = default_roles.get(role_id, {"id": role_id})
            role.update({key: value for key, value in item.items() if value is not None})
            default_roles[role_id] = role
    return default_roles


def _default_workshop_roles() -> list[dict[str, Any]]:
    return [
        {
            "id": "creative_planner",
            "name": "创意策划师",
            "type": "writer",
            "model_slot": "llm",
            "temperature": 0.85,
            "max_tokens": 1600,
            "system_prompt": "你是短漫剧创意策划师。只输出 JSON。",
            "user_prompt": "生成 3 个候选故事方向，每个候选要有 logline、hook、reversal 和 ending_hook。",
        },
        {
            "id": "commercial_reviewer",
            "name": "商业爽点评审员",
            "type": "reviewer",
            "model_slot": "llm_fast",
            "temperature": 0.35,
            "max_tokens": 900,
            "system_prompt": "你是短剧商业爽点评审员。只输出评审 JSON。",
            "user_prompt": "检查候选是否有强钩子、明确冲突、反转和结尾钩子。",
        },
        {
            "id": "story_architect",
            "name": "故事架构师",
            "type": "writer",
            "model_slot": "llm",
            "temperature": 0.72,
            "max_tokens": 2200,
            "system_prompt": "你是故事架构师和人物关系设计师。只输出 JSON。",
            "user_prompt": "生成 story_bible，包含 selected_idea、story_spine、characters、locations、rules。",
        },
        {
            "id": "character_reviewer",
            "name": "人设一致性评审员",
            "type": "reviewer",
            "model_slot": "llm_fast",
            "temperature": 0.3,
            "max_tokens": 900,
            "system_prompt": "你是人设一致性评审员。只输出评审 JSON。",
            "user_prompt": "检查角色是否必要、动机是否清晰、视觉锁定是否可复用。",
        },
        {
            "id": "beat_designer",
            "name": "剧情节拍设计师",
            "type": "writer",
            "model_slot": "llm",
            "temperature": 0.7,
            "max_tokens": 2100,
            "system_prompt": "你是剧情节拍设计师。只输出 JSON。",
            "user_prompt": "生成 5-6 个剧情节拍，每个节拍有剧情变化和视觉瞬间。",
        },
        {
            "id": "rhythm_reviewer",
            "name": "节奏评审员",
            "type": "reviewer",
            "model_slot": "llm_fast",
            "temperature": 0.3,
            "max_tokens": 900,
            "system_prompt": "你是节奏评审员。只输出评审 JSON。",
            "user_prompt": "检查剧情节奏是否有钩子、升级、反转和结尾。",
        },
        {
            "id": "drama_writer",
            "name": "短剧编剧",
            "type": "writer",
            "model_slot": "llm",
            "temperature": 0.72,
            "max_tokens": 2600,
            "system_prompt": "你是短剧编剧。只输出 JSON。",
            "user_prompt": "把节拍扩写成动作和短台词，输出 script_beats。",
        },
        {
            "id": "dialogue_reviewer",
            "name": "台词评审员",
            "type": "reviewer",
            "model_slot": "llm_fast",
            "temperature": 0.3,
            "max_tokens": 900,
            "system_prompt": "你是台词评审员。只输出评审 JSON。",
            "user_prompt": "检查台词是否短、口语化、推动剧情。",
        },
        {
            "id": "storyboard_director",
            "name": "分镜导演",
            "type": "writer",
            "model_slot": "llm",
            "temperature": 0.64,
            "max_tokens": 2600,
            "system_prompt": "你是分镜导演。只输出 JSON。",
            "user_prompt": "把台本改写成每幕两个镜头的 storyboard_beats。",
        },
        {
            "id": "production_reviewer",
            "name": "生产可行性评审员",
            "type": "reviewer",
            "model_slot": "llm_fast",
            "temperature": 0.25,
            "max_tokens": 900,
            "system_prompt": "你是生产可行性评审员。只输出评审 JSON。",
            "user_prompt": "检查镜头是否可画、可视频化、角色和场景引用是否有效。",
        },
        {
            "id": "formatter",
            "name": "格式化工程师",
            "type": "writer",
            "model_slot": "llm",
            "temperature": 0.28,
            "max_tokens": 3200,
            "system_prompt": "你是格式化工程师。只输出 ProjectBrief JSON。",
            "user_prompt": "把所有阶段产物转换成最终 project JSON。project_id 会作为默认保存文件名主体，必须根据剧情生成简短英文或拼音风格名称，不要使用 ai_story/new_story 等通用名。characters 必须包含 gender；voice_type 可以为 null，保存允许为空，真正生成配音或一键出片前由系统要求用户补齐腾讯云音色。",
        },
        {
            "id": "structure_reviewer",
            "name": "结构校验员",
            "type": "reviewer",
            "model_slot": "llm_fast",
            "temperature": 0.2,
            "max_tokens": 900,
            "system_prompt": "你是结构校验员。只输出评审 JSON。",
            "user_prompt": "检查最终 project JSON 是否能被系统直接保存。角色音色 voice_type 可以为空但不能重复；不要因为 voice_type 为 null 判失败，音色必填检查发生在真正生成配音或一键出片前。",
        },
    ]


def _workshop_stages(raw_stages: Any) -> list[dict[str, str]]:
    defaults = [
        {"id": "ideas", "name": "创意扩展", "writer": "creative_planner", "reviewer": "commercial_reviewer", "output": "ideas.json"},
        {"id": "story_bible", "name": "故事圣经", "writer": "story_architect", "reviewer": "character_reviewer", "output": "story_bible.json"},
        {"id": "beats", "name": "剧情节拍", "writer": "beat_designer", "reviewer": "rhythm_reviewer", "output": "beats.json"},
        {"id": "script", "name": "短剧台本", "writer": "drama_writer", "reviewer": "dialogue_reviewer", "output": "script.json"},
        {"id": "storyboard", "name": "分镜设计", "writer": "storyboard_director", "reviewer": "production_reviewer", "output": "storyboard_beats.json"},
        {"id": "final_project", "name": "结构入库", "writer": "formatter", "reviewer": "structure_reviewer", "output": "final_project.json"},
    ]
    if not isinstance(raw_stages, list):
        return defaults
    stages: list[dict[str, str]] = []
    for item in raw_stages:
        if not isinstance(item, dict):
            continue
        stage = {
            "id": str(item.get("id") or "").strip(),
            "name": str(item.get("name") or item.get("id") or "").strip(),
            "writer": str(item.get("writer") or "").strip(),
            "reviewer": str(item.get("reviewer") or "").strip(),
            "output": str(item.get("output") or "").strip(),
        }
        if stage["id"] and stage["writer"] and stage["reviewer"]:
            stages.append(stage)
    return stages or defaults


def _workshop_revision_attempts(payload: dict[str, Any]) -> int:
    mode_defaults = {"fast": 1, "standard": 2, "strict": 3}
    mode = str(payload.get("mode") or "standard").strip()
    raw_value = payload.get("max_revision_attempts")
    try:
        value = int(raw_value if raw_value is not None else mode_defaults.get(mode, 2))
    except (TypeError, ValueError):
        value = mode_defaults.get(mode, 2)
    return max(0, min(value, 4))


def _run_workshop_stage(
    *,
    config: Any,
    env_path: Path,
    roles: dict[str, dict[str, Any]],
    stage: dict[str, str],
    story_request: dict[str, Any],
    context: dict[str, Any],
    max_revision_attempts: int,
    log_dir: Path,
    warnings: list[str],
    used_models: list[str],
    job: WorkshopJob | None = None,
) -> dict[str, Any]:
    writer = roles.get(stage["writer"])
    reviewer = roles.get(stage["reviewer"])
    if not writer:
        raise ValueError(f"Missing writer role for stage {stage['id']}: {stage['writer']}")
    if not reviewer:
        raise ValueError(f"Missing reviewer role for stage {stage['id']}: {stage['reviewer']}")

    artifact: dict[str, Any] = {}
    review: dict[str, Any] = {}
    attempt_records: list[dict[str, Any]] = []
    for attempt in range(max_revision_attempts + 1):
        _check_workshop_cancel(job)
        _append_workshop_log(log_dir, f"CALL writer stage={stage['id']} attempt={attempt} role={writer.get('name') or writer.get('id')}")
        writer_input: dict[str, Any] = {
            "stage": stage,
            "attempt": attempt,
            "story_request": story_request,
            "upstream_context": context,
            "output_contract": _workshop_output_contract(stage["id"], story_request),
        }
        if attempt > 0:
            writer_input["previous_output"] = artifact
            writer_input["review"] = review
            writer_input["rewrite_rules"] = [
                "只修 review.issues 指出的部分。",
                "必须保留 review.must_keep。",
                "不得改动 review.must_not_change。",
                "输出结构应与 previous_output 保持兼容。",
            ]
        artifact = _call_workshop_role_json(
            config=config,
            env_path=env_path,
            role=writer,
            role_input=writer_input,
            log_dir=log_dir,
            file_name=f"{stage['id']}_attempt_{attempt}_writer.json",
            used_models=used_models,
        )
        _check_workshop_cancel(job)
        try:
            _append_workshop_log(log_dir, f"CALL reviewer stage={stage['id']} attempt={attempt} role={reviewer.get('name') or reviewer.get('id')}")
            raw_review = _call_workshop_role_json(
                config=config,
                env_path=env_path,
                role=reviewer,
                role_input={
                    "stage": stage,
                    "attempt": attempt,
                    "story_request": story_request,
                    "upstream_context": context,
                    "artifact": artifact,
                    "review_contract": _workshop_review_contract(),
                },
                log_dir=log_dir,
                file_name=f"{stage['id']}_attempt_{attempt}_review.json",
                used_models=used_models,
            )
            review = _normalize_workshop_review(raw_review)
            _check_workshop_cancel(job)
        except Exception as exc:
            if isinstance(exc, WorkshopCancelled):
                raise
            review = {
                "passed": True,
                "score": 0,
                "severity": "medium",
                "summary": f"评审调用失败，已带 warning 继续：{exc}",
                "issues": [],
                "revision_brief": "",
                "must_keep": [],
                "must_not_change": [],
            }
            warnings.append(f"{stage['name']}评审调用失败，已继续：{exc}")
            _write_workshop_json(log_dir, f"{stage['id']}_attempt_{attempt}_review_failed.json", review)

        attempt_records.append(
            {
                "attempt": attempt,
                "passed": bool(review.get("passed")),
                "score": review.get("score"),
                "severity": review.get("severity"),
                "summary": review.get("summary"),
            }
        )
        _update_workshop_job(
            job,
            artifacts=[*_job_artifacts_without_pending(job), _stage_progress_summary(stage, writer, reviewer, artifact, review, attempt_records)],
        ) if job is not None else None
        if review.get("passed"):
            break
        if attempt >= max_revision_attempts:
            severity = str(review.get("severity") or "medium")
            message = f"{stage['name']}未通过评审，已达到最大返工次数：{review.get('summary') or '无摘要'}"
            if severity == "critical":
                raise ValueError(message)
            warnings.append(message)

    _write_workshop_json(
        log_dir,
        f"{stage['id']}_summary.json",
        {
            "stage": stage,
            "attempts": attempt_records,
            "artifact": artifact,
            "review": review,
        },
    )
    return {
        "stage": stage,
        "writer": writer,
        "reviewer": reviewer,
        "artifact": artifact,
        "review": review,
        "attempts": attempt_records,
        "passed": bool(review.get("passed")),
    }


def _call_workshop_role_json(
    *,
    config: Any,
    env_path: Path,
    role: dict[str, Any],
    role_input: dict[str, Any],
    log_dir: Path,
    file_name: str,
    used_models: list[str],
) -> dict[str, Any]:
    provider = _workshop_role_provider(config, role)
    client = SiliconFlowClient.from_provider(provider, env_path=env_path)
    client.timeout = 360
    model_name = SiliconFlowClient.model_name(provider)
    used_models.append(model_name)
    messages = [
        {
            "role": "system",
            "content": (
                f"{role.get('system_prompt') or ''}\n\n"
                "你必须只输出合法 JSON 对象，不要输出 Markdown、解释文字或代码块。"
            ).strip(),
        },
        {
            "role": "user",
            "content": (
                f"{role.get('user_prompt') or ''}\n\n"
                "输入材料 JSON：\n"
                f"{json.dumps(role_input, ensure_ascii=False, indent=2)}"
            ).strip(),
        },
    ]
    response = client.chat_completion(
        provider,
        messages=messages,
        temperature=float(role.get("temperature") or provider.extra.get("temperature", 0.7)),
        top_p=0.9,
        max_tokens=int(role.get("max_tokens") or provider.extra.get("max_tokens", 2000)),
        response_format={"type": "json_object"},
    )
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
    data = _extract_json_object(content)
    _write_workshop_json(
        log_dir,
        file_name,
        {
            "role": {key: role.get(key) for key in ["id", "name", "type", "model_slot", "temperature", "max_tokens"]},
            "model": model_name,
            "input": role_input,
            "raw_content": content,
            "json": data,
        },
    )
    return data


def _workshop_role_provider(config: Any, role: dict[str, Any]) -> Any:
    slot = str(role.get("model_slot") or "llm").strip() or "llm"
    provider = _enabled_provider(config, slot)
    if not provider:
        provider = _enabled_provider(config, "llm")
    if not provider:
        raise ValueError(f"LLM provider slot is not enabled: {slot}")
    return provider


def _workshop_output_contract(stage_id: str, story_request: dict[str, Any]) -> dict[str, Any]:
    contracts: dict[str, Any] = {
        "ideas": {
            "candidates": [
                {
                    "id": "idea_1",
                    "title": "候选标题",
                    "logline": "一句话故事",
                    "core_conflict": "核心冲突",
                    "hook": "前三秒钩子",
                    "reversal": "主要反转",
                    "ending_hook": "结尾钩子",
                    "risk": "潜在问题",
                }
            ]
        },
        "story_bible": {
            "selected_idea": {},
            "story_spine": {},
            "characters": [],
            "locations": [],
            "rules": [],
            "must_keep": [],
            "must_avoid": [],
        },
        "beats": {"beats": [{"id": "beat_1", "summary": "", "turning_point": "", "emotion": "", "location_id": "", "characters": []}]},
        "script": {
            "script_beats": [
                {
                    "id": "beat_1",
                    "summary": "",
                    "emotion": "",
                    "location_id": "",
                    "characters": [],
                    "action_first": "",
                    "dialogue_first": "",
                    "action_second": "",
                    "dialogue_second": "",
                }
            ]
        },
        "storyboard": {
            "storyboard_beats": [
                {
                    "id": "beat_1",
                    "summary": "",
                    "emotion": "",
                    "location_id": "",
                    "characters": [],
                    "action_first": "",
                    "dialogue_first": "",
                    "production_mode_first": "image_to_video",
                    "action_second": "",
                    "dialogue_second": "",
                    "production_mode_second": "image_to_video",
                }
            ]
        },
        "final_project": {
            "project_id": "english_or_pinyin_story_slug_used_as_default_filename",
            "title": "剧名",
            "genre": story_request["genre"],
            "format": "vertical_dynamic_comic",
            "aspect_ratio": "9:16",
            "target_duration_sec": story_request["target_duration_sec"],
            "audience": "短剧用户",
            "logline": "一句话故事",
            "visual_style": story_request["style"],
            "tone": "节奏和情绪要求",
            "characters": [],
            "locations": [],
            "beats": [],
        },
    }
    return contracts.get(stage_id, {})


def _workshop_review_contract() -> dict[str, Any]:
    return {
        "passed": False,
        "score": 0,
        "severity": "none|low|medium|high|critical",
        "summary": "整体评价",
        "issues": [
            {
                "id": "issue_1",
                "severity": "high",
                "field": "字段或段落",
                "problem": "问题",
                "fix_instruction": "返工指令",
            }
        ],
        "revision_brief": "返工摘要",
        "must_keep": [],
        "must_not_change": [],
    }


def _normalize_workshop_review(raw_review: dict[str, Any]) -> dict[str, Any]:
    review = raw_review.get("review") if isinstance(raw_review.get("review"), dict) else raw_review
    passed = _boolish(review.get("passed"))
    if "passed" not in review and review.get("score") is not None:
        try:
            passed = int(review.get("score")) >= 80
        except (TypeError, ValueError):
            passed = False
    severity = str(review.get("severity") or ("none" if passed else "medium")).lower()
    if severity not in {"none", "low", "medium", "high", "critical"}:
        severity = "medium"
    try:
        score = int(review.get("score") if review.get("score") is not None else (85 if passed else 60))
    except (TypeError, ValueError):
        score = 85 if passed else 60
    return {
        "passed": passed,
        "score": max(0, min(score, 100)),
        "severity": severity,
        "summary": str(review.get("summary") or ""),
        "issues": review.get("issues") if isinstance(review.get("issues"), list) else [],
        "revision_brief": str(review.get("revision_brief") or ""),
        "must_keep": _as_list(review.get("must_keep")),
        "must_not_change": _as_list(review.get("must_not_change")),
    }


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"true", "yes", "y", "1", "通过", "pass", "passed"}


def _extract_workshop_project_data(context: dict[str, Any], story_request: dict[str, Any]) -> dict[str, Any]:
    final_artifact = context.get("final_project") or {}
    if isinstance(final_artifact.get("project"), dict):
        candidate = final_artifact["project"]
    elif isinstance(final_artifact, dict) and {"project_id", "title", "logline"}.issubset(final_artifact.keys()):
        candidate = final_artifact
    else:
        candidate = _coerce_project_from_workshop_context(context, story_request)
    data = _normalize_outline_data(
        dict(candidate),
        str(story_request["genre"]),
        int(story_request["target_duration_sec"]),
    )
    fallback = _project_data(
        _fallback_outline_project(
            str(story_request["theme"]),
            str(story_request["genre"]),
            int(story_request["target_duration_sec"]),
        )
    )
    if not data.get("characters"):
        data["characters"] = fallback["characters"]
    if not data.get("locations"):
        data["locations"] = fallback["locations"]
    if not data.get("beats"):
        data["beats"] = fallback["beats"]
    return data


def _coerce_project_from_workshop_context(context: dict[str, Any], story_request: dict[str, Any]) -> dict[str, Any]:
    ideas = context.get("ideas") if isinstance(context.get("ideas"), dict) else {}
    bible = context.get("story_bible") if isinstance(context.get("story_bible"), dict) else {}
    script = context.get("script") if isinstance(context.get("script"), dict) else {}
    storyboard = context.get("storyboard") if isinstance(context.get("storyboard"), dict) else {}
    selected = bible.get("selected_idea") if isinstance(bible.get("selected_idea"), dict) else {}
    candidates = ideas.get("candidates") if isinstance(ideas.get("candidates"), list) else []
    first_candidate = candidates[0] if candidates and isinstance(candidates[0], dict) else {}
    title = selected.get("title") or first_candidate.get("title") or "AI 剧本工坊草稿"
    logline = selected.get("logline") or first_candidate.get("logline") or str(story_request["theme"])
    characters = _coerce_workshop_characters(_first_list([bible, script, storyboard], ["characters", "roles"]))
    locations = _coerce_workshop_locations(_first_list([bible, script, storyboard], ["locations", "scenes"]))
    beats = _coerce_workshop_beats(_first_list([storyboard, script, context.get("beats") or {}], ["storyboard_beats", "script_beats", "beats"]))
    return {
        "project_id": _project_id(title),
        "title": title,
        "genre": story_request["genre"],
        "format": "vertical_dynamic_comic",
        "aspect_ratio": "9:16",
        "target_duration_sec": story_request["target_duration_sec"],
        "audience": "短剧用户",
        "logline": logline,
        "visual_style": story_request["style"],
        "tone": f"{story_request['mode']} 模式，多角色评审生成，结尾类型：{story_request['ending_type']}。",
        "characters": characters,
        "locations": locations,
        "beats": beats,
    }


def _first_list(sources: list[Any], keys: list[str]) -> list[Any]:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if isinstance(value, list) and value:
                return value
    return []


def _coerce_workshop_characters(items: list[Any]) -> list[dict[str, Any]]:
    characters: list[dict[str, Any]] = []
    for index, item in enumerate(items[:4], start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("character_name") or f"角色{index}")
        characters.append(
            {
                "id": _project_id(item.get("id") or ("protagonist" if index == 1 else f"character_{index}")),
                "name": name,
                "role": str(item.get("role") or ("主角" if index == 1 else "配角")),
                "appearance": str(item.get("appearance") or item.get("visual") or "待补充外貌。"),
                "personality": str(item.get("personality") or item.get("motivation") or ""),
                "gender": _normalize_gender(item.get("gender") or _guess_character_gender(item)),
                "voice_style": "",
                "voice_type": None,
                "visual_lock": _as_list(item.get("visual_lock") or item.get("visual_tags") or item.get("visual_identity")),
            }
        )
    return characters


def _coerce_workshop_locations(items: list[Any]) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    for index, item in enumerate(items[:4], start=1):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("scene_name") or f"场景{index}")
        locations.append(
            {
                "id": _project_id(item.get("id") or ("main_location" if index == 1 else f"location_{index}")),
                "name": name,
                "description": str(item.get("description") or item.get("visual") or "待补充场景描述。"),
                "visual_lock": _as_list(item.get("visual_lock") or item.get("visual_tags") or item.get("key_props")),
            }
        )
    return locations


def _coerce_workshop_beats(items: list[Any]) -> list[dict[str, Any]]:
    beats: list[dict[str, Any]] = []
    for index, item in enumerate(items[:8], start=1):
        if not isinstance(item, dict):
            continue
        shots = item.get("shots") if isinstance(item.get("shots"), list) else []
        first = shots[0] if len(shots) > 0 and isinstance(shots[0], dict) else {}
        second = shots[1] if len(shots) > 1 and isinstance(shots[1], dict) else {}
        beats.append(
            {
                "id": _project_id(item.get("id") or ("hook" if index == 1 else f"beat_{index}")),
                "summary": str(item.get("summary") or item.get("title") or f"第 {index} 幕剧情。"),
                "emotion": str(item.get("emotion") or item.get("mood") or ""),
                "location_id": _project_id(item.get("location_id") or item.get("location") or "main_location"),
                "characters": _as_list(item.get("characters")) or ["protagonist"],
                "action_first": str(item.get("action_first") or first.get("action") or first.get("description") or "镜头 1 动作待补充。"),
                "dialogue_first": str(item.get("dialogue_first") or first.get("dialogue") or "旁白：剧情推进。"),
                "production_mode_first": _normalize_production_mode(item.get("production_mode_first") or first.get("production_mode")),
                "action_second": str(item.get("action_second") or second.get("action") or second.get("description") or "镜头 2 动作待补充。"),
                "dialogue_second": str(item.get("dialogue_second") or second.get("dialogue") or "旁白：留下悬念。"),
                "production_mode_second": _normalize_production_mode(item.get("production_mode_second") or second.get("production_mode")),
            }
        )
    return beats


def _summarize_workshop_stage(stage_result: dict[str, Any]) -> dict[str, Any]:
    stage = stage_result["stage"]
    return {
        "stage_id": stage["id"],
        "stage_name": stage["name"],
        "status": "done" if stage_result["passed"] else "failed",
        "writer": stage_result["writer"].get("name") or stage["writer"],
        "reviewer": stage_result["reviewer"].get("name") or stage["reviewer"],
        "passed": stage_result["passed"],
        "attempts": len(stage_result["attempts"]),
        "attempt_records": stage_result["attempts"],
        "artifact": stage_result["artifact"],
        "review": stage_result["review"],
    }


def _pending_workshop_stage(stage: dict[str, str], roles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    writer = roles.get(stage["writer"], {})
    reviewer = roles.get(stage["reviewer"], {})
    return {
        "stage_id": stage["id"],
        "stage_name": stage["name"],
        "status": "running",
        "writer": writer.get("name") or stage["writer"],
        "reviewer": reviewer.get("name") or stage["reviewer"],
        "passed": False,
        "attempts": 0,
        "note": "阶段已开始，等待模型返回。",
    }


def _stage_progress_summary(
    stage: dict[str, str],
    writer: dict[str, Any],
    reviewer: dict[str, Any],
    artifact: dict[str, Any],
    review: dict[str, Any],
    attempt_records: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "stage_id": stage["id"],
        "stage_name": stage["name"],
        "status": "running",
        "writer": writer.get("name") or stage["writer"],
        "reviewer": reviewer.get("name") or stage["reviewer"],
        "passed": bool(review.get("passed")),
        "attempts": len(attempt_records),
        "attempt_records": attempt_records,
        "artifact": artifact,
        "review": review,
        "note": "当前阶段已有一次模型返回，等待评审通过或返工完成。",
    }


def _job_artifacts_without_pending(job: WorkshopJob | None) -> list[dict[str, Any]]:
    if job is None:
        return []
    with WORKSHOP_JOB_LOCK:
        current_stage = job.current_stage
        return [item for item in job.artifacts if item.get("stage_id") != current_stage]


def _write_workshop_json(log_dir: Path, file_name: str, payload: Any) -> str:
    path = log_dir / file_name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path.relative_to(ROOT))


def _append_workshop_log(log_dir: Path, message: str) -> None:
    path = log_dir / "generation.log"
    with path.open("a", encoding="utf-8") as file:
        file.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def _import_script(payload: dict[str, Any]) -> dict[str, Any]:
    script = str(payload.get("script") or "").strip()
    if not script:
        raise ValueError("Script text is required.")
    genre = str(payload.get("genre") or "古风悬疑").strip()
    target_duration_sec = int(payload.get("target_duration_sec") or 60)
    config_path = _safe_path(str(payload.get("config") or "config/pipeline.siliconflow.yaml"))
    env_path = _safe_path(str(payload.get("env_file") or ".env"))
    config = load_config(config_path)
    provider = config.providers.get("llm")
    if not provider or not provider.enabled:
        raise ValueError("LLM provider slot is not enabled in config.")

    truncated = len(script) > 12000
    script_for_model = script[:12000]
    client = SiliconFlowClient.from_provider(provider, env_path=env_path)
    client.timeout = 180
    messages = [
        {
            "role": "system",
            "content": (
                "你是短漫剧剧本整理师。你的任务是把用户已有剧本规范化为系统可用的项目 JSON。"
                "只输出 JSON 对象，不要输出 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": _script_import_prompt(script_for_model, genre, target_duration_sec, truncated),
        },
    ]
    warnings: list[str] = []
    if truncated:
        warnings.append("原剧本超过 12000 字，本次只导入前 12000 字。")
    try:
        response = client.chat_completion(
            provider,
            messages=messages,
            temperature=0.35,
            top_p=0.85,
            max_tokens=3000,
            response_format={"type": "json_object"},
        )
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        raw_data = _extract_json_object(content)
        data = raw_data.get("project", raw_data)
        if not isinstance(data, dict):
            raise ValueError("LLM response did not contain a project object.")
        data = _normalize_outline_data(data, genre, target_duration_sec)
        project = ProjectBrief.model_validate(data)
        source = "llm"
    except Exception as exc:
        project = _fallback_import_project(script, genre, target_duration_sec)
        source = "fallback"
        warnings.append(f"AI 模型导入失败，已使用本地拆分草稿：{exc}")

    yaml_content = _project_to_yaml(project)
    return {
        "content": yaml_content,
        "data": _project_data(project),
        "project_id": project.project_id,
        "title": project.title,
        "model": SiliconFlowClient.model_name(provider),
        "source": source,
        "warning": "；".join(warnings),
    }


def _outline_prompt(theme: str, genre: str, target_duration_sec: int) -> str:
    return f"""
请根据主题生成一个短漫剧粗略大纲，并严格输出 JSON。

主题：{theme}
类型：{genre}
目标时长：{target_duration_sec} 秒

要求：
- 输出对象本身就是 project，不要包 Markdown。
- project_id 必须是英文小写、数字、下划线组成。
- format 固定为 vertical_dynamic_comic，aspect_ratio 固定为 "9:16"。
- 生成 2 个角色，每个角色要有 id/name/role/appearance/personality/gender/voice_style/visual_lock。
- gender 只能是 female、male、neutral 或空字符串；voice_type 可以省略或填 null，后续由网页选择腾讯云音色。
- 生成 2 个场景，每个场景要有 id/name/description/visual_lock。
- visual_lock 必须是字符串数组，不要写成单个字符串。
- 生成 4 个 beats，每个 beat 是一幕，每幕拆成两个镜头。
- 每个 beat 必须包含：
  id, summary, emotion, location_id, characters,
  action_first, dialogue_first, production_mode_first,
  action_second, dialogue_second, production_mode_second。
- dialogue_first/dialogue_second 可以是“旁白：...”或“角色名：...”，不要写长段对白。
- production_mode_first 和 production_mode_second 默认使用 image_to_video。
- 内容要是粗略可编辑大纲，不要过度展开成长剧本。每个字段控制在 35 个汉字以内。

JSON 字段结构示例：
{{
  "project_id": "moon_lamp_case",
  "title": "月下借命灯",
  "genre": "{genre}",
  "format": "vertical_dynamic_comic",
  "aspect_ratio": "9:16",
  "target_duration_sec": {target_duration_sec},
  "audience": "短剧用户",
  "logline": "一句话故事",
  "visual_style": "中国古风国漫，竖屏 9:16...",
  "tone": "强钩子、快节奏、结尾留悬念。",
  "characters": [],
  "locations": [],
  "beats": []
}}
""".strip()


def _script_import_prompt(script: str, genre: str, target_duration_sec: int, truncated: bool) -> str:
    truncation_note = "注意：剧本文本已因过长被截断，请只整理已提供部分。" if truncated else ""
    return f"""
请把下面用户已有剧本规范化为本系统可用的短漫剧 project JSON。

类型：{genre}
目标时长：{target_duration_sec} 秒
{truncation_note}

整理要求：
- 尽量保留原剧本的人物关系、关键设定、情节顺序、已有对白。
- 不要求完全照搬格式，必须转成下方 JSON 字段。
- project_id 必须是英文小写、数字、下划线组成。
- format 固定为 vertical_dynamic_comic，aspect_ratio 固定为 "9:16"。
- characters 从原剧本提取 1-4 个主要角色；每个角色补充 gender；visual_lock 必须是字符串数组。
- gender 只能是 female、male、neutral 或空字符串；voice_type 可以省略或填 null，后续由网页选择腾讯云音色。
- locations 从原剧本提取 1-4 个主要场景；visual_lock 必须是字符串数组。
- beats 按剧情顺序拆成 4-8 幕；每幕拆成两个镜头。
- 每个 beat 必须包含：
  id, summary, emotion, location_id, characters,
  action_first, dialogue_first, production_mode_first,
  action_second, dialogue_second, production_mode_second。
- 如果原剧本有对白，优先放进 dialogue_first/dialogue_second。
- 每个镜头生成方式默认 image_to_video。
- 如果原文很长，请保留最适合 {target_duration_sec} 秒样片的核心冲突和反转。

输出 JSON 示例：
{{
  "project_id": "imported_story",
  "title": "剧名",
  "genre": "{genre}",
  "format": "vertical_dynamic_comic",
  "aspect_ratio": "9:16",
  "target_duration_sec": {target_duration_sec},
  "audience": "短剧用户",
  "logline": "一句话故事",
  "visual_style": "中国国漫，竖屏 9:16，角色一致，高清，无文字，无水印。",
  "tone": "短剧节奏，强钩子，结尾留悬念。",
  "characters": [],
  "locations": [],
  "beats": []
}}

待规范化剧本：
---
{script}
---
""".strip()


def _extract_json_object(content: str) -> dict[str, Any]:
    if not content.strip():
        raise ValueError("LLM returned empty content.")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        end = content.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("LLM response did not contain JSON.")
        data = json.loads(content[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("LLM response JSON must be an object.")
    return data


def _normalize_outline_data(data: dict[str, Any], genre: str, target_duration_sec: int) -> dict[str, Any]:
    normalized = dict(data)
    normalized["project_id"] = _project_id(normalized.get("project_id") or normalized.get("title") or "ai_story")
    normalized.setdefault("title", "AI 生成大纲")
    normalized.setdefault("genre", genre)
    normalized["format"] = "vertical_dynamic_comic"
    normalized["aspect_ratio"] = "9:16"
    normalized["target_duration_sec"] = int(normalized.get("target_duration_sec") or target_duration_sec)
    normalized.setdefault("audience", "短剧用户")
    normalized.setdefault("visual_style", "中国国漫，竖屏 9:16，角色一致，高清，无文字，无水印。")
    normalized.setdefault("tone", "强钩子、快节奏、信息逐层翻转，结尾留悬念。")
    characters = [item for item in normalized.get("characters") or [] if isinstance(item, dict)]
    for index, character in enumerate(characters, start=1):
        character.setdefault("id", "protagonist" if index == 1 else f"character_{index}")
        character.setdefault("name", f"角色{index}")
        character.setdefault("role", "主角" if index == 1 else "配角")
        character.setdefault("appearance", "待补充外貌。")
        character.setdefault("personality", "")
        character["gender"] = _normalize_gender(character.get("gender") or _guess_character_gender(character))
        character.setdefault("voice_style", "")
        character["voice_type"] = _normalize_voice_type(character.get("voice_type"))
        character["visual_lock"] = _as_list(character.get("visual_lock"))
    normalized["characters"] = characters

    locations = [item for item in normalized.get("locations") or [] if isinstance(item, dict)]
    for index, location in enumerate(locations, start=1):
        location.setdefault("id", "main_location" if index == 1 else f"location_{index}")
        location.setdefault("name", f"场景{index}")
        location.setdefault("description", "待补充场景描述。")
        location["visual_lock"] = _as_list(location.get("visual_lock"))
    normalized["locations"] = locations

    default_location = locations[0]["id"] if locations else None
    default_characters = [characters[0]["id"]] if characters else []
    beats = [item for item in normalized.get("beats") or [] if isinstance(item, dict)]
    for index, beat in enumerate(beats, start=1):
        beat.setdefault("id", "hook" if index == 1 else f"beat_{index}")
        beat.setdefault("summary", f"第 {index} 幕剧情。")
        beat.setdefault("emotion", "")
        beat["location_id"] = beat.get("location_id") or default_location
        beat["characters"] = _as_list(beat.get("characters")) or default_characters
        beat.setdefault("action_first", "镜头 1 动作待补充。")
        beat.setdefault("dialogue_first", "旁白：待补充。")
        beat.setdefault("action_second", "镜头 2 动作待补充。")
        beat.setdefault("dialogue_second", "旁白：待补充。")
        beat["production_mode_first"] = _normalize_production_mode(beat.get("production_mode_first"))
        beat["production_mode_second"] = _normalize_production_mode(beat.get("production_mode_second"))
    normalized["beats"] = beats
    return normalized


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    parts = re.split(r"[\n,，、；;]+", text)
    return [part.strip() for part in parts if part.strip()]


def _normalize_gender(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"female", "f", "woman", "girl", "女", "女性", "女声"}:
        return "female"
    if text in {"male", "m", "man", "boy", "男", "男性", "男声"}:
        return "male"
    if text in {"neutral", "child", "kid", "中性", "童声", "儿童"}:
        return "neutral"
    return ""


def _guess_character_gender(character: dict[str, Any]) -> str:
    text = " ".join(
        str(character.get(key) or "")
        for key in ["name", "role", "appearance", "personality", "voice_style"]
    )
    if re.search(r"女|她|少女|女子|姑娘|娘子|夫人|女声|女性|丫鬟|小姐", text):
        return "female"
    if re.search(r"男|他|少年|男子|公子|郎君|将军|老者|男声|男性|书生", text):
        return "male"
    if re.search(r"童声|孩子|孩童|儿童", text):
        return "neutral"
    return ""


def _normalize_voice_type(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_production_mode(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"image_to_video", "static_motion", "manual_review"}:
        return text
    if text in {"图生视频", "视频生成", "ai_video", "video"}:
        return "image_to_video"
    if text in {"静态", "静态分镜", "fallback", "static"}:
        return "static_motion"
    if text in {"人工", "人工复核", "review"}:
        return "manual_review"
    return "image_to_video"


def _fallback_outline_project(theme: str, genre: str, target_duration_sec: int) -> ProjectBrief:
    project_id = _project_id(theme[:40])
    data = {
        "project_id": project_id,
        "title": "AI 大纲待生成",
        "genre": genre,
        "format": "vertical_dynamic_comic",
        "aspect_ratio": "9:16",
        "target_duration_sec": target_duration_sec,
        "audience": "短剧用户",
        "logline": theme,
        "visual_style": "中国古风国漫，竖屏 9:16，角色脸型稳定，服装和道具一致，高清，无文字，无水印。",
        "tone": "强钩子、快节奏、信息逐层翻转，结尾留悬念。",
        "characters": [
            {
                "id": "protagonist",
                "name": "主角",
                "role": "主角",
                "appearance": "请补充年龄、发型、服装、标志物和气质。",
                "personality": "冷静、执着、行动力强。",
                "gender": "",
                "voice_style": "年轻主角音色，情绪克制。",
                "voice_type": None,
                "visual_lock": ["固定发型", "固定服装", "固定道具"],
            },
            {
                "id": "opponent",
                "name": "关键人物",
                "role": "对手或引路人",
                "appearance": "请补充外貌和标志物。",
                "personality": "隐藏秘密，推动反转。",
                "gender": "",
                "voice_style": "低音，语速稳定。",
                "voice_type": None,
                "visual_lock": ["固定服装", "固定标志物"],
            },
        ],
        "locations": [
            {
                "id": "main_location",
                "name": "主场景",
                "description": "围绕主题设置主要场景，突出时代感、光线和关键物件。",
                "visual_lock": ["固定场景元素", "关键道具"],
            }
        ],
        "beats": [
            {
                "id": "hook",
                "summary": "开场异常事件出现，主角被迫介入。",
                "emotion": "惊疑",
                "location_id": "main_location",
                "characters": ["protagonist"],
                "action_first": "主角进入主场景，发现与主题相关的异常线索。",
                "dialogue_first": "旁白：这一夜，平静被一个不该出现的东西打破。",
                "production_mode_first": "image_to_video",
                "action_second": "关键物件或人物出现，线索指向更深的秘密。",
                "dialogue_second": "主角：这不是意外。",
                "production_mode_second": "image_to_video",
            },
            {
                "id": "clue",
                "summary": "主角追查线索，发现旧事与自己有关。",
                "emotion": "追查",
                "location_id": "main_location",
                "characters": ["protagonist", "opponent"],
                "action_first": "主角检查线索，发现被刻意隐藏的痕迹。",
                "dialogue_first": "主角：有人想让我看到这一切。",
                "production_mode_first": "image_to_video",
                "action_second": "关键人物出现，阻止主角继续追查。",
                "dialogue_second": "关键人物：再往前一步，你会后悔。",
                "production_mode_second": "image_to_video",
            },
            {
                "id": "reversal",
                "summary": "真相第一次反转，主角发现自己并非旁观者。",
                "emotion": "反转",
                "location_id": "main_location",
                "characters": ["protagonist", "opponent"],
                "action_first": "主角把线索拼合，发现目标不是别人而是自己。",
                "dialogue_first": "旁白：她以为自己在查案，其实案子一直在等她。",
                "production_mode_first": "image_to_video",
                "action_second": "关键人物露出真正目的。",
                "dialogue_second": "关键人物：你终于走到这里了。",
                "production_mode_second": "image_to_video",
            },
            {
                "id": "cliffhanger",
                "summary": "结尾钩子抛出更大危机。",
                "emotion": "悬念",
                "location_id": "main_location",
                "characters": ["protagonist"],
                "action_first": "主角触碰关键物件，场景发生异变。",
                "dialogue_first": "主角：原来真正的入口在这里。",
                "production_mode_first": "image_to_video",
                "action_second": "画面定格在新的危险或更大的秘密上。",
                "dialogue_second": "旁白：她打开的不是答案，是下一场局。",
                "production_mode_second": "image_to_video",
            },
        ],
    }
    return ProjectBrief.model_validate(data)


def _fallback_import_project(script: str, genre: str, target_duration_sec: int) -> ProjectBrief:
    paragraphs = _script_segments(script)
    title = _guess_script_title(paragraphs)
    core = paragraphs[1:] if title != "导入剧本" and len(paragraphs) > 1 else paragraphs
    if not core:
        core = ["导入剧本内容待补充。"]
    beat_count = min(max(len(core), 2), 6)
    selected = core[:beat_count]
    characters = _guess_character_names(script)
    protagonist = characters[0] if characters else "主角"
    support = characters[1] if len(characters) > 1 else "关键人物"
    data = {
        "project_id": f"imported_script_{int(time.time())}",
        "title": title,
        "genre": genre,
        "format": "vertical_dynamic_comic",
        "aspect_ratio": "9:16",
        "target_duration_sec": target_duration_sec,
        "audience": "短剧用户",
        "logline": _compact_text(core[0], 80),
        "visual_style": "中国国漫，竖屏 9:16，角色脸型稳定，服装和道具一致，高清，无文字，无水印。",
        "tone": "按原剧本节奏整理，保留核心冲突、反转和结尾悬念。",
        "characters": [
            {
                "id": "protagonist",
                "name": protagonist,
                "role": "主角",
                "appearance": "请根据原剧本补充年龄、发型、服装、标志物和气质。",
                "personality": "由原剧本导入，待细化。",
                "gender": "",
                "voice_style": "主角音色，情绪清晰。",
                "voice_type": None,
                "visual_lock": ["固定发型", "固定服装", "固定道具"],
            },
            {
                "id": "supporting_role",
                "name": support,
                "role": "关键人物",
                "appearance": "请根据原剧本补充外貌和标志物。",
                "personality": "推动冲突或反转。",
                "gender": "",
                "voice_style": "与主角区分的音色。",
                "voice_type": None,
                "visual_lock": ["固定服装", "固定标志物"],
            },
        ],
        "locations": [
            {
                "id": "main_location",
                "name": "主场景",
                "description": "从原剧本导入的主要发生地点，待补充视觉细节。",
                "visual_lock": ["关键场景元素", "主要道具"],
            }
        ],
        "beats": [],
    }
    for index, segment in enumerate(selected, start=1):
        first, second = _split_segment_for_shots(segment)
        data["beats"].append(
            {
                "id": "hook" if index == 1 else f"beat_{index}",
                "summary": _compact_text(segment, 42),
                "emotion": "悬念" if index == beat_count else ("惊疑" if index == 1 else "推进"),
                "location_id": "main_location",
                "characters": ["protagonist", "supporting_role"],
                "action_first": _compact_text(first, 55) or "根据原剧本补充镜头 1 动作。",
                "dialogue_first": _extract_dialogue(first) or "旁白：剧情继续推进。",
                "production_mode_first": "image_to_video",
                "action_second": _compact_text(second, 55) or "根据原剧本补充镜头 2 动作。",
                "dialogue_second": _extract_dialogue(second) or "旁白：留下新的悬念。",
                "production_mode_second": "image_to_video",
            }
        )
    return ProjectBrief.model_validate(data)


def _script_segments(script: str) -> list[str]:
    lines = [line.strip() for line in script.replace("\r\n", "\n").split("\n")]
    blocks: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(" ".join(current))
    if len(blocks) >= 2:
        return [_compact_text(block, 240) for block in blocks if block.strip()]
    sentences = re.split(r"(?<=[。！？!?])", script)
    return [_compact_text(sentence, 240) for sentence in sentences if sentence.strip()]


def _guess_script_title(paragraphs: list[str]) -> str:
    if not paragraphs:
        return "导入剧本"
    first = paragraphs[0].strip(" #《》")
    if 2 <= len(first) <= 18 and not re.search(r"[。！？!?：:，,]", first):
        return first
    match = re.search(r"(?:标题|剧名|片名)[:：]\s*([^\n]{2,24})", paragraphs[0])
    return match.group(1).strip("《》 ") if match else "导入剧本"


def _guess_character_names(script: str) -> list[str]:
    names: list[str] = []
    for name in re.findall(r"([\u4e00-\u9fa5]{2,4})[:：]", script):
        if name not in {"旁白", "镜头", "场景", "内景", "外景"} and name not in names:
            names.append(name)
    return names[:4]


def _split_segment_for_shots(segment: str) -> tuple[str, str]:
    midpoint = max(1, len(segment) // 2)
    split_at = segment.find("。", midpoint)
    if split_at < 0:
        split_at = segment.find("，", midpoint)
    if split_at < 0:
        split_at = midpoint
    return segment[: split_at + 1].strip(), segment[split_at + 1 :].strip()


def _extract_dialogue(text: str) -> str:
    match = re.search(r"([\u4e00-\u9fa5]{2,4}|旁白)[:：]\s*([^。！？!?]{1,36})", text)
    if match:
        return f"{match.group(1)}：{match.group(2).strip()}"
    return ""


def _compact_text(text: str, limit: int) -> str:
    compact = re.sub(r"\s+", " ", str(text)).strip()
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _project_id(value: Any) -> str:
    text = str(value or "ai_story").strip().lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or f"ai_story_{int(time.time())}"


def _safe_project_file_stem(value: Any, fallback: str = "new_manga_project") -> str:
    text = str(value or "").strip()
    text = re.sub(r"\.ya?ml$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._ ")
    if text in {"", ".", ".."}:
        text = _project_id(fallback) or "new_manga_project"
    return text


def _project_from_content(content: str) -> ProjectBrief:
    data = yaml.safe_load(content) or {}
    if not isinstance(data, dict):
        raise ValueError("Project YAML must be a mapping/object.")
    return ProjectBrief.model_validate(data)


def _project_to_yaml(project: ProjectBrief) -> str:
    data = project.model_dump(mode="json", exclude_none=True)
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _validate_unique_character_voices(project: ProjectBrief) -> None:
    assigned: dict[int, str] = {}
    for character in project.characters:
        if character.voice_type is None:
            continue
        if character.voice_type in assigned:
            raise ValueError(
                f"角色音色不能重复：{assigned[character.voice_type]} 和 {character.name} "
                f"都使用 VoiceType {character.voice_type}。"
            )
        assigned[character.voice_type] = character.name or character.id


def _project_data(project: ProjectBrief) -> dict[str, Any]:
    return project.model_dump(mode="json", exclude_none=True)


def _project_response(path: Path, project: ProjectBrief, content: str) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(ROOT)),
        "content": content,
        "data": _project_data(project),
        "project_id": project.project_id,
        "title": project.title,
    }


def _project_file_path(rel_path: str) -> Path:
    if not rel_path:
        raise ValueError("Project path is empty.")
    path = _safe_path(rel_path)
    projects_dir = (ROOT / "data" / "projects").resolve()
    if path.parent != projects_dir:
        raise ValueError("Project YAML must be saved directly under data/projects/.")
    if path.suffix not in {".yaml", ".yml"}:
        raise ValueError("Project file must end with .yaml or .yml.")
    return path


def _latest_outputs() -> dict[str, str]:
    outputs: dict[str, str] = {}
    run_dir = _latest_run_dir()
    if run_dir is None:
        return _with_latest_web_api_log(outputs)

    if run_dir is not None:
        project_id = run_dir.parent.name
        episode_text = run_dir.name.removeprefix("episode_")
        candidates = {
            "final_video": run_dir / "final" / f"{project_id}_episode_{episode_text}_sample.mp4",
            "script": run_dir / "script.md",
            "storyboard": run_dir / "storyboard.html",
            "render_report": run_dir / "reports" / "render_report.json",
            "stage_report": run_dir / "reports" / "stage_report.json",
        }
        for name, path in candidates.items():
            if path.exists():
                outputs[name] = str(path.relative_to(ROOT))
        logs = sorted((run_dir / "logs").glob("render_*.log"), key=lambda item: item.stat().st_mtime, reverse=True) if (run_dir / "logs").exists() else []
        if logs:
            outputs["latest_log"] = str(logs[0].relative_to(ROOT))
    return _with_latest_web_api_log(outputs)


def _with_latest_web_api_log(outputs: dict[str, str]) -> dict[str, str]:
    web_api_dir = ROOT / "outputs" / "web_api"
    web_api_logs: list[Path] = []
    if web_api_dir.exists():
        web_api_logs.extend(web_api_dir.glob("*.log"))
        web_api_logs.extend(web_api_dir.glob("script_workshop_*/generation.log"))
    web_api_logs = sorted(web_api_logs, key=lambda item: item.stat().st_mtime, reverse=True)
    if web_api_logs:
        outputs["latest_web_api_log"] = str(web_api_logs[0].relative_to(ROOT))
    return outputs


def _latest_run_dir() -> Path | None:
    outputs_dir = ROOT / "outputs"
    if not outputs_dir.exists():
        return None
    run_dirs = [path for path in outputs_dir.glob("*/episode_*") if path.is_dir()]
    if not run_dirs:
        return None
    return max(run_dirs, key=_run_dir_mtime)


def _run_dir_mtime(run_dir: Path) -> float:
    project_id = run_dir.parent.name
    episode_text = run_dir.name.removeprefix("episode_")
    candidates = [
        run_dir,
        run_dir / "script.md",
        run_dir / "storyboard.html",
        run_dir / "reports" / "render_report.json",
        run_dir / "reports" / "stage_report.json",
        run_dir / "final" / f"{project_id}_episode_{episode_text}_sample.mp4",
    ]
    if (run_dir / "logs").exists():
        candidates.extend((run_dir / "logs").glob("render_*.log"))
    return max((path.stat().st_mtime for path in candidates if path.exists()), default=0.0)


def _parse_byte_range(range_header: str, file_size: int) -> tuple[int, int] | None:
    if not range_header or not range_header.startswith("bytes=") or file_size <= 0:
        return None
    spec = range_header.removeprefix("bytes=").split(",", 1)[0].strip()
    if "-" not in spec:
        return None
    start_text, end_text = spec.split("-", 1)
    try:
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
    except ValueError:
        return None
    if start < 0 or start >= file_size or end < start:
        return None
    return start, min(end, file_size - 1)


def _safe_path(rel_path: str) -> Path:
    path = (ROOT / rel_path).resolve()
    if path != ROOT and ROOT not in path.parents:
        raise ValueError("Path is outside project root.")
    return path


if __name__ == "__main__":
    serve()
