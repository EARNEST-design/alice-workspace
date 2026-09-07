"""Byte-exact tests for the disconnected Maestro compact protocol."""

import pytest

from alice.hardware.maestro_protocol import (
    encode_get_errors,
    encode_get_position,
    encode_set_target,
    parse_error_register,
    parse_position,
)


def test_set_target_uses_compact_protocol_quarter_microseconds() -> None:
    assert encode_set_target(6, 5059) == bytes([0x84, 6, 67, 39])


def test_queries_and_little_endian_responses_are_exact() -> None:
    assert encode_get_position(6) == bytes([0x90, 6])
    assert encode_get_errors() == bytes([0xA1])
    assert parse_position(bytes([0xC3, 0x13])) == 5059
    assert parse_error_register(bytes([0x05, 0x01])) == 0x0105


@pytest.mark.parametrize("channel", [-1, 24, 128])
def test_protocol_rejects_invalid_channel(channel: int) -> None:
    with pytest.raises(ValueError, match="channel"):
        encode_set_target(channel, 5059)


@pytest.mark.parametrize("target", [-1, 16_384])
def test_protocol_rejects_unencodable_target(target: int) -> None:
    with pytest.raises(ValueError, match="target"):
        encode_set_target(6, target)


@pytest.mark.parametrize("payload", [b"", b"\x01", b"\x01\x02\x03"])
def test_response_parser_rejects_wrong_length(payload: bytes) -> None:
    with pytest.raises(ValueError, match="exactly two bytes"):
        parse_position(payload)
