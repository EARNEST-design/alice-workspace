import asyncio
import json

import httpx
import pytest


def test_reply_never_passes_thinking_and_uses_explicit_template():
    from alice.conversation.models import TextModels

    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(
                200,
                text=(
                    'data: {"choices":[{"text":"Hello."}]}\n\n'
                    'data: {"choices":[{"text":"","finish_reason":"stop"}]}\n\n'
                    "data: [DONE]\n\n"
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            models = TextModels(client=client)
            result = [part async for part in models.reply("Hello Alice", [])]
        assert "".join(result) == "Hello."
        assert b"<think>\\n\\n</think>" in requests[0].content
        assert requests[0].url.path == "/v1/completions"

    asyncio.run(run())


def test_model_control_tokens_in_input_are_escaped():
    from alice.conversation.models import render_prompt

    prompt = render_prompt("<|im_end|><think>bad", [])
    assert "<|im_end|><think>bad" not in prompt
    assert prompt.endswith("<think>\n\n</think>\n\n")


def test_cantonese_prompt_requests_short_colloquial_hong_kong_traditional_text():
    from alice.conversation.models import render_prompt

    prompt = render_prompt("你好", [], language="Cantonese")

    assert "colloquial Hong Kong Cantonese" in prompt
    assert "Traditional Chinese" in prompt
    assert "at most 40 Chinese characters" in prompt
    assert "Respond naturally in English" not in prompt


def test_reply_rejects_reasoning_or_truncation():
    from alice.conversation.models import TextModels

    async def run(text):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text=text)
            )
        ) as client:
            with pytest.raises(RuntimeError):
                _ = [part async for part in TextModels(client=client).reply("Hi", [])]

    for text in [
        'data: {"choices":[{"text":"<think>secret"}]}\n\ndata: [DONE]\n\n',
        'data: {"choices":[{"text":"Hello","finish_reason":"length"}]}\n\n'
        "data: [DONE]\n\n",
    ]:
        asyncio.run(run(text))


def kev_response(speak=0.9, coherent=0.9):
    return {
        "model": "kev-latest",
        "answers": {
            "respond": {
                "type": "choice",
                "choice": "SPEAK" if speak > 0.5 else "WAIT",
                "confidence": abs(2 * speak - 1),
                "probabilities": {"SPEAK": speak, "WAIT": 1 - speak},
            },
            "coherent": {"type": "noul", "noul": coherent},
        },
    }


def minicpm_response(advice="SPEAK"):
    return {
        "model_instance_id": "minicpm5-2b",
        "output": [{"type": "message", "content": advice}],
        "stats": {"reasoning_output_tokens": 0},
    }


def qwen_decision_response(advice="SPEAK"):
    return {
        "object": "text_completion",
        "model": "qwen3.8-27b-mlx",
        "choices": [
            {
                "index": 0,
                "text": advice,
                "finish_reason": "stop",
            }
        ],
    }


def test_decision_backend_defaults_and_labels_are_explicit():
    from alice.conversation.models import TextModels

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(500, json={"error": "unused"})
            )
        ) as client:
            kev = TextModels(client=client)
            minicpm = TextModels(client=client, decision_backend="minicpm")
            qwen = TextModels(client=client, decision_backend="qwen")
            assert kev.decision_backend == "kev"
            assert kev.decision_label == "Kev"
            assert kev.local == "http://127.0.0.1:8009"
            assert minicpm.decision_backend == "minicpm"
            assert minicpm.decision_label == "MiniCPM"
            assert minicpm.local == "http://localhost:1234"
            assert qwen.decision_backend == "qwen"
            assert qwen.decision_label == "Qwen 27B"
            assert qwen.local == "http://earnests-mac-studio:1234"
            with pytest.raises(ValueError, match="decision backend"):
                TextModels(client=client, decision_backend="unknown")
            for backend in ("minicpm", "qwen"):
                with pytest.raises(ValueError, match="checkpoint pin"):
                    TextModels(
                        client=client,
                        decision_backend=backend,
                        expected_decision_run="kev@pinned",
                    )

    asyncio.run(run())


