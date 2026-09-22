from __future__ import annotations

import asyncio
import io
import json
import wave
from collections.abc import AsyncIterator
from email import policy
from email.parser import BytesParser

import httpx
import numpy as np
import pytest

import alice.conversation.asr as asr_module
from alice.conversation.asr import QwenAsrClient, Transcript


def _chunk(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


def _delta(content: str, *, finish_reason: str | None = None) -> bytes:
    choice: dict[str, object] = {
        "delta": {"content": content, "reasoning_content": None}
    }
    if finish_reason is not None:
        choice["finish_reason"] = finish_reason
        choice["stop_reason"] = None
    return _chunk(
        {
            "id": "transcribe-test",
            "object": "transcription.chunk",
            "created": 1,
            "model": "qwen3-asr",
            "choices": [choice],
        }
    )


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class GateStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False
        self.started = asyncio.Event()
        self._release = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _delta("language English<asr_text>")
        self.started.set()
        await self._release.wait()

    async def aclose(self) -> None:
        self.closed = True
        self._release.set()


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, stream: httpx.AsyncByteStream, *, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.stream = stream
        self.status = status

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(await request.aread())
        return httpx.Response(
            self.status,
            headers={"content-type": "text/event-stream"},
            stream=self.stream,
            request=request,
        )


def _multipart_parts(request: httpx.Request, body: bytes) -> dict[str, bytes]:
    content_type = request.headers["content-type"]
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    )
    parts: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        payload = part.get_payload(decode=True)
        assert isinstance(name, str)
        assert isinstance(payload, bytes)
        parts[name] = payload
    return parts


async def _collect(client: QwenAsrClient, pcm: np.ndarray) -> list[Transcript]:
    return [item async for item in client.transcribe(pcm)]


