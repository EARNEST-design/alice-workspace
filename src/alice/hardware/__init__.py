"""Hardware descriptions for Alice.

Adapters intentionally use explicit submodule imports so importing the safety
supervisor cannot cycle through its hardware dependencies.
"""

from alice.hardware.manifest import HardwareManifest, load_manifest

__all__ = ["HardwareManifest", "load_manifest"]
