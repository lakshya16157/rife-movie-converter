# RIFE Movie Converter

A Windows desktop app for generating intermediate video frames with RIFE 4.25 on an NVIDIA GPU. It exports a 10-bit HEVC MKV, copies audio and subtitles, and can play the growing export in mpv.

1. Install [64-bit Python 3.12](https://www.python.org/downloads/release/python-31210/), [7-Zip](https://www.7-zip.org/), and a current NVIDIA driver.
2. Clone or download this repository. Run **Set Up RIFE.cmd** once to download the model, FFmpeg, mpv and the GPU runtime.
3. Open **Open RIFE Movie Converter.cmd**, choose a movie, and try **Export first 30 sec**.

See [SETUP.md](SETUP.md) for GPU requirements, download sizes, and troubleshooting. Conversion and playback work offline after setup.

## Export settings

- **Source**, **1440p**, **1080p**, and **720p** control the export resolution. Downsampling uses VapourSynth Spline36 before inference and preserves the aspect ratio.
- **Frame multiplier** defaults to **2**: a 23.976 fps movie becomes 47.952 fps. Type any number above 1, up to 32 (2.5 turns 23.976 fps into 59.94), or pick **60 fps**. Whole numbers keep every original frame; other values regenerate most frames. Higher multipliers take longer and need more bitrate; the app shows a bitrate guide in the settings table. RIFE skips interpolation across detected cuts.
- **Auto** uses a video bitrate based on the resolution. Enter a number to override it. The app shows an estimated size before export.
- **Grain reduction** (Off by default; Light, Medium, Strong, or a typed strength from 0 to 20) denoises before interpolation so real and generated frames match. **Method** picks **Fast (NLMeans)** or the slower, more detail-preserving **Best (BM3D)**.
- **Deblocking** (Off by default; Light, Medium, Strong, or a typed strength from 0 to 100) runs the DPIR model on the GPU to repair blocky compression damage in low-bitrate sources. It slows exports and builds its own TensorRT engine on first use.
- **Parallel processing** scales how many frames are worked on at once. 1 is the tested default; lower it if memory runs out.
- The app supports progressive, constant-frame-rate SDR, HDR10, and HLG. Supported Dolby Vision Profile 5 and 8 video is mapped to HDR10. Profile 7 uses its HDR10 base layer.

## Performance table

Each export adds a row with the GPU, output size, settings, output fps, playback speed, megapixels per second, average GPU and encoder load, and peak video memory. Timing starts at the first encoded frame, so engine builds do not count. Megapixels per second compares runs at different resolutions. Rows are saved to `runtime/benchmarks.csv`; copy that file between PCs to compare GPUs.

Exports use both NVENC chips on cards that have two, after a one-frame probe confirms the card accepts split encoding.

## Watch while exporting

Click **Watch export** to play the growing file in mpv over a local connection. If playback catches up with the export, it buffers. The app estimates how much of the movie should be ready before you start. Reopen the finished MKV for normal seeking.

## Upstream projects

[RIFE](https://github.com/hzwer/Practical-RIFE), [vs-mlrt](https://github.com/AmusementClub/vs-mlrt), [VapourSynth](https://www.vapoursynth.com/), [FFMS2](https://github.com/FFMS/ffms2), [Akarin](https://github.com/AkarinVS/vapoursynth-plugin), [vszip](https://github.com/dnjulek/vapoursynth-zip), [FFmpeg](https://ffmpeg.org/), [mpv](https://mpv.io/), and [NVIDIA TensorRT](https://developer.nvidia.com/tensorrt).
