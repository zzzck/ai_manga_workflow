from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sqlite3
import zipfile

import typer
from dotenv import dotenv_values, load_dotenv
from rich.console import Console
from rich.table import Table

from .config import dump_yaml, load_config, load_project
from .pipeline import run_pipeline
from .render import render_sample, run_render_stages


app = typer.Typer(no_args_is_help=True, help="AI 漫剧自动化工作流 CLI。")
console = Console()


def _zip_path(zip_file: zipfile.ZipFile, source: Path, arcname: Path, exclude: set[Path] | None = None) -> int:
    exclude = {item.resolve() for item in (exclude or set())}
    if not source.exists():
        return 0
    if source.is_file():
        if source.resolve() in exclude or source.name in {".DS_Store"}:
            return 0
        zip_file.write(source, arcname.as_posix())
        return 1
    count = 0
    for item in sorted(source.rglob("*")):
        if (
            not item.is_file()
            or item.resolve() in exclude
            or item.name in {".DS_Store"}
            or "__pycache__" in item.parts
        ):
            continue
        zip_file.write(item, (arcname / item.relative_to(source)).as_posix())
        count += 1
    return count


def _backup_sqlite_database(source: Path, target: Path) -> bool:
    if not source.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    source_conn = sqlite3.connect(str(source))
    try:
        target_conn = sqlite3.connect(str(target))
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()
    return True


def _env_lookup(name: str, env_values: dict[str, str | None]) -> str:
    return str(env_values.get(name) or "") or os.getenv(name, "")


def _provider_required_envs(provider: object) -> list[str]:
    names: list[str] = []
    api_key_env = getattr(provider, "api_key_env", "")
    if api_key_env:
        names.append(str(api_key_env))
    extra = getattr(provider, "extra", {}) or {}
    provider_name = getattr(provider, "provider", "")
    if provider_name == "tencentcloud":
        for key in ["secret_id_env", "secret_key_env"]:
            value = extra.get(key)
            if isinstance(value, str) and value:
                names.append(value)
        if extra.get("include_app_id", False):
            value = extra.get("app_id_env")
            if isinstance(value, str) and value:
                names.append(value)
    return sorted(dict.fromkeys(names))


def _writable_path_state(path: Path, *, directory: bool) -> tuple[bool, str]:
    target = path if directory else path.parent
    if target.exists() and not target.is_dir():
        return False, f"{target} 不是目录"
    if not target.exists():
        parent = target.parent
        if not parent.exists():
            return False, f"父目录不存在：{parent}"
        target = parent
    test_file = target / ".ai_manga_write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        return True, str(target)
    except Exception as exc:
        return False, str(exc)


def _add_deploy_check(rows: list[tuple[str, str, str]], status: str, item: str, detail: str) -> None:
    rows.append((status, item, detail))


def _is_allowed_restore_member(name: str, include_env: bool) -> bool:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        return False
    if name == ".env":
        return include_env
    allowed_prefixes = [
        "data/server/ai_manga.sqlite3",
        "data/users/",
        "data/projects/",
        "outputs/",
    ]
    return any(name == prefix.rstrip("/") or name.startswith(prefix) for prefix in allowed_prefixes)


def _restore_target_path(root: Path, member_name: str) -> Path:
    target = (root / member_name).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Invalid backup member path: {member_name}")
    return target


@app.command("check")
def check_config(
    config: Path = typer.Option(Path("config/pipeline.example.yaml"), "--config", "-c", help="工作流配置文件。"),
    project: Path = typer.Option(Path("data/projects/demo_story.yaml"), "--project", "-p", help="项目设定文件。"),
) -> None:
    """Validate config and project files."""
    pipeline_config = load_config(config)
    brief = load_project(project)

    table = Table(title="Workflow Check")
    table.add_column("Item")
    table.add_column("Value")
    table.add_row("Project", f"{brief.title} ({brief.project_id})")
    table.add_row("Format", f"{brief.format} / {brief.aspect_ratio}")
    table.add_row("Characters", str(len(brief.characters)))
    table.add_row("Locations", str(len(brief.locations)))
    table.add_row("Beats", str(len(brief.beats)))
    table.add_row("Output Dir", str(pipeline_config.project.get("output_dir", "outputs")))
    console.print(table)

    provider_table = Table(title="Provider Slots")
    provider_table.add_column("Slot")
    provider_table.add_column("Enabled")
    provider_table.add_column("Provider")
    provider_table.add_column("Model")
    for name, provider in pipeline_config.providers.items():
        provider_table.add_row(name, str(provider.enabled), provider.provider or "(empty)", provider.model or "(empty)")
    console.print(provider_table)


