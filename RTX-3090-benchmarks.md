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

## Running RIFE differently on the RTX 3090

The baseline for this comparison was the Standard model on `main` at `b2619f9`. The same PC exported three 12-second scenes near 24:00, 1:30:00 and 1:50:30 from the 3840×1586 film. Each export produced 576 frames at 2×, using 10-bit HEVC P5 at 15.5 Mb/s with AAC audio copied. The table sums the elapsed times with warm engines. Each scene used a different model order. A first export at a new size can also include engine compilation. [Published Standard and Lite weights](https://github.com/AmusementClub/vs-mlrt/releases/tag/external-models)

| Model and execution method | Time for 36 s of film | 4K VMAF against withheld film frames |
|---|---:|---:|
| Standard RIFE 4.25, mixed FP16/FP32 | 39.60 s | 87.29 |
| RIFE 4.25 Lite, same precision and full resolution | 36.47 s | 87.11 |
| Half-resolution motion estimation, native-resolution warp with FP32 coordinates | 36.53 s | 86.77 |
| Standard with 40 residual convolutions quantized to INT8 | 41.05 s | 81.99 |

The desktop app also exported the same 30-second scene with each selectable model. Standard took 29.55 s and Lite took 27.42 s. The app displayed 52.6 and 57.0 output frames/s respectively after startup. Both files contained 1,438 Main 10 video frames at 3840×1586 and 1,409 AAC packets.

To measure interpolation accuracy, every other film frame was withheld. Each model reconstructed the missing frames from their neighbors. The scores above use 72 generated frames before video encoding, in three short sequences per scene. Lite averaged 0.18 VMAF points below Standard. Its worst paired drop was 3.19 points. A crop of that frame showed a tightly spaced pattern of vertical ridges, with softer and displaced detail in Lite. In the ordinary 23.976 → 47.952 fps exports, 81 sampled generated frames from the encoded Lite output scored 99.97 on average against encoded Standard, with a minimum of 99.14. That second comparison measures agreement between models. Only the withheld frames provide a real reference for interpolation accuracy.

The INT8 experiment used symmetric activation scales calibrated on 36 frame pairs from separate timestamps, per-output-channel weight scales, and explicit ONNX quantize/dequantize operations. The motion heads and warping operations kept their existing precision. These results apply to that calibration and layer selection on this 3090. [TensorRT quantization documentation](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/inference-library/work-quantized-types.html)

The motion experiment ran the Standard model's flow and blend-mask prediction at half width and height, upsampled those predictions, and used them to warp and blend the original 4K RGB frames. Precision in the final sampling coordinates mattered. The first implementation scored 74.50 on the withheld frames. Explicit FP32 coordinate math and final sampling raised that to 86.77. The corrected version used about 5.1 GB of total reported GPU memory during export, versus about 7.0 GB with Lite, and scored 99.78 against the ordinary encoded Standard output.

With Lite, switching NVENC from P5 to P4 saved 0.39 seconds across the three exports combined. A 4 MiB Windows pipe saved 0.19 seconds on the single Lite scene tested. Across all three test segments, none of the 861 adjacent source-frame pairs had matching decoded-frame checksums, so an exact-duplicate shortcut would have skipped no inference on this sample.

On scene B, Lite's elapsed export times at parallel settings 1, 1.5 and 2 were 12.42, 12.69 and 12.80 seconds. Total GPU memory reported rose from 7.0 to 8.4 and 9.9 GB. The half-resolution motion prototype also finished fastest at 1 on this scene.

A separate 2.5× compatibility check used a 24-frame, 24 fps input. Both Standard and Lite produced 59 frames tagged at 60 fps. The bundled fractional scheduler stops after the first repeated final source frame. All 2× exports in the comparison produced the full expected frame count.

## Distributing work on the Ryzen 5950X and RTX 3090

The CPU decodes the source and prepares frames, TensorRT runs interpolation on the GPU, and NVENC encodes the result. At 3840×1586 and 47.952 fps, passing planar 10-bit output through a pipe moves about 876 MB of picture data per second of playback. The updated app lets FFmpeg read frames directly from VapourSynth in host RAM. It requests eight upcoming output frames at the default parallel setting, allowing the stages to overlap. FFmpeg still transfers frames to the GPU for encoding. [FFmpeg VapourSynth input implementation](https://github.com/FFmpeg/FFmpeg/blob/master/libavformat/vapoursynth.c)

These experiments used Lite, source resolution, 2×, P5 and warm engines unless indicated. Elapsed times include encoding and muxing except for the explicitly marked inference and hashing trials.

In the final app test, the same 30-second section of scene B was exported four times in direct, pipe, pipe, direct order. CPU use is the summed converter process-tree load expressed in logical cores, sampled after the first encoded frame. Host memory is the peak summed process working set during that interval. The app's fps measurement also starts at the first encoded frame.

| Final 30-second Lite runs | Elapsed | Output fps | Average logical cores | Peak host memory |
|---|---:|---:|---:|---:|
| Y4M pipe | 27.23 / 27.13 s | 57.5 / 57.7 | 11.58 / 11.74 | 6.18 / 6.16 GiB |
| Direct frames, eight requested ahead | 25.96 / 25.90 s | 61.1 / 61.1 | 7.19 / 7.01 | 6.72 / 6.71 GiB |

The direct path delivered 4.8% higher end-to-end throughput and used 39% less CPU on average. Each export had 1,438 Main 10 video frames and 1,409 AAC packets, with matching video presentation timestamps and audio packet timing. The Lite 12-second scene A export took 11.65 s versus 12.10 s through the pipe. Standard gained less on the 12-second scene B export, at 13.12 s versus 13.31 s. Sixteen queued frames gave a fast short run but took 27.08 s and 7.37 GiB in the 30-second test, so the app uses eight at the default parallel setting.

Across the two 30-second Lite comparisons, the direct output scored 99.94 and 99.95 mean 4K VMAF against the corresponding pipe output. Each comparison sampled the same 71 frame positions, mostly interpolated frames. The lowest score was 99.11. The 12-second scene A comparison averaged 99.86 over 32 positions with a minimum of 98.74. These scores measure agreement between encoded exports of the same model. A separate 2.5× HDR test with BM3D retained PQ and BT.2020 tags, 10-bit video, non-square pixels, the subtitle track, audio and a chapter.

| Alternative tested | Measured outcome |
|---|---|
| Two simultaneous workers splitting one 12-second clip on the 3090 | 16.22 s including joining, versus 14.81 s with one worker in the same harness. Rendering and hashing without encoding took 12.31 s versus 13.30 s. Both splits retained all 576 frames. |
| CPU inference alongside GPU work | A CPU-only ONNX Runtime FP32 trial using 12 inference threads produced 0.23 full-resolution Lite midpoints/s after warmup. |
| More CPU threads, decoder threads and prefetch on the pipe | 12.66 s on scene B with 24 VapourSynth threads, 12 decoder threads and 20 frames requested ahead, versus 12.35 s with the existing pipe settings. |
| One TensorRT stream | 14.48 s on scene B versus 12.35 s with two. |
| NVDEC through BestSource | With indexes cached, scene B improved from 12.35 to 11.80 s and scene A took 12.59 s versus 12.58 s. First-time indexing added about 23 s in these trials. |
| NVDEC and GPU color conversion feeding TensorRT, then downloading frames for FFmpeg | 29.91 s for 30 s of film versus 28.32 s through the original pipe. Average CPU use fell from 11.46 to 2.75 logical cores. Direct GPU-surface handoff to NVENC remains untested. |
| Half-resolution motion with a full-resolution warp and direct frame transport | 10.39 s versus 11.93 s for full-resolution Lite with the same transport on scene B. The earlier withheld-frame results above show the interpolation tradeoff. |

The two-worker experiment cut at output frame 283. Each worker could read the neighboring source frames, and the original-frame checksums matched the single-worker render. Of the generated frames, 195 had different checksums. Independently encoding the two pieces also restarted rate control. The joined output scored 99.32 mean 4K VMAF against the single-worker encode over 32 sampled frames, with a minimum of 93.31.

## Optimization backlog for native 4K

These are proposed experiments. The comparison point is the local 3090 running Lite, source resolution, 2×, P5 and direct frame transport. Start with a trace, then choose between the first two engineering experiments below according to where the export spends its time.

| Priority | Candidate | How to decide |
|---|---|---|
| First measurement | Trace a short clip and a sustained export. Time indexing, engine loading, decoding, color conversion, PCIe transfers, inference, encoder waits and muxing. Record source and output fps, clock speed, temperature, power and other GPU activity. | Compare the same scene, model, cleanup, multiplier and encoder. Log time to first frame, steady fps and total elapsed separately. This gives a future 0.75× to 0.37× drop a diagnostic trail. [NVIDIA performance measurement guide](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/performance/measurement-techniques.html) |
| First model experiment | Cache each source frame's features in VRAM. In consecutive pairs A–B and B–C, frame B enters the Lite model's four-layer image encoder twice. Extract that subgraph and retain B's features for the next pair. Higher multipliers can also reuse features for several timepoints. | Profile the four layers first. Benchmark the saved computation against extra VRAM traffic, engine calls and possible loss of TensorRT fusion. Compare generated frames before encoding. |
| First transport experiment | Feed GPU-resident 10-bit P010 surfaces to NVENC. Start with the encoder handoff alone. Keep the same P5 options, timestamps and FFmpeg track muxing. | Compare the final MKV time against direct VapourSynth. The previous GPU prototype still downloaded frames and lost on wall time. Validate chroma location, 10-bit range, SDR and HDR ramps. [NVIDIA encoder resource guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-video-encoder-api-prog-guide/index.html) |

| Part of the process | Other experiments worth testing | Where they could pay off or change the picture |
|---|---|---|
| Opening a movie | Cache the stream and HDR probe results by source identity and modification time. Cache the one-frame split-encoder capability probe by GPU, driver, FFmpeg build and output format/size. | Repeated samples and short exports benefit most. Keep source-index and model-engine reuse, both already present. |
| Starting a batch | Index the next queued movie on the CPU while the current one renders. Try a persistent render worker for consecutive jobs of the same model and shape. | Measure queue completion and disk contention. A persistent worker must release the prior movie's frames, filters and metadata. |
| Reading and decoding | Try NVDEC on long or CPU-heavy sources. On slow network storage, compare direct reading with staging the compressed source on a local SSD. | Include staging and cold indexing in the timer. The previous hardware-decoder test had mixed warm results and roughly 23 s of initial indexing. |
| Preparing frames | Reuse decoded neighbor frames and packed model inputs on the GPU. For supported formats, test one kernel for color conversion, padding and writing the input tensor. | Verify matrix, range, chroma siting, dither and edge pixels. Preserve the original YUV frames for integer-rate output. |
| CPU, RAM and PCIe | Tune decoder threads, TensorRT streams, prefetch depth and cache budget independently. Where host transfers remain, try a bounded ring of reusable pinned buffers with asynchronous copies. | The current Parallel setting couples several controls. Keep eight frames ahead as the baseline and measure stalls, peak memory and complete export time. [NVIDIA transfer measurement guide](https://docs.nvidia.com/deeplearning/tensorrt/10.x.x/performance/hw-sw-environment.html) |
| TensorRT execution | Try a fixed midpoint engine for 2× with the timepoint fixed at 0.5. Inspect the compiled engine for remaining grid construction, tensor reformatting and launch gaps. Try two adjacent pairs per batch only if the trace shows spare execution capacity. | Compare engine build cost, steady throughput, memory and output against the current two-stream engine. Some constant operations may already be folded. |
| Fractional rates | Generate frame indices and timepoints in bounded windows for long 2.5× and exact-60-fps exports. The bundled fractional scheduler builds four movie-length Python lists. | Targets startup and RAM. Match the existing rational timestamps, scene-cut behavior, frame count and final-frame convention. |
| Scene cuts and repeats | Verify that a detected cut really avoids a model invocation. For animation, slides and freezes, test an exact-repeat fast path after enabled cleanup, using all planes and confirming equality on hash hits. | Measure the fraction of eligible pairs and the cost of detection. The earlier film sample had zero exact repeats in 861 pairs. Avoid approximate freezes on slow pans, grain and fades. |
| Cleanup filters | For DPIR, sweep tile size and overlap and reuse buffers. For BM3D or NLMeans, profile a cleanup-enabled film separately and compare CPU and GPU implementations only at matched settings. | Only exports with cleanup enabled can benefit. Check temporal neighbors, tile seams, texture and grain at native resolution. |
| Black bars | For sources with encoded letterboxing, test computing motion only in the active picture, with a border for context, then restore the complete original canvas. | Conditional, with visual risk at the boundary. Check subtitles, overlays and changes of aspect ratio. The measured 3840×1586 film is already cropped. |
| Model precision | Profile the expensive layers before trying selective lower precision. Keep motion coordinates, warps and blending at validated precision. Also test CUDA graph capture independently of half-precision I/O on the HDR path. | Separate scheduling gains from precision changes. The earlier 40-convolution INT8 trial was slower and lost over five withheld-frame VMAF points versus Standard. |
| Adaptive interpolation | Try half-resolution motion estimation with a full-resolution warp on easier shots and full-resolution inference on difficult shots. Use a consistent choice across a shot. | Highest measured speed opportunity involving a picture tradeoff. Test hair, railings, occlusions, credits and pans. Score the worst shots and inspect switches in motion. |
| HEVC encoding | If the encoder is limiting throughput, try 16-frame lookahead against the current 32 while retaining temporal AQ, P5 and the same bitrate. | Check grain, dark scenes, file size and bitrate spikes. P4 saved only 0.39 s across three earlier clips, so it is a low priority. |
| Muxing, disk writes and preview | Try buffered writes for unattended exports and retain frequent flushing while watching a growing file. Measure with preview both open and closed. | Greatest potential on slow disks or shares. Preserve sparse-subtitle interleaving, track timing, chapters and seekability. |
| Several GPUs or PCs | Assign whole movies to separate GPUs first. For a single long movie, prototype scene-aligned frame ranges with neighboring source frames for RIFE and temporal cleanup. Stage the compressed source once per worker and mux original tracks centrally. | Measure transfer, slowest-worker and join time. Verify frame ordering, timestamps and quality around every join. Two workers sharing the tested 3090 already lost time. |
| Interrupted jobs | For long exports, test resumable completed segments with a manifest keyed by source and settings. | Saves repeated work after interruption. Measure the cost of restarting encoder rate control at segment boundaries and inspect the joins. |

### How to judge a candidate

Use identical source frames and settings, alternate execution order and record cold and warm timings. Include a sustained run with the GPU at operating temperature. Use the existing baseline's run-to-run variation to judge small gains. Track first-frame delay, full-export wall time, steady output fps, CPU consumption and peak RAM/VRAM separately.

For scheduling and transport changes, compare original and generated frames separately before encoding and check the finished MKV's presentation timestamps, audio, subtitles, chapters and color metadata. For model or precision changes, withhold real frames and reconstruct them. Record average and worst-scene quality, then watch those scenes at native size and playback speed. Include full-height 3840×2160 material, HDR gradients, grain, fast motion, fades and credits alongside the cropped SDR film used for the current speed baseline.

## General update and Lite green-frame verification

With the green-frame check enabled, a 30-second 3840×1586 Lite export took 25.20 and 25.27 s. The previous published Y4M build at `1dceb17` took 28.00 and 27.38 s on the same input. The finished files matched on video presentation timestamps and AAC packet bytes and timestamps. Each contained 1,438 video frames and 1,409 audio packets. The test used warm TensorRT engines, 2× and P5. The updated output scored 99.96 and 100.00 mean 4K VMAF against the prior build across 71 sampled positions per run.

Across five checks, the median media probe took 38 ms before caching and 0.32 ms when reopening the same file. The median NVENC split-mode capability probe took 320 ms. Subsequent exports at that resolution reused the in-session result. Separate tests covered audio-only, subtitle-only, combined audio and subtitles, and video-only inputs. They compared copied audio, subtitles, attachments, chapter metadata, video timestamps and HDR tags.

The reported five-second 4K clip had 114 affected frames, all at interpolated timestamps. Of those, 112 were almost entirely green and two had smaller green patches. Fresh Lite exports of the same source opening came out clean through both frame transports. Applying the production detection and frame-selection logic to the decoded faulty sample replaced all 114 frames with their preceding source frames. The other 128 frames matched pixel for pixel. Synthetic checks also covered fractional timing and valid saturated green and black frames.
