"""Export the API contract without starting services or calling providers."""
from pathlib import Path
import json
import sys
import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from src.api.main import app

if __name__ == "__main__":
    destination = ROOT / "frontend" / "types" / "openapi.json"
    destination.write_text(json.dumps(app.openapi(), indent=2) + "\n")
    print(destination)
