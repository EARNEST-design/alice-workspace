# ADR 0010: Live speech composition and fault boundaries

- Date: 2026-09-10
- Status: implemented for incremental audio and simulated motion; physical face execution pending
- Implements the software portion of ADR 0009

## Interfaces and clocks

`SpeechClause` is a frozen, provider-independent committed event. One response
has at most 32 clauses and 4,000 text characters. Duplicate/replaced clauses,
sequence gaps, generation changes and missing end-of-response markers fail.
Live affect applies at clause sample onset; offline fractional cues are unchanged.

`PocketTtsWorker` owns one spawned process, one warm January English model and
cached Azelma voice state. IPC capacity defaults to four chunks. Pocket 3.1.0's
internal latent and decoder queues are bounded through a module-local factory
inside that process; the standard library queue implementation is not patched.
An upstream error may join a decoder blocked on its queue, so progress deadlines
also apply: 30 seconds to the first result, 10 seconds between results, excluding
time spent backpressured by the consumer. Timeout invalidates the generation and
terminates the owned process. Close uses bounded join, terminate and kill.

`PcmTimeline` keeps complete 20 ms RMS windows and smoothing state across chunk
boundaries. Calibration remains gate 0.01/full-open 0.06; there is no future
utterance normalization. PCM occupies a fixed ring (two seconds by default),
with a 200 ms startup target. Generated, queued, submitted and DAC-played sample
positions are distinct. The callback only copies PCM and records its DAC origin.
Underflow aborts. Explicit silence has closed aperture. Mouth aperture uses
100 ms lookahead; affect and ownership use the audible sample.

## Expression provenance and continuation

No fitted motion package was found. `authored-expression/v1` chooses reviewed
smile/frown/neutral anchors using an explicitly authored valence threshold,
scaled amplitude and 1.6-second anchor transition. Blink/gaze events use the
existing configured priors, with a zero residual. This is not learned emotion.
The separate learned-fallback mode uses the empty evidence support set and
returns neutral. Loading new support coordinates requires a separately verified
fitted package; this factory refuses to silently adopt them.

The production motion runtime uses one-second lookahead and 400 ms accepted
prefixes at the existing 5 Hz motion cadence. The bridge retains speculative
boundary state separately and promotes it only when the DAC reaches that
boundary. New audible affect is used at the next replan, with up to one prefix
plus consumer polling delay; it is never applied to past or unplayed audio.
Snapshots include the pending prefix, current state, observation, RNG and all
configuration identities. Head generation is separately selectable and disabled
by default; all 11 proposed channels are retained.

Integration exposed repeated transient-event accumulation in the production
composer. `GeneratorState.composed_anchor_target` now preserves the complete
anchor pose before residual/event overlays. Replanning from the already
blink-displaced output added the same absolute blink envelope repeatedly.
Replanning from the separate anchor boundary prevents this while keeping actual
composed state, response estimates and event phases intact. New bridge identity
includes `event-free-anchor/v2`; cross-revision snapshot migration is not claimed.

## Fault and artifact ordering

Cancellation and sibling faults invalidate queued PCM immediately. A consumer
thread already running is then joined before the single terminal ownership
release, preventing late generation output after release. Device creation/start
are joined on cancellation so their eventual handles can be closed. No model or
serial work occurs in the callback or asyncio loop.

A fault release is an event at the last audible sample. Its duplicate timestamp
is retained in the trace but excluded from finite-difference derivative metrics.
The original failure and manifest survive artifact finalization.

The CLI records generated WAV, per-chunk timing/checksums, complete composed
targets, state, derivative metrics, source text, source hashes and a replay.
These are software proposals, not serial receipts or measured mechanics. The
raw mouth proposals still require the accepted jaw trajectory guard; other
channels require their own reviewed caps in Task 6.
