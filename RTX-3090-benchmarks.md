# RTX 3090 export benchmarks

This Windows build was tested with an RTX 3090 (24 GB), a Ryzen 9 5950X, and the runtime used by upstream commit `a16c08f`. The desktop shortcut is named **RIFE Movie Converter - RTX 3090**.

## Method

Two clips from the same Blu-ray encode of *Troy*, taken near 00:24:00 and 01:30:00, were remuxed without re-encoding. Both were 3840×1586, BT.709, at 24000/1001 fps. Tests used the source resolution, RIFE 4.25 at 2×, 10-bit HEVC, a 15.5 Mb/s video target, copied AAC audio, and disabled cleanup filters.

The timing covers the complete export, including worker startup and writing the MKV. The final comparison uses cached TensorRT engines and source indexes. Compiling an engine for a new frame size can add time to the first export. A speed of 1.00× means one second of movie exported per second of wall time.

For the quality comparison, every seventh frame of each 12-second segment was saved losslessly from the upstream renderer before encoding. This samples both original and interpolated frames. The decoded exports were compared to these same 83 reference frames per scene with SSIM and the 4K VMAF model `vmaf_4k_v0.6.1`.

## Final 30-second exports

The actual upstream and tuned app builds each exported 1,438 video frames per scene plus AAC audio. Scene A ran upstream first, and scene B ran the tuned build first.

| Scene | Upstream time | RTX 3090 time | Upstream playback speed | RTX 3090 playback speed |
|---|---:|---:|---:|---:|
| A | 40.25 s | 31.22 s | 0.745× | 0.961× |
| B | 41.00 s | 30.22 s | 0.731× | 0.992× |
| Combined | **81.25 s** | **61.44 s** | **0.738×** | **0.976×** |

The tuned build used **24.4% less wall time** for the same output, a **32.2% throughput increase**. Output sizes were 59.61 and 62.46 MB, compared with 59.25 and 62.70 MB upstream. Results apply to these two scenes and this GPU.

## Selected 12-second trials

| Configuration | Scene A wall time | Scene B wall time | Scene A SSIM | Scene B SSIM |
|---|---:|---:|---:|---:|
| Upstream P7, full-resolution multipass | 17.44 s | 17.33 s | 0.993852 | 0.994935 |
| P6 single-pass | 16.94 s | 16.66 s | 0.993775 | 0.994736 |
| P6 single-pass with CUDA graphs and FP16 RGB input/output | 13.83 s | 13.88 s | 0.993775 | 0.994736 |
| P7 full-resolution multipass with CUDA graphs and FP16 RGB input/output | 17.22 s | 17.25 s | 0.993852 | 0.994935 |
| P5 single-pass with CUDA graphs, FP16 RGB input/output, temporal AQ and 32-frame lookahead | 13.17 s | 13.20 s | 0.993726 | 0.994690 |
| Same P5 configuration with temporal AQ and lookahead disabled | 12.62 s | 12.47 s | 0.993702 | 0.994494 |

The tuned renderer uses two TensorRT streams and eight prefetched frames. Testing three streams, twelve prefetched frames, or a larger Windows pipe buffer yielded little benefit. The FP16 RGB input/output path is enabled for BT.709 2× exports. Encoding uses 10-bit HEVC.

Raising the TensorRT builder optimization level from 3 to 5 took over six minutes for a new 4K engine. With otherwise identical P6 settings, scene A changed from 13.83 to 13.80 seconds and scene B from 13.88 to 13.84 seconds. Level 3 is used in the launcher.

## Quality of the deployed preset

| 4K VMAF | Upstream | RTX 3090 launcher | P5 with temporal AQ and lookahead off |
|---|---:|---:|---:|
| Scene A | 99.765 | 99.730 | 99.676 |
| Scene B | 98.532 | 98.465 | 98.487 |
| Mean across all 166 sampled frames | 99.148 | 99.098 | 99.082 |
| Lowest sampled frame | 95.998 | 94.827 | 94.316 |

The largest paired VMAF drop for an individual sampled frame was 2.0 points. The launcher keeps both spatial and temporal adaptive quantization and 32-frame lookahead. Its scene B export was 24.43 MB versus 24.47 MB upstream. The two local MKVs in the **Benchmark samples** folder can be viewed at native resolution for a visual comparison.
