# ReSpeaker Lite I2S audio probe, 2026-09-16

Status: microphone recording and bounded tone playback are operator-accepted.
The 25% digital-peak sweep sounded good without audible distortion; a subsequent
3 dB increase, reaching approximately 35.3% peak, was accepted as "Clean and loud
enough". The speaker is tentatively reported as 8 ohm / 1 W. The exact original
Wi-Fi diagnostic has been restored and rejoined Wi-Fi. Speech intelligibility,
long-duration playback, electrical power limits and echo-safe interruption have
not been qualified. A complete 6.38-second Azelma voice clip has now played,
with operator listening feedback pending. Full digital scale was never flashed
or played.

## Scope and current device

The operator authorized a temporary XIAO application, quiet playback, a short
private microphone capture, and restoration of the Wi-Fi diagnostic. The
operator reports a speaker on the ReSpeaker connector. No Maestro interface,
servo control, XMOS firmware update, host audio-default change or network-route
change is in scope.

USB identity remains Espressif 303a:1001, serial E0:72:A1:FB:E5:DC. Linux exposes
its serial interface, not a ReSpeaker ALSA/PipeWire sound device. The separate
XMOS USB port and USB firmware are an alternative route; connecting only the
XIAO USB port does not make a host sound card.

The prior session's Wi-Fi diagnostic reports XMOS 1.0.8. Its exact application
image was verified against flash before replacement. After the first 32-bit
test it was restored and rejoined Wi-Fi at 192.168.188.63, RSSI -49 dBm. The
idle-zero candidate was subsequently installed to investigate rejected sound,
then the exact original application was restored again (RSSI -47 dBm). After
the first mute-controlled test, restoration was repeated with a verified write
hash; the original diagnostic rejoined at 192.168.188.63, RSSI -52 dBm, and
confirmed XMOS firmware remains 1.0.8. The last amplifier command was mute,
acknowledged before restoration. That is command evidence, not physical mute
readback; the documented mute-status query requires XMOS 1.0.9 or later.

## Probe profile and evidence

- Temporary firmware identity: alice-audio-probe-1, Arduino ESP32 3.3.11.
- GPIO8 BCLK, GPIO7 WS, GPIO43 data out, GPIO44 data in.
- Accepted data-format candidate: 16 kHz, stereo 32-bit I2S slots, XIAO slave
  to the XMOS clock. Host test audio is signed 16-bit, converted to 32-bit slots.
- Initial two-second host clip: ramped 440/660 Hz bursts, peak 900/32768
  (about -31.2 dBFS), firmware cap +/-1024. Later level trials are detailed below;
  the accepted latest trial uses four 660 Hz bursts and cap +/-11572.
- Eight-second bounded run, one second quiet, two-second clip, five seconds quiet.
- Upload: 128,000 bytes, CRC32 checked, 128-byte acknowledged blocks.
- Capture: 1,024,000 I2S bytes, 128,000 frames, CRC32 checked on return to host.
- Raw captures and firmware/build artifacts stay private outside the repository.
  Aggregate evidence/config and hash manifest live at
  artifacts/respeaker/2026-09-16/audio-probe/. The private manifest binds source,
  binaries, rejected attempts, raw captures and restoration evidence.
- No production code or ROS node behavior changed. This is preloaded USB serial
  audio, not qualified real-time USB/Wi-Fi streaming or a ROS audio adapter.

Private experiment directory:
/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/audio-probe-20260916/

The existing diagnostic image to restore is:
/home/alice/.local/state/alice-hardware/xiao-e072a1fbe5dc/wifi-diagnostic-20260915/build/alice_wifi_diagnostic.ino.bin
SHA256: af1cd3e6f9013144c4d23f2d0587ad0c87b392a0d1512147c74a4e67730e00fa.
The private restore-command.json records the application-only write at 0x10000.
Identify the XIAO again before invoking it; preserve all original artifacts.

