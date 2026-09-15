"""Strict proof of an unshifted host monotonic domain across private namespaces."""

import importlib

import pytest

BOOT = "550e8400-e29b-41d4-a716-446655440000"
ZERO = "monotonic           0         0\nboottime            0         0\n"


def proof(**changes):
    module = importlib.import_module("alice_nodes.clock")
    args = {
        "epoch": "epoch-one",
        "boot_id": BOOT,
        "namespace": "time:[4026531834]",
        "children_namespace": "time:[4026531834]",
        "offsets": ZERO,
    }
    return module.proof_from_metadata(**(args | changes))


def test_private_namespace_targets_share_only_the_zero_offset_host_proof():
    assert proof().startswith("host-monotonic-zero/v2:")
    assert (
        proof(namespace="time:[4026532555]", children_namespace="time:[4026532555]")
        == proof()
    )
    assert proof(epoch="epoch-two") != proof()
    assert proof(boot_id="650e8400-e29b-41d4-a716-446655440000") != proof()


@pytest.mark.parametrize(
    "offsets",
    [
        "",
        "monotonic 0 0\n",
        "monotonic 0 0\nmonotonic 0 0\n",
        "monotonic 0 0\nunknown 0 0\n",
        "monotonic 0 1\nboottime 0 0\n",
        "monotonic 1 0\nboottime 0 0\n",
        "monotonic 0 0\nboottime -1 0\n",
        "monotonic 0 0\nboottime 0 0 extra\n",
        "monotonic zero 0\nboottime 0 0\n",
        ZERO + "extra 0 0\n",
        ZERO + "\n",
    ],
)
def test_missing_malformed_duplicate_unknown_or_nonzero_offsets_rejected(offsets):
    with pytest.raises(ValueError):
        proof(offsets=offsets)


@pytest.mark.parametrize(
    "boot_id",
    ["", "not-a-uuid", "00000000-0000-0000-0000-000000000000", BOOT + " extra"],
)
def test_boot_identity_must_be_valid(boot_id):
    with pytest.raises(ValueError):
        proof(boot_id=boot_id)


@pytest.mark.parametrize(
    "namespace", ["", "time:123", "pid:[4026531834]", "time:[bad]"]
)
def test_current_namespace_metadata_remains_required(namespace):
    with pytest.raises(ValueError):
        proof(namespace=namespace)


def test_missing_kernel_metadata_is_not_replaced_with_a_fallback(monkeypatch):
    module = importlib.import_module("alice_nodes.clock")

    def unavailable(*args):
        raise FileNotFoundError("missing kernel clock metadata")

    monkeypatch.setattr(module.Path, "read_text", unavailable)
    with pytest.raises(FileNotFoundError):
        module.clock_proof("epoch-one")


@pytest.mark.parametrize(
    "children_namespace",
    ["", "time:123", "pid:[4026531834]", "time:[bad]", "time:[4026532555]"],
)
def test_offsets_must_belong_to_current_namespace(children_namespace):
    with pytest.raises(ValueError, match="namespace"):
        proof(children_namespace=children_namespace)


def test_missing_children_namespace_is_not_replaced(monkeypatch):
    module = importlib.import_module("alice_nodes.clock")
    original = module.os.readlink

    def readlink(path):
        if path == "/proc/self/ns/time_for_children":
            raise FileNotFoundError("missing child namespace")
        return original(path)

    monkeypatch.setattr(module.os, "readlink", readlink)
    with pytest.raises(FileNotFoundError, match="child namespace"):
        module.clock_proof("epoch-one")
