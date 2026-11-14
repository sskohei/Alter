"""Export the unmounted review contract separately from the live OpenAPI."""

import json
from pathlib import Path

from alter_api.contracts.frontend_preview import create_contract_app

path = Path(__file__).resolve().parents[1] / "packages/api-client/preview.openapi.json"
path.write_text(json.dumps(create_contract_app().openapi(), indent=2, sort_keys=True) + "\n")
