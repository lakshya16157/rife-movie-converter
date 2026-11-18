# RIFE Movie Converter

A Windows desktop app for generating intermediate video frames with RIFE 4.25 on an NVIDIA GPU. It exports a 10-bit HEVC MKV, copies audio and subtitles, and can play the growing export in mpv.

1. Install [64-bit Python 3.12](https://www.python.org/downloads/release/python-31210/), [7-Zip](https://www.7-zip.org/), and a current NVIDIA driver.
2. Clone or download this repository. Run **Set Up RIFE.cmd** once to download the model, FFmpeg, mpv and the GPU runtime.
3. Open **Open RIFE Movie Converter.cmd**, choose a movie, and try **Export first 30 sec**.

See [SETUP.md](SETUP.md) for GPU requirements, download sizes, and troubleshooting. Conversion and playback work offline after setup.

## Export settings

- **Source**, **1440p**, **1080p**, and **720p** control the export resolution. Downsampling uses VapourSynth Spline36 before inference and preserves the aspect ratio.
- **Frame multiplier** defaults to **2**: a 23.976 fps movie becomes 47.952 fps. Type any whole number from 2 to 32 (for example 6 or 10), or pick **60 fps**. Higher multipliers take longer and need more bitrate; the app shows a bitrate guide under the estimate. RIFE skips interpolation across detected cuts.
- **Auto** uses a video bitrate based on the resolution. Enter a number to override it. The app shows an estimated size before export.
- **Grain reduction** (Off, Light, Medium, Strong) denoises before interpolation so real and generated frames match. **Method** picks **Fast (NLMeans)** or the slower, more detail-preserving **Best (BM3D)**.
- **Deblocking** (Off by default) runs the DPIR model on the GPU to repair blocky compression damage in low-bitrate sources. It slows exports and builds its own TensorRT engine on first use.
- The app supports progressive, constant-frame-rate SDR, HDR10, and HLG. Supported Dolby Vision Profile 5 and 8 video is mapped to HDR10. Profile 7 uses its HDR10 base layer.

## Watch while exporting

Click **Watch export** to play the growing file in mpv over a local connection. If playback catches up with the export, it buffers. The app estimates how much of the movie should be ready before you start. Reopen the finished MKV for normal seeking.

## Upstream projects

[RIFE](https://github.com/hzwer/Practical-RIFE), [vs-mlrt](https://github.com/AmusementClub/vs-mlrt), [VapourSynth](https://www.vapoursynth.com/), [FFMS2](https://github.com/FFMS/ffms2), [Akarin](https://github.com/AkarinVS/vapoursynth-plugin), [vszip](https://github.com/dnjulek/vapoursynth-zip), [FFmpeg](https://ffmpeg.org/), [mpv](https://mpv.io/), and [NVIDIA TensorRT](https://developer.nvidia.com/tensorrt).