@app.command("provider-status")
def provider_status(
    config: Path = typer.Option(Path("config/pipeline.siliconflow.yaml"), "--config", "-c", help="工作流配置文件。"),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="环境变量文件。"),
) -> None:
    """Show configured provider slots without making remote API calls."""
    if env_file.exists():
        load_dotenv(env_file)
    pipeline_config = load_config(config)

    table = Table(title="Provider Status")
    table.add_column("Slot")
    table.add_column("Enabled")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Endpoint")
    table.add_column("API Key")

    for name, provider in pipeline_config.providers.items():
        if not provider.enabled:
            key_state = "disabled"
        elif not provider.api_key_env:
            key_state = "-"
        else:
            key_state = "set" if os.getenv(provider.api_key_env, "") else f"missing: {provider.api_key_env}"
        model_env = provider.extra.get("model_env")
        model = os.getenv(model_env, "") if isinstance(model_env, str) and os.getenv(model_env, "") else provider.model
        table.add_row(
            name,
            str(provider.enabled),
            provider.provider or "-",
            model or "-",
            provider.endpoint or "-",
            key_state,
        )
    console.print(table)


@app.command("deploy-check")
def deploy_check_command(
    config: Path = typer.Option(Path("config/pipeline.siliconflow.yaml"), "--config", "-c", help="部署使用的流程配置文件。"),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="部署使用的环境变量文件。"),
    strict: bool = typer.Option(True, "--strict/--no-strict", help="存在 FAIL 项时是否返回非 0 退出码。"),
) -> None:
    """Check local deploy prerequisites without calling remote model APIs."""
    env_values = dotenv_values(env_file) if env_file.exists() else {}
    if env_file.exists():
        load_dotenv(env_file, override=True)

    rows: list[tuple[str, str, str]] = []
    _add_deploy_check(rows, "OK" if env_file.exists() else "FAIL", ".env", str(env_file) if env_file.exists() else f"缺少 {env_file}")

    secret_key = _env_lookup("AI_MANGA_SECRET_KEY", env_values)
    if not secret_key:
        _add_deploy_check(rows, "FAIL", "AI_MANGA_SECRET_KEY", "未设置")
    elif secret_key in {"local-dev-change-me", "change-this-long-random-string"}:
        _add_deploy_check(rows, "FAIL", "AI_MANGA_SECRET_KEY", "仍是示例值，部署前必须更换")
    elif len(secret_key) < 24:
        _add_deploy_check(rows, "WARN", "AI_MANGA_SECRET_KEY", "长度偏短，建议使用更长随机字符串")
    else:
        _add_deploy_check(rows, "OK", "AI_MANGA_SECRET_KEY", "已设置")

    admin_password = _env_lookup("AI_MANGA_ADMIN_PASSWORD", env_values)
    if not admin_password:
        _add_deploy_check(rows, "FAIL", "AI_MANGA_ADMIN_PASSWORD", "未设置")
    elif admin_password in {"admin123456", "change-this-password"}:
        _add_deploy_check(rows, "FAIL", "AI_MANGA_ADMIN_PASSWORD", "仍是默认或示例密码")
    elif len(admin_password) < 10:
        _add_deploy_check(rows, "WARN", "AI_MANGA_ADMIN_PASSWORD", "长度偏短，建议至少 10 位")
    else:
        _add_deploy_check(rows, "OK", "AI_MANGA_ADMIN_PASSWORD", "已设置")

    db_path = Path(_env_lookup("AI_MANGA_DB_PATH", env_values) or "data/server/ai_manga.sqlite3").expanduser().resolve()
    writable, detail = _writable_path_state(db_path, directory=False)
    _add_deploy_check(rows, "OK" if writable else "FAIL", "SQLite 路径", f"{db_path}；{detail}")
    for label, path in [
        ("用户数据目录", Path("data/users")),
        ("全局项目目录", Path("data/projects")),
        ("输出目录", Path("outputs")),
    ]:
        writable, detail = _writable_path_state(path, directory=True)
        _add_deploy_check(rows, "OK" if writable else "FAIL", label, detail)

    for name in ["AI_MANGA_MAX_ACTIVE_JOBS_PER_USER", "AI_MANGA_MAX_ACTIVE_JOBS_TOTAL"]:
        raw = _env_lookup(name, env_values) or "0"
        try:
            value = int(raw)
            _add_deploy_check(rows, "OK" if value >= 0 else "FAIL", name, f"{value}；0 表示不限制")
        except ValueError:
            _add_deploy_check(rows, "FAIL", name, f"不是整数：{raw}")

    if not config.exists():
        _add_deploy_check(rows, "FAIL", "流程配置", f"不存在：{config}")
    else:
        try:
            pipeline_config = load_config(config)
            enabled_slots = [name for name, provider in pipeline_config.providers.items() if provider.enabled]
            missing_envs: list[str] = []
            for provider in pipeline_config.providers.values():
                if not provider.enabled:
                    continue
                for env_name in _provider_required_envs(provider):
                    if not _env_lookup(env_name, env_values):
                        missing_envs.append(env_name)
            if missing_envs:
                _add_deploy_check(rows, "FAIL", "模型环境变量", "缺少：" + ", ".join(sorted(dict.fromkeys(missing_envs))))
            elif enabled_slots:
                _add_deploy_check(rows, "OK", "模型环境变量", "已配置启用槽位：" + ", ".join(enabled_slots))
            else:
                _add_deploy_check(rows, "WARN", "模型环境变量", "没有启用的 provider 槽位")
        except Exception as exc:
            _add_deploy_check(rows, "FAIL", "流程配置", str(exc))

    table = Table(title="Deploy Check")
    table.add_column("Status")
    table.add_column("Item")
    table.add_column("Detail")
    for status, item, detail in rows:
        style = "green" if status == "OK" else ("yellow" if status == "WARN" else "red")
        table.add_row(f"[{style}]{status}[/{style}]", item, detail)
    console.print(table)

    fail_count = sum(1 for status, _, _ in rows if status == "FAIL")
    warn_count = sum(1 for status, _, _ in rows if status == "WARN")
    console.print(f"Summary: {fail_count} FAIL, {warn_count} WARN")
    if strict and fail_count:
        raise typer.Exit(1)


