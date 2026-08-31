import builtins
import importlib
import io
import sys

from alice.perception.camera import CameraInfo
from alice.perception.cli import main


def test_list_command_reports_injected_camera_capabilities() -> None:
    output = io.StringIO()

    exit_code = main(
        ["list"],
        stdout=output,
        enumerate_cameras=lambda: [
            CameraInfo(
                camera_id="alice-face-webcam",
                device="/dev/video0",
                label="USB Camera",
                capabilities=("640x480@30", "1280x720@30"),
            )
        ],
    )

    assert exit_code == 0
    assert "alice-face-webcam" in output.getvalue()
    assert "/dev/video0" in output.getvalue()
    assert "1280x720@30" in output.getvalue()


def test_cli_module_import_does_not_import_serial_or_actuator_modules(
    monkeypatch,
) -> None:
    forbidden_imports: list[str] = []
    real_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "serial" or name.startswith("alice.act"):
            forbidden_imports.append(name)
            raise AssertionError(f"unexpected import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("alice.perception.cli", None)
    sys.modules.pop("alice.perception.camera", None)

    importlib.import_module("alice.perception.cli")

    assert forbidden_imports == []
