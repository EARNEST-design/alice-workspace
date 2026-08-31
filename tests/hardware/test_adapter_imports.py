"""Fresh-process import tests for safety/hardware boundary isolation."""

import subprocess
import sys


def test_safety_supervisor_import_does_not_cycle_through_adapters() -> None:
    """Eager hardware exports must not make supervisor import order-dependent."""

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from alice.safety.supervisor import SafetySupervisor; "
            "assert SafetySupervisor.__name__ == 'SafetySupervisor'",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
