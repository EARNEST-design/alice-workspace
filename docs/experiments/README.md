# Experiment runs

Passive capture stores derived `BlendshapeObservation` records by default and
does not write raw frame files unless `retain_frames: true` and a non-empty
structured `retention_policy.raw_approval` (approval ID, scope, expiry, and
retention duration) are both present in the reviewed run configuration.

Use the read-only camera discovery command before configuring a run:

```bash
uv run alice-camera list
```

That command may inspect video-device capabilities but must stay separate from
capture. A reviewed passive run should use a stable camera identifier, 640x480
at 10 samples/second for 120 seconds (`requested_width: 640`,
`requested_height: 480`, `requested_fps: 10`, `duration_seconds: 120`,
`sample_count: 1200`, `sample_interval_ms: 100`), a maximum observation-age
gate, and typed setup evidence for stable camera identity, full-face framing,
participant exclusion, lighting, placement, focus, and exposure. A condition
that cannot genuinely be measured is recorded explicitly as `state: unknown`.
The example file is
`config/experiments/passive-alice-face.example.yaml`.

Capture `manifest.json` is immutable. Offline analysis publishes a complete
generation under `analysis/generations/<generation-id>/` containing a separate
`analysis-manifest.json`, metrics, acceptance checks, and conclusion. The
analysis manifest records exact input/threshold hashes, analyzer package/Git
identity, and hashes for every derived artifact. Generation directories are
staged, file- and directory-synced, and atomically renamed as a unit.

```bash
uv run alice-passive-capture analyze artifacts/RUN
uv run alice-passive-capture compare artifacts/REPEAT-1 artifacts/REPEAT-2 \
  --output-dir artifacts/COMPARISON
```

Lag-1 gating uses a configured *maximum absolute* autocorrelation: both strong
positive and strong negative serial dependence indicate a non-stationary or
alternating signal. Any evaluable failed check dominates undefined checks;
`inconclusive` is used only when no check fails and at least one is undefined.
