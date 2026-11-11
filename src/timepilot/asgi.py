"""ASGI entrypoint for Docker and direct uvicorn deployment."""
from timepilot.api import create_app

app = create_app(embedded_worker=True)
