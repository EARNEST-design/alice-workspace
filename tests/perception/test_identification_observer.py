from __future__ import annotations

from alice.experiments.manifest import NegotiatedCameraSettings
from alice.perception.identification_observer import ProductionIdentificationObserver


class FakeCamera:
    camera_id = "usb-046d_HD_Webcam_C525_79C73260-video-index0"

    def __init__(self) -> None:
        self.reads = 0
        self.closed = False
        self.negotiated_settings = NegotiatedCameraSettings.unavailable()

    def read(self) -> object:
        self.reads += 1
        return object()

    def close(self) -> None:
        self.closed = True


class FakeDetector:
    detector_name = "mediapipe-face-landmarker"
    model_sha256 = "a" * 64

    def __init__(self) -> None:
        self.frames: list[object] = []
        self.closed = False

    def observe(self, frame: object, run_id: str) -> object:
        self.frames.append(frame)
        return (run_id, frame)

    def close(self) -> None:
        self.closed = True


def test_production_observer_reads_exactly_one_frame_and_owns_cleanup() -> None:
    camera = FakeCamera()
    detector = FakeDetector()
    observer = ProductionIdentificationObserver._from_owned_components_for_test(
        camera=camera, detector=detector
    )

    assert observer.observe(run_id="run", step_id="step")[0] == "run"
    assert camera.reads == 1
    observer.close()
    assert camera.closed is True
    assert detector.closed is True


def test_production_observer_public_constructor_has_no_injection_hooks() -> None:
    import inspect

    constructor = inspect.signature(ProductionIdentificationObserver).parameters
    assert not {"camera", "detector", "factory"} & constructor.keys()
    parameters = inspect.signature(ProductionIdentificationObserver.open).parameters
    assert not {"camera", "detector", "factory"} & parameters.keys()
    assert "camera_device" in parameters
    assert "model_path" in parameters
