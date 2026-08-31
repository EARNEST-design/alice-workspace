import importlib
import sys


def test_experiments_package_import_does_not_eagerly_import_passive_capture() -> None:
    sys.modules.pop("alice.experiments", None)
    sys.modules.pop("alice.experiments.passive_capture", None)

    importlib.import_module("alice.experiments")

    assert "alice.experiments.passive_capture" not in sys.modules