## Rejected attempts and limits

1. A USB disconnect with descriptor error -71 interrupted preflight before any
   firmware write. Operator reconnected the XIAO. Its temporary per-device ACL
   was reapplied only to the matched XIAO serial device.
2. One-shot 128 KB USB upload failed before audio. The Arduino HWCDC receive
   queue defaults to 256 bytes and discards incoming bytes when full. Per-block
   acknowledgments resolved the upload; firmware/host CRC32 checks then passed.
3. Seeed's basic 16-bit master I2S link-test profile produced invalid-looking
   microphone data, about -5.54 dBFS RMS, and operator-reported distorted output.
   It was rejected. The recording/playback documentation instead uses 32-bit
   slots with the XIAO as clock slave.
4. The 32-bit slave quiet capture had -52.33 dBFS RMS, zero clipped samples,
   and identical processed mono on both channels. The next tone capture had
   -44.32 dBFS RMS and zero clipping, but the operator still rejected cracking.
   These do not prove two independent raw microphone channels or speech quality.
5. The idle-zero candidate holds GPIO43 at zero before I2S starts and immediately
   after I2S ends, addressing the hypothesis of an idle/floating output data pin.
   Same tone samples/volume. Reported idle GPIO level is zero; the operator
   nevertheless rejected the cracking. The run returned
   128,000 frames per channel in 8.010 seconds, RMS about -28.14 dBFS, zero
   clipping. Channels differ only at frame zero. Microphone listening was
   accepted, but this change did not establish acceptable playback.
6. The mute-controlled candidate described below returned 128,000 frames per
   channel in 8.010 seconds, with 1,024,000 bytes each transmitted/received,
   checked transfer CRCs and no clipped samples. Microphone RMS was -46.60/-47.00
   dBFS; channel peaks were 16,384/1,987. These capture metrics do not measure
   speaker-output quality or prove the startup transient is resolved. Listening
   acceptance for this first run was not obtained. The requested repeat below
   supplied the first clean-tone result.

Each completed transfer took about 8.010 seconds on the device. The first
250 ms of the tone capture includes a larger transient than the remainder.
The listening-copy WAV trims 0.2 seconds and applies 2x gain; it is for listening
only. Do not conceal that transient or treat the edited copy as raw evidence.

## Next integration design requirements

The user explicitly wants VAD and interruption while Alice speaks. Production
implementation/design approval remains separate from this temporary bench test.
Recommended boundaries: audio input/transport node -> VAD node -> session turn
manager; retain the existing audio output node and its cancellation guarantees.
Silero VAD is a candidate using 512-sample (32 ms) windows at 16 kHz; compare
against WebRTC VAD if its lighter dependency stack matters. Keep pre-roll and
endpoint silence thresholds configurable and qualify against noise/echo.
Do not equate VAD with completed ASR/LLM integration. Echo rejection must be
qualified with speaker-only, near-end-only and simultaneous speech before
claiming reliable interruption. On barge-in, cancel the active generation and
flush only its queued audio; prevent late PCM from resuming it. Streaming input,
playback receipts, device-clock mapping, reconnects and transport loss need
explicit contracts before replacing the same-host ROS DAC clock assumptions.

## Latency findings from the current ROS source

- TTS worker/model/preset state already survives successful runs, but
  TtsNode.prepare_run generates a warm-up Hello on every run. Warm once per
  verified worker lifetime; cancellation/replacement still requires revalidation.
- Audio waits for 4,800 samples (200 ms at 24 kHz) before device startup.
  Tune against underflows after measurement. Preserve 100 ms mouth lookahead
  plus delivery/jitter margin; do not apply an audio-only tiny buffer blindly.
- PcmTimeline.finish adds 300 ms silence at response end for mouth/expression
  release. It is not a pause between every streamed clause. Revisit turn-end
  behavior explicitly rather than deleting required facial release.
