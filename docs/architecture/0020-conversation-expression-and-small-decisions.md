# ADR 0020: Conversation, expressive speech and a dedicated decision model

Date: 2026-09-22. Status: next-session direction requested by the operator;
implementation and candidate promotion remain uncompleted.

## Current boundary

The live conversation bench recognizes English/Cantonese, admits addressed turns,
streams a remote Qwen reply and plays local female speech through PipeWire. It
does not command facial servos. Separately, the repository has sample-clock speech
composition, guarded selected-face execution and an eight-participant ROS runtime.
The latter's physical expression acceptance is incomplete. Joining these existing
systems is the next milestone, not rebuilding their controllers.

## Decisions

1. Separate speaking admission from response writing. Prefer a small dedicated
   model for admission; reserve remote Qwen for generating replies. Keep the current
   Qwen admission adapter as an explicit temporary baseline until a smaller
   candidate passes the same frozen test and latency criteria. No automatic
   per-turn escalation to Qwen is planned: uncertainty/timeout means WAIT.
2. Compare local Kev with TypeSafe Jev using identical inputs and labels. Jev is
   documented as a hosted System One API requiring an API key; its local weights,
   parameter count and local deployment availability are not established here.
   A Kev alias named `jev-latest` is not evidence of running TypeSafe's Jev.
3. Treat the current negative Kev result as evidence about the tested revisions,
   question formulation and CPU/int8 execution, not proof that the model family
   cannot work. Audit encoding/truncation, typed question design, option order and
   quantization before a new trial. Measure English and Cantonese separately.
4. Use the existing ROS Session/RunSpeech authority for composed output. The
   conversation process supplies committed text/affect clauses and observes action
   feedback. Exactly one output path plays PCM, and Maestro remains the sole
   actuator owner. The present `pw-cat` path remains an explicit audio-only mode;
   it cannot run simultaneously with ROS playback for the same generation.
   Physical ROS admission is currently blocked by the unqualified complete-host
   device-owner verifier. Preserve that explicit rejection until separately
   qualified. Also add a bounded pre-play phase: current five-second prebuffer
   startup cannot cover measured ~8.9 s Cantonese synthesis plus reply generation.
   Maestro must not gain device/motion authority while waiting for that initial PCM.
5. All mouth and expression timing follows validated played audio samples. Mouth
   keeps the accepted 100 ms lookahead; affect changes at the audible clause
   boundary, subject to the existing accepted-prefix cadence (up to 400 ms), never
   when a network token or future PCM arrives. Do not claim phoneme/viseme accuracy:
   the initial mouth signal is the existing PCM amplitude envelope.
6. The reply writer may propose a small typed delivery label per committed clause.
   A checked-in authored mapping converts it to bounded affect. This describes
   Alice's intended delivery, not a diagnosis of the human's emotions. Missing or
   unsupported labels use neutral. No fitted emotion model is currently qualified;
   empty training support and zero residual fixtures must remain honestly labeled.
7. Start with mouth only, then the existing selected facial channels 3/4/5/6/9/11.
   Head/neck/gaze remain outside the first integrated trial. Reuse channel-specific
   limits, watchdogs, controller identity checks and cancellation semantics.
8. Keep capture, VAD, overlap analysis, playback clock, expression composition and
   actuator control local. Cantonese TTS is the first offload candidate if needed,
   provided it returns generation-tagged PCM into the same local bounded timeline.
   Do not offload the real-time actuator loop or infer spare capacity on the Mac.

## Alternatives and tradeoffs

Keeping Qwen for every admission preserves today's tested adapter, but adds
network dependence and an eight-second worst-case wait. Promoting Kev immediately
would ignore missed calls and false activations from its previous test. We instead
keep the working fallback while qualifying a dedicated decision path.

Driving jaw position from reply text or wall-clock TTS requests would be easier,
but would lose synchronization during synthesis stalls, language switches and
queued playback. The existing sample timeline is the integration boundary.

The existing host `SpeechStreamSession`/`ExpressionBridge`/`FaceRuntime` remain
reference implementations and test components. Creating a second live face owner
inside the dashboard would duplicate the already established ROS authority.

## Sources and execution

Primary sources checked on 2026-09-22: [Kev](https://github.com/jaredpalmer/kev),
[Jev introduction](https://docs.typesafe.ai/introduction),
[Jev API quickstart](https://docs.typesafe.ai/introduction/quickstart), and
[confidence semantics](https://docs.typesafe.ai/confidence). Their general claims
do not replace Alice-specific evaluation. Model decision scores are never ASR
confidence or a measured accuracy rate.

Read the [handoff](../checkpoints/2026-09-22-conversation-face-handoff.md), then
the [speech/face plan](../superpowers/plans/2026-09-22-conversation-face-integration.md)
and [small-decision plan](../superpowers/plans/2026-09-22-small-speech-admission.md).
The tracks can progress independently in simulation. Hardware integration and
model promotion have separate acceptance gates. This session only saves and
publishes the existing work and these plans.