@app.command("run")
def run_command(
    config: Path = typer.Option(Path("config/pipeline.example.yaml"), "--config", "-c", help="工作流配置文件。"),
    project: Path = typer.Option(Path("data/projects/demo_story.yaml"), "--project", "-p", help="项目设定文件。"),
    episode: int | None = typer.Option(None, "--episode", "-e", help="分集编号。默认读取配置。"),
) -> None:
    """Run the structure workflow and create production artifacts."""
    result = run_pipeline(project_path=project, config_path=config, episode=episode)
    console.print(f"[bold green]Workflow complete[/bold green]: {result.run_dir}")
    console.print(f"Shots: {result.shot_count}, duration: {result.total_duration_sec}s")
    if result.warnings:
        console.print("[yellow]Warnings:[/yellow]")
        for warning in result.warnings:
            console.print(f"- {warning}")


@app.command("render-sample")
def render_sample_command(
    config: Path = typer.Option(Path("config/pipeline.siliconflow.yaml"), "--config", "-c", help="工作流配置文件。"),
    project: Path = typer.Option(Path("data/projects/ancient_short.yaml"), "--project", "-p", help="项目设定文件。"),
    episode: int | None = typer.Option(None, "--episode", "-e", help="分集编号。默认读取配置。"),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="环境变量文件。"),
    key_shots: str = typer.Option("auto", "--key-shots", help="图生视频镜头。auto=读取 production_mode=image_to_video；空字符串=关闭视频接口；也可传逗号分隔镜头 ID。"),
    video_timeout_sec: int = typer.Option(900, "--video-timeout-sec", help="单个图生视频任务等待秒数。"),
) -> None:
    """Generate a watchable sample video with SiliconFlow assets and FFmpeg."""
    if key_shots.strip().lower() == "auto":
        selected = None
    else:
        selected = [item.strip() for item in key_shots.split(",") if item.strip()]
    report = render_sample(
        project_path=project,
        config_path=config,
        episode=episode,
        key_shots=selected,
        env_path=env_file,
        video_timeout_sec=video_timeout_sec,
    )
    console.print(f"[bold green]Sample rendered[/bold green]: {report['final_video']}")
    console.print(f"Duration: {report['duration_sec']}s")
    console.print(f"Log: {report['log']}")
    console.print(f"Event log: {report['event_log']}")
    if report.get("warnings"):
        console.print("[yellow]Warnings:[/yellow]")
        for warning in report["warnings"]:
            console.print(f"- {warning}")


