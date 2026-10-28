"""ASGI entry point: ``uvicorn app.asgi:app``. Exits with a readable message when
configuration is invalid."""

from app.config import load_settings_or_exit
from app.main import create_app

app = create_app(load_settings_or_exit())
