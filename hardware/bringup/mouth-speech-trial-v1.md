# Mouth-only speech trial

Updated 2026-09-09 at the operator's explicit request. This replaces the earlier
multi-step readiness procedure, typed RUN phrase and routine OFF acknowledgment.
The operator states Alice is small, connected, powered and ready, and keeps a
hand at the master switch. A direct request to run is sufficient for this
attended bench setup; do not ask the same readiness questions again.

## Run

1. Use `alice-jaw-trial --enable-hardware` for the requested physical trial.
   The default still verifies without devices; `--play` uses mock servos.
2. Automatically check Maestro serial 00037376/interface 00, free interfaces,
   error register and current jaw output. These are machine checks. If jaw PWM
   is disabled (output 0), enable only channel 6 at calibrated Home 5059, wait
   250 ms, and confirm output/error state before the supervised trajectory.
   This first PWM enable has no known mechanical starting position or velocity;
   record it separately. It changes no other output or firmware setting. Any
   positive output within the trial range can be used as the starting position
   after a second stable reading 100 ms later. Seed the supervisor with that
   measured position, not an assumed Home. Out-of-range or changing output stops.
3. Send only `mouth_open`, channel 6. All 11 semantic channels are wired through
   the original source manifest; later motion trials can add channels deliberately.
   No other channel targets are changed. The optional runtime response override
   below changes only jaw speed/acceleration and leaves EEPROM unchanged.
4. Ramp into the closed pose, play Azelma's 6.38-second clip once, then ramp Home.
   The operator approved the full mouth range on 2026-09-09. The initial
   `jaw-full-range-v1.json` uses -1 to +1, mapping to 4608–5440
   quarter-microseconds, spanning both sides of neutral 5059. Command caps remain
   0.1 per step, 2 normalized units/s, 4 units/s². The full-range profile
   waits 100 ms after each acknowledged output; the earlier profile used 40 ms. Endpoint convergence uses half a calibrated pulse
   unit before issuing the exact endpoint through the same limits. Entry/exit
   deadline 8 s each in the trial config; whole run 25 s. Firmware speed/acceleration remain 0/11.
5. Completion means controller Home confirmed and serial closed. Leave power
   management to the operator; no routine power-off or additional acknowledgment
   is required. Ask for perceived timing feedback after the trial.

## Stop and evidence

Ctrl-C, controller/audio faults, stale source frames, scheduling gaps and timeout
stop audio, revoke command authority and close serial. No automatic recovery move
is sent after a fault. The operator controls the master switch. Serial closure
alone does not remove servo power. The software watchdog is in the same process.

Retain the exact input/code/config/procedure hashes, audio sample timestamps,
commands and controller results. Logs do not assert fresh physical inspections,
actual servo pose or measured synchronization. The recorded supply is rated
6 V / 1 A; electrical margin remains unmeasured and is not a repeated readiness
gate. The speech runner itself opens no camera or microphone. At the operator
request, a separate C525 robot-camera observer can measure lip landmarks and
blendshapes during a trial. Do not open the human-facing C920 or a microphone. Readiness simplification does not alter calibration; the later full-range
profile uses the full previously calibrated mouth interval.

## Faster speech profile

The operator requested faster movement after observing the slow full-range run.
`jaw-speech-fast-v1.json` keeps channel 6 and the same -1…+1 absolute range,
but uses step cap 0.4, rate cap 10 normalized units/s, acceleration cap 200
units/s², 30 ms response smoothing and a 40 ms minimum interval between target writes. These are command
limits, not fitted mechanical response guarantees. Without the optional override, firmware speed/acceleration stays 0/11. This profile is an attended, camera-observed tuning trial; normal
supervisor checks, one in-flight command, timeout and fault/cancel handling remain.

## Streaming targets while the jaw moves

Use `--stream-targets` with the fast speech profile. Normal target updates send
channel 6's next bounded target and read the current output/error register once;
they do not wait for every intermediate target to settle. This matches the
working mimicry transport behavior. Receipts are explicitly `sent`, not APPLIED.
The lifecycle uses the supervisor's preflight/arming gate; a dedicated streaming
trajectory guard validates step/rate/acceleration, elapsed time, absolute range,
run duration and approval age before each write. It does not consume or fabricate
settled-output permits. Full calibration/expiry/channel/token checks also run at
the adapter boundary. Errors/disabled/out-of-range output stop the stream.

Only normal completion waits for observed controller Home. Audio remains on a
separate producer/DAC clock and uses the same latest-frame coalescing. The
existing settled adapter API and its APPLIED semantics remain unchanged.

## Runtime jaw speed/acceleration test

The device settings export on 2026-09-09 confirmed channel 6 speed 0 and
acceleration 11. Pololu defines zero as unlimited for both parameters. The user
requested a faster servo response. Add `--fast-jaw-response` to the hardware
`--stream-targets` invocation to set runtime speed 0 / acceleration 0 once the
jaw output is at Home 5059. This removes the additional firmware ramp; the
software's calibrated range, step, rate and acceleration limits still apply.
The setting commands are fixed to channel 6 (0x87/0x89); no EEPROM is written.
Normal completion restores the reviewed stored speed 0 / acceleration 11
on the serial-owner thread after Home confirmation, before serial closure.
A transport fault records that restoration could not be confirmed; it does not
try further writes through an ambiguous connection.

Streaming kinematics use host SetTarget write-start timestamps, separately from
readback/watchdog completion timestamps. The adapter rejects a planned command
more than 2 ms old immediately before writing, matching the planner's dispatch
margin. These timestamps do not measure USB delivery, PWM edges or physical
servo motion. Variable readback latency must not distort trajectory derivatives.
A cleanup error is retained without replacing the original failure.

Protocol reference: https://www.pololu.com/docs/0J40/5.e


## Mouth timing compensation

The 0/0 runtime trial completed with all immediate PWM readbacks matching targets.
The operator saw improvement with some remaining delay. `jaw-speech-lead-v1.json`
keeps the same motion caps and adds 100 ms aperture lookahead into retained PCM.
Only mouth aperture advances; current audio/affect/ownership timestamps and
expression lookup remain unchanged. Terminal lookahead returns closed aperture.
No lookahead bypasses cancellation, freshness checks or the motion limiter.
The default lead remains zero; this is an explicit tuning profile. Future
incremental speech requires at least this much PCM in its startup/rolling buffer,
without waiting for the complete LLM response or synthesized utterance.

Watchdog and fault closure perform no serial restoration transactions, preventing
interleaving with an in-flight exchange. Normal Home completion restores the
reviewed response profile on the serial owner; faults log non-restoration.
