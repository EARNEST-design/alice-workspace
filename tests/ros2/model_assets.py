"""Qualification cache inspection; production PREPARE verifies independently."""

import json
from pathlib import Path

from alice_nodes.model_assets import verify_model_assets

assets = verify_model_assets()
assets["verification"] = "current mounted cache inspection; not loaded-engine identity"
Path("/artifacts/model-assets.json").write_text(json.dumps(assets, indent=2))
print("verified model, tokenizer and Azelma bytes without network or credentials")
