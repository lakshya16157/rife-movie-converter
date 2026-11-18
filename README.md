# RIFE Movie Converter

A Windows desktop app for generating intermediate video frames with RIFE 4.25 on an NVIDIA GPU. It exports a 10-bit HEVC MKV, copies audio and subtitles, and can play the growing export in mpv.

This RTX 3090 build defaults to source resolution, 2× interpolation, and both cleanup filters off. See the [RTX 3090 benchmark results](RTX-3090-benchmarks.md) for the tested settings and quality comparison.

1. Install [64-bit Python 3.12](https://www.python.org/downloads/release/python-31210/), [7-Zip](https://www.7-zip.org/), and a current NVIDIA driver.
2. Clone or download this repository. Run **Set Up RIFE.cmd** once to download the model, FFmpeg, mpv and the GPU runtime.
3. Open **Open RIFE Movie Converter.cmd**, choose a movie, and try **Export first 30 sec**.

See [SETUP.md](SETUP.md) for GPU requirements, download sizes, and troubleshooting. Conversion and playback work offline after setup.

## Export settings

- **RIFE model** defaults to **Standard**. **Lite** uses the RIFE 4.25 Lite weights at the selected output size. To keep 4K, leave **Resolution** on **Source**. Existing installations can get the weights by running **Set Up RIFE.cmd** once.
- Lite checks generated frames for patches of zeroed color samples that can cause green flicker. If it finds that pattern, it uses the preceding source frame at that timestamp and records the replacement in the export log.
- **Source**, **1440p**, **1080p**, and **720p** control the export resolution. Downsampling uses VapourSynth Spline36 before inference and preserves the aspect ratio.
- **Frame multiplier** defaults to **2**: a 23.976 fps movie becomes 47.952 fps. Type any number above 1, up to 32 (2.5 turns 23.976 fps into 59.94), or pick **60 fps**. Whole numbers keep every original frame; other values regenerate most frames. Higher multipliers take longer and need more bitrate; the app shows a bitrate guide in the settings table. RIFE skips interpolation across detected cuts.
- **Auto** uses a video bitrate based on the resolution. Enter a number to override it. The app shows an estimated size before export.
- **Grain reduction** (Off by default; Light, Medium, Strong, or a typed strength from 0 to 20) denoises before interpolation so real and generated frames match. **Method** picks **Fast (NLMeans)** or the slower, more detail-preserving **Best (BM3D)**.
- **Deblocking** (Off by default; Light, Medium, Strong, or a typed strength from 0 to 100) runs the DPIR model on the GPU to repair blocky compression damage in low-bitrate sources. It slows exports and builds its own TensorRT engine on first use.
- **Encoder** picks the NVENC preset: **Fast (P5 single-pass)**, tuned for single-encoder cards such as the RTX 3090, or **Best (P7 two-pass)**, which costs almost nothing on cards with two encoder chips. On first start the app times the card's encoder for a few seconds and picks the default; the answer is cached in `runtime/cache/gpu-defaults.json`.
- **Parallel processing** scales how many frames are worked on at once. 1 is the tested default; lower it if memory runs out.
- With the current runtime, the CPU decodes and prepares upcoming frames in RAM while TensorRT interpolates and NVENC encodes. FFmpeg reads the VapourSynth frames directly. Each export log identifies the frame transport used.
- The app reuses media and encoder probe results during a session. It uses separate source readers when both audio and subtitles are present, so sparse subtitles cannot pull audio ahead of the video during export.
- The app supports progressive, constant-frame-rate SDR, HDR10, and HLG. Supported Dolby Vision Profile 5 and 8 video is mapped to HDR10. Profile 7 uses its HDR10 base layer.

## Performance table

Each export adds a row with the GPU, output size, settings, output fps, playback speed, megapixels per second, average GPU load, Task Manager's 3D load, encoder load, peak video memory, and the encoder preset. Timing starts at the first encoded frame, so engine builds do not count. Megapixels per second compares runs at different resolutions. Rows are saved to `runtime/benchmarks.csv`; copy that file between PCs to compare GPUs.

Exports use both NVENC chips on cards that have two, after a one-frame probe confirms the card accepts split encoding.

The export log records time to first encoded progress and total render and encode time. The performance table shows the rate after startup.

## Watch while exporting

Click **Watch export** to play the growing file in mpv over a local connection. If playback catches up with the export, it buffers. The app estimates how much of the movie should be ready before you start. Reopen the finished MKV for normal seeking.

## Upstream projects

[RIFE](https://github.com/hzwer/Practical-RIFE), [vs-mlrt](https://github.com/AmusementClub/vs-mlrt), [VapourSynth](https://www.vapoursynth.com/), [FFMS2](https://github.com/FFMS/ffms2), [Akarin](https://github.com/AkarinVS/vapoursynth-plugin), [vszip](https://github.com/dnjulek/vapoursynth-zip), [FFmpeg](https://ffmpeg.org/), [mpv](https://mpv.io/), and [NVIDIA TensorRT](https://developer.nvidia.com/tensorrt).
