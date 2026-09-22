# Speech evidence and Kev implementation plan

User-authorized scope: reduce garbage-triggered replies, include confidence and
multi-speaker context, and replace MiniCPM with Kev if local qualification succeeds.
Preserve existing worktree and accepted English/Cantonese voices.

1. Reproduce missing admission evidence and wake bypass in regression tests.
2. Read hosted ASR protocol; preserve unavailable confidence as null.
3. Implement bounded pinned segmentation; test padding, overlap, local speaker
   count, malformed output and short name calls; benchmark only synthetic audio.
4. Make overlong VAD speech recover after quiet and pass endpoint evidence.
5. Route every real wake/follow-up through evidence-aware admission, preserving
   Stop, expiry, capture draining and no work outside wake policy.
6. Install isolated pinned Kev on loopback, measure synthetic English/Cantonese
   positives and negatives, and choose a model on measured quality and latency.
7. Add strict probabilistic Kev adapter, dashboard evidence and local controls;
   retain remote Qwen reply and accepted local voices.
8. Run focused and full conversation/speech tests, static checks, independent
   review, actual dry chain and bounded live service check. Record metrics,
   provenance, limitations and checkpoint. Do not imply synthetic probes are
   general accuracy or physical hearing confirmation.

Parallel ownership: VAD/recovery and runtime regressions; overlap detector and
synthetic probes; Kev external deployment/probes; parent admission, adapter,
dashboard and integration. No commits or motion actions.