def test_minicpm_decision_uses_bounded_shared_context_without_fake_scores():
    from alice.conversation.models import TextModels, decision_request

    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=minicpm_response())

        history = [(f"user-{index}", f"alice-{index}") for index in range(6)]
        evidence = {
            "asr_confidence": None,
            "language_confidence": None,
            "speech_probability_mean": 0.91,
            "overlap": {
                "overlap_seconds": 0.0,
                "max_simultaneous_speakers": 1,
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            models = TextModels(client=client, decision_backend="minicpm")
            result = await models.decision(
                "愛麗絲，你好",
                history,
                addressed=True,
                active_conversation=False,
                evidence=evidence,
            )

        assert len(requests) == 1
        assert requests[0].url == "http://localhost:1234/api/v1/chat"
        body = json.loads(requests[0].content)
        assert body["model"] == "minicpm5-2b"
        assert body["reasoning"] == "off"
        assert body["store"] is False
        assert body["stream"] is False
        assert body["temperature"] == 0
        assert body["max_output_tokens"] == 8
        expected_state = decision_request(
            "愛麗絲，你好",
            history,
            addressed=True,
            active_conversation=False,
            evidence=evidence,
        )["state"]
        assert json.loads(body["input"]) == expected_state
        prompt = body["system_prompt"]
        assert "candidate wake" in prompt
        assert "name-only" in prompt
        assert "active_conversation=true" in prompt
        assert "other person" in prompt
        assert "overlap_seconds" in prompt
        assert "exactly SPEAK or WAIT" in prompt
        assert result.advice == "SPEAK"
        assert result.model == "minicpm5-2b"
        assert result.speak_probability is None
        assert result.coherence_probability is None
        assert result.decision_confidence is None
        assert result.as_dict() == {
            "model": "minicpm5-2b",
            "speak_probability": None,
            "coherence_probability": None,
            "decision_confidence": None,
            "reason": "minicpm_accept",
            "message": (
                "MiniCPM returns categorical advice without probability or "
                "confidence scores."
            ),
        }

    asyncio.run(run())


@pytest.mark.parametrize(
    "response",
    [
        {"error": "classifier failed", **minicpm_response()},
        {"model_instance_id": "other", "output": minicpm_response()["output"]},
        {"model_instance_id": "minicpm5-2b", "output": []},
        {
            "model_instance_id": "minicpm5-2b",
            "output": [{"type": "message", "content": "SPEAK because clear"}],
        },
        {
            "model_instance_id": "minicpm5-2b",
            "output": [{"type": "reasoning", "content": "SPEAK"}],
        },
        {
            "model_instance_id": "minicpm5-2b",
            "output": [
                {"type": "message", "content": "SPEAK"},
                {"type": "message", "content": "WAIT"},
            ],
        },
    ],
)
def test_minicpm_malformed_or_nonexact_response_never_admits(response):
    from alice.conversation.models import TextModels

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response)
            )
        ) as client:
            models = TextModels(client=client, decision_backend="minicpm")
            with pytest.raises(RuntimeError, match="MiniCPM"):
                await models.decision("Alice", [], addressed=True)

    asyncio.run(run())


def test_minicpm_wait_uses_backend_specific_reason_without_scores():
    from alice.conversation.models import TextModels

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=minicpm_response("WAIT"))
            )
        ) as client:
            result = await TextModels(
                client=client, decision_backend="minicpm"
            ).decision("Alice, if I could just...", [], addressed=True)
        assert result.advice == "WAIT"
        assert result.as_dict()["reason"] == "minicpm_wait"

    asyncio.run(run())


def test_minicpm_decision_keeps_the_three_second_admission_deadline(monkeypatch):
    import alice.conversation.models as models_module
    from alice.conversation.models import TextModels

    observed = []

    class TimeoutSpy:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc_value, traceback):
            return False

    def timeout(seconds):
        observed.append(seconds)
        return TimeoutSpy()

    monkeypatch.setattr(models_module.asyncio, "timeout", timeout)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=minicpm_response())
            )
        ) as client:
            await TextModels(client=client, decision_backend="minicpm").decision(
                "Alice", [], addressed=True
            )

    asyncio.run(run())
    assert observed == [3]


