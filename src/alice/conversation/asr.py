from __future__ import annotations

import asyncio
import io
import json
import re
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import numpy as np
from numpy.typing import NDArray

_SAMPLE_RATE = 16_000
_MAX_AUDIO_SAMPLES = 15 * _SAMPLE_RATE
_MAX_TRANSCRIPT_CHARACTERS = 2_000
_MAX_SSE_RECORDS = 2_048
_MAX_SSE_RECORD_BYTES = 64 * 1_024
_TOTAL_TIMEOUT_SECONDS = 10.0
_TEXT_MARKER = "<asr_text>"
_MAX_METADATA_CHARACTERS = 128
_ENGLISH = frozenset({"English"})
_ENGLISH_CANTONESE = frozenset({"English", "Cantonese"})
# Canonical language names published by Qwen3-ASR's inference utilities.
_LANGUAGES = {
    name.lower(): name
    for name in (
        "Chinese",
        "English",
        "Cantonese",
        "Arabic",
        "German",
        "French",
        "Spanish",
        "Portuguese",
        "Indonesian",
        "Italian",
        "Korean",
        "Russian",
        "Thai",
        "Vietnamese",
        "Japanese",
        "Turkish",
        "Hindi",
        "Malay",
        "Dutch",
        "Swedish",
        "Danish",
        "Finnish",
        "Polish",
        "Czech",
        "Filipino",
        "Persian",
        "Greek",
        "Romanian",
        "Hungarian",
        "Macedonian",
        "None",
    )
}


@dataclass(frozen=True)
class Transcript:
    text: str
    final: bool
    language: str | None = "English"
    ignored_reason: str | None = None


