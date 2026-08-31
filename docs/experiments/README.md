# Experiment runs

Passive capture stores derived `BlendshapeObservation` records by default and
does not write raw frame files unless `retain_frames: true` and a non-empty
`retention_approval` are both present in the reviewed run configuration.

Use the read-only camera discovery command before configuring a run:

```bash
uv run alice-camera list
```

That command may inspect video-device capabilities but must stay separate from
capture. A reviewed passive run should use a stable camera identifier, 640x480
at 10 samples/second for 120 seconds (`requested_width: 640`,
`requested_height: 480`, `requested_fps: 10`, `duration_seconds: 120`,
`sample_count: 1200`, `sample_interval_ms: 100`), and operator-entered notes
for camera placement and lighting. The example file is
`config/experiments/passive-alice-face.example.yaml`.
