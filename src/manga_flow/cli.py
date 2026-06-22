from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sqlite3
import zipfile

import typer
from dotenv import load_dotenv
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
