"""CLI entrypoint. Commands operate on exactly the same SDK as the API."""
from __future__ import annotations

import json
import os
import logging
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from timepilot.config import Settings, python_check
from timepilot.demo import bootstrap
from timepilot.sdk import TimePilot

app = typer.Typer(add_completion=False, help="TimePilot - industrial time-series modeling framework")
plugins_app = typer.Typer(help="Manage model plugins")
datasets_app = typer.Typer(help="Import datasets")
dev_app = typer.Typer(help="Developer tools")
native_app = typer.Typer(help="Parquet / DuckDB native BI assets")
app.add_typer(plugins_app, name="plugins")
app.add_typer(datasets_app, name="datasets")
app.add_typer(dev_app, name="dev")
app.add_typer(native_app, name="native-bi")
console = Console()


@app.command()
def doctor(strict: bool = typer.Option(False, help="Require exact Python 3.12.12")):
    ok, message = python_check(strict=strict)
    console.print(("[green]PASS[/green] " if ok else "[red]FAIL[/red] ") + message)
    settings = Settings.from_env()
    from timepilot.plugins import PluginRegistry
    console.print(f"Storage: {settings.home}")
    console.print(f"Database: {settings.db_url.split('@')[-1]}")
    table = Table("Plugin", "Available", "Version")
    for item in PluginRegistry().catalog():
        table.add_row(item["plugin_id"], "yes" if item["available"] else "no", item["version"])
    console.print(table)
    if strict and not ok:
        raise typer.Exit(code=2)


@app.command()
def demo(wait: bool = typer.Option(True, help="Execute a synthetic monthly-demand test")):
    client = TimePilot()
    result = bootstrap(client, run=True)
    console.print(f"[bold]SYNTHETIC DEMO[/bold] dataset={result['dataset']['name']} run={result['run_id']}")
    if wait:
        run = client.runs.get(result["run_id"])
        run.wait(timeout=900)
        console.print_json(json.dumps(run.report()))
        console.print(f"Results at: {client.engine.run_dir(run.id)}")


@app.command("run")
def run_task(config: Path, dataset: str, wait: bool = True):
    client = TimePilot()
    task = client.tasks.from_file(config)
    run = client.runs.create(task=task, dataset=dataset)
    console.print(f"Run created: {run.id}")
    if wait:
        run.wait(timeout=None)
        console.print_json(json.dumps(run.report()))


@app.command()
def worker(once: bool = typer.Option(False, help="Handle one queued run and exit")):
    client = TimePilot()
    console.print("Worker started. Use Ctrl+C to stop.")
    try:
        while True:
            claimed = client.engine.work_once()
            if claimed:
                console.print(f"Processed: {claimed}")
            if once:
                return
            if claimed is None:
                time.sleep(client.settings.worker_poll_seconds)
    except KeyboardInterrupt:
        console.print("Worker stopped")


@app.command()
def inspect(run_id: str):
    client = TimePilot()
    from timepilot.sdk import RunHandle
    handle = client.runs.get(run_id)
    console.print_json(json.dumps({"run": handle.status(), "events": handle.events()}, default=str))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, no_worker: bool = False):
    if host not in {"127.0.0.1", "localhost", "::1"} and os.getenv('TIMEPILOT_ENTERPRISE_MODE')!='1':
        console.print('[red]Refusing network binding without enterprise identity and project RBAC.[/red]')
        raise typer.Exit(code=3)
    try:
        import uvicorn
        from timepilot.api import create_app
    except ImportError as exc:
        console.print("Install dependencies into your active Python environment: python -m pip install -r requirements.txt")
        raise typer.Exit(code=2) from exc
    console.print(f"TimePilot local workbench: http://{host}:{port}")
    uvicorn.run(create_app(embedded_worker=not no_worker), host=host, port=port, log_level="info")