@app.command("stage")
def stage_command(
    stages: str = typer.Option("all", "--stages", help="逗号分隔流程：structure,images,voice,videos,compose,all。"),
    config: Path = typer.Option(Path("config/pipeline.siliconflow.yaml"), "--config", "-c", help="工作流配置文件。"),
    project: Path = typer.Option(Path("data/projects/ancient_short.yaml"), "--project", "-p", help="项目设定文件。"),
    episode: int | None = typer.Option(None, "--episode", "-e", help="分集编号。默认读取配置。"),
    env_file: Path = typer.Option(Path(".env"), "--env-file", help="环境变量文件。"),
    key_shots: str = typer.Option("auto", "--key-shots", help="图生视频镜头。auto=读取 image_to_video；空字符串=关闭视频接口；也可传逗号分隔镜头 ID。"),
    video_timeout_sec: int = typer.Option(900, "--video-timeout-sec", help="单个图生视频任务等待秒数。"),
) -> None:
    """Run selected workflow stages."""
    if key_shots.strip().lower() == "auto":
        selected = None
    else:
        selected = [item.strip() for item in key_shots.split(",") if item.strip()]
    report = run_render_stages(
        project_path=project,
        config_path=config,
        stages=[item.strip() for item in stages.split(",") if item.strip()],
        episode=episode,
        key_shots=selected,
        env_path=env_file,
        video_timeout_sec=video_timeout_sec,
    )
    console.print(f"[bold green]Stages complete[/bold green]: {', '.join(report['stages'])}")
    console.print(f"Run dir: {report['run_dir']}")
    console.print(f"Report: {report['report']}")
    console.print(f"Log: {report['log']}")
    console.print(f"Event log: {report['event_log']}")
    if report.get("final_video"):
        console.print(f"Final video: {report['final_video']}")
    if report.get("warnings"):
        console.print("[yellow]Warnings:[/yellow]")
        for warning in report["warnings"]:
            console.print(f"- {warning}")


@app.command("web")
def web_command(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址。"),
    port: int = typer.Option(8765, "--port", help="监听端口。"),
) -> None:
    """Start the local web console."""
    from .web import serve

    serve(host=host, port=port)


@app.command("serve")
def serve_command(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址。"),
    port: int = typer.Option(8000, "--port", help="监听端口。"),
) -> None:
    """Start the deployable FastAPI server with login, admin, quota and protected console."""
    import uvicorn

    uvicorn.run("manga_flow.server.app:app", host=host, port=port, reload=False)


@app.command("backup-server")
def backup_server_command(
    output: Path = typer.Option(Path("backups"), "--output", "-o", help="备份 zip 文件路径，或备份目录。"),
    include_outputs: bool = typer.Option(True, "--include-outputs/--no-outputs", help="是否打包 outputs 产物目录。"),
    include_env: bool = typer.Option(False, "--include-env", help="是否把 .env 放进备份包。默认不包含，避免泄露密钥。"),
) -> None:
    """Create a deployable-server data backup zip without exposing secrets by default."""
    from .server import db

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = output
    if backup_path.suffix.lower() != ".zip":
        backup_path.mkdir(parents=True, exist_ok=True)
        backup_path = backup_path / f"ai_manga_backup_{timestamp}.zip"
    else:
        backup_path.parent.mkdir(parents=True, exist_ok=True)

    db_path = db.database_path()
    tmp_db_snapshot = backup_path.parent / f".{backup_path.stem}_sqlite_snapshot.tmp"
    excluded_paths = {backup_path, tmp_db_snapshot}
    file_count = 0
    try:
        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_file:
            if _backup_sqlite_database(db_path, tmp_db_snapshot):
                file_count += _zip_path(zip_file, tmp_db_snapshot, Path("data/server/ai_manga.sqlite3"))
            for source_text, archive_text in [
                ("data/users", "data/users"),
                ("data/projects", "data/projects"),
            ]:
                file_count += _zip_path(zip_file, Path(source_text), Path(archive_text), exclude=excluded_paths)
            if include_outputs:
                file_count += _zip_path(zip_file, Path("outputs"), Path("outputs"), exclude=excluded_paths)
            if include_env:
                file_count += _zip_path(zip_file, Path(".env"), Path(".env"), exclude=excluded_paths)
    finally:
        tmp_db_snapshot.unlink(missing_ok=True)

    console.print(f"[bold green]Backup created[/bold green]: {backup_path}")
    console.print(f"Files archived: {file_count}")
    console.print(f"SQLite source: {db_path if db_path.exists() else 'not found'}")
    if not include_env:
        console.print("[yellow]Note:[/yellow] .env was not included. Keep model keys and admin secrets backed up separately.")


