"""Explicit qualification-only participant instrumentation and bounded faults."""


def main():
    import importlib
    import json
    import os
    import sys
    import time
    from pathlib import Path

    from alice_nodes.base import spin

    role, fault = sys.argv[1:3]
    sys.argv = [sys.argv[0], *sys.argv[3:]]
    module = importlib.import_module("alice_nodes." + role)
    if os.environ.get("ALICE_CLOCK_FAULT") == "1":
        import alice_nodes.base

        alice_nodes.base.clock_proof = lambda epoch: (
            "incompatible-clock-version:" + "0" * 64
        )

    # Methods are replaced before subscriptions capture bound callables.
    cls = getattr(
        module,
        {
            "tts": "TtsNode",
            "audio": "AudioNode",
            "expression": "ExpressionNode",
            "maestro": "MaestroNode",
            "recorder": "RecorderNode",
            "motion": "MotionNode",
            "session": "SessionNode",
        }[role],
    )
    if role == "maestro":
        original_target = cls.target
        original_finalize = cls.finalize_run

        def target(self, message):
            admitted = time.monotonic_ns()
            original_target(self, message)
            if (
                not self.error
                and self._control_source == message.header.source_monotonic_ns
            ):
                if not hasattr(self, "_timings"):
                    self._timings = []
                self._timings.append(
                    {
                        "source_ns": message.header.source_monotonic_ns,
                        "admission_ns": admitted,
                    }
                )

        def finalize(self, outcome):
            original_finalize(self, outcome)
            (self.local_dir / "qualification-timing.json").write_text(
                json.dumps(getattr(self, "_timings", []))
            )

        cls.target, cls.finalize_run = target, finalize
        if fault == "controller":
            original_start = cls.start_run

            def start(self):
                original_start(self)
                # A mock controller error propagates through the same owner watchdog.
                self.runtime.error = RuntimeError("injected controller receipt error")

            cls.start_run = start
    elif role == "expression" and fault == "delayed-expression":
        original_speech = cls.speech

        def speech(self, message):
            time.sleep(0.35)
            return original_speech(self, message)

        cls.speech = speech
    elif role == "tts":
        original_send = cls.send

        def send(self, clause, pcm, first, final):
            if not hasattr(self, "_injected_publisher"):
                self._injected_publisher = self.publisher
                owner = self

                class Publisher:
                    count = 0

                    def publish(self, message):
                        self.count += 1
                        if fault == "underflow" and self.count > 12:
                            return
                        if fault == "gap" and self.count == 5:
                            return
                        if fault == "stale" and self.count == 5:
                            message.header.source_monotonic_ns -= 251_000_000
                        owner._injected_publisher.publish(message)
                        if fault == "duplicate" and self.count == 5:
                            owner._injected_publisher.publish(message)

                self.publisher = Publisher()
            return original_send(self, clause, pcm, first, final)

        cls.send = send
    elif role == "recorder" and fault == "recorder-failure":

        def record(self, *args):
            raise OSError("injected recorder storage failure")

        cls.record = record

    if fault == "backpressure" and role == "tts":
        original_send_bounded = cls.send
        original_final_bounded = cls.finalize_run

        def send_bounded(self, *args):
            original_send_bounded(self, *args)
            self._max_outstanding = max(
                getattr(self, "_max_outstanding", 0),
                self.ledger.sent_samples - self.ledger.cumulative_consumed_samples,
            )

        def long_clause(self, clause):
            import numpy as np

            count = self.rate * 4
            for offset in range(0, count, 480):
                self.send(
                    clause,
                    np.zeros(480, dtype=np.float32),
                    offset == 0,
                    offset + 480 == count,
                )
            self.source_final = clause.end_of_response
            self.progress += 1

        def bounded_final(self, outcome):
            original_final_bounded(self, outcome)
            (self.local_dir / "qualification-credit.json").write_text(
                json.dumps(
                    {
                        "max_outstanding": getattr(self, "_max_outstanding", 0),
                        "sent": self.ledger.sent_samples,
                        "capacity": self.ledger.capacity_samples,
                    }
                )
            )

        cls.send, cls.clause, cls.finalize_run = (
            send_bounded,
            long_clause,
            bounded_final,
        )
    elif fault == "backpressure" and role == "audio":
        original_prepare_credit = cls.prepare_run
        original_finalize_credit = cls.finalize_run

        def prepare_credit(self):
            import copy

            from alice_nodes import contracts as wire

            original_prepare_credit(self)
            publisher = self.credit_pub
            owner = self
            self._duplicate_credits = 0

            class DuplicateCredit:
                def publish(self, message):
                    publisher.publish(message)
                    duplicate = copy.deepcopy(message)
                    duplicate.header = wire.stream_header_to_msg(owner.header("credit"))
                    publisher.publish(duplicate)
                    owner._duplicate_credits += 1

            self.credit_pub = DuplicateCredit()

        def finalize_credit(self, outcome):
            original_finalize_credit(self, outcome)
            (self.local_dir / "qualification-duplicate-credit.json").write_text(
                json.dumps({"count": self._duplicate_credits})
            )

        cls.prepare_run, cls.finalize_run = prepare_credit, finalize_credit

        def slow_play(self):
            from types import SimpleNamespace

            import numpy as np

            origin = time.monotonic()
            out = np.empty((480, 1), np.float32)
            while not self.cancel.is_set():
                result = self.player.callback(
                    out,
                    480,
                    SimpleNamespace(
                        outputBufferDacTime=self.player.submitted_samples / 24000
                    ),
                    None,
                )
                if result == "abort":
                    raise RuntimeError(self.player.error)
                target = origin + 2 * self.player.submitted_samples / 24000
                while time.monotonic() < target:
                    if self.cancel.wait(0.002):
                        return
                    self._emit_clock((time.monotonic() - origin) / 2)
                if result == "stop":
                    return

        cls._simulated_play = slow_play

    if fault == "callback-underflow" and role == "audio":
        original_prepare = cls.prepare_run

        def prepare_underflow(self):
            original_prepare(self)
            original_callback = self.player.callback

            def callback(out, frames, timing, status):
                if self.player.submitted_samples >= 480:
                    status = "injected output underflow"
                return original_callback(out, frames, timing, status)

            self.player.callback = callback

        cls.prepare_run = prepare_underflow

    if fault == "offline-profile" and role in {"expression", "motion", "audio"}:
        operation = {
            "expression": "speech",
            "motion": "expression",
            "audio": "_emit_clock",
        }[role]
        original_operation = getattr(cls, operation)
        original_finalize_profile = cls.finalize_run

        def measured(self, *args):
            start = time.monotonic_ns()
            source = (
                args[0].header.source_monotonic_ns
                if role != "audio"
                else self.last_dac_ns
            )
            try:
                return original_operation(self, *args)
            finally:
                if not hasattr(self, "_stages"):
                    self._stages = []
                if len(self._stages) < 10000:
                    self._stages.append(
                        {
                            "source_ns": source,
                            "entry_ns": start,
                            "exit_ns": time.monotonic_ns(),
                        }
                    )

        def profile_final(self, outcome):
            original_finalize_profile(self, outcome)
            (self.local_dir / "qualification-stage.json").write_text(
                json.dumps(getattr(self, "_stages", []))
            )

        setattr(cls, operation, measured)
        cls.finalize_run = profile_final

    if fault in {"selfkill-session", "selfkill-audio"} and fault.endswith(role):
        import signal

        from alice_nodes.base import write_json
        from std_srvs.srv import Trigger

        original_crash_init = cls.__init__

        def crash_init(self, **kwargs):
            original_crash_init(self, **kwargs)

            def crash(request, response):
                stamp = time.monotonic_ns()
                write_json(
                    Path("/artifacts/selfkill-marker.json"),
                    {
                        "role": role,
                        "marker_ns": stamp,
                        "maximum_marker_write_ns": 2_000_000,
                        "measurement": (
                            "participant CLOCK_MONOTONIC before bounded "
                            "marker write and self SIGKILL"
                        ),
                    },
                )
                elapsed = time.monotonic_ns() - stamp
                if elapsed > 2_000_000:
                    write_json(
                        Path("/artifacts/selfkill-rejected.json"),
                        {"marker_write_ns": elapsed},
                    )
                    response.success = False
                    return response
                os.kill(os.getpid(), signal.SIGKILL)
                raise AssertionError("SIGKILL unexpectedly returned")

            self.create_service(
                Trigger, "/qualification/crash", crash, callback_group=self.group
            )

        cls.__init__ = crash_init

    if fault in {
        "queued-start",
        "success-retirement-error",
        "success-retirement-timeout",
    }:
        import threading

        from std_srvs.srv import Trigger

        original_init = cls.__init__
        original_prepare_queued = cls.prepare_run
        original_start_queued = cls.start_run

        def init_queued(self, **kwargs):
            original_init(self, **kwargs)
            self._qa_release = threading.Event()

            def release(request, response):
                self._qa_release.set()
                response.success = True
                return response

            self.create_service(
                Trigger,
                f"/qualification/{role}/release",
                release,
                callback_group=self.group,
            )

        def prepare_queued(self):
            original_prepare_queued(self)
            if getattr(self, "_qa_injected", False):
                return
            self._qa_injected = True

            def blocked():
                Path("/artifacts/lifecycle-entered.json").write_text(
                    json.dumps({"monotonic_ns": time.monotonic_ns()})
                )
                self._qa_release.wait(10)
                Path("/artifacts/lifecycle-retired.json").write_text(
                    json.dumps({"monotonic_ns": time.monotonic_ns()})
                )
                if fault == "success-retirement-error":
                    raise RuntimeError("late admitted operation failure")

            self.submit(blocked)

        def start_queued(self):
            Path("/artifacts/factory-called.json").write_text("true")
            return original_start_queued(self)

        cls.__init__, cls.prepare_run, cls.start_run = (
            init_queued,
            prepare_queued,
            start_queued,
        )

    if fault == "tts-stall-cancel" and role == "tts":
        from stalled_backend import StalledBackend

        from alice.speech.tts_worker import PocketTtsWorker

        original_prepare_stall = cls.prepare_run
        original_finalize_stall = cls.finalize_run

        def prepare_tts_stall(self):
            original_prepare_stall(self)
            self.engine = PocketTtsWorker(backend=StalledBackend())
            (self.local_dir / "model.json").write_text(
                json.dumps(StalledBackend.identity)
            )

        def finalize_tts_stall(self, outcome):
            engine = self.engine
            original_finalize_stall(self, outcome)
            (self.local_dir / "qualification-owned-worker.json").write_text(
                json.dumps({"alive": bool(engine and engine.is_alive)})
            )

        cls.prepare_run, cls.finalize_run = prepare_tts_stall, finalize_tts_stall
    elif fault == "expression-stall-cancel" and role == "expression":
        original_prepare_stall = cls.prepare_run

        def prepare_expression_stall(self):
            original_prepare_stall(self)
            advance = self.bridge.advance

            def stalled(*args):
                Path("/artifacts/expression-stall-entered.json").write_text(
                    json.dumps({"monotonic_ns": time.monotonic_ns()})
                )
                time.sleep(2)
                Path("/artifacts/expression-stall-retired.json").write_text(
                    json.dumps({"monotonic_ns": time.monotonic_ns()})
                )
                return advance(*args)

            self.bridge.advance = stalled

        cls.prepare_run = prepare_expression_stall
    elif fault == "external-relay-aging" and role == "session":
        original_relay = cls.relay_external

        def relay_aged(self, queued, **kwargs):
            stamp = queued[0].source_monotonic_ns
            time.sleep(0.3)
            Path("/artifacts/relay-aged.json").write_text(
                json.dumps(
                    {
                        "original_ns": stamp,
                        "forward_attempt_ns": time.monotonic_ns(),
                        "source_unchanged": queued[0].source_monotonic_ns == stamp,
                    }
                )
            )
            return original_relay(self, queued, **kwargs)

        cls.relay_external = relay_aged

    manifest = json.loads(Path("/opt/alice/source-manifest.json").read_text())
    spin(
        module.create_node,
        args=[
            *sys.argv[1:],
            "--ros-args",
            "-p",
            "image_identity:=" + os.environ["ALICE_IMAGE_ID"],
            "-p",
            "code_identity:=sha256:" + manifest["sha256"],
        ],
    )


if __name__ == "__main__":
    main()
