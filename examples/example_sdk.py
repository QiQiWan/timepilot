"""Use with: python examples/example_sdk.py (runs on synthetic data only)."""
from pathlib import Path
from timepilot import TimePilot
from timepilot.demo import demo_csv
from timepilot.data import create_dataset

client = TimePilot()
if client.store.dataset_by_name_or_id("sales_monthly") is None:
    manifest = create_dataset(client.store, client.settings, demo_csv(), "synthetic.csv",
                              name="sales_monthly", frequency="MS", unit="vehicles")
task = client.tasks.from_file(Path(__file__).parent / "task.yaml")
run = client.runs.create(task=task, dataset="sales_monthly")
run.wait(timeout=180)
report = run.report()
print("Run ID:", run.id)
print("Independent test metrics:", report["metrics"])
print("Saved-model forecast:", run.predict("example_A"))