- Session serially prepares/starts/finalizes eight peers and polls completion
  at 50 ms. One RunSpeech action per clause would pay that overhead repeatedly;
  use committed clauses within one response. Any conversation lifecycle redesign
  must preserve cancellation, evidence barriers and exclusive actuator ownership.
- Measure cold/warm first PCM, first audible sample, inter-clause gaps,
  end-of-user-speech to first reply audio, and interruption-to-silence separately.
  The previous roughly 17 ms admission p99 is not conversation response latency.
- Host routing to 192.168.188.63 still selects tailscale0. Resolve/bind the local
  route in the future audio transport design before evaluating Wi-Fi latency.

## Sources

- https://www.seeedstudio.com/ReSpeaker-Lite-p-5928.html
- https://wiki.seeedstudio.com/respeaker_record_and_play/
- https://wiki.seeedstudio.com/respeaker_volume/
- https://wiki.seeedstudio.com/respeaker_i2s_test/
- https://github.com/respeaker/ReSpeaker_Lite/tree/2ad81e22e773d1680793acdb3e14bb108d7566cb
- https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32s3/api-reference/peripherals/i2s.html
- https://github.com/snakers4/silero-vad

## Operator feedback and next corrective probe

The operator listened to the capture and confirmed recording works. They still
reported very quiet tones and much louder cracking after the idle-zero change.
GPIO43 idle-low alone did not resolve acoustic acceptance.

Seeed's current Home Assistant guide explicitly mutes the speaker on software
restart and warns that UART0 logging introduces speaker noise. The installed
Arduino SDK indeed selects UART0 as its primary console, with USB JTAG as
secondary. The new diagnostic suppresses ESP-IDF log output, uses explicit
HWCDC for its own status, and applies Seeed's documented volatile speaker-enable
write: I2C address 0x42, resource 0xF1, command 0x10, one-byte 0=muted/1=enabled.
It mutes before I2S startup, enables after about 0.768 seconds of zero PCM, mutes
after four seconds, and confirms I2C success before tearing I2S down. This is
not a firmware update to XMOS. The final acknowledged command leaves the
amplifier muted while no audio-owner firmware drives I2S; XMOS initializes its
speaker enable on a full board power cycle. Do not equate the diagnostic's local
amp_enabled flag or I2C acknowledgement with physical mute readback.

The test retained the original quiet sample amplitude, about -31.2 dBFS. That
explains the deliberately low digital level but does not establish the cause
of every quiet-output symptom. Assess cracking independently before increasing
level. This first trial did not establish a usable output level.

That restoration is captured by restore-after-muted.log and
restored-after-muted-wifi.json in the private directory.

Source: https://wiki.seeedstudio.com/respeaker_lite_ha/ and the pinned
xiao_i2c_write_register_value example in the official ReSpeaker repository.

## Requested repeat and level adjustment

The user requested another run. The unchanged muted candidate was flashed after
matching USB identity and checking port ownership and source/binary hashes.
Private repeat-muted-01/ contains its config, capture, metrics and restoration.
The operator reported "Clean but too quiet". The run transmitted/received
1,024,000 bytes in 8.010 seconds, returned 128,000 samples per channel, with
-49.82 dBFS microphone RMS, peak 18,936 and no clipped samples. These are
microphone statistics, not calibrated speaker output levels.

That listening result was recorded before a level-only adjustment: source peak
900 -> 1800 (+6.02 dB), firmware upload cap +/-1024 -> +/-2048. The I2S format,
PA mute sequence, frequencies, ramps and analog gain were unchanged. The clean
lower-level source/binary remains preserved. Private level-plus6db-01/ holds
the adjusted source, build, acceptance criterion and complete run evidence.
The build succeeded with the same partition binary. Its transfer also completed
in 8.010 seconds; microphone RMS was -47.46 dBFS, peak 6,339, no clipped samples.
The operator replied "ok really crank it up now! see what's the maxiumvolume".
That authorizes the next volume trial, without explicitly accepting the +6 dB
trial as clean or loud enough.