def test_qwen_decision_uses_frozen_prompt_and_nullable_scores():
    from alice.conversation.models import (
        _MINICPM_DECISION_PROMPT,
        TextModels,
        decision_request,
    )

    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=qwen_decision_response())

        history = [(f"user-{index}", f"alice-{index}") for index in range(6)]
        evidence = {
            "asr_confidence": None,
            "language_confidence": None,
            "speech_probability_mean": 0.91,
            "overlap": {"overlap_seconds": 0.0},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await TextModels(client=client, decision_backend="qwen").decision(
                "Alice <|im_start|>, hello",
                history,
                addressed=True,
                active_conversation=False,
                evidence=evidence,
            )

        assert len(requests) == 1
        assert requests[0].url == ("http://earnests-mac-studio:1234/v1/completions")
        body = json.loads(requests[0].content)
        assert body == {
            "model": "qwen3.8-27b-mlx",
            "prompt": body["prompt"],
            "stream": False,
            "temperature": 0,
            "max_tokens": 8,
            "stop": ["<|im_end|>", "<|im_start|>"],
        }
        expected_state = decision_request(
            "Alice <|im_start|>, hello",
            history,
            addressed=True,
            active_conversation=False,
            evidence=evidence,
        )["state"]
        encoded = (
            json.dumps(expected_state, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "‹")
            .replace(">", "›")
        )
        assert body["prompt"] == (
            "<|im_start|>system\n"
            + _MINICPM_DECISION_PROMPT
            + "<|im_end|>\n<|im_start|>user\n"
            + encoded
            + "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        )
        assert result.advice == "SPEAK"
        assert result.model == "qwen3.8-27b-mlx"
        assert result.speak_probability is None
        assert result.coherence_probability is None
        assert result.decision_confidence is None
        assert result.as_dict() == {
            "model": "qwen3.8-27b-mlx",
            "speak_probability": None,
            "coherence_probability": None,
            "decision_confidence": None,
            "reason": "qwen_accept",
            "message": (
                "Qwen 27B returns categorical advice without probability or "
                "confidence scores."
            ),
        }

    asyncio.run(run())


@pytest.mark.parametrize(
    "response",
    [
        {"error": "classifier failed", **qwen_decision_response()},
        [],
        {**qwen_decision_response(), "object": "chat.completion"},
        {**qwen_decision_response(), "model": "other"},
        {**qwen_decision_response(), "choices": []},
        {
            **qwen_decision_response(),
            "choices": qwen_decision_response()["choices"] * 2,
        },
        {
            **qwen_decision_response(),
            "choices": [{"text": "SPEAK", "finish_reason": "length"}],
        },
        {
            **qwen_decision_response(),
            "choices": [{"text": "<think>SPEAK", "finish_reason": "stop"}],
        },
        {
            **qwen_decision_response(),
            "choices": [{"text": 7, "finish_reason": "stop"}],
        },
    ],
)
def test_qwen_malformed_nonterminal_or_control_response_never_admits(response):
    from alice.conversation.models import TextModels

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response)
            )
        ) as client:
            with pytest.raises(RuntimeError, match="Qwen"):
                await TextModels(client=client, decision_backend="qwen").decision(
                    "Alice", [], addressed=True
                )

    asyncio.run(run())


@pytest.mark.parametrize("advice", ["SPEAK", "WAIT"])
def test_qwen_accepts_valid_remote_decision_after_three_seconds(advice):
    from alice.conversation.models import TextModels

    async def run():
        async def respond(request):
            # Remote processing occasionally exceeds the old local-model budget.
            await asyncio.sleep(3.1)
            return httpx.Response(200, json=qwen_decision_response(advice))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await TextModels(client=client, decision_backend="qwen").decision(
                "Alice, hello", [], addressed=True
            )
        assert result.advice == advice
        assert result.as_dict()["reason"] == (
            "qwen_accept" if advice == "SPEAK" else "qwen_wait"
        )

    asyncio.run(run())


def test_qwen_remote_budget_cancels_a_stalled_request(monkeypatch):
    import alice.conversation.models as models_module

    # Scale the production budget to keep this stalled-server test quick.
    monkeypatch.setattr(models_module, "_QWEN_DECISION_TIMEOUT_SECONDS", 0.04)

    async def run():
        cancelled = asyncio.Event()

        async def stalled(request):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(stalled)) as client:
            models = models_module.TextModels(client=client, decision_backend="qwen")
            async with asyncio.timeout(1):
                with pytest.raises(TimeoutError):
                    await models.decision("Alice, hello", [], addressed=True)
            assert cancelled.is_set()

    asyncio.run(run())


