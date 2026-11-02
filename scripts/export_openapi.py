import json
from pathlib import Path

from alter_api.main import create_app

path = Path(__file__).resolve().parents[1] / "packages/api-client/openapi.json"
path.write_text(json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n")