@plugins_app.command("list")
def list_plugins():
    client = TimePilot()
    table = Table("Plugin", "Available", "Version", "Description")
    for item in client.plugins.catalog():
        table.add_row(item["plugin_id"], "yes" if item["available"] else "no",
                      item["version"], item["description"])
    console.print(table)


@datasets_app.command("import")
def import_dataset(path: Path, name: str, frequency: str = "MS", unit: str = "unit",
                   timestamp_col: str = "timestamp", target_col: str = "target",
                   entity_col: str = "series_id", available_at_col: str = "available_at",
                   source_timezone: str = "UTC"):
    from timepilot.data import DataValidationError
    try:
        manifest = TimePilot().datasets.from_file(path, name=name, frequency=frequency, unit=unit,
                                                  timestamp_col=timestamp_col, target_col=target_col,
                                                  entity_col=entity_col, available_at_col=available_at_col,
                                                  source_timezone=source_timezone)
    except (DataValidationError, ValueError) as exc:
        console.print(f"[red]Data import failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print(f"Dataset imported: {manifest.dataset_id}, {manifest.record_count} observations")


@native_app.command("import")
def native_bi_import(path: Path, name: str):
    """Import a trusted LOCAL file without uploading its contents through HTTP."""
    if not path.is_file():
        console.print(f"[red]File not found: {path}[/red]")
        raise typer.Exit(code=2)
    from timepilot.native_bi import NativeBIService, NativeBIError
    client=TimePilot()
    try:
        result=NativeBIService(client.store,client.settings).import_from_path(path,name)
    except (NativeBIError, ImportError) as exc:
        console.print(f"[red]Native BI import failed:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    console.print_json(json.dumps(result,ensure_ascii=False))


@native_app.command("list")
def native_bi_list():
    from timepilot.native_bi import NativeBIService
    client=TimePilot()
    console.print_json(json.dumps(NativeBIService(client.store,client.settings).list_assets(),ensure_ascii=False))


@native_app.command("verify")
def native_bi_verify(asset_id: str):
    from timepilot.native_bi import NativeBIService
    client=TimePilot()
    result=NativeBIService(client.store,client.settings).verify(asset_id)
    console.print_json(json.dumps(result))
    if not result['valid']:
        raise typer.Exit(code=2)


@dev_app.command("check")
def dev_check(strict: bool = False):
    ok, message = python_check(strict=strict)
    console.print(message)
    if not ok:
        raise typer.Exit(code=2)
    client = TimePilot()
    console.print(f"{len(client.plugins.catalog())} model plugins registered")
    if Path("tests").is_dir():
        result = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"], check=False)
        if result.returncode:
            raise typer.Exit(code=result.returncode)
    else:
        console.print("No tests/ directory in cwd; skipping project test suite")


@dev_app.command("scaffold-model")
def scaffold_model(name: str, dest: Path = Path("src/timepilot/models")):
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise typer.BadParameter("Model identifier must be lowercase snake_case")
    target = dest / f"{name}.py"
    if target.exists():
        raise typer.BadParameter(f"File exists: {target}")
    dest.mkdir(parents=True, exist_ok=True)
    klass = "".join(part.title() for part in name.split("_"))
    template = (
        '"""Model plugin template. Register after implementation and testing."""\n'
        'from timepilot.plugins import Capabilities, ModelPlugin\n\n\n'
        'class {class_name}(ModelPlugin):\n'
        '    def capabilities(self):\n'
        '        return Capabilities("{name}", "0.1.0", "Describe forecasting assumptions")\n\n'
        '    def fit(self, history, horizon, frequency, output_dir, budget_seconds, *, seasonal_period=None):\n'
        '        raise NotImplementedError("Implement fit and model artifact writing")\n\n'
        '    def predict(self, artifact, horizon, frequency):\n'
        '        raise NotImplementedError("Return float vector of length horizon")\n'
    )
    target.write_text(template.format(class_name=klass, name=name), encoding="utf-8")
    console.print(f"Generated: {target}; register in PluginRegistry and add contract tests")


if __name__ == "__main__":
    app()
