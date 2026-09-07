"""Pure Pololu Maestro compact-protocol encoding and parsing."""

from __future__ import annotations

SET_TARGET = 0x84
GET_POSITION = 0x90
GET_ERRORS = 0xA1


def _channel(value: int) -> int:
    if isinstance(value, bool) or not 0 <= value <= 23:
        raise ValueError("channel must be an integer in [0, 23]")
    return value


def encode_set_target(channel: int, target_qus: int) -> bytes:
    """Encode Set Target; target units are quarter microseconds."""

    checked_channel = _channel(channel)
    if isinstance(target_qus, bool) or not 0 <= target_qus <= 0x3FFF:
        raise ValueError("target must be an integer in [0, 16383]")
    return bytes(
        [SET_TARGET, checked_channel, target_qus & 0x7F, (target_qus >> 7) & 0x7F]
    )


def encode_get_position(channel: int) -> bytes:
    return bytes([GET_POSITION, _channel(channel)])


def encode_get_errors() -> bytes:
    return bytes([GET_ERRORS])


def _parse_u16(payload: bytes) -> int:
    if len(payload) != 2:
        raise ValueError("response must contain exactly two bytes")
    return int.from_bytes(payload, byteorder="little", signed=False)


def parse_position(payload: bytes) -> int:
    return _parse_u16(payload)


def parse_error_register(payload: bytes) -> int:
    return _parse_u16(payload)
