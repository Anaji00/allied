"""Writes the API's OpenAPI schema to a file, for the frontend's generated types.

The frontend builds its typed API client from this schema, so request and
response shapes cannot drift from the FastAPI models without the type check
noticing. No server needs to be running; the schema comes from the app object.

    python scripts/export_openapi.py frontend/openapi.json

Run from the repository root. `npm run api:types` in frontend/ calls this and
then regenerates frontend/src/lib/api/schema.d.ts.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Importing the app runs pending migrations against DATABASE_URL. Point it at a
# throwaway file so exporting the schema never touches a real database.
os.environ["DATABASE_URL"] = "sqlite:///" + str(Path(tempfile.mkdtemp()) / "openapi.db").replace("\\", "/")

from app.main import app  # noqa: E402


def main() -> None:
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "openapi.json")
    target.write_text(json.dumps(app.openapi(), indent=2), encoding="utf-8")
    print(f"Wrote {target} ({len(app.openapi()['paths'])} paths)")


if __name__ == "__main__":
    main()
