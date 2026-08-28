# Alice motor map

Observed physical functions. Direction polarity is recorded only where both target directions were visually confirmed.

| Maestro channel | Physical function | Evidence |
|---:|---|---|
| 0 | Neck rotation | Operator confirmed during 40% sweep on 2026-08-28 |
| 1 | Gimbal left/right used to tilt head | Operator confirmed during 40% sweep on 2026-08-28; slightly janky at slow speed |
| 2 | Face up/down | Operator observation during 40% sweep on 2026-08-28 |
| 3 | Lower eyelids, both eyes | Operator corrected/confirmed during 60% sweep on 2026-08-28 |
| 4 | Upper eyelids, both eyes | Operator confirmed during 60% sweep on 2026-08-28 |
| 5 | Forehead frown actuator | Operator confirmed during 90% sweep on 2026-08-28 |
| 6 | Mouth opening; chin up/down | Operator confirmed: decreasing target closes mouth/chin up, increasing target opens mouth/chin down |
| 8 | Right-eye horizontal movement | Operator confirmed: decreasing target looks right, increasing target looks left |
| 9 | Left mouth corner, vertical smile/frown | Paired-expression test corrected polarity: decreasing target lowers corner (frown), increasing target raises corner (smile) |
| 10 | Left-eye horizontal movement | Operator confirmed: decreasing target looks right, increasing target looks left; linkage was manually freed from a jam during mapping |
| 11 | Right mouth corner, vertical smile/frown | Paired-expression test corrected polarity: decreasing target raises corner (smile), increasing target lowers corner (frown) |

All connected channels have an identified physical function. Both eye-horizontal servos share polarity: decreasing target looks right and increasing target looks left. The mouth-corner servos have opposite electrical polarity so their expression targets move numerically in opposite directions. Channels 3 and 4 move together to close the eyes; coordinated motion may also create an apparent upward gaze and requires a later paired-motion experiment. The operator confirmed channel 7 is physically disconnected; it is not in the ROS motor map and its firmware Home mode is Off.
