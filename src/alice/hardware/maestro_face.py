"""Separate selected-face streaming interface; the jaw-only guard is unchanged."""

from threading import Event, get_ident

from alice.contracts.actuation import PoseRequest
from alice.hardware.face_scope import FACE_CHANNELS, face_profiles
from alice.hardware.maestro_adapter import (
    MaestroAdapter,
    MaestroConnectionError,
    MaestroPreflightSnapshot,
    MaestroStreamReceipt,
)
from alice.hardware.maestro_protocol import (
    encode_get_errors,
    encode_get_position,
    encode_set_target,
    parse_error_register,
    parse_position,
)


class FaceTransactionError(MaestroConnectionError):
    def __init__(self, detail: str, evidence: dict[str, object]) -> None:
        super().__init__(detail)
        self.evidence = evidence


class MaestroFaceAdapter(MaestroAdapter):
    """One serial owner; close may revoke from the independent watchdog.

    Reuses framing, lifecycle and read-only primitives. No model receives this
    object. The trusted FaceCommandStream enforces sent-command derivatives.
    """

    def open(self, explicit_enable_token: str, *, cancel: Event | None = None) -> None:
        if cancel is not None and cancel.is_set():
            raise MaestroConnectionError("face startup cancelled")
        definitions = self._manifest.actuators
        expected = {
            "mouth_open": (6, 4608, 5059, 5440, 0, 11),
            "lower_eyelids": (3, 2880, 5626, 6400, 0, 0),
            "upper_eyelids": (4, 3840, 6173, 7232, 0, 0),
            "forehead_frown": (5, 4032, 5918, 6592, 0, 0),
            "left_mouth_corner": (9, 5120, 6499, 6912, 50, 10),
            "right_mouth_corner": (11, 5120, 5524, 6912, 50, 10),
        }
        actual = {
            a.name: (
                a.channel,
                a.software_min_qus,
                a.home_qus,
                a.software_max_qus,
                a.firmware_speed,
                a.firmware_acceleration,
            )
            for a in definitions
        }
        if (
            self._manifest.hardware_id != "alice-face-speech-trial-v1"
            or actual != expected
        ):
            raise MaestroConnectionError(
                "selected-face mapping differs from reviewed scope"
            )
        self._stopped = cancel if cancel is not None else Event()
        self._owner = get_ident()
        super().open(explicit_enable_token)

    def _check_owner(self) -> None:
        if self._stopped.is_set() or self._poisoned or self._transport is None:
            raise MaestroConnectionError("face stream is closed or revoked")
        if get_ident() != self._owner:
            raise MaestroConnectionError("face serial access requires its single owner")

    def _write_all(self, payload: bytes) -> None:
        self._check_owner()
        super()._write_all(payload)

    def _read_exact(self, size: int) -> bytes:
        self._check_owner()
        return super()._read_exact(size)

    def close(self) -> None:
        if hasattr(self, "_stopped"):
            self._stopped.set()
        super().close()

    def stream_face_target(
        self, token: str, request: PoseRequest
    ) -> MaestroStreamReceipt:
        evidence: dict[str, object] = {
            "write_started_monotonic_ns": None,
            "write_completed": False,
            "target_qus": None,
            "observed_qus": None,
        }
        try:
            self._check_owner()
            if token != self._enable_token or len(request.targets) != 1:
                raise MaestroConnectionError("invalid face authorization")
            self._manifest.validate_request(request, now_monotonic_ns=self._clock())
            target = request.targets[0]
            config = face_profiles()[target.actuator_name]
            if (
                not config.closed_position
                <= target.normalized_position
                <= config.open_position
            ):
                raise MaestroConnectionError("face target exceeds selected trial range")
            definition = self._manifest.actuator(target.actuator_name)
            qus = definition.target_qus(target.normalized_position)
            sent_ns = self._clock()
            if not 0 <= sent_ns - request.issued_monotonic_ns <= 2_000_000:
                raise MaestroConnectionError("face dispatch deadline missed")
            evidence.update(write_started_monotonic_ns=sent_ns, target_qus=qus)
            self._write_all(encode_set_target(definition.channel, qus))
            evidence["write_completed"] = True
            self._write_all(encode_get_errors())
            errors = parse_error_register(self._read_exact(2))
            if errors:
                raise MaestroConnectionError(f"face controller error={errors}")
            self._write_all(encode_get_position(definition.channel))
            observed = parse_position(self._read_exact(2))
            evidence["observed_qus"] = observed
            if (
                not definition.software_min_qus
                <= observed
                <= definition.software_max_qus
            ):
                raise MaestroConnectionError(
                    f"face controller output outside calibration: {observed}"
                )
            return MaestroStreamReceipt(
                target_qus=qus,
                observed_qus=observed,
                sent_monotonic_ns=sent_ns,
                reported_monotonic_ns=self._clock(),
            )
        except BaseException as exc:
            evidence["error"] = f"{type(exc).__name__}: {exc}"
            self._poison_transport()
            raise FaceTransactionError(str(exc), evidence) from exc

    def initialize_disabled_home(self, token: str) -> MaestroPreflightSnapshot:
        """Disabled channels start at Home; enabled channels must already be Home.

        First PWM enable cannot establish mechanical start speed. This startup
        is retained separately and is allowed only by the attended trial root.
        """
        try:
            if token != self._enable_token:
                raise MaestroConnectionError("invalid face authorization")
            names = tuple(FACE_CHANNELS)
            before = self.read_only_preflight(names)
            if before.controller_error_register or any(
                before.positions_qus[n] not in (0, self._manifest.actuator(n).home_qus)
                for n in names
            ):
                raise MaestroConnectionError(
                    "face startup requires disabled or Home outputs"
                )
            for n in names:
                if before.positions_qus[n] == 0:
                    a = self._manifest.actuator(n)
                    self._write_all(encode_set_target(a.channel, a.home_qus))
                    self._wait_for_target(
                        actuator_name=n, channel=a.channel, target_qus=a.home_qus
                    )
            self._sleeper(0.25)
            after = self.read_only_preflight(names)
            if after.controller_error_register or any(
                after.positions_qus[n] != self._manifest.actuator(n).home_qus
                for n in names
            ):
                raise MaestroConnectionError("face startup Home check failed")
            return after
        except BaseException:
            self._poison_transport()
            raise

    def enable_fast_jaw_response(self, explicit_enable_token: str) -> None:
        try:
            if (
                explicit_enable_token != self._enable_token
                or self.jaw_response_override is not None
            ):
                raise MaestroConnectionError("invalid jaw response authorization")
            before = self.read_only_preflight(tuple(FACE_CHANNELS))
            if before.controller_error_register or any(
                before.positions_qus[n] != self._manifest.actuator(n).home_qus
                for n in FACE_CHANNELS
            ):
                raise MaestroConnectionError(
                    "jaw response requires all selected outputs at Home"
                )
            self.jaw_response_override = {
                "channel": 6,
                "speed": 0,
                "acceleration": 0,
                "restore_speed": 0,
                "restore_acceleration": 11,
                "persistent_settings_changed": False,
                "status": "requested",
            }
            self._set_jaw_response(acceleration=0)
            self.jaw_response_override["status"] = "active"
        except BaseException:
            self._poison_transport()
            raise
