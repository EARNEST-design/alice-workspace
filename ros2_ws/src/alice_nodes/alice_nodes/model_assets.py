"""Approved Pocket model bytes, verified before a ROS worker may load them."""

import hashlib
from pathlib import Path

model = "snapshots/d29db7978e464fb90cb3359ee0c69a273b9142cc/"
voice = "snapshots/e81d79e8194ad4c7ce879c87a4258ef20cbf2487/"
APPROVED_ASSETS = {
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


def verify_model_assets(root=None, expected=None):
    """Check cache paths and bytes; return immutable-by-value evidence for loading.

    Defaults use the same Hugging Face cache constant as the model loader.
    Explicit paths/manifests support controlled asset qualification fixtures.
    """
    if root is None:
        from huggingface_hub.constants import HF_HUB_CACHE

        root = Path(HF_HUB_CACHE) / "models--kyutai--pocket-tts-without-voice-cloning"
    root = Path(root).resolve(strict=True)
    expected = APPROVED_ASSETS if expected is None else expected
    assets = []
    for relative, digest in expected.items():
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("model cache asset escapes repository: " + relative)
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != digest:
            raise ValueError("model cache checksum mismatch: " + relative)
        assets.append(
            {
                "repository_relative_path": relative,
                "sha256": actual,
                "size": path.stat().st_size,
            }
        )
    return {"assets": assets, "verification": "verified before worker model loading"}
