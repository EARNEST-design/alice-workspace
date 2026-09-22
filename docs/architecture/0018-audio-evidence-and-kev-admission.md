# ADR 0018: Audio evidence and probabilistic turn admission

Date: 2026-09-22. Status: implemented for attended bench testing; Qwen temporary default.
Supersedes ADR 0017's explicit-wake bypass and text-only follow-up context.

## Context

The operator observed speech triggered by recognition garbage and requested
confidence and awareness of multiple speakers, then requested Kev in place of
MiniCPM. A recognized wake name previously bypassed the advisory model. Qwen's
hosted ASR advertises a generic transcription schema but actually rejects
`verbose_json` with HTTP 400. Its streaming response exposes no transcript or
language confidence and no diarization. These quantities must remain unknown;
Silero speech probability is not transcription correctness.

## Decision

Keep perception, admission and spoken replies separate. Qwen ASR remains the
transcriber and the remote Qwen 27B remains the reply model. Provide Kev's dedicated
loopback System One service as an experimental alternative to MiniCPM. Require
real local synthetic-case qualification before changing the live default.

Each completed utterance carries Silero mean probability, voiced fraction and
voiced duration, measured audio duration/RMS/clipping, the ASR language tag,
explicitly unknown ASR/language confidence, and local pyannote segmentation.
Silero aggregates start at the trigger frame and include endpoint silence;
preroll is excluded from the probability denominator.

A pinned MIT-licensed sherpa-onnx redistribution of pyannote segmentation 3.0
estimates speech and simultaneous speakers on bounded 16 kHz mono audio. Local
speaker channels are not identities and are never added across windows. Report
both maximum simultaneous speakers and the maximum distinct channels within a
10-second window. This is not full diarization or proof of the number of people.
Initial bench vetoes: >=200 ms overlapping speech or <120 ms detected speech.
A configured analysis failure also waits. One tracked inference may remain after
cancellation; no new inference can queue until it completes, and late results
cannot open engagement or speak.

A wake name is a candidate, and must pass the same decision as follow-ups before
opening the existing 30-second window. Outside an active window, absent a wake
name, no decision/reply/TTS request occurs. An accepted window still needs a clear
addressed turn. Kev receives bounded transcript/history and all audio evidence;
its separate response-choice and intelligibility questions share context. Initial
bench policy requires P(SPEAK)>=0.75 and P(coherent)>=0.65. These are policy
thresholds, not demonstrated error rates. API/model errors and malformed responses
wait. The advertised checkpoint is checked before each decision; a model-name
echo in a response is not accepted as model identity. Kev confidence is labeled as decision evidence, never ASR accuracy.

Default overlap policy is to wait while voices overlap and allow different people
to take turns. No speaker ownership or enrollment is inferred. The optional
operator preference question remains unanswered; this stated default applies.

Overlong continuous speech (>15 seconds including preroll) emits one discard,
releases buffers, and waits for 320 ms consecutive quiet before accepting a fresh
utterance. It neither kills the session nor sends truncated speech to ASR.

Synthetic dry replay has an explicit, private wake/decision bypass for pipeline
checks; audio evidence still applies and the bypass rejects audible output. A dry
replay therefore does not qualify live admission. Real decision probes must call
the selected backend adapter and run without that bypass.

## Boundaries

No human microphone audio or transcripts are retained as experiment files.
Live UI/ROS events remain in bounded memory. Models and synthetic WAVs live outside
the repository; manifests record source, licenses, revisions, hashes and seeds.
No speaker identity, direction finding, emotion classification, voice training,
motion or firmware changes are part of this work. Existing mute handling, accepted
female voices, output cap, reply guard, Stop and bounded sessions are preserved.

## Sources

- https://github.com/jaredpalmer/kev
- https://huggingface.co/pyannote/segmentation-3.0
- https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html
- Hosted Qwen ASR OpenAPI and actual synthetic verbose_json rejection, 2026-09-22.

Final measured model choice, verification and limitations are recorded in the
companion experiment report before acceptance.

## Kev qualification outcome

The frozen 14-case synthetic screen did not qualify any examined local candidate.
The 0.6B model accepted multiple obvious negatives; 0.8B rejected all six valid
calls; 4B Qwen3 dynamic-int8 accepted one of six valid calls and one unfinished
Cantonese phrase. Its short median was 2.105 seconds and maximum-size Cantonese
requests took 32.4–32.7 seconds, exceeding the three-second client deadline.
The adapter, isolated environment, pinned downloads and reproducible loader are
preserved for experiments. The proposed MiniCPM fallback also failed the subsequent complete-evidence
held-out screen (6/12, all SPEAK), so that initial recommendation was withdrawn.
The existing remote Qwen 27B is the temporary live decision default, based on
12/12 correct responses on the same frozen full-evidence cases, median 938 ms
and maximum 1962 ms. MiniCPM and Kev remain explicitly selectable experiments.
No automated shadow inference is added. Temperature/thresholds were not tuned to
turn these failing tests into apparent success. Broader or fine-tuned Kev models
may behave differently; none is implicitly qualified here.

## Qwen fallback and data boundary

The existing remote `qwen3.8-27b-mlx` endpoint now receives a separate bounded,
non-thinking categorical completion before the reply request. It receives
candidate transcripts, bounded history and audio evidence even when admission
ultimately waits. This is the same configured Tailscale host used for replies;
it uses the existing plain-HTTP endpoint over that network. The model alias is
verified in the response, but the server API does not supply an immutable weight
revision. No ASR or decision confidence is fabricated for categorical output.
The client requires exact SPEAK/WAIT, one successful finished choice and no
model-error marker, with an eight-second total deadline for remote Qwen. An error waits. The
remote endpoint is therefore needed for both decision and reply in this default.

The original three-second Qwen deadline was increased on 2026-09-22 after a real
recognized wake passed audio checks but timed out before admission. The same
request later completed in 1.1–1.4 seconds, and the operator heard a subsequent
reply before this change was deployed. This is resilience for intermittent remote
slowness, not a demonstrated fix to the server-side cause. Earlier maximum-size
Cantonese context took 5.757 seconds. Fast decisions return immediately; the
eight-second cap bounds a stalled call. Local MiniCPM/Kev keep three seconds,
Stop cancels the pending decision, and follow-up wake expiry is still checked
before replying. During a pending decision the microphone keeps draining while
new turn admission is guarded. See the decision-timeout experiment report.

The first 14-case remote diagnostic scored 13/14: an unaddressed Cantonese
background comment was classified SPEAK. The deterministic outside-wake gate
would suppress that case before any model call. The separate frozen 12-case
full-evidence screen passed all positives and negatives. These screens are
limited synthetic checks, not evidence of perfect intent recognition.
