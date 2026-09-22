# ReSpeaker direct USB bench, 2026-09-21

The operator approved replacing XMOS I2S 1.0.8 with official USB 2.0.7 and running
a bounded microphone/playback test. USB 2.0.7 remains installed.

**Later operator confirmation:** "the speakers work correctly as linux output".
Linux speaker output is accepted; the earlier audibility and connector questions
below are superseded by this observation. Echo/double-talk and the complete
conversation loop remain unqualified. The earlier
XIAO/I2S audio profile requires restoring I2S firmware before reuse.

- Identity: Seeed ReSpeaker Lite `2886:0019`, serial `0000000001`, USB path `6-1`.
- Current descriptor: 2.07; ALSA card ID `Lite`; capture/playback S16_LE, 16 kHz,
  stereo. Both microphone channels in the inspected captures are identical;
  do not call them independent raw microphones.
- Initial connection was the XIAO serial port. The operator moved the USB cable
  to XMOS. No XIAO application, Maestro configuration or actuator was modified.
- Installed official image: `respeaker_lite_usb_dfu_firmware_v2.0.7.bin`, SHA256
  `ea96ae0af2a3a116285ced032b11678ec5989a14d3736c579efa939375f9277f`.
- Official I2S 1.0.8 restore image is staged, SHA256
  `6bfecb791d9838d2bbdd12ba97817f39ec97555626fcfa78e716eb82349bae22`.
  Both match official Git blobs at `2ad81e22e773d1680793acdb3e14bb108d7566cb`.
- Device readback returned zero bytes with a failure message. No exact backup
  of the installed I2S image is claimed. Only upgrade partition 1 was written.
- USB firmware upload completed; dfu-util returned 251 at reset. Subsequent
  independent version/audio enumeration verified the running USB image.
- Full-duplex transport ran for 30 seconds. The PortAudio CallbackStop path
  needed a bounded abort; explicit abort was separately tested with silence.
- Microphone-only capture found two speech bursts and return to silence under
  post-capture Silero analysis. Recording had zero clipped samples. This is basic
  microphone/VAD evidence, not full recognition or conversation qualification.
- Playback was attempted at signed-16-bit peaks 2,048 and 11,572. Native replay
  completed with verified PipeWire links. Operator reported no audible output,
  then clarified hearing it at some point. Consistent audible playback and
  echo rejection remain unresolved. No digital or analog gain was raised beyond
  the earlier accepted source peak; software output volume was left as found.
- Current observed PipeWire profile: IEC958 stereo output plus analog stereo
  input, unmuted, output channel scalar 0.288499. USB audio at the ALSA boundary
  is still PCM. The later Linux-output acceptance removes the need for a connector question.

Private captures, firmware and source provenance:
`/home/alice/.local/state/alice-hardware/respeaker-usb-20260921/` (mode 0700).
Aggregate measurements: `artifacts/conversation/2026-09-21-readiness/`.
[Full experiment](../docs/experiments/2026-09-21-conversation-readiness.md).

Remaining: qualify speaker-only echo and
simultaneous speech, implement/qualify streaming ASR and turn management, and
measure actual end-of-user-speech to first audible reply. No permanent audio,
VAD or conversation node was added by these diagnostic probes.

## Hosted-ASR bench follow-up, 2026-09-21

USB 2.0.7, VID/PID 2886:0019 and serial 0000000001 remain the accepted device
baseline. Native PipeWire inventory currently exposes `.iec958-stereo` for both
input and output. The host lacks `pactl`; the integration uses `pw-dump` and
resolves these routes dynamically after the USB identity check.

A synthetic ASR-to-TTS reply drained through the verified ReSpeaker sink,
74,880 samples at 24 kHz. Two bounded 20-second microphone windows completed
without capture overflow/stall; the second had no VAD-positive speech and Stop
returned in 6.90 ms. No new raw capture was retained. Acoustic stop timing and
echo/full-duplex rejection remain unknown; no new human audibility confirmation
was collected. No firmware or motor changes. See
`docs/experiments/2026-09-21-qwen-asr-bench.md` for inference results/limitations.

## Exact-zero microphone diagnosis, 2026-09-22

The operator reported speech probability stuck at 0%. Fresh dashboard telemetry
updated normally (40 observed samples over 20 seconds) with RMS/peak exactly
zero. A separate three-second native PipeWire capture contained 48,000 zero
samples on each channel, ruling out only-left-channel and stale-UI explanations.
No raw audio was saved. Source and both capture links were running, software
mute/softMute false, both channel volumes 1.0. ALSA clock valid; firmware still
2.07. The pinned VAD scored the existing synthetic speech fixture above 0.99999
and produced three completed segments, with no playback. Live probability
approximately 0.0014577 is correctly rounded to 0% by the UI.

Silence originates upstream of VAD in the input path. Physical mute is a
hypothesis, not established: the operator was asked to check the red mute
indicator and toggle Mute only if lit. No firmware, routing, gain, model or code
change was made. The existing bounded listening session was left available for
the operator check. Evidence: `artifacts/conversation/2026-09-22-zero-input/`.
[Official mute button/indicator guide](https://wiki.seeedstudio.com/reSpeaker_usb_v3/).

Follow-up hosted Qwen ASR recheck transcribed the existing synthetic English
fixture including “I am Alice” in 136.9 ms. Fresh live capture still had exact
zero RMS/peak and no new ASR request; the recognizer is available, while live
audio is not reaching VAD. Physical mute indicator question remains unanswered.

The operator subsequently confirmed the board had been muted and unmuted it.
Live ASR/LLM/TTS events then resumed, resolving the exact-zero input issue. A
separate generation-cancellation/capture-overflow software fault was diagnosed
and corrected; see `docs/experiments/2026-09-22-language-switch.md`. The direct
English→Cantonese→English physical test drained all three phrases with verified
ReSpeaker routing; operator audibility feedback was requested.
