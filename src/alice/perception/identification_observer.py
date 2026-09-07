"""Trusted production composition for Phase 2 robot-face observations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, cast

from alice.contracts.blendshapes import BlendshapeObservation
from alice.experiments.manifest import IdentificationObserverProvenance
from alice.perception.camera import OpenCVCamera
from alice.perception.mediapipe_adapter import MediaPipeBlendshapeAdapter
from alice.perception.model_manifest import validate_model_artifact

C525_CAMERA_ID: Final = "usb-046d_HD_Webcam_C525_79C73260-video-index0"
C525_DEVICE: Final = f"/dev/v4l/by-id/{C525_CAMERA_ID}"


class ProductionIdentificationObserver:
    """Own the exact reviewed camera and pinned MediaPipe implementation.

    Public construction intentionally exposes configuration values, never live
    camera/detector objects or factories. Tests may replace only the private
    component boundary without making it part of the trusted API.
    """

    _camera: Any
    _detector: Any
    _provenance: IdentificationObserverProvenance
    _closed: bool

    def __init__(self) -> None:
        raise TypeError("use ProductionIdentificationObserver.open()")

    @classmethod
    def _from_owned_components(
        cls, *, camera: Any, detector: Any, model_sha256: str
    ) -> ProductionIdentificationObserver:
        self = object.__new__(cls)
        self._camera = camera
        self._detector = detector
        self._provenance = IdentificationObserverProvenance(
            camera_id=C525_CAMERA_ID,
            detector="mediapipe-face-landmarker",
            detector_model_sha256=model_sha256,
            camera_settings=camera.negotiated_settings,
        )
        self._closed = False
        return self

    @classmethod
    def open(
        cls,
        *,
        camera_device: str,
        model_path: Path,
        expected_model_sha256: str,
        width: int,
        height: int,
        fps: float,
    ) -> ProductionIdentificationObserver:
        if camera_device != C525_DEVICE:
            raise ValueError("exact reviewed C525 stable selector is required")
        actual_hash = validate_model_artifact(model_path)
        if actual_hash != expected_model_sha256:
            raise ValueError("MediaPipe model checksum differs from reviewed config")
        camera = OpenCVCamera(
            camera_id=C525_CAMERA_ID,
            device=camera_device,
            width=width,
            height=height,
            fps=fps,
        )
        camera.open()
        try:
            detector = MediaPipeBlendshapeAdapter(
                camera_id=C525_CAMERA_ID,
                model_path=model_path,
            )
        except BaseException:
            camera.close()
            raise
        return cls._from_owned_components(
            camera=camera, detector=detector, model_sha256=actual_hash
        )

    @classmethod
    def _from_owned_components_for_test(
        cls, *, camera: Any, detector: Any
    ) -> ProductionIdentificationObserver:
        return cls._from_owned_components(
            camera=camera,
            detector=detector,
            model_sha256=detector.model_sha256,
        )

    @property
    def provenance(self) -> IdentificationObserverProvenance:
        return self._provenance

    def observe(self, *, run_id: str, step_id: str) -> BlendshapeObservation:
        del step_id
        if self._closed:
            raise RuntimeError("identification observer is closed")
        frame = self._camera.read()
        return cast(BlendshapeObservation, self._detector.observe(frame, run_id))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        detector_error: BaseException | None = None
        try:
            self._detector.close()
        except BaseException as error:
            detector_error = error
        try:
            self._camera.close()
        except BaseException:
            if detector_error is None:
                raise
        if detector_error is not None:
            raise detector_error
