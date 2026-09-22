# Conversation host memory relief, 2026-09-22

The operator reported memory pressure and suggested offloading models. The location
clarification remained unanswered during this check; measurements below describe
only the Linux dashboard host. No application behavior, voice or audio setting changed.

## Evidence and actions

The Linux host has 27.63 GiB physical RAM, initially about 16 GiB available and
3.6 GiB swap occupied. Memory PSI averages were zero; initial interval vmstat samples
showed no paging. Occupied swap alone is not evidence of current pressure.
Kernel history records two earlier **Kev service cgroup** OOM kills (12:04 and
12:07), not a demonstrated whole-host OOM. Kev is now inactive; Diart has no running
worker. The previous Diart trial measured about 0.6 GiB peak process RSS.

The idle dashboard retained Cantonese CosyVoice at 5,019,656 KiB RSS (4.79 GiB)
and English PocketTTS at 923,724 KiB (0.88 GiB). Stop intentionally cancels active
speech but does not unload idle workers; this cleanup did not change that policy.
Both voices remain warm during active sessions to avoid language-switch cold starts.

Local MiniCPM was still loaded although the live decision backend is remote Qwen.
Unloaded exactly `minicpm5-2b` using the installed LM Studio CLI after inspecting its
help. The API confirmed zero loaded local instances; reported GPU VRAM allocation
fell from 2,760,994,816 to 1,127,763,968 bytes (1.52 GiB). Do not add this GPU counter
to system-memory changes: accounting can overlap.

With session idle and capture stopped, gracefully terminated only the exact owned
conversation CLI from the preserved worktree. It reaped both voice workers, then
restarted with identical Qwen, overlap, accepted voice and live-output configuration.
No model download was removed. Available RAM increased from 16.75 GiB after MiniCPM
unload to 22.17 GiB after the idle worker release: a 5.41 GiB point-in-time gain.
Other applications continue running, so this is a host snapshot rather than an
isolated allocator measurement. Memory PSI stayed zero. Final vmstat intervals
showed no swap-out and at most 28 KiB/s swap-in; old swap remains occupied.

## Verification and current operation

Exactly one replacement dashboard process, no child voice workers, and none of the
old owned process IDs remain. The dashboard is idle, output enabled, hosted ASR
ready for English/Cantonese, Qwen decision/reply ready, ROS connected. Start reloads
both voices before capture; allow the normal cold warmup. No microphone, synthesis
or motion test was performed for this operational cleanup. No application code
changed, so no new unit-test run was necessary.

Current execution session: 38809; launch log outside the repository:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/bench-service-memory-relief-20260922.log`.

## Placement decision

Keep microphone/playback, VAD and overlap local. Keep ASR, admission and replies on
the existing remote services. Do not load MiniCPM/Kev alongside this default without
a bounded experiment. Cantonese TTS is the largest useful next offload candidate if
sustained pressure recurs; English TTS and the small Diart trial are lower priorities.
A remote TTS deployment is not implemented or claimed. Mac Studio currently advertises
both Qwen and GPT-OSS 120B as loaded; its available RAM was not measured. Leave that
potentially unrelated model untouched until its ownership and need are established.

Redacted before/after state, process and memory metrics, configuration and SHA256
manifest: `artifacts/conversation/2026-09-22-memory/` (ignored). No raw human audio,
transcript, credentials or embeddings retained. No commit, merge or push.
