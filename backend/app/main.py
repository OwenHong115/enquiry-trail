"""API entrypoint for uvicorn: `uvicorn app.main:app`."""

from app.bootstrap import create_app

app = create_app()
