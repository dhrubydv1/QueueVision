"""Write the API's OpenAPI schema to ``dashboard/openapi.json`` (input to ``npm run gen:api``).

Builds the app with a no-op tracker factory so no model is loaded; safe in CI's light
Python environment. Output is sorted and indented so ``git diff`` detects staleness.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

from ai_engine.config import PROJECT_ROOT
from api.main import create_app
from api.settings import ApiSettings

OUT = PROJECT_ROOT / "dashboard" / "openapi.json"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        settings = ApiSettings(data_dir=Path(tmp), camera_dir=PROJECT_ROOT / "configs" / "cameras")
        app = create_app(settings, tracker_factory=lambda: None)
        schema = app.openapi()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
