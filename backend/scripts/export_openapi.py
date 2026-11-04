"""Export the FastAPI OpenAPI schema offline (portfolio D07).

Builds the app with local mock settings and writes app.openapi()
as JSON. The database URL is a placeholder: SQLAlchemy creates the
engine lazily and nothing connects, and the mock CRM/extractor
resolvers make no provider calls.

Usage: python scripts/export_openapi.py <output-path>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.bootstrap import create_app
from app.settings import Settings


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: export_openapi.py <output-path>", file=sys.stderr)
        return 2
    settings = Settings(
        database_url="postgresql+psycopg://offline:offline@127.0.0.1:1/offline",
        session_secret="offline-export-secret",
    )
    schema = create_app(settings).openapi()
    Path(sys.argv[1]).write_text(
        json.dumps(schema, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
