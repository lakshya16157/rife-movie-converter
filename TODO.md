# To-do ideas

Written 2026-11-21, after the Fast model landed in `e63c096`. Numbers are from an RTX 5070 Ti with a Ryzen 7 9800X3D at 3840×1586 unless marked as an estimate.

## 1. Get the RTX 3090 above realtime with Fast

Nothing below has run on the 3090 yet. The estimates assume it is 30–40% slower than the 5070 Ti at interpolation, and that its encoder tops out near 75 fps at 4K P5 (it showed 67% load at 50 fps).

| 3090, Fast, 4K | Interpolation can supply | Encoder can take | Likely result |
|---|---|---|---|
| 3× (72 fps) | about 80 fps | about 75 fps | about 1.0× |
| 5× (120 fps) | about 83 fps | about 75 fps | about 0.65× |

- [ ] On the 3090, run **Export first 30 sec** with Fast at 3× and 5× and record the stats rows. The Encoder % column decides the next step.
- [ ] If Encoder % is near 100, add a faster encoder choice (P1 to P3 single-pass) and measure it on the 3090. On the 5070 Ti one encoder chip alone took 151 fps at P5 and 159 at P1, so this card cannot show whether the preset helps.
- [ ] If the GPU is the limit instead, try quarter-size motion (`fast=4` in the job). It scored 76.4 VMAF on withheld frames against 82.98 for half-size, so treat it as a last resort.

## 2. Realtime playback with play, pause and seek

Build this on mpv. The mpv in `runtime/mpv` already lists a VapourSynth filter, so it can run the Fast engine live. VLC has no such hook and would need a native C plugin with its own TensorRT integration.

Playback has no encoder, which is the 3090's expected weak spot. Without encoding the 5070 Ti ran 5× at 145 fps; scaled down, the 3090 would reach roughly 105 to 110 fps. That is short of 120 but enough for 3× and 2.5×.

- [ ] Write a playback script that reads mpv's `video_in`, runs the Fast engine and returns the clip. vsmlrt's `RIFE(video_player=True)` shows the scheduling a player needs.
- [ ] Pick the rate from the display: 2.5× for a 60 Hz screen, 5× for 120 Hz. Frames above the refresh rate are wasted.
- [ ] Add a **Play with frame generation** button that starts mpv with the filter.
- [ ] Measure seek delay and dropped frames. Engines must be built before playback starts, since a first build takes minutes.
- [ ] Check HDR sources. Their path uses full-precision frames and ran 3× at only 49 fps in a short test.

## 3. Borrow more from how televisions do it

Televisions reach 120 fps with a dedicated chip in the display path. What they do, and what Fast already copies:

| Television technique | In Fast today |
|---|---|
| Frames never leave the chip: no copies to RAM, no upload, download or encode | No. Copying, not GPU maths, capped Fast near 110 fps |
| Block matching with a few candidate vectors per block, a few operations per pixel | No. RIFE spends thousands of operations per pixel |
| Motion found on a small brightness-only picture, applied to the full picture | Yes, at half size, on RGB |
| One motion field per frame pair, scaled for each in-between frame | No. Motion is estimated again for every in-between frame |
| Blend or repeat frames where vectors look unreliable | Only at detected scene cuts |

- [ ] Reuse one motion field per source pair for all in-between frames. Expect halos at moving edges, as on televisions. Measure against withheld frames before keeping it.
- [ ] Cut the copies: keep decoded frames, the engine and the encoder input on the GPU. An earlier NVDEC prototype that still downloaded frames lost on wall time, so the encoder handoff is the part to prove first.
- [ ] Move the 10-bit colour conversion into the engine so frames cross system RAM once.

## 4. Known problems

- [ ] **Lite on the RTX 5070 Ti.** 2,842 of 2,880 generated frames came back invalid and were replaced by the preceding source frame, so its exports are mostly duplicated frames and its speed figures on that card mean nothing. Find the failing layer (fp16 overflow is the suspect) or hide Lite on affected cards.
- [ ] **Fast memory use.** Exports peaked at 8.6 to 10.8 GiB of RAM against 6.6 for Standard. A 6 GB frame cache dropped 5× from 145 to 113 fps without encoding. Check behaviour on a 16 GB machine.
- [ ] **Fast above 6× and at fractional rates** runs one frame per GPU call. 2.5× reached 70 fps; the whole-multiplier path reaches about 110.
- [ ] **Fast below 1080p** is untested. The half-size motion picture gets small there.
- [ ] **Orphaned engines.** Each size and multiplier writes its own model and engine files under `runtime/plugins/models/rife_v2`. Nothing removes old ones.