def test_documented_qwen_no_speech_prefix_commits_empty_final_only() -> None:
    async def scenario() -> None:
        stream = ChunkStream(
            [
                _delta("language N"),
                _delta("one<asr_"),
                _delta("text>"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            assert await _collect(client, np.zeros(8000, np.float32)) == [
                Transcript(text="", final=True, language=None)
            ]
        assert stream.closed

    asyncio.run(scenario())


def test_no_speech_metadata_cannot_release_nonempty_transcript() -> None:
    async def scenario() -> None:
        stream = ChunkStream(
            [
                _delta("language None<asr_text>"),
                _delta("invented words"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            with pytest.raises(RuntimeError, match="no-speech"):
                await _collect(client, np.zeros(8000, np.float32))
        assert stream.closed

    asyncio.run(scenario())


def test_transcribe_sends_mono_pcm16_wav_and_strips_fragmented_qwen_prefix() -> None:
    async def scenario() -> None:
        sse = b"".join(
            [
                _delta("language Eng"),
                _delta("lish<asr_"),
                _delta("text>Hello"),
                _delta(" world"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        # Split both SSE syntax and JSON across transport chunks.
        stream = ChunkStream([sse[:13], sse[13:71], sse[71:153], sse[153:]])
        transport = RecordingTransport(stream)
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient(
            "https://asr.example/v1", model="bench-model", client=http_client
        )
        pcm = np.array([-1.0, -0.25, 0.0, 0.25, 1.0], dtype=np.float32)

        transcripts = await _collect(client, pcm)

        assert transcripts == [
            Transcript(text="Hello", final=False),
            Transcript(text=" world", final=False),
            Transcript(text="Hello world", final=True),
        ]
        assert len(transport.requests) == 1
        request = transport.requests[0]
        assert request.method == "POST"
        assert request.url == httpx.URL("https://asr.example/v1/audio/transcriptions")
        parts = _multipart_parts(request, transport.bodies[0])
        assert parts["model"] == b"bench-model"
        assert parts["language"] == b"en"
        assert parts["temperature"] == b"0"
        assert parts["seed"] == b"7"
        assert parts["stream"] == b"true"
        with wave.open(io.BytesIO(parts["file"]), "rb") as wav_file:
            assert wav_file.getparams()[:4] == (1, 2, 16_000, 5)
            decoded = np.frombuffer(wav_file.readframes(5), dtype="<i2")
        np.testing.assert_array_equal(
            decoded, np.array([-32767, -8191, 0, 8191, 32767], dtype=np.int16)
        )

        await client.close()
        assert not http_client.is_closed
        await http_client.aclose()
        assert stream.closed

    asyncio.run(scenario())


def test_bilingual_mode_omits_hint_and_accepts_fragmented_cantonese() -> None:
    async def scenario() -> None:
        stream = ChunkStream(
            [
                _delta("language Can"),
                _delta("tonese<asr_"),
                _delta("text>你好"),
                _delta("，愛麗絲。"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        transport = RecordingTransport(stream)
        async with httpx.AsyncClient(transport=transport) as http:
            client = QwenAsrClient(
                "https://asr.example/v1",
                client=http,
                languages=frozenset({"English", "Cantonese"}),
            )
            result = await _collect(client, np.zeros(8000, np.float32))

        assert result == [
            Transcript("你好", False, language="Cantonese"),
            Transcript("，愛麗絲。", False, language="Cantonese"),
            Transcript("你好，愛麗絲。", True, language="Cantonese"),
        ]
        parts = _multipart_parts(transport.requests[0], transport.bodies[0])
        assert "language" not in parts

    asyncio.run(scenario())


def test_bilingual_mode_accepts_chinese_tag_as_cantonese_alias() -> None:
    async def scenario() -> None:
        stream = ChunkStream(
            [
                _delta("language Chinese<asr_text>你今日想聽啲咩？"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient(
                "https://asr.example/v1",
                client=http,
                languages=frozenset({"English", "Cantonese"}),
            )
            result = await _collect(client, np.zeros(8000, np.float32))

        assert result == [
            Transcript("你今日想聽啲咩？", False, language="Chinese"),
            Transcript("你今日想聽啲咩？", True, language="Chinese"),
        ]

    asyncio.run(scenario())


def test_bilingual_mode_still_ignores_other_recognized_languages() -> None:
    async def scenario() -> None:
        stream = ChunkStream(
            [
                _delta("language Arabic<asr_text>background words"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient(
                "https://asr.example/v1",
                client=http,
                languages=frozenset({"English", "Cantonese"}),
            )
            result = await _collect(client, np.zeros(8000, np.float32))

        assert result == [
            Transcript(
                "",
                True,
                language="Arabic",
                ignored_reason="Skipped Arabic result; English/Cantonese mode",
            )
        ]

    asyncio.run(scenario())


def test_exact_metadata_delta_can_precede_an_empty_transcript() -> None:
    async def scenario() -> None:
        body = b"".join(
            [
                _delta("language English<asr_text>"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        transport = RecordingTransport(ChunkStream([body]))
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient("https://asr.example/v1", client=http_client)

        assert await _collect(client, np.zeros(32, dtype=np.float32)) == [
            Transcript(text="", final=True)
        ]

        await http_client.aclose()

    asyncio.run(scenario())


def test_unexpected_prefix_diagnostic_identifies_bounded_wire_prefix() -> None:
    async def scenario() -> None:
        stream = ChunkStream([_delta("language Klingon<asr_text>")])
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            with pytest.raises(RuntimeError, match="unsupported Qwen ASR language"):
                await _collect(client, np.zeros(8000, np.float32))

    asyncio.run(scenario())


@pytest.mark.parametrize("language", ["Arabic", "Chinese", "Cantonese", "French"])
def test_other_recognized_languages_are_skipped_in_english_mode(language) -> None:
    async def scenario() -> None:
        stream = ChunkStream(
            [
                _delta("language "),
                _delta(language[:2]),
                _delta(language[2:] + "<asr_"),
                _delta("text>"),
                _delta("background words"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            result = await _collect(client, np.zeros(8000, np.float32))
        assert len(result) == 1
        assert result[0].final and result[0].text == ""
        assert result[0].language == language
        assert result[0].ignored_reason == f"Skipped {language} result; English mode"
        assert stream.closed

    asyncio.run(scenario())


def test_language_metadata_allows_fragmented_whitespace_and_case() -> None:
    async def scenario() -> None:
        stream = ChunkStream(
            [
                _delta("\n"),
                _delta("LANGUAGE\teng"),
                _delta("lish\n"),
                _delta("<asr_text>Hello"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            result = await _collect(client, np.zeros(8000, np.float32))
        assert result == [Transcript("Hello", False), Transcript("Hello", True)]

    asyncio.run(scenario())


def test_metadata_is_bounded_before_the_text_marker_arrives() -> None:
    async def scenario() -> None:
        stream = ChunkStream([_delta("language " + " " * 256)])
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            with pytest.raises(RuntimeError, match="metadata exceeds"):
                await _collect(client, np.zeros(8000, np.float32))

    asyncio.run(scenario())


def test_header_limit_does_not_depend_on_text_marker_chunk_boundaries() -> None:
    async def scenario() -> None:
        header = "language English" + " " * (128 - len("language English"))
        stream = ChunkStream(
            [
                _delta(header + "<asr_"),
                _delta("text>Hello"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            result = await _collect(client, np.zeros(8000, np.float32))
        assert result == [Transcript("Hello", False), Transcript("Hello", True)]

    asyncio.run(scenario())


def test_skipped_language_still_requires_successful_stream_termination() -> None:
    async def scenario() -> None:
        stream = ChunkStream([_delta("language Arabic<asr_text>background words")])
        async with httpx.AsyncClient(transport=RecordingTransport(stream)) as http:
            client = QwenAsrClient("https://asr.example/v1", client=http)
            with pytest.raises(RuntimeError, match="DONE"):
                await _collect(client, np.zeros(8000, np.float32))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "pcm",
    [
        np.array([], dtype=np.float32),
        np.array([np.nan], dtype=np.float32),
        np.array([np.inf], dtype=np.float32),
        np.zeros(16_000 * 15 + 1, dtype=np.float32),
    ],
    ids=["empty", "nan", "infinite", "overlong"],
)
def test_invalid_audio_is_rejected_before_network(pcm: np.ndarray) -> None:
    async def scenario() -> None:
        transport = RecordingTransport(ChunkStream([]))
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient("https://asr.example/v1", client=http_client)

        with pytest.raises(ValueError):
            await _collect(client, pcm)

        assert transport.requests == []
        await http_client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (
            _delta("language English<asr_text>truncated", finish_reason="length")
            + b"data: [DONE]\n\n",
            "finish_reason",
        ),
        (_chunk({"error": {"message": "decoder failed"}}), "decoder failed"),
        (b"data: {not json}\n\n", "JSON"),
        (_chunk({"choices": []}), "one choice"),
        (_delta("language English<asr_text>truncated"), "DONE"),
        (b"data: [DONE]\n\n", "stop"),
    ],
    ids=[
        "length",
        "server-error",
        "malformed-json",
        "malformed-choice",
        "eof",
        "done-before-stop",
    ],
)
def test_invalid_or_truncated_stream_is_rejected(body: bytes, match: str) -> None:
    async def scenario() -> None:
        transport = RecordingTransport(ChunkStream([body]))
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient("https://asr.example/v1", client=http_client)

        with pytest.raises(RuntimeError, match=match):
            await _collect(client, np.zeros(32, dtype=np.float32))

        await http_client.aclose()

    asyncio.run(scenario())


def test_transcript_length_is_bounded() -> None:
    async def scenario() -> None:
        body = b"".join(
            [
                _delta("language English<asr_text>" + "a" * 2_000),
                _delta("b"),
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        transport = RecordingTransport(ChunkStream([body]))
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient("https://asr.example/v1", client=http_client)

        with pytest.raises(RuntimeError, match="2,000"):
            await _collect(client, np.zeros(32, dtype=np.float32))

        await http_client.aclose()

    asyncio.run(scenario())


def test_sse_record_count_is_bounded() -> None:
    async def scenario() -> None:
        body = b"".join(
            [_delta("language English<asr_text>")]
            + [_delta("") for _ in range(2_048)]
            + [
                _delta("", finish_reason="stop"),
                b"data: [DONE]\n\n",
            ]
        )
        transport = RecordingTransport(ChunkStream([body]))
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient("https://asr.example/v1", client=http_client)

        with pytest.raises(RuntimeError, match="too many records"):
            await _collect(client, np.zeros(32, dtype=np.float32))

        await http_client.aclose()

    asyncio.run(scenario())


def test_cancellation_closes_response_and_propagates() -> None:
    async def scenario() -> None:
        stream = GateStream()
        transport = RecordingTransport(stream)
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient("https://asr.example/v1", client=http_client)

        task = asyncio.create_task(_collect(client, np.zeros(32, dtype=np.float32)))
        await asyncio.wait_for(stream.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert stream.closed
        await http_client.aclose()

    asyncio.run(scenario())


def test_total_deadline_closes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        stream = GateStream()
        transport = RecordingTransport(stream)
        http_client = httpx.AsyncClient(transport=transport)
        client = QwenAsrClient("https://asr.example/v1", client=http_client)

        with pytest.raises(TimeoutError):
            await _collect(client, np.zeros(32, dtype=np.float32))

        assert stream.closed
        await http_client.aclose()

    monkeypatch.setattr(asr_module, "_TOTAL_TIMEOUT_SECONDS", 0.01)
    asyncio.run(scenario())