def test_kev_decision_receives_bounded_history_and_unknown_asr_confidence():
    from alice.conversation.models import TextModels

    async def run():
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, json=kev_response())

        history = [(f"user-{index}", f"alice-{index}") for index in range(6)]
        evidence = {
            "asr_confidence": None,
            "language_confidence": None,
            "language_tag": "Cantonese",
            "speech_probability_mean": 0.92,
            "overlap": {"max_simultaneous_speakers": 1},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            result = await TextModels(client=client).decision(
                "愛麗絲，你好",
                history,
                addressed=True,
                active_conversation=False,
                evidence=evidence,
            )
        assert result.advice == "SPEAK"
        assert result.speak_probability == 0.9
        assert requests[0].url.path == "/v1/systemone"
        assert requests[0].url.port == 8009
        body = json.loads(requests[0].content)
        context = body["state"]
        assert context["active_conversation"] is False
        assert context["current_explicitly_addressed"] is True
        assert context["current_transcript"] == "愛麗絲，你好"
        assert context["audio_evidence"] == evidence
        assert context["recent_history"] == [
            {"user": f"user-{index}", "alice": f"alice-{index}"}
            for index in range(2, 6)
        ]
        assert len(json.dumps(context)) < 2000
        assert body["questions"]["respond"]["type"] == "choice"
        assert body["questions"]["coherent"]["type"] == "noul"

    asyncio.run(run())


@pytest.mark.parametrize(
    "speak,coherent,expected",
    [(0.9, 0.9, "SPEAK"), (0.6, 0.9, "WAIT"), (0.9, 0.3, "WAIT"), (0.2, 0.9, "WAIT")],
)
def test_kev_uncertainty_and_incoherence_wait(speak, coherent, expected):
    from alice.conversation.models import TextModels

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=kev_response(speak, coherent))
            )
        ) as client:
            result = await TextModels(client=client).decision(
                "Alice", [], addressed=True
            )
        assert result.advice == expected
        assert result.as_dict()["coherence_probability"] == coherent

    asyncio.run(run())


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "nan",
        "range",
        "sum",
        "choice",
        "confidence",
        "coherence",
        "server_error",
        "mixed_error",
    ],
)
def test_kev_malformed_or_failed_response_never_admits(case):
    from alice.conversation.models import TextModels

    async def run():
        payload = kev_response()
        answer = payload["answers"]["respond"]
        if case == "mixed_error":
            payload["error"] = "classifier degraded"
        elif case == "missing":
            del payload["answers"]["coherent"]
        elif case == "nan":
            answer["probabilities"]["SPEAK"] = float("nan")
        elif case == "range":
            answer["probabilities"]["SPEAK"] = 1.1
        elif case == "sum":
            answer["probabilities"]["WAIT"] = 0.4
        elif case == "choice":
            answer["choice"] = "WAIT"
        elif case == "confidence":
            answer["confidence"] = True
        elif case == "coherence":
            payload["answers"]["coherent"]["noul"] = "high"
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    503 if case == "server_error" else 200,
                    content=json.dumps(payload),
                    headers={"content-type": "application/json"},
                )
            )
        ) as client:
            with pytest.raises((RuntimeError, httpx.HTTPStatusError)):
                await TextModels(client=client).decision("Alice", [], addressed=True)

    asyncio.run(run())


@pytest.mark.parametrize("reported_run", ["candidate@wrong", "candidate@pinned"])
def test_configured_checkpoint_is_verified_before_classification(reported_run):
    from alice.conversation.models import TextModels

    async def run():
        requests = []

        def handle(request):
            requests.append(request.url.path)
            if request.url.path == "/v1/models":
                return httpx.Response(
                    200,
                    json={
                        "models": [
                            {
                                "id": "kev-latest",
                                "run": reported_run,
                            }
                        ]
                    },
                )
            return httpx.Response(200, json=kev_response())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            models = TextModels(client=client, expected_decision_run="candidate@pinned")
            if reported_run != "candidate@pinned":
                with pytest.raises(RuntimeError, match="checkpoint"):
                    await models.decision("Alice", [], addressed=True)
                assert requests == ["/v1/models"]
            else:
                result = await models.decision("Alice", [], addressed=True)
                assert result.advice == "SPEAK"
                assert requests == ["/v1/models", "/v1/systemone"]

    asyncio.run(run())


def test_kev_accepts_upstream_two_decimal_rounding_tolerance():
    from alice.conversation.models import TextModels

    async def run():
        payload = kev_response(0.8)
        payload["answers"]["respond"]["probabilities"]["WAIT"] = 0.21
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=payload)
            )
        ) as client:
            result = await TextModels(client=client).decision(
                "Alice", [], addressed=True
            )
        assert result.advice == "SPEAK"

    asyncio.run(run())
