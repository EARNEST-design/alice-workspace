"""Reviewed six-channel speech scope and conservative command profiles."""

from alice.hardware.manifest import HardwareManifest
from alice.speech.jaw_trial import JawTrialConfig

SOURCE_CALIBRATION = "8ad9ad1f59dc70c47515c9a37b4531c5070c22a40ed66fb673d0e72a0d3dadb4"
FACE_CHANNELS = {
    "mouth_open": 6,
    "lower_eyelids": 3,
    "upper_eyelids": 4,
    "forehead_frown": 5,
    "left_mouth_corner": 9,
    "right_mouth_corner": 11,
}


def face_profiles() -> dict[str, JawTrialConfig]:
    """Reuse the qualified trajectory algorithm with distinct channel caps.

    These are host command limits, not measured mechanical velocity. Only the
    jaw uses the accepted rapid/full-range profile. Others retain firmware
    settings. Lids allow calibrated full closure; corners allow +/- .8.
    """
    profiles = {}
    for name in FACE_CHANNELS:
        extent = 0.8 if "mouth_corner" in name else (0.5 if "eyelids" in name else 0.3)
        profiles[name] = JawTrialConfig(
            closed_position=-extent,
            open_position=extent,
            max_step=0.08,
            max_rate_per_s=0.8,
            max_acceleration_per_s2=4,
            response_time_s=0.18,
        )
        if "eyelids" in name:
            profiles[name] = JawTrialConfig(
                closed_position=-1,
                open_position=1,
                max_step=0.1,
                max_rate_per_s=1.5,
                max_acceleration_per_s2=8,
                response_time_s=0.1,
            )
    profiles["mouth_open"] = JawTrialConfig(
        closed_position=-1,
        open_position=1,
        max_step=0.4,
        max_rate_per_s=10,
        max_acceleration_per_s2=200,
        response_time_s=0.03,
        mouth_lead_s=0.1,
        phase_timeout_s=8,
    )
    return profiles


def face_manifest(full: HardwareManifest) -> HardwareManifest:
    if full.calibration_sha256 != SOURCE_CALIBRATION:
        raise ValueError("face scope requires the reviewed source calibration")
    document = full.model_dump(mode="json")
    document["hardware_id"] = "alice-face-speech-trial-v1"
    document["actuators"] = [
        full.actuator(name).model_dump(mode="json") for name in FACE_CHANNELS
    ]
    document["preflight_requirements"] = [
        r.model_dump(mode="json")
        for r in full.preflight_requirements
        if r.requirement_id == "maestro-command-interface-role-verified"
    ] + [
        {
            "requirement_id": "attended-session-authorized",
            "description": "Explicit face run under the standing attended bench setup.",
            "satisfied": False,
            "evidence": [
                {
                    "source": "hardware/bringup/face-speech-trial-v1.md",
                    "detail": "Operator at master switch; supply margin unmeasured.",
                }
            ],
        }
    ]
    return HardwareManifest.model_validate(document)
