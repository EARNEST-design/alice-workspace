"""Offline, no-device qualification of a real node PREPARE warmup."""

import json
import os
from pathlib import Path

import rclpy
from alice_nodes.base import RuntimePaths
from alice_nodes.tts import create_node


def main():
    # Reuse the generated request fixture; no model mocking or PCM publication.
    from test_lifecycle import prepare

    rclpy.init()
    node = create_node(
        paths=RuntimePaths(
            Path("/workspace/config"),
            Path("/workspace/hardware"),
            Path(os.environ["ALICE_WARM_OUTPUT"]),
            Path("/workspace/config/speech"),
        )
    )
    node.set_parameters([rclpy.parameter.Parameter("tts_mode", value="pocket")])
    try:
        reply = node.begin(prepare(node, "offline-warm-epoch"))
        assert reply.accepted, reply.error
        assert node.engine.is_alive
        assert node.ledger.sent_samples == 0
        assert node.model_identity
        print(
            json.dumps(
                {
                    "prepared": reply.accepted,
                    "transport_samples": node.ledger.sent_samples,
                    "model": node.model_identity,
                    "raw_retained": False,
                }
            ),
            flush=True,
        )
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