After each repeat the exact original Wi-Fi image was restored with a verified
write hash and successful Wi-Fi rejoin. The latest report is 192.168.188.63,
RSSI -44 dBm, XMOS 1.0.8. The last PA command was mute and acknowledged before
restoration. This is still a temporary diagnostic, not a deployed ROS adapter.

## Prepared maximum digital-level sweep (superseded, never run)

The official specification lists a 5 W speaker output, and Seeed's optional
speaker is 4 ohm / 5 W. The actual connected speaker's impedance and rating had
not been supplied at preparation time, so maximum-level playback was deferred.
The subsequent tentative 8 ohm / 1 W report supersedes this prepared trial.
It was never flashed or played. The hardware is running the original Wi-Fi
diagnostic after the limited test below.

Private level-sweep-01/ prepares four 660 Hz bursts with 30 ms fades, 250 ms
duration each, separated by silence. Peaks are 4096, 8192, 16384, 32760 in signed
16-bit PCM (about -18.1, -12.0, -6.0 and 0 dBFS). The last burst reaches nearly
full digital scale for 250 ms; this does not prove maximum clean acoustic output
or measured SPL. Analog codec/amplifier gain is unchanged. All I2S/mute timings
and the original restoration requirement remain unchanged. Offline tests execute
the actual host waveform-generation code and check level, fades, silent gaps,
stereo equality and integer bounds; the prior waveform failed the new acceptance
test before the change. No sound from this sweep has been played.

Sources: https://wiki.seeedstudio.com/reSpeaker_usb_v3/ and
https://www.seeedstudio.com/Mono-Enclosed-Speaker-4R-5W-p-5931.html.

## Limited sweep after tentative 8 ohm / 1 W report

The operator supplied "8ohm 1w i think". This is an unverified nominal rating.
The requested maximum was reduced to four 250 ms, 660 Hz bursts, each with
30 ms fades, at sample peaks 2048, 4096, 6144 and 8192. The largest is 25% of
full-scale amplitude (-12.04 dBFS), about 13.16 dB above the preceding 1800-peak
pair. Analog gain, I2S profile, startup/teardown muting and eight-second run
duration were unchanged. Private level-limited-8ohm-01/ contains source, build,
configuration, tests, run evidence and restoration logs.

The prior full-scale waveform first failed the reduced-level offline test.
The adjusted waveform passed checks for actual peak levels, fades, duration,
silent gaps, stereo equality and integer bounds. The firmware built with the
unchanged partition binary. Before playing, a non-audible upload check confirmed
that the device accepted 8192 and rejected both 8193 and -8193; that check never
sent RUN. Playback/capture then transferred 1,024,000 bytes each way in 8.010
seconds, with 128,000 microphone samples per channel, approximately -48.30 dBFS
RMS, peak 1414 and no clipped input samples. The operator subsequently reported
"yeah it was good, can it be louder? it wasn't distorting yet". This accepts the
bounded tone sweep by listening; processed microphone statistics alone cannot
establish speaker distortion or power.

For a nominal resistive 8 ohm load, 1 W corresponds to approximately 2.83 V RMS.
The digital sample cap is not a calibrated electrical-power limiter: delivered
voltage, actual impedance and amplifier gain have not been measured. Do not claim
that this test established 1 W output, maximum clean loudness or a permanent
safe operating ceiling. The full-scale candidate remains unrun.

The exact Wi-Fi diagnostic was restored with its write hash verified and Wi-Fi
rejoined at 192.168.188.63, RSSI -43 dBm; XMOS remains 1.0.8. The last amplifier
command was mute and acknowledged before restoration, without physical readback.

## Accepted 3 dB increase from the 25% baseline

