# 40% facial-servo calibration sweep

- Date: 2026-08-28
- Controller: Mini Maestro 12, serial `00037376`
- Status: approved by operator; staged one channel at a time
- Purpose: visually identify movement and confirm stored ranges without touching endpoints

## Preconditions

- Operator confirms Alice is clear of obstructions and can disconnect servo power immediately.
- Controller baseline reports all mapped channels at Home and error register `0x0000`.
- Skip channel 7: the operator confirmed it is physically disconnected. Channels 0–1 were initially thought unused, then the operator identified them as neck/head axes and explicitly added them to the sweep.
- Never run another serial/ROS actuator process concurrently.

## Motion profile

For each channel, move Home → Lower → Home → Upper → Home. Lower and Upper are 40% of the respective Home-to-limit distance, rounded to the nearest quarter-microsecond. Temporarily set runtime speed to `5` and acceleration to `2`. Restore the exported runtime speed and acceleration after the channel returns Home.

| Channel | Firmware Min | Lower 40% | Home | Upper 40% | Firmware Max | Restore speed/accel |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 4928 | 5859 | 6480 | 7011 | 7808 | 20 / 25 |
| 1 | 3712 | 5089 | 6007 | 6804 | 8000 | 25 / 25 |
| 2 | 3712 | 5949 | 7440 | 7997 | 8832 | 25 / 25 |
| 3 | 2880 | 4528 | 5626 | 5936 | 6400 | 0 / 0 |
| 4 | 3840 | 5240 | 6173 | 6597 | 7232 | 0 / 0 |
| 5 | 4032 | 5164 | 5918 | 6188 | 6592 | 0 / 0 |
| 6 | 4608 | 4879 | 5059 | 5211 | 5440 | 0 / 11 |
| 8 | 5632 | 5853 | 6000 | 6058 | 6144 | 50 / 10 |
| 9 | 5120 | 5947 | 6499 | 6664 | 6912 | 50 / 10 |
| 10 | 5696 | 5878 | 6000 | 6160 | 6400 | 50 / 10 |
| 11 | 5120 | 5362 | 5524 | 6079 | 6912 | 50 / 10 |

## Abort and verification

- On Ctrl+C or command failure, command every tested channel back to its stored Home and restore its exported runtime speed/acceleration.
- Poll Maestro status until reported position equals target; do not advance on timeout.
- Check the error register after each leg and stop on any nonzero value.
- Record observed facial part/direction, unexpected noise, binding, or absent movement per channel.
- This procedure changes runtime targets/speed/acceleration only; it does not write persistent firmware configuration.

## Observations

- Channel 0: neck rotation confirmed; sweep completed, returned Home, restored speed/acceleration `20/25`, errors `0x0000`.
- Channel 1: head-tilt gimbal confirmed; slightly janky at slow speed but otherwise accepted by operator; returned Home, restored speed/acceleration `25/25`, errors `0x0000`.
- Channel 2: face up/down; 40% sweep completed cleanly, returned Home, errors `0x0000`.
- Channels 3, 4, 5, 6, 8, and 9: controller sweeps completed, returned Home, restored exported speed/acceleration, errors `0x0000`; physical functions awaiting operator identification.
- Channel 10: lower leg commanded to `5878`, but reported position oscillated from `5881` to `5888` and did not meet the procedure's exact-equality criterion before timeout. Abort handling returned it to Home `6000`, restored speed/acceleration `50/10`, and status reported errors `0x0000`. Upper leg was not attempted.
- Channel 11: not attempted because the procedure stopped at channel 10.

## Higher-amplitude mapping pass

- Channel 3 at 60%: both lower eyelids confirmed; returned Home, errors `0x0000`.
- Channel 4 at 60%: both upper eyelids confirmed; returned Home, errors `0x0000`.
- Channel 5 at 60%, repeated once: movement was initially difficult to identify; both passes returned Home with errors `0x0000`.
- Channel 5 at 90%: forehead frown actuator confirmed; returned Home, restored speed/acceleration `0/0`, errors `0x0000`.
- Channel 6 at 60%: mouth opening through chin up/down confirmed; returned Home, restored speed/acceleration `0/11`, errors `0x0000`.
- Channel 8 at 60%, repeated once: right-eye horizontal movement confirmed; returned Home, restored speed/acceleration `50/10`, errors `0x0000`.
- Channel 9 at 60%, then 90%: left mouth corner confirmed. Initial isolated visual interpretation was later corrected by paired-expression testing: decreasing target lowers the corner (frown); increasing target raises it (smile). Returned Home, restored speed/acceleration `50/10`, errors `0x0000`.
- Channel 10 at 60%, then three 90% passes: initially no physical motion despite valid pulse output. Operator manually freed a mechanical jam; left-eye horizontal movement then confirmed. Returned Home, restored speed/acceleration `50/10`, errors `0x0000`. Inspect linkage before future unattended use.
- Channel 11 at 60%, then 90%: right mouth corner confirmed. Initial isolated visual interpretation was later corrected by paired-expression testing: decreasing target raises the corner (smile); increasing target lowers it (frown), the opposite polarity from channel 9. Returned Home, restored speed/acceleration `50/10`, errors `0x0000`.
- Channel 8 direction pass at 90%: right eye; decreasing target looks right, increasing target looks left. Returned Home, restored `50/10`, errors `0x0000`.
- Channel 10 direction pass at 90% after jam was freed: left eye; same polarity as channel 8. Returned Home, restored `50/10`, errors `0x0000`.

## Paired mouth-expression tests

Channels 6, 9, and 11 were coordinated at temporary runtime speed `5` and acceleration `2`, with Home between expressions and automatic Home/setting restoration on exit.

1. The first 75% pairing used the isolated corner interpretations. It produced an open-mouth frown (“astonished”) followed by a closed-mouth smile (“silly smirk”), proving that both corner polarities had been recorded backward.
2. A corrected 75% pairing produced the intended corner directions, but the closed-mouth pose was not closed enough.
3. The final operator-approved 100% firmware-limit pairing held each pose for five seconds and was visually accepted:
   - Smile/open: channel 6 `5440`, channel 9 `6912`, channel 11 `5120`.
   - Frown/closed: channel 6 `4608`, channel 9 `5120`, channel 11 `6912`.
   - Home: channel 6 `5059`, channel 9 `6499`, channel 11 `5524`.

Every trial returned all three channels Home, restored channel 6 to speed/acceleration `0/11` and channels 9/11 to `50/10`, and ended with controller errors `0x0000`.