@app.command("restore-server-backup")
def restore_server_backup_command(
    backup: Path = typer.Argument(..., help="由 backup-server 生成的 zip 备份文件。"),
    apply: bool = typer.Option(False, "--apply", help="真正写入文件。默认只预览，不修改本地数据。"),
    force: bool = typer.Option(False, "--force", help="允许覆盖已存在文件。必须与 --apply 一起使用。"),
    include_env: bool = typer.Option(False, "--include-env", help="允许恢复 .env。默认跳过，避免覆盖当前密钥配置。"),
) -> None:
    """Restore a deployable-server backup zip with explicit apply/force safeguards."""
    if not backup.exists():
        raise typer.BadParameter(f"Backup file does not exist: {backup}")
    if backup.suffix.lower() != ".zip":
        raise typer.BadParameter("Backup file must be a .zip file.")

    root = Path.cwd().resolve()
    restored = 0
    skipped = 0
    conflicts: list[str] = []
    invalid: list[str] = []
    candidates: list[tuple[zipfile.ZipInfo, Path]] = []
    with zipfile.ZipFile(backup) as zip_file:
        for info in zip_file.infolist():
            name = info.filename
            if info.is_dir():
                continue
            if not _is_allowed_restore_member(name, include_env):
                skipped += 1
                continue
            try:
                target = _restore_target_path(root, name)
            except ValueError:
                invalid.append(name)
                continue
            candidates.append((info, target))
            if target.exists() and not force:
                conflicts.append(name)

        table = Table(title="Restore Preview" if not apply else "Restore Plan")
        table.add_column("Member")
        table.add_column("Target")
        table.add_column("Status")
        for info, target in candidates:
            if target.exists() and not force:
                status_text = "conflict"
            elif target.exists() and force:
                status_text = "overwrite"
            else:
                status_text = "create"
            table.add_row(info.filename, str(target), status_text)
        console.print(table)
        if skipped:
            console.print(f"Skipped members: {skipped}")
        if invalid:
            console.print("[red]Invalid members:[/red] " + ", ".join(invalid))
        if conflicts:
            console.print("[red]Conflicts:[/red] " + ", ".join(conflicts))
            console.print("Use --apply --force to overwrite existing files after confirming the backup is correct.")
            raise typer.Exit(1)
        if not apply:
            console.print("Dry run only. Re-run with --apply to restore files.")
            return

        for info, target in candidates:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zip_file.open(info) as source, target.open("wb") as destination:
                destination.write(source.read())
            restored += 1
    console.print(f"[bold green]Restore complete[/bold green]: {restored} files restored from {backup}")
    if not include_env:
        console.print("[yellow]Note:[/yellow] .env was not restored. Use --include-env only when you intend to overwrite local secrets.")


@app.command("init-project")
def init_project(
    project_id: str = typer.Argument(..., help="项目 ID，例如 urban_rebirth_001。"),
    title: str = typer.Argument(..., help="项目标题。"),
    output: Path = typer.Option(Path("data/projects"), "--output", "-o", help="项目 YAML 输出目录。"),
) -> None:
    """Create a new project brief template."""
    payload = {
        "project_id": project_id,
        "title": title,
        "genre": "",
        "format": "vertical_dynamic_comic",
        "aspect_ratio": "9:16",
        "target_duration_sec": 75,
        "audience": "",
        "logline": "在这里写一句话故事。",
        "visual_style": "在这里写固定画风。",
        "tone": "节奏、爽点和情绪要求。",
        "characters": [
            {
                "id": "protagonist",
                "name": "主角名",
                "role": "主角",
                "appearance": "年龄、发型、服装、气质。",
                "personality": "性格关键词。",
                "gender": "",
                "voice_style": "",
                "voice_type": None,
                "visual_lock": ["固定发型", "固定服装", "固定标志物"],
            }
        ],
        "locations": [
            {
                "id": "main_location",
                "name": "主场景名",
                "description": "场景描述。",
                "visual_lock": ["固定元素 1", "固定元素 2"],
            }
        ],
        "beats": [
            {"id": "hook", "summary": "前三秒钩子。", "emotion": "震惊", "location_id": "main_location"},
            {"id": "conflict", "summary": "冲突升级。", "emotion": "紧张", "location_id": "main_location"},
            {"id": "cliffhanger", "summary": "结尾留钩子。", "emotion": "悬念", "location_id": "main_location"},
        ],
    }
    path = output / f"{project_id}.yaml"
    dump_yaml(path, payload)
    console.print(f"[green]Created[/green] {path}")


if __name__ == "__main__":
    app()
