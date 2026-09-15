"""Verify exact public offline assets inside the running TTS container."""

import hashlib
import json
from pathlib import Path

root = Path("/models/hub/models--kyutai--pocket-tts-without-voice-cloning")
model = "snapshots/d29db7978e464fb90cb3359ee0c69a273b9142cc/"
voice = "snapshots/e81d79e8194ad4c7ce879c87a4258ef20cbf2487/"
expected = {
    model + "languages/english_2026-01/model.safetensors": (
        "58aa704a88faad35f22c34ea1cb55c4c5629de8b8e035c6e4936e2673dc07617"
    ),
    model + "tokenizer.model": (
        "d461765ae179566678c93091c5fa6f2984c31bbe990bf1aa62d92c64d91bc3f6"
    ),
    voice + "languages/english_2026-01/embeddings/azelma.safetensors": (
        "c80991c79e18fe6eabb4dc053fe42a668b3f6ab5365c63a1437465af1de7f3a8"
    ),
}
assets = []
for relative, digest in expected.items():
    path = root / relative
    assert path.resolve().is_relative_to(root.resolve()), "cache symlink escape"
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    assert actual == digest, relative
    assets.append(
        {
            "repository_relative_path": relative,
            "sha256": actual,
            "size": path.stat().st_size,
        }
    )
Path("/artifacts/model-assets.json").write_text(
    json.dumps(
        {
            "assets": assets,
            "verification": "exact mounted bytes before offline graph inference",
        },
        indent=2,
    )
)
print("verified model, tokenizer and Azelma bytes without network or credentials")