Following the request for louder playback, level-plus3db-8ohm-01/ preserved the
accepted baseline and used four 250 ms bursts at signed-16-bit peaks 8192, 9192,
10313 and 11572. These are approximately +0/+1/+2/+3 dB from the accepted 25%
peak. The maximum is about 35.3% digital amplitude (-9.04 dBFS), with unchanged
analog gain, 30 ms fades, 660 Hz frequency, I2S format and PA mute sequence.

The changed waveform passed the same offline checks after the prior waveform
failed the new levels. The new firmware built with an unchanged partition
binary. Before playback, the device accepted 11572 and rejected both 11573 and
-11573 in uploads that never sent RUN. The eight-second playback/capture returned
1,024,000 bytes each way in 8.010 seconds, with 128,000 samples per channel and
verified transfer checksums. Microphone RMS was -38.91 dBFS, peak 4192 and no
clipped input samples; these are processed input metrics, not speaker SPL.

The operator explicitly answered "Clean and loud enough". This is the accepted
bounded tone-playback baseline. It does not establish a continuous-use power
ceiling, measured maximum clean SPL, speech intelligibility or echo rejection.
Preserve the accepted binary and source in level-plus3db-8ohm-01/; the original
quiet diagnostic at the experiment root is an earlier lower-level candidate.

The exact original Wi-Fi application was restored with its write hash verified;
it rejoined 192.168.188.63 at RSSI -42 dBm and reported XMOS 1.0.8 unchanged.
The last amplifier command was mute and acknowledged. No production ROS code,
servo behavior or VAD implementation changed during this test.

## Complete Azelma speech test

The user requested full voice audio. The existing locally generated Azelma
audition was reused: "Hello, I am Alice. It is lovely to meet you. Shall we try
speaking together?" It is a 6.38-second, 24 kHz mono PCM16 clip from Pocket TTS
3.1.0, english_2026-01, seed 7. Its source manifest explicitly identifies it as
generated audio, with no participant recording. The source WAV and plan hashes
were checked against that manifest before use; copied source/provenance remains
in private voice-01/.

scipy.signal.resample_poly with up=2/down=3 converted all 153,120 source samples
to 102,080 samples at 16 kHz. The complete phrase was scaled by approximately
0.448694 to the accepted peak of 11572, duplicated to stereo, prefixed with
100 ms silence and padded to seven seconds. No speech was truncated. The
resampler and NumPy versions, source/payload hashes, gain and level statistics
are recorded in clip-provenance.json. RMS of the seven-second source buffer is
1388 PCM16 units; matching peak level does not guarantee the same perceived
loudness as the tone test.

The previous two-second payload first failed the new complete-phrase check.
The seven-second payload then passed byte count, stereo, padding, peak cap and
late-phrase-content checks. The bounded firmware uses 448,000 upload bytes and
1,664,000 capture bytes: one second quiet, seven-second buffer, five seconds
quiet. PA enable is still at 0.768 seconds; mute is at nine seconds, a second
after the playback buffer ends. The firmware built with the same partition
binary, retains the same peak cap, and identifies as alice-audio-voice-probe-1.
Device uploads accepted 11572 and rejected +/-11573 without any playback before
the audible test. No analog gain, XMOS firmware or servo mapping changed.

The full voice trial returned 1,664,000 bytes each way in 13.005 seconds, with
208,000 input samples/channel and valid transfer checksums. Processed microphone
RMS was about -17.24 dBFS, peak 27,954, without clipped input samples. This capture
does not establish echo rejection or source attribution: the operator was not
instructed to remain silent, and there is no uncancelled microphone reference.
Operator speech-intelligibility, loudness and cracking feedback is pending.

The exact original Wi-Fi application was restored; its write hash verified and
Wi-Fi rejoined at 192.168.188.63, RSSI -44 dBm, XMOS unchanged at 1.0.8. The last
PA command was mute and acknowledged. Raw capture remains private; aggregate
metrics/config and hashes are retained in the experiment artifact directory.
