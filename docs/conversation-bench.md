# Attended conversation bench

The host service connects ReSpeaker USB audio to local Silero VAD, hosted Qwen
ASR, local speaker-overlap analysis, Qwen admission and replies, and local
speech. Open [the dashboard](http://127.0.0.1:8765). It starts idle. Both **Wake
listening** and **Conversation (wake required)** wait for “Alice”, “愛麗絲” or
“爱丽丝” at the beginning of a phrase. A candidate wake must pass the audio and
decision checks before opening a conversation. Follow-ups remain eligible
for 30 seconds after the last successful output and still require a clear,
addressed turn. Outside that window, background speech makes no decision, reply
or speech request. Say the wake name again to resume.

ASR and language confidence are explicitly unknown because the hosted backend
does not return them. The speech-probability meter is Silero's speech estimate,
not transcript accuracy. The speaker display estimates simultaneous speech and
local model channels; it does not identify people or count people taking turns.
The bench waits on >=200 ms overlap, <120 ms segmentation speech, analysis
failure, or rejected decision advice. Continuous speech longer than 15 seconds is
discarded until 320 ms of quiet, without ending the session. The default backend
is the existing remote Qwen 27B, returning a label without a confidence score.
Remote Qwen admission may take up to eight seconds; quick decisions return
immediately. Timeout or invalid output waits without generating speech. Stop
cancels a pending decision. This is separate from the later reply and TTS latency.
MiniCPM is available for comparison. Experimental Kev
uses P(SPEAK)>=0.75 and P(coherent)>=0.65; these are not measured accuracy.
See [ADR 0018](architecture/0018-audio-evidence-and-kev-admission.md) for the
acceptance status and evidence boundaries.

**Stop** ends capture and cancels the turn. Each live session ends after five
minutes. Both voices warm before the microphone opens; the first start can take
tens of seconds. The dashboard shows engagement, language, voice and pipeline
states. Without `--enable-audio`, output remains dry.

The current Stop action leaves idle voice models warm. The measured resident cost is
about 4.8 GiB for Cantonese and 0.9 GiB for English; a graceful dashboard process
restart releases them and the next Start warms them again. MiniCPM is unloaded in
the remote-Qwen configuration. See the [memory investigation](experiments/2026-09-22-memory-relief.md)
for the measured cleanup and offload priorities.

The default configuration is English; the optional local voice below enables
English and Cantonese. Other ASR language tags are skipped. VAD uses 300 ms
silence, 200 ms pre-roll and 32 ms frames. Silence rejected locally makes no ASR
request. Only validated final transcripts can admit replies.

## Start and stop

Run from the preserved `streaming-affect-motion` worktree:

```bash
uv sync --locked --extra speech --extra ml --extra conversation
.venv/bin/python -m alice.conversation.cli \
  --vad-model /home/alice/.local/state/alice-hardware/respeaker-usb-20260921/faster-whisper-env/lib/python3.13/site-packages/silero_vad/data/silero_vad.onnx \
  --asr-url https://work.manakin-gecko.ts.net:10000/v1 \
  --decision-backend qwen \
  --decision-url http://earnests-mac-studio:1234 \
  --overlap-model /home/alice/.local/state/alice-hardware/respeaker-usb-20260921/overlap/sherpa-onnx-pyannote-segmentation-3-0/model.onnx \
  --replay-file /home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/audio-probe-20260916/voice-01/playback.wav \
  --enable-audio
```

Without `--enable-audio`, speech synthesis runs with dry output. The optional
replay file must be explicitly selected synthetic PCM16, 16 kHz WAV. **Synthetic
replay** uses real ASR, overlap analysis, remote replies and dry speech output;
it bypasses the live wake/decision policy and does not open the microphone. It
does not qualify Kev admission. The installed ONNX file is checked against its
qualified SHA256. Supply the same verified artifact at another path on another host.

Close the service with Ctrl-C or SIGTERM to its own process. Closing the browser
tab does not stop capture; use Stop first. The five-minute bound still applies.
The service binds only to `127.0.0.1`; controls require the same host/origin.

## Experimental Kev

Kev is implemented as a selectable backend, but is not the live default.
The default now uses the existing remote Qwen 27B for admission as well as replies:
a separate categorical request shares the audio evidence and bounded text context.
This sends candidate transcript/evidence before admission to the same configured
Tailscale host already used for replies. Scores remain unknown, not invented.
MiniCPM accepted all 12 fuller synthetic held-out cases, including all negatives;
Qwen correctly classified 12/12, with a 938 ms median and 1.962 s maximum. These
small synthetic screens do not establish general accuracy. Maximum-context probes
took 2.919 s in English and 5.757 s in Cantonese. The three-second client limit
therefore rejects some long Cantonese contexts; it does not wait indefinitely or
speak from a late result. Server computation may continue after the client stops. Actual
local synthetic tests did not qualify the examined candidates: 0.6B admitted
other-addressee/gibberish/fragment cases, 0.8B rejected every valid call under the
initial policy, and quantized Qwen3 4B missed five of six positives and admitted
an unfinished Cantonese phrase. The 4B model took about 2.1 seconds for short
requests and up to 32.7 seconds for a maximum-size Cantonese context. These are
specific local results, not a claim that all Kev models are unsuitable.

The optional client uses `--decision-backend kev --decision-url
http://127.0.0.1:8009` and checks the exact `--decision-checkpoint` pin advertised
by `/v1/models` before each decision. It does not fall back silently. The
three-second deadline prevents late output but cannot cancel native inference
in the server. Do not treat this candidate as a qualified low-latency service.
See [ADR 0018](architecture/0018-audio-evidence-and-kev-admission.md),
`scripts/serve-kev-int8.py`, `requirements/kev-cpu-py312.txt`, and the experiment
report for the isolated setup, pins, measured memory requirements and startup.

## ROS observation

ROS is optional and receives observation events; it does not own audio or
actuation. On this bench, the existing ROS image is pinned by digest below.
Use the absolute path of this worktree for `ALICE_BENCH_WORKTREE`:

```bash
ALICE_BENCH_WORKTREE=/home/alice/alice-workspace/.worktrees/streaming-affect-motion
docker run -d --name alice-conversation-observer --network host \
  --user 1000:1000 --read-only --cap-drop ALL \
  --security-opt no-new-privileges --pids-limit 128 --memory 256m \
  --tmpfs /tmp:rw,nosuid,nodev,size=64m \
  -e ROS_DOMAIN_ID=74 -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
  -e ROS_LOG_DIR=/tmp/ros \
  -v "$ALICE_BENCH_WORKTREE/src/alice/conversation/ros_observer.py:/opt/observer.py:ro" \
  --entrypoint /bin/bash \
  sha256:bbe986a71e4aca8a3c7d6403ddbc3a030fce33909f5cf37a92e846943a78507b \
  -lc 'source /opt/ros/lyrical/setup.bash && python3 /opt/observer.py'
```

If this named container already exists, use `docker start
alice-conversation-observer`; do not create a second observer. Stop it with
`docker stop alice-conversation-observer`. No devices are mounted. Discovery is
local and uses domain 74. The topic is `/alice/conversation/events`, type
`std_msgs/msg/String`, with `bench-event/v1` JSON containing session, sequence,
monotonic timestamp, stage, state and details. The dashboard marks the observer
disconnected after its heartbeat expires.

## Interpretation and limits

- Qwen ASR at [the operator's service](https://work.manakin-gecko.ts.net:10000/docs)
  receives completed microphone segments as multipart WAV. SSE streams the
  resulting text; this is not continuously streamed microphone input.
  Its documented no-speech metadata yields an empty final and no reply. A later
  direct digital-silence probe returned a server error instead; the client fails
  closed and shows the error. This does not bypass local VAD.
- ASR timing begins at upload preparation, after endpoint/cancellation work.
  The 300 ms silence setting is additional. LLM timing starts at its request;
  TTS first PCM starts at clause submission. Output drain is a software event,
  not an acoustic latency measurement.
- The capture queue is bounded and faults on overflow or stale audio. Failed,
  truncated and cancelled recognition cannot commit a late reply. The browser
  and ROS show errors and cancellation rather than assuming completion.
- A reply guard is enabled by default from recognition through generation and
  playback. Meters stay live, but speech during that turn is not admitted. Wait
  until Alice finishes before the next phrase; Stop remains available. Full
  duplex echo cancellation and interruption are not qualified. Experimental
  `--experimental-barge-in` is opt-in and needs controlled double-talk tests.
- Audio exists only in bounded memory in this service. Transcript/reply text
  appears in the in-memory dashboard and ROS event history. Stop retires active
  work but leaves that history visible; restarting the service clears it.
  Microphone audio goes to the selected ASR endpoint, and transcript text goes
  to the configured local decision service and remote LM Studio endpoints. Server-side
  retention at those endpoints has not been inspected.
- The decision model gates all real wakes and follow-ups; broad classification accuracy is
  not qualified by the synthetic bench cases.
  Replay bypasses wake only in its dry synthetic path. Acoustic latency and
  full-duplex measurements remain separate work.

Implementation decision: [ADR 0015](architecture/0015-qwen-asr-conversation-bench.md).
Measurements and verification: [September 21 experiment](experiments/2026-09-21-qwen-asr-bench.md).

## Cantonese and English

The installed female voice is CosyVoice-300M-SFT `粤语女`, speed 0.85 with speech
level normalization. The operator accepted voice, pace and volume on a longer
physical sample. English retains Azelma. Replies display Traditional Chinese;
character forms are converted only inside the Cantonese speech helper.

Add these arguments to the launch command above on this host:

```bash
--cantonese-model /home/alice/.cache/huggingface/hub/models--FunAudioLLM--CosyVoice-300M-SFT/snapshots/fbb71de2afe387ed854eebd80b9f3d078c6b9869 \
--cantonese-source /home/alice/.local/state/alice-hardware/respeaker-usb-20260921/cosyvoice-src \
--cantonese-python /home/alice/.local/state/alice-hardware/respeaker-usb-20260921/cosyvoice-env/bin/python
```

The model is already installed privately. On another host, create an isolated
Python 3.12 environment with `uv venv --python 3.12 /absolute/voice-env`. Put
`setuptools<81` in a temporary build-constraints file, then install the tested
freeze using the CPU wheel index (do not alter the main Alice environment):

```bash
uv pip install --python /absolute/voice-env/bin/python \
  --extra-index-url https://download.pytorch.org/whl/cpu \
  --index-strategy unsafe-best-match \
  --build-constraint /absolute/build-constraints.txt \
  -r requirements/cosyvoice-cpu.txt
uv pip check --python /absolute/voice-env/bin/python
```

Clone official `https://github.com/QwenAudio/CosyVoice` outside the repo, checkout
`074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc`, then initialize its recursive
submodules. Matcha-TTS must resolve to `dd9105b34bf2be2230f4aa1e4769fb586a3c824e`.
Use Hugging Face `snapshot_download` for `FunAudioLLM/CosyVoice-300M-SFT`, revision
`fbb71de2afe387ed854eebd80b9f3d078c6b9869`, selecting `README.md`, `cosyvoice.yaml`,
`llm.pt`, `flow.pt`, `hift.pt`, `spk2info.pt`, `campplus.onnx` and
`speech_tokenizer_v1.onnx`. Download before starting the service. Use that
snapshot directory, source checkout and environment executable in the flags.

Cantonese synthesis is CPU-only and completes a clause before sending PCM.
Earlier warm short clauses took 2.8–5.1 seconds at speed 1.0; the accepted longer
sample at 0.85 took 24.1 seconds to first PCM including cold startup, then played
6.15 seconds. Warmup avoids model loading during the first live reply, but
Cantonese still adds several seconds. Arbitrary pronunciation remains subject
to testing. No cloud TTS or voice cloning is configured.

See [ADR 0016](architecture/0016-local-cantonese-conversation.md),
[ADR 0017](architecture/0017-addressed-conversation.md) and
[the experiment](experiments/2026-09-21-cantonese-conversation.md).

Final warm integration at speed 0.85 measured 8.864 seconds to first PCM for a
4.238-second generated reply; both-voice warmup was 16.297 seconds.
