"""Lazy offline TTS with bounded transport credits; synthetic by default."""

import asyncio
import threading
import time

import numpy as np
from alice_interfaces.msg import PcmChunk, PcmCredit
from alice_interfaces.msg import SpeechClause as ClauseMsg
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup

from alice.contracts.speech_stream import ClauseSequence, SpeechClause
from alice.speech.tts_worker import PocketTtsWorker
from alice_nodes import contracts as wire
from alice_nodes.base import RELIABLE, RuntimeNode, spin, write_json
from alice_nodes.transport import CreditLedger, PcmPacket, SequenceGuard


class TtsNode(RuntimeNode):
    def __init__(self, **kwargs):
        super().__init__("tts", **kwargs)
        self.declare_parameter("tts_mode", "synthetic")
        self.engine = None
        self._inference_done = threading.Event()
        self._inference_done.set()
        self.publisher = self.create_publisher(PcmChunk, "/alice/speech/pcm", RELIABLE)
        self.create_subscription(
            ClauseMsg,
            "/alice/speech/clauses",
            self.receive_clause,
            RELIABLE,
            callback_group=MutuallyExclusiveCallbackGroup(),
        )
        # Credits must remain live while the producer worker waits for capacity.
        self.create_subscription(
            PcmCredit,
            "/alice/speech/pcm_credit",
            self.credit,
            RELIABLE,
            callback_group=self.group,
        )

    def prepare_run(self):
        self.rate = 24000
        self.ledger = CreditLedger(self.identity, capacity_samples=self.rate * 2)
        self.credit_sequence = SequenceGuard(self.identity, exact=True, max_gap_ns=None)
        self.clauses = ClauseSequence()
        self.source_final = False
        self.last_production = time.monotonic_ns()
        self.model_identity = {
            "mode": "deterministic-sine/v1",
            "seed": self.binding.seed,
        }
        mode = self.get_parameter("tts_mode").value
        if mode not in {"synthetic", "pocket"}:
            raise ValueError("unknown TTS mode")
        if mode == "pocket":
            if self.engine is None:
                self.engine = PocketTtsWorker(offline=True)

            async def warm():
                warmup = SpeechClause(
                    generation_id="warm-" + self.incarnation,
                    clause_id="warm",
                    sequence=0,
                    text="Hello.",
                    vector=(0, 0, 0),
                    intensity=0,
                    seed=self.binding.seed,
                    end_of_response=True,
                )
                async for _ in self.engine.stream(warmup):
                    pass

            self.run_inference(lambda: asyncio.wait_for(warm(), 35))
            self.model_identity = self.engine.identity
        write_json(self.local_dir / "model.json", self.model_identity)

    def credit(self, message):
        if not self.current(message) or self.error:
            return
        try:
            if message.header.publisher_incarnation != self.peers.get("audio"):
                raise ValueError("credit incarnation mismatch")
            with self._lock:
                wire.apply_pcm_credit(
                    message,
                    self.ledger,
                    self.credit_sequence,
                    now_monotonic_ns=time.monotonic_ns(),
                )
        except Exception as exc:
            self.fail(str(exc))

    def receive_clause(self, message):
        if not self.current(message) or self.error:
            return
        try:
            header = wire.stream_header_from_msg(message.header)
            if not self.admit_header(
                header, "session", "clauses", exact=True, sparse=True
            ):
                return
            clause = wire.speech_clause_from_msg(message)
            self.clauses.commit(clause)
            identity = self.identity
            self.submit(
                lambda: (
                    self.clause(clause)
                    if identity == self.identity and not self.error
                    else None
                )
            )
        except Exception as exc:
            self.fail(str(exc))

    def clause(self, clause):
        async def produce():
            if self.engine is None:
                count = self.rate // 2
                pcm = (
                    0.12 * np.sin(2 * np.pi * 180 * np.arange(count) / self.rate)
                ).astype(np.float32)
                for offset in range(0, count, self.rate // 50):
                    self.send(
                        clause,
                        pcm[offset : offset + self.rate // 50],
                        offset == 0,
                        offset + self.rate // 50 == count,
                    )
            else:
                first = True
                async for chunk in self.engine.stream(clause):
                    if chunk.sample_rate != self.rate:
                        raise ValueError("model sample rate differs from credited rate")
                    for offset in range(0, max(1, len(chunk.pcm)), self.rate // 50):
                        pcm = chunk.pcm[offset : offset + self.rate // 50]
                        self.send(
                            clause,
                            pcm,
                            first,
                            chunk.final and offset + self.rate // 50 >= len(chunk.pcm),
                        )
                        first = False

        self.run_inference(produce)
        if self.cancel.is_set():
            return
        self.source_final = clause.end_of_response
        self.progress += 1

    def run_inference(self, operation):
        with self._lock:
            if self.cancel.is_set():
                raise RuntimeError("TTS cancelled")
            self._inference_done.clear()
            engine, cancel = self.engine, self.cancel

        async def run():
            task = asyncio.create_task(operation())

            async def interrupt():
                while not cancel.is_set():
                    await asyncio.sleep(0.002)
                # Use the existing owned-process terminate/join/kill mechanism,
                # on its owning event loop even when stream() has no first chunk.
                if engine is not None:
                    await engine.close()
                task.cancel()

            stopper = asyncio.create_task(interrupt())
            try:
                await task
            except asyncio.CancelledError:
                raise RuntimeError("TTS cancelled") from None
            finally:
                if cancel.is_set():
                    await stopper
                else:
                    stopper.cancel()
                    await asyncio.gather(stopper, return_exceptions=True)

        try:
            asyncio.run(run())
        finally:
            self._inference_done.set()

    def send(self, clause, pcm, first, final):
        deadline = time.monotonic() + 3
        while True:
            if self.cancel.is_set():
                raise RuntimeError("TTS cancelled")
            with self._lock:
                if self.ledger.available_samples >= len(pcm):
                    offset = self.ledger.sent_samples
                    self.ledger.reserve(len(pcm))
                    break
            if time.monotonic() > deadline:
                raise RuntimeError("PCM credit progress expired")
            time.sleep(0.002)
        packet = PcmPacket(
            self.header("pcm"),
            clause.clause_id,
            clause.sequence,
            offset,
            self.rate,
            tuple(float(v) for v in pcm),
            first,
            clause if first else None,
            final,
            final and clause.end_of_response,
        )
        self.publisher.publish(wire.pcm_packet_to_msg(packet))
        self.last_production = time.monotonic_ns()

    def validate_end(self, outcome):
        if outcome == "success" and not self.source_final:
            raise RuntimeError("TTS source not final")

    def finalize_run(self, outcome):
        if outcome != "success":
            if not self._inference_done.wait(2):
                raise RuntimeError("TTS cancellation cleanup deadline expired")
            if self.engine is not None:
                asyncio.run(self.engine.close())
                self.engine = None
        write_json(
            self.local_dir / "tts.json",
            {
                "transport_samples": self.ledger.sent_samples,
                "source_final": self.source_final,
            },
        )

    def destroy_node(self):
        result = super().destroy_node()
        if self.engine is not None:
            asyncio.run(self.engine.close())
        return result


def create_node(**kwargs):
    return TtsNode(**kwargs)


def main():
    spin(create_node)
