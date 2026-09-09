# ADR 0009: Incremental speech and affect streaming

- Date: 2026-09-09
- Status: next implementation direction requested by the operator; not implemented
- Supersedes: ADR 0007's full-utterance buffering choice for interactive speech

Start producing audible speech before the LLM finishes its response and before
TTS finishes generating the first sentence. Keep the existing offline replay
path for reproducible tests, but do not make a complete WAV a live prerequisite.

Use an asyncio orchestrator with bounded queues between three independent stages:
incremental LLM clauses/cues, a dedicated local Pocket TTS process, and a PCM
playback buffer. Keep the model and Azelma voice warm. The installed 3.1.0 source
in `pocket_tts/models/tts_model.py` exposes `generate_audio_stream`, yielding PCM
chunks before the sentence completes; it is not thread-safe. Feed each committed
clause to this generator while the LLM continues generating later clauses. Do
not run model work or blocking servo I/O inside the audio callback/event loop.

The LLM stream should emit complete short clause events with an ID, text, affect
value and optional emphasis cues. Do not wait for one complete response JSON.
Sentence boundaries are useful initial commit points; later add a bounded
punctuation-aware clause policy. Once a clause's audio is queued for playback,
its text and cues are committed. Cancellation starts a new utterance generation
ID and flushes queued PCM, affect and obsolete motion together.

Append generated PCM chunks to a bounded ring buffer, assign contiguous absolute
sample offsets, and derive a causal jaw envelope with smoothing state carried
across chunks. Use a short configurable prebuffer; measure time to first audible
sample, not just first generated chunk. Do not normalize against the RMS of a
future complete utterance. An actual device underrun aborts the utterance; an
intentional starvation gap must insert explicit silence and close the mouth.
Generation time, buffered time and DAC-played sample position are separate.

Drive mouth and expression from DAC playback position. Clause emotion becomes
active when that clause is heard. Mid-clause cue fractions cannot depend on a
final duration that does not yet exist: use clause-boundary cues first, then
incremental alignment or only revise audio/cues that remain uncommitted. Never
retime already-played samples to fit the finished sentence. Feed the resulting
AffectIntent into the motion generator, compose jaw ownership, then validate
physical targets through the supervisor/servo consumer.

Use plain asyncio plus a dedicated process initially. ROS 2 is a possible later
transport for the same versioned chunk/cue/clock messages if distributed robot
nodes need it; it is not needed just to stream local audio. Preserve this
interface separation so transport does not redefine speech timing.

Acceptance tests must show the first PCM chunk is played before the generator
finishes and before the LLM completes; sample offsets stay monotonic across
clause/chunk boundaries; queue growth is bounded; cancellation cannot leak old
PCM or targets; injected starvation produces synchronized silence/closure; and
jitter/slow servo ACKs cannot block the speaker clock. Benchmark cold/warm first
audio latency, real-time factor, buffer depth, underflow count and actual
controller cadence on this box. No physical sync accuracy is claimed from PCM
or controller acknowledgments alone.


Hardware refinement: runtime jaw speed/acceleration 0/0 removed the measured
controller-output ramp. The operator accepted a 100 ms aperture lead for
remaining software/mechanical delay in the retained hardware trial. Incremental playback must hold at least the
configured mouth lead in already-generated PCM and derive future aperture from
that buffer, while affect/ownership cues follow the current DAC sample. This is
a bounded startup buffer; it must not wait for full LLM text or full TTS output.

Implementation sequence: [next-session plan](../superpowers/plans/2026-09-09-speech-emotion-integration-next-session.md).