class QwenAsrClient:
    def __init__(
        self,
        base_url: str,
        model: str = "qwen3-asr",
        *,
        client: httpx.AsyncClient | None = None,
        languages: frozenset[str] = _ENGLISH,
    ) -> None:
        if languages not in {_ENGLISH, _ENGLISH_CANTONESE}:
            raise ValueError("ASR languages must be English or English plus Cantonese")
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"
        self._model = model
        self._languages = languages
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(_TOTAL_TIMEOUT_SECONDS),
            follow_redirects=False,
        )

    async def transcribe(self, pcm: NDArray[np.float32]) -> AsyncIterator[Transcript]:
        wav = _encode_wav(pcm)
        fields = {
            "model": self._model,
            "temperature": "0",
            "seed": "7",
            "stream": "true",
        }
        if self._languages == _ENGLISH:
            fields["language"] = "en"
        files = {"file": ("audio.wav", wav, "audio/wav")}

        text_parts: list[str] = []
        text_length = 0
        prefix_buffer = ""
        prefix_seen = False
        no_speech = False
        language: str | None = "English"
        ignored_reason: str | None = None
        stopped = False
        done = False

        async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
            async with self._client.stream(
                "POST",
                self._url,
                data=fields,
                files=files,
                headers={"accept": "text/event-stream"},
                follow_redirects=False,
                timeout=httpx.Timeout(_TOTAL_TIMEOUT_SECONDS),
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if content_type.partition(";")[0].strip() != "text/event-stream":
                    raise RuntimeError("ASR response is not an SSE stream")

                async for data in _iter_sse_data(response):
                    if done:
                        raise RuntimeError("ASR stream contains data after DONE")
                    if data == "[DONE]":
                        if not stopped:
                            raise RuntimeError("ASR stream reached DONE before stop")
                        done = True
                        continue
                    if stopped:
                        raise RuntimeError("ASR stream contains a chunk after stop")

                    content, finish_reason = _parse_chunk(data)
                    if not prefix_seen:
                        prefix_buffer += content
                        metadata = _metadata(prefix_buffer)
                        if metadata is not None:
                            language, content = metadata
                            prefix_seen = True
                            no_speech = language is None
                            accepted = language in self._languages or (
                                language == "Chinese"
                                and self._languages == _ENGLISH_CANTONESE
                            )
                            if language is not None and not accepted:
                                mode = "/".join(
                                    candidate
                                    for candidate in ("English", "Cantonese")
                                    if candidate in self._languages
                                )
                                ignored_reason = (
                                    f"Skipped {language} result; {mode} mode"
                                )
                            prefix_buffer = ""
                        else:
                            content = ""

                    if no_speech:
                        if content.strip():
                            raise RuntimeError(
                                "Qwen no-speech metadata has nonempty text"
                            )
                        content = ""
                    if content:
                        text_length += len(content)
                        if text_length > _MAX_TRANSCRIPT_CHARACTERS:
                            raise RuntimeError(
                                "ASR transcript exceeds the 2,000-character limit"
                            )
                        if ignored_reason is None:
                            text_parts.append(content)
                            yield Transcript(
                                text=content, final=False, language=language
                            )

                    if finish_reason is not None:
                        if finish_reason != "stop":
                            raise RuntimeError(
                                f"ASR finish_reason must be stop, got {finish_reason!r}"
                            )
                        if not prefix_seen:
                            raise RuntimeError("ASR stopped before its metadata prefix")
                        stopped = True

        if not done:
            raise RuntimeError("ASR stream ended without DONE")
        if not stopped:
            raise RuntimeError("ASR stream ended without stop")
        yield Transcript(
            text="".join(text_parts),
            final=True,
            language=language,
            ignored_reason=ignored_reason,
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _metadata(buffer: str) -> tuple[str | None, str] | None:
    """Wait for the bounded complete header; SSE deltas may split any token."""
    header, marker, text = buffer.partition(_TEXT_MARKER)
    pending_marker = (
        0
        if marker
        else max(
            (
                length
                for length in range(1, len(_TEXT_MARKER))
                if header.endswith(_TEXT_MARKER[:length])
            ),
            default=0,
        )
    )
    if len(header) - pending_marker > _MAX_METADATA_CHARACTERS:
        raise RuntimeError("Qwen ASR metadata exceeds 128-character limit")
    if not marker:
        return None
    match = re.fullmatch(r"\s*language\s+([a-z]+)\s*", header, re.IGNORECASE)
    if match is None:
        raise RuntimeError("invalid Qwen ASR metadata header")
    canonical = _LANGUAGES.get(match[1].lower())
    if canonical is None:
        raise RuntimeError("unsupported Qwen ASR language tag")
    return (None if canonical == "None" else canonical), text


def _encode_wav(pcm: NDArray[np.float32]) -> bytes:
    if not isinstance(pcm, np.ndarray) or pcm.ndim != 1:
        raise ValueError("PCM must be a one-dimensional numpy array")
    if pcm.dtype != np.float32:
        raise ValueError("PCM must use float32 samples")
    if pcm.size == 0:
        raise ValueError("PCM must contain at least one sample")
    if pcm.size > _MAX_AUDIO_SAMPLES:
        raise ValueError("PCM exceeds the 15-second utterance limit")
    if not np.isfinite(pcm).all():
        raise ValueError("PCM samples must be finite")

    pcm16 = (np.clip(pcm, -1.0, 1.0) * 32_767).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(_SAMPLE_RATE)
        wav_file.writeframes(pcm16.tobytes())
    return output.getvalue()


def _parse_chunk(data: str) -> tuple[str, str | None]:
    try:
        payload: Any = json.loads(data)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ASR stream contains malformed JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("ASR stream chunk must be a JSON object")
    if "error" in payload:
        error = payload["error"]
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
        else:
            message = str(error)
        raise RuntimeError(f"ASR server error: {message}")

    choices = payload.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise RuntimeError("ASR stream chunk must contain one choice")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise RuntimeError("ASR stream choice must be an object")
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        raise RuntimeError("ASR stream choice is missing delta")
    content = delta.get("content")
    if not isinstance(content, str):
        raise RuntimeError("ASR stream delta content must be text")
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        raise RuntimeError("ASR stream finish_reason must be text or null")
    return content, finish_reason


async def _iter_sse_data(response: httpx.Response) -> AsyncIterator[str]:
    buffer = bytearray()
    record_lines: list[bytes] = []
    record_size = 0
    record_count = 0

    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                break
            line = bytes(buffer[:newline]).removesuffix(b"\r")
            del buffer[: newline + 1]
            if line:
                record_size += len(line)
                if record_size > _MAX_SSE_RECORD_BYTES:
                    raise RuntimeError("ASR SSE record is too large")
                record_lines.append(line)
                continue
            if record_lines:
                record_count += 1
                if record_count > _MAX_SSE_RECORDS:
                    raise RuntimeError("ASR SSE stream contains too many records")
                yield _decode_sse_record(record_lines)
                record_lines = []
                record_size = 0
        if len(buffer) + record_size > _MAX_SSE_RECORD_BYTES:
            raise RuntimeError("ASR SSE record is too large")

    if buffer:
        record_size += len(buffer)
        if record_size > _MAX_SSE_RECORD_BYTES:
            raise RuntimeError("ASR SSE record is too large")
        record_lines.append(bytes(buffer).removesuffix(b"\r"))
    if record_lines:
        record_count += 1
        if record_count > _MAX_SSE_RECORDS:
            raise RuntimeError("ASR SSE stream contains too many records")
        yield _decode_sse_record(record_lines)


def _decode_sse_record(lines: list[bytes]) -> str:
    data_lines: list[bytes] = []
    for line in lines:
        if line.startswith(b":"):
            continue
        field, separator, value = line.partition(b":")
        if field != b"data" or not separator:
            raise RuntimeError("ASR SSE record contains an unsupported field")
        data_lines.append(value.removeprefix(b" "))
    if not data_lines:
        raise RuntimeError("ASR SSE record contains no data")
    try:
        return b"\n".join(data_lines).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("ASR SSE record is not valid UTF-8") from exc
