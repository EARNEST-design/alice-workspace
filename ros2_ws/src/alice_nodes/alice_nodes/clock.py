"""Versioned proof of the unshifted Linux host CLOCK_MONOTONIC domain."""

import hashlib
import os
import re
import uuid
from pathlib import Path

VERSION = "host-monotonic-zero/v2"


def proof_from_metadata(
    *, epoch: str, boot_id: str, namespace: str, children_namespace: str, offsets: str
) -> str:
    """Reject shifted/unsupported clocks; no translation or namespace-ID spoofing."""
    if not isinstance(epoch, str) or not 1 <= len(epoch) <= 128:
        raise ValueError("invalid clock proof epoch")
    boot = boot_id.strip()
    parsed = uuid.UUID(boot)
    if str(parsed) != boot or parsed.int == 0:
        raise ValueError("invalid kernel boot identity")
    if re.fullmatch(r"time:\[[0-9]+\]", namespace) is None:
        raise ValueError("current time namespace metadata is required")
    if re.fullmatch(r"time:\[[0-9]+\]", children_namespace) is None:
        raise ValueError("child time namespace metadata is required")
    if namespace != children_namespace:
        raise ValueError("offset metadata does not belong to current time namespace")
    rows = [line.split() for line in offsets.splitlines()]
    if len(rows) != 2 or sorted(rows) != [
        ["boottime", "0", "0"],
        ["monotonic", "0", "0"],
    ]:
        raise ValueError("complete zero monotonic/boottime offsets are required")
    # proc_timens_show_offsets reads time_ns_for_children. Equality above binds
    # those records to this participant, rather than a future child namespace.
    # Linux offsets are immutable once a process enters the namespace. Different
    # namespace identities with zero offsets share the same kernel host clock.
    digest = hashlib.sha256((VERSION + "\0" + epoch + "\0" + boot).encode()).hexdigest()
    return VERSION + ":" + digest


def clock_proof(epoch: str) -> str:
    """Read current kernel metadata; never persist raw IDs or epoch-salted proof."""
    return proof_from_metadata(
        epoch=epoch,
        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text(),
        namespace=os.readlink("/proc/self/ns/time"),
        children_namespace=os.readlink("/proc/self/ns/time_for_children"),
        offsets=Path("/proc/self/timens_offsets").read_text(),
    )
