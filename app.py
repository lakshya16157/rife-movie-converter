"""RIFE Movie Converter for Windows."""
import csv
import hashlib
import json
import math
import msvcrt
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from fractions import Fraction
from functools import cache, lru_cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent
RUNTIME = (ROOT / "runtime").resolve()
FFMPEG = RUNTIME / "probe" / "ffmpeg.exe"
FFPROBE = FFMPEG.with_name("ffprobe.exe")
PYTHON = RUNTIME / "work-python" / "Scripts" / "python.exe"
PLAYER = RUNTIME / "mpv" / "mpv.exe"
CACHE = RUNTIME / "cache"
CACHE.mkdir(exist_ok=True)
HIDDEN = {"creationflags": subprocess.CREATE_NO_WINDOW}
PREFERRED_MOVIE = CACHE / "preferred-movie.txt"
STATS = RUNTIME / "benchmarks.csv"
GPU_DEFAULTS = CACHE / "gpu-defaults.json"
GPU_QUERY = ["nvidia-smi", "-i", "0", "--format=csv,noheader,nounits"]
# Task Manager's "3D" graph: the 3D engine summed over every process on the adapter the encoder runs on.
# Prints one whole number every two seconds. English counter names only; elsewhere the column stays 0.
ENGINE_3D = (
    "while ($true) { $s = (Get-Counter '\\GPU Engine(*engtype_3D)\\Utilization Percentage' -ErrorAction "
    "SilentlyContinue).CounterSamples; $luid = $null; foreach ($x in $s) { if ($x.InstanceName -match "
    "'pid_%d_luid_(0x[0-9a-f]+_0x[0-9a-f]+)') { $luid = $Matches[1]; break } }; if ($luid) { [int]($s | "
    "Where-Object { $_.InstanceName -like ('*luid_' + $luid + '*') } | Measure-Object CookedValue -Sum).Sum }; "
    "Start-Sleep -Seconds 1 }"
)
# key, heading, width. One row per export; megapixels/s compares runs at different sizes.
STAT_COLUMNS = (
    ("when", "When", 105), ("gpu", "GPU", 110), ("output", "Output", 85), ("multiplier", "Rate / model", 85),
    ("cleanup", "Cleanup", 130), ("parallel", "Parallel", 55), ("fps", "Output fps", 70),
    ("speed", "Playback speed", 95), ("mps", "Megapixels/s", 80), ("gpu_load", "GPU %", 50),
    ("gpu_3d", "3D %", 45), ("encoder_load", "Encoder %", 65), ("vram", "VRAM GB", 60),
    ("encoder", "Encoder", 70),
)

RESOLUTIONS = {
    "Source": (None, 15.5),
    "1440p": ((2560, 1440), 12.0),
    "1080p": ((1920, 1080), 8.0),
    "720p": ((1280, 720), 5.0),
}
GRAIN = {"Off": (0, 0), "Light": (1.2, 3), "Medium": (2.5, 5), "Strong": (4.0, 8)}  # (NLMeans h, BM3D sigma)
GRAIN_METHODS = ("Fast (NLMeans)", "Best (BM3D)")
# NVENC preset and multipass mode. Fast is the RTX 3090 tuning; Best is free only where the encoder has headroom.
ENCODERS = {"Fast (P5 single-pass)": ("p5", "disabled"), "Best (P7 two-pass)": ("p7", "fullres")}
DEBLOCK = {"Off": 0, "Light": 20, "Medium": 35, "Strong": 50}  # DPIR strength
FRAME_RATES = ("2", "2.5", "3", "4", "6", "10", "60 fps")  # suggestions; any multiplier above 1, up to 32
RATE_ERROR = "Enter a frame multiplier above 1 and up to 32 (for example 2 or 2.5), or 60 fps."
GRAIN_ERROR = "Grain reduction: pick a level or enter a strength from 0 to 20."
DEBLOCK_ERROR = "Deblocking: pick a level or enter a strength from 0 to 100."
PARALLEL_ERROR = "Parallel processing: enter a number from 0.25 to 4."
BITRATE_ERROR = "Enter Auto or a video bitrate from 1 to 120 Mb/s."
BITRATE_NOTE = (
    "Auto, or a number from 1 to 120. Guide at 2×: about 5 Mb/s for 720p, 8 for 1080p, 12 for 1440p and 16 for 4K looks good on clean "
    "video. Below that, expect blocking in dark, grainy or fast scenes. Around double those values the picture "
    "is close to what the filters produce. Higher multipliers need more: roughly +50% at 4× and double at 10×. "
    "Auto does not rise with the multiplier, and no bitrate can add detail the source lacks."
)


def output_size(video, resolution):
    width, height = video["width"], video["height"]
    bounds, _ = RESOLUTIONS[resolution]
    if bounds is None or (width <= bounds[0] and height <= bounds[1]):
        return width, height
    scale = min(Fraction(bounds[0], width), Fraction(bounds[1], height))
    # 4:2:0 HEVC requires even dimensions. Round to the nearest even pixel.
    return tuple(max(2, 2 * int(n * scale / 2 + Fraction(1, 2))) for n in (width, height))


def output_sar(video, size):
    sar = video.get("sample_aspect_ratio", "1:1")
    if sar in ("1:1", "N/A", "0:1", None):
        return Fraction(1)
    width, height = size
    return (Fraction(sar.replace(":", "/")) * Fraction(video["width"], video["height"])
            * Fraction(height, width))


def best_encoder(gpu):
    """Default Encoder setting for this card. Measured once per GPU, then read from the cache."""
    try:
        saved = json.loads(GPU_DEFAULTS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if gpu not in saved:
        def seconds(split):
            started = time.monotonic()
            done = subprocess.run(
                [FFMPEG, "-v", "error", "-f", "lavfi", "-i", "color=s=3840x2160:r=60", "-frames:v", "120",
                 "-c:v", "hevc_nvenc", "-profile:v", "main10", "-pix_fmt", "p010le", "-preset", "p7",
                 "-multipass", "fullres", "-split_encode_mode", split, "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **HIDDEN)
            return math.inf if done.returncode else time.monotonic() - started

        plain, split = seconds("disabled"), seconds("forced")
        if plain == math.inf:
            return next(iter(ENCODERS))  # no working NVENC to measure; keep the fast preset and retry next start
        # Split encoding is only much faster with a second encoder chip, and then the slow preset is nearly free.
        saved[gpu] = split < 0.75 * plain
        GPU_DEFAULTS.write_text(json.dumps(saved), encoding="utf-8")
    return list(ENCODERS)[saved[gpu]]


@cache
def embedded_python():
    """Check the local FFmpeg/Python combination once. Older installs use the pipe."""
    if not FFMPEG.with_name("portable.vs").is_file():
        return {}
    settings = dict(PYTHONHOME=sys.base_prefix, PYTHONPATH=str(PYTHON.parent.parent / "Lib/site-packages"),
                    PYTHONNOUSERSITE="1")
    script = b"import vapoursynth as vs\nvs.core.std.BlankClip(length=1).set_output()\n"
    try:
        probe = subprocess.run([FFMPEG, "-v", "error", "-f", "vapoursynth", "-i", "pipe:0", "-f", "null", "-"],
                               input=script, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=dict(os.environ, **settings), timeout=10, **HIDDEN)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return settings if probe.returncode == 0 else {}


def timecode(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def probe_movie(path):
    path = Path(path).resolve()
    stat = path.stat()
    identity = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    video, rate, duration = _probe_movie(path, identity)
    return video.copy(), rate, duration


@lru_cache(maxsize=16)
def _probe_movie(path, _identity):
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
        **HIDDEN, capture_output=True, encoding="utf-8", errors="replace", check=True,
    )
    data = json.loads(result.stdout)
    video = next((s for s in data["streams"]
                  if s["codec_type"] == "video" and not s["disposition"].get("attached_pic")), None)
    if video is None:
        raise ValueError("Choose a file with a video track.")
    rate = Fraction(video.get("avg_frame_rate", "0/1").replace("0/0", "0/1"))
    if not rate:
        rate = Fraction(video["r_frame_rate"])
    duration = float(video.get("duration") or data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0 or rate <= 0:
        raise ValueError("The video needs a known duration and frame rate.")
    start = video.get("start_time", "0")
    container_start = data["format"].get("start_time", "0")
    video["offset"] = float(start if start != "N/A" else 0) - float(
        container_start if container_start != "N/A" else 0)
    stream_types = {stream.get("codec_type") for stream in data["streams"]}
    video["split_track_inputs"] = {"audio", "subtitle"} <= stream_types
    inspect_color(path, video, duration)
    return video, rate, duration


@lru_cache(maxsize=32)
def supports_split_encode(ffmpeg, size):
    # The app uses FFmpeg's default GPU. Recheck when its process or encoder resolution changes.
    probe = subprocess.run(
        [ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=s={}x{}".format(*size), "-frames:v", "1",
         "-c:v", "hevc_nvenc", "-profile:v", "main10", "-pix_fmt", "p010le",
         "-split_encode_mode", "forced", "-f", "null", "-"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **HIDDEN)
    return probe.returncode == 0


def inspect_color(path, video, duration):
    stream_data = video.get("side_data_list", [])
    dovi = next((item for item in stream_data if item.get("side_data_type") == "DOVI configuration record"), None)
    transfer = video.get("color_transfer")
    video["export_color"] = {"smpte2084": "HDR10", "arib-std-b67": "HLG"}.get(transfer, "SDR")
    if dovi is None and transfer != "smpte2084":
        return

    # Container Dolby Vision declarations can outlive the frame metadata after a transcode.
    points = (0, int(duration / 3), int(2 * duration / 3)) if dovi else (0,)
    intervals = ",".join(f"{point}%+#12" for point in dict.fromkeys(points))
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", str(video["index"]), "-read_intervals", intervals,
         "-show_frames", "-show_entries",
         "frame=color_transfer,color_space,color_primaries:frame_side_data=side_data_type,red_x,red_y,"
         "green_x,green_y,blue_x,blue_y,white_point_x,white_point_y,max_luminance,min_luminance,max_content,max_average",
         "-of", "json", path],
        **HIDDEN, capture_output=True, encoding="utf-8", errors="replace", check=True, timeout=20,
    )
    frames = json.loads(result.stdout).get("frames", [])
    frame_data = [side for frame in frames for side in frame.get("side_data_list", [])]
    if dovi:
        profile = dovi.get("dv_profile")
        has_rpu = any(item.get("side_data_type") == "Dolby Vision RPU Data" for item in frame_data)
        if has_rpu and video["codec_name"] == "hevc" and profile in (5, 8):
            video.update(dovi_map=True, export_color="HDR10 (from Dolby Vision)",
                         color_space="bt2020nc", color_transfer="smpte2084",
                         color_primaries="bt2020", color_range="tv", chroma_location="left",
                         mastering_display="G(8500,39850)B(6550,2300)R(35400,14600)WP(15635,16450)L(10000000,50)")
            return
        if profile == 7 and (has_rpu or transfer == "smpte2084"):
            video.update(export_color="HDR10 base layer", color_space="bt2020nc",
                         color_transfer="smpte2084", color_primaries="bt2020", color_range="tv")
        elif profile == 8 and not has_rpu and frames and all(
            frame.get("color_transfer") in ("bt709", "smpte170m", "bt470bg", "iec61966-2-1")
            for frame in frames
        ):
            video["export_color"] = "SDR (Dolby Vision tag only)"
        elif video["export_color"] == "SDR" or profile == 5:
            video["color_error"] = "This Dolby Vision profile needs supported per-frame color metadata."
            return

    if video.get("color_transfer") == "smpte2084":
        for side in stream_data + frame_data:
            if side.get("side_data_type") == "Mastering display metadata":
                try:
                    primaries = "".join(
                        f"{name}({round(Fraction(side[key + '_x']) * 50000)},"
                        f"{round(Fraction(side[key + '_y']) * 50000)})"
                        for name, key in (("G", "green"), ("B", "blue"), ("R", "red"), ("WP", "white_point"))
                    )
                    high, low = (round(Fraction(side[key + "_luminance"]) * 10000)
                                 for key in ("max", "min"))
                    video["mastering_display"] = f"{primaries}L({high},{low})"
                except (KeyError, ValueError, ZeroDivisionError):
                    continue
                break


class Preview:
    """Serve the growing export on loopback and wait for the writer at temporary EOF."""

    def __init__(self, job):
        token = "/" + uuid.uuid4().hex + ".mkv"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != token:
                    self.send_error(404)
                    return
                self.connection.settimeout(10)
                self.send_response(200)
                self.send_header("Content-Type", "video/x-matroska")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                position = 0
                try:
                    while True:
                        with job.file_lock:
                            with job.path.open("rb") as stream:
                                stream.seek(position)
                                block = stream.read(128 * 1024)
                            done = job.done.is_set()
                        if block:
                            self.wfile.write(block)
                            position += len(block)
                        elif done:
                            break
                        else:
                            job.done.wait(0.2)
                except (ConnectionError, TimeoutError, FileNotFoundError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}{token}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Export:
    def __init__(self, source, output, video, source_rate, target, duration, bitrate, sample, events,
                 resolution="Source", denoise=0, bm3d=0, deblock=0, parallel=1, labels=None,
                 encoder=("p5", "disabled"), lite_model=False, fast=0):
        self.lite_model, self.fast = lite_model, fast
        self.denoise, self.bm3d, self.deblock, self.parallel = denoise, bm3d, deblock, parallel
        self.preset, self.multipass = encoder
        self.labels = dict(labels or {}, when=time.strftime("%Y-%m-%d %H:%M"),
                           encoder=self.preset.upper() + ("" if self.multipass == "disabled" else " 2-pass"))
        self.stats = None
        self.first = None  # (time, frame) of the first encoded frame, so engine builds do not count
        self.samples = []
        self.engine = []
        self.source, self.output = source, output
        self.path = output.with_suffix(".partial.mkv")
        self.video, self.source_rate, self.target = video, source_rate, target
        self.duration = min(duration, 30) if sample else duration
        self.sample, self.bitrate, self.events = sample, bitrate, events
        self.size = output_size(video, resolution)
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.file_lock = threading.Lock()
        self.preview = None
        key = uuid.uuid4().hex[:10]
        self.log = CACHE / f"export-{key}.log"
        self.config = CACHE / f"export-{key}.json"
        self.encoder = None

    def cancel_render(self):
        if self.cancel.is_set():
            return
        self.cancel.set()
        if self.encoder:
            threading.Thread(target=self.stop_worker, args=(self.encoder,), daemon=True).start()

    def sample_gpu(self, monitor):
        for line in monitor.stdout:
            try:
                sample = [float(value) for value in line.split(",")]
            except ValueError:
                continue
            if self.first and len(sample) == 3:
                self.samples.append(sample)

    def sample_3d(self, counters):
        for line in counters.stdout:
            if self.first and line.strip().isdigit():
                self.engine.append(int(line))

    def measure(self, frame):
        now = time.monotonic()
        if frame and self.first is None:
            self.first = (now, frame)
        started, first = self.first or (now, frame)
        if now - started < 2:
            return
        fps = (frame - first) / (now - started)
        width, height = self.size
        gpu, encoder, memory = zip(*self.samples) if self.samples else ((0,), (0,), (0,))
        self.stats = dict(
            self.labels, fps=round(fps, 1), speed=round(fps / float(self.target), 2),
            mps=round(fps * width * height / 1e6), gpu_load=round(sum(gpu) / len(gpu)),
            gpu_3d=round(sum(self.engine) / len(self.engine)) if self.engine else 0,
            encoder_load=round(sum(encoder) / len(encoder)), vram=round(max(memory) / 1024, 1),
        )

    def stop_worker(self, worker):
        if worker and worker.poll() is None:
            subprocess.run(["taskkill", "/PID", str(worker.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **HIDDEN)
            worker.wait()

    def run(self):
        started = time.monotonic()
        worker = None
        monitor = counters = None
        lock = None
        try:
            lock = (CACHE / "export.lock").open("a+b")
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as error:
                raise RuntimeError("Another RIFE export is running in this app folder.") from error
            if self.video.get("color_error"):
                raise ValueError(self.video["color_error"])
            stat = self.source.stat()
            index_key = hashlib.sha256(
                f"{self.source}|{stat.st_size}|{stat.st_mtime_ns}".encode()
            ).hexdigest()[:24]
            matrix_tag = self.video.get("color_space")
            matrices = {"bt709": "709", "smpte170m": "170m", "bt470bg": "470bg",
                        "bt2020nc": "2020ncl", "fcc": "fcc", "smpte240m": "240m"}
            matrix = matrices.get(matrix_tag, "709" if self.video["height"] >= 720 else "170m")
            full = self.video.get("color_range") == "pc" or self.video.get("pix_fmt", "").startswith("yuvj")
            chroma_map = {"topleft": "top_left", "bottomleft": "bottom_left"}
            chroma = self.video.get("chroma_location", "left")
            if chroma not in ("left", "center", "topleft", "top", "bottomleft", "bottom"):
                chroma = "left"
            job = dict(
                source=str(self.source), index=str(CACHE / f"{index_key}.ffindex"),
                track=self.video["index"], source_rate=str(self.source_rate),
                output_rate=str(self.target), sample_seconds=self.duration if self.sample else 0,
                matrix=matrix, range="full" if full else "limited",
                chroma=chroma_map.get(chroma, chroma), size=self.size,
                dovi_map=self.video.get("dovi_map", False), denoise=self.denoise,
                bm3d=self.bm3d, deblock=self.deblock, parallel=self.parallel, lite_model=self.lite_model, fast=self.fast,
            )
            self.config.write_text(json.dumps(job), encoding="utf-8")
            input_tags = []
            if master := self.video.get("mastering_display"):
                input_tags += ["-mastering_display:v:0", master]
            track_input = ["-i", self.source]
            if self.sample:
                track_input = ["-t", str(self.duration), *track_input]
            # Sparse subtitles can pull a shared input far ahead of its copied audio.
            subtitle_input = 2 if self.video.get("split_track_inputs", True) else 1
            source_inputs = track_input * subtitle_input
            split_encode = ["-split_encode_mode", "forced"] if supports_split_encode(FFMPEG, self.size) else []
            embedded = embedded_python()
            # FFmpeg can otherwise exit successfully after a frame request fails mid-stream.
            video_input = ["-xerror", "-f", "vapoursynth", "-i", ROOT / "export.vpy"] if embedded else [
                "-f", "yuv4mpegpipe", "-i", "pipe:0"]
            args = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin", "-n",
                    "-progress", "pipe:1", "-stats_period", "1",
                    "-itsoffset", str(self.video["offset"]), *input_tags, *video_input,
                    *source_inputs,
                    "-map", "0:v:0", "-map", "1:a?", "-map", f"{subtitle_input}:s?", "-map", f"{subtitle_input}:t?",
                    "-map_metadata", "1", "-map_chapters", "-1" if self.sample else "1",
                    "-c:a", "copy", "-c:s", "copy", "-c:t", "copy", "-c:v", "hevc_nvenc", "-profile:v", "main10", "-pix_fmt", "p010le",
                    "-preset", self.preset, "-tune", "hq", "-multipass", self.multipass,
                    "-spatial-aq", "1", "-temporal-aq", "1",
                    "-rc-lookahead", "32", *split_encode, "-rc", "vbr", "-b:v", f"{self.bitrate}M",
                    "-maxrate", f"{self.bitrate * 2}M", "-bufsize", f"{self.bitrate * 2}M",
                    "-g", str(round(self.target * 2)), "-color_range", "pc" if full else "tv"]
            tags = [f"range={'full' if full else 'limited'}", f"chroma_location={chroma}"]
            for option, key in (("colorspace", "color_space"), ("color_trc", "color_transfer"),
                                ("color_primaries", "color_primaries")):
                if self.video.get(key) not in (None, "unknown", "reserved"):
                    args += [f"-{option}", self.video[key]]
                    tags.append(f"{option}={self.video[key]}")
            filters = ["setparams=" + ":".join(tags)]
            sar = output_sar(self.video, self.size)
            if sar != 1:
                filters.append(f"setsar=sar={sar}:max=65535")
            args += ["-vf", ",".join(filters), "-chroma_sample_location", chroma]
            args += ["-flush_packets", "1", "-cluster_time_limit", "1000",
                     "-max_interleave_delta", "1000000", self.path]
            env = dict(os.environ, PYTHONIOENCODING="utf-8", RIFE_JOB=str(self.config), **embedded)
            with self.log.open("wb") as log:
                log.write(b"Frame transport: embedded VapourSynth\n" if embedded else b"Frame transport: Y4M pipe\n")
                log.flush()
                if not embedded:
                    worker = subprocess.Popen([PYTHON, ROOT / "render.py", self.config],
                                              stdout=subprocess.PIPE, stderr=log, env=env, **HIDDEN)
                with subprocess.Popen(args, stdin=worker.stdout if worker else subprocess.DEVNULL,
                                      env=env, stdout=subprocess.PIPE,
                                      stderr=log, encoding="utf-8", errors="replace", **HIDDEN) as encoder:
                    self.encoder = encoder
                    if worker:
                        worker.stdout.close()
                    if self.cancel.is_set():
                        self.stop_worker(encoder)
                    try:
                        monitor = subprocess.Popen(
                            [*GPU_QUERY, "--query-gpu=utilization.gpu,utilization.encoder,memory.used", "-l", "2"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, **HIDDEN)
                        threading.Thread(target=self.sample_gpu, args=(monitor,), daemon=True).start()
                        counters = subprocess.Popen(
                            ["powershell", "-NoProfile", "-Command", ENGINE_3D % encoder.pid],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, **HIDDEN)
                        threading.Thread(target=self.sample_3d, args=(counters,), daemon=True).start()
                    except OSError:
                        pass  # no nvidia-smi: the stats table shows zeros for GPU load
                    progress = {}
                    for line in encoder.stdout:
                        key, _, val = line.strip().partition("=")
                        progress[key] = val
                        if key == "progress":
                            frame = int(progress.get("frame", "0"))
                            self.measure(frame)
                            # FFmpeg's own average counts the wait for the first frame; prefer ours.
                            fps = self.stats["fps"] if self.stats else float(progress.get("fps", "0"))
                            self.events.put(("progress", (frame / float(self.target), fps)))
                            progress.clear()
                    encoded = encoder.wait()
                if encoded or self.cancel.is_set():
                    self.stop_worker(worker)
                produced = worker.wait() if worker else 0
                if self.first:
                    log.write(f"First encoded progress after {self.first[0] - started:.2f} s\n".encode("ascii"))
                log.write(f"Export process elapsed {time.monotonic() - started:.2f} s\n".encode("ascii"))
            if self.cancel.is_set():
                self.events.put(("cancelled", None))
            elif encoded or produced:
                raise RuntimeError("Video processing failed.")
            else:
                with self.file_lock:
                    self.path.rename(self.output)
                    self.path = self.output
                self.events.put(("complete", self.output))
        except Exception as error:
            self.events.put(("error", str(error)))
        finally:
            try:
                for sampler in (monitor, counters):
                    if sampler:
                        sampler.kill()
                self.stop_worker(worker)
                self.config.unlink(missing_ok=True)
            finally:
                if lock is not None:
                    lock.close()
                self.done.set()

    def watch(self):
        if self.done.is_set():
            url = str(self.path)
        else:
            if self.preview is None:
                self.preview = Preview(self)
            url = self.preview.url
        subprocess.Popen([PLAYER, "--no-config", "--hwdec=auto-safe", "--force-media-title=RIFE export",
                          "--cache=yes", "--cache-secs=10", "--cache-pause=yes", "--cache-pause-wait=2",
                          "--", url], **HIDDEN)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("RIFE Movie Converter")
        self.geometry("1130x940")
        self.minsize(1120, 920)
        self.source = None
        self.job = None
        self.events = queue.Queue()
        self.rate = tk.StringVar(value="2")
        self.model = tk.StringVar(value="Standard")
        self.resolution = tk.StringVar(value="Source")
        self.bitrate = tk.StringVar(value="Auto")
        self.grain = tk.StringVar(value="Off")
        self.parallel = tk.StringVar(value="1")
        self.encoder_quality = tk.StringVar(value=next(iter(ENCODERS)))
        self.live = None
        try:
            self.gpu = subprocess.run([*GPU_QUERY, "--query-gpu=name"], capture_output=True, text=True,
                                      **HIDDEN).stdout.strip().removeprefix("NVIDIA ").removeprefix("GeForce ")
        except OSError:
            self.gpu = ""
        self.detected = None
        if self.gpu:
            threading.Thread(target=lambda: setattr(self, "detected", best_encoder(self.gpu)), daemon=True).start()
            self.title(f"RIFE Movie Converter — {self.gpu}")
        self.grain_method = tk.StringVar(value=GRAIN_METHODS[0])
        self.deblock = tk.StringVar(value="Off")
        self.movie = tk.StringVar(value="Choose a movie to get started.")
        self.description = tk.StringVar()
        self.estimate = tk.StringVar()
        self.status = tk.StringVar(value="Ready")
        self.detail = tk.StringVar(value="Play the converted file in mpv while the export runs.")
        self.percent = tk.DoubleVar()
        self.build()
        self.rate.trace_add("write", self.update_estimate)
        self.resolution.trace_add("write", self.update_estimate)
        self.bitrate.trace_add("write", self.update_estimate)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(250, self.poll)

    def build(self):
        panel = ttk.Frame(self, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="RIFE Movie Converter", font=("Segoe UI", 20, "bold")).pack(anchor="w")
        pick = ttk.Frame(panel)
        pick.pack(fill="x", pady=(16, 4))
        self.pick = ttk.Button(pick, text="Choose movie…", command=self.choose)
        self.pick.pack(side="left", padx=(0, 12))
        ttk.Label(pick, textvariable=self.movie, wraplength=500).pack(side="left", fill="x")
        ttk.Label(panel, textvariable=self.description).pack(anchor="w", pady=(0, 10))
        table = ttk.Frame(panel)
        table.pack(fill="x", pady=6)
        table.columnconfigure(2, weight=1)
        for column, heading in enumerate(("Option", "Value", "Description")):
            ttk.Label(table, text=heading, font=("Segoe UI", 9, "bold")).grid(
                row=0, column=column, sticky="w", padx=(0, 16), pady=(0, 4))
        ttk.Separator(table).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 4))
        self.resolution_option = ttk.Combobox(table, textvariable=self.resolution, values=tuple(RESOLUTIONS),
                                              state="readonly", width=14)
        self.fps_option = ttk.Combobox(table, textvariable=self.rate, values=FRAME_RATES, width=14)
        self.model_option = ttk.Combobox(table, textvariable=self.model, values=("Standard", "Lite", "Fast"),
                                         state="readonly", width=14)
        self.bitrate_entry = ttk.Entry(table, textvariable=self.bitrate, width=17)
        self.grain_option = ttk.Combobox(table, textvariable=self.grain, values=tuple(GRAIN), width=14)
        self.method_option = ttk.Combobox(table, textvariable=self.grain_method, values=GRAIN_METHODS,
                                          state="readonly", width=14)
        self.deblock_option = ttk.Combobox(table, textvariable=self.deblock, values=tuple(DEBLOCK), width=14)
        self.parallel_entry = ttk.Entry(table, textvariable=self.parallel, width=17)
        self.encoder_option = ttk.Combobox(table, textvariable=self.encoder_quality, values=tuple(ENCODERS),
                                           state="readonly", width=20)
        rows = (
            ("Resolution", self.resolution_option,
             "Output size. Source keeps the original size; the others only scale down."),
            ("RIFE model", self.model_option,
             "Fast finds motion on a half-size picture and moves the full-size pixels, as televisions do. About "
             "twice the speed of Standard at 4K; best at 1080p and above. Lite is a smaller model at full size. "
             "Compare a sample for differences around moving edges."),
            ("Frame multiplier", self.fps_option,
             "How many times the frame rate is multiplied. Type any number above 1, up to 32 (2.5 turns 24 fps "
             "into 60), or pick 60 fps. Whole numbers keep every original frame; other values regenerate most "
             "frames. Higher is smoother but slower, and interpolation errors show more."),
            ("Video bitrate (Mb/s)", self.bitrate_entry, BITRATE_NOTE),
            ("Grain reduction", self.grain_option,
             "Removes film grain before interpolation so it does not pulse. Pick a level or type a strength "
             "from 0 to 20. Too strong looks waxy."),
            ("Grain method", self.method_option,
             "Fast is NLMeans. Best is BM3D, which keeps more fine detail but is slower."),
            ("Deblocking", self.deblock_option,
             "Repairs blocky compression damage in low-bitrate sources with the DPIR model. Pick a level or type "
             "a strength from 0 to 100. Much slower."),
            ("Parallel processing", self.parallel_entry,
             "Scales how many frames are worked on at once. 1 is the tested default. Lower it (0.5) if memory "
             "runs out; raise it (1.5 or 2) if the GPU and CPU are not busy. From 0.25 to 4."),
            ("Encoder", self.encoder_option,
             "Best gives slightly higher quality at the same bitrate. It is nearly free on cards with two encoder "
             "chips and about 30% slower on cards with one. The default comes from a one-time speed test of "
             "this card's encoder."),
        )
        for row, (name, control, description) in enumerate(rows, start=2):
            ttk.Label(table, text=name).grid(row=row, column=0, sticky="nw", padx=(0, 16), pady=4)
            control.grid(row=row, column=1, sticky="nw", padx=(0, 16), pady=4)
            ttk.Label(table, text=description, wraplength=560, justify="left", foreground="#555555").grid(
                row=row, column=2, sticky="w", pady=4)
        ttk.Label(panel, textvariable=self.estimate).pack(anchor="w", pady=(1, 12))
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(0, 15))
        self.start = ttk.Button(actions, text="Export movie…", state="disabled", command=lambda: self.export(False))
        self.sample = ttk.Button(actions, text="Export first 30 sec…", state="disabled", command=lambda: self.export(True))
        self.watch = ttk.Button(actions, text="Watch export", state="disabled", command=self.watch_export)
        self.stop = ttk.Button(actions, text="Cancel export", state="disabled", command=self.stop_export)
        for button in (self.start, self.sample, self.watch, self.stop):
            button.pack(side="left", padx=(0, 8))
        ttk.Progressbar(panel, variable=self.percent, maximum=100).pack(fill="x", pady=(0, 10))
        ttk.Label(panel, textvariable=self.status, font=("Segoe UI", 10, "bold"), wraplength=610).pack(anchor="w")
        ttk.Label(panel, textvariable=self.detail, wraplength=610).pack(anchor="w", pady=(4, 12))
        self.tree = ttk.Treeview(panel, columns=[key for key, _, _ in STAT_COLUMNS], show="headings", height=6)
        for key, heading, width in STAT_COLUMNS:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, anchor="w", stretch=key == "cleanup")
        self.tree.pack(fill="x", pady=(0, 10))
        if STATS.is_file():
            with STATS.open(newline="", encoding="utf-8") as file:
                for row in list(csv.DictReader(file))[-30:]:
                    self.tree.insert("", 0, values=[row.get(key, "") for key, _, _ in STAT_COLUMNS])
        buttons = ttk.Frame(panel)
        buttons.pack(fill="x")
        self.log_button = ttk.Button(buttons, text="Open export log", state="disabled",
                                     command=lambda: os.startfile(self.job.log))
        self.log_button.pack(side="left", padx=(0, 8))
        self.stats_button = ttk.Button(buttons, text="Open stats file", command=lambda: os.startfile(STATS),
                                       state="normal" if STATS.is_file() else "disabled")
        self.stats_button.pack(side="left")

    def choose(self):
        folder = self.source.parent if self.source else Path.home() / "Videos"
        name = filedialog.askopenfilename(initialdir=folder, title="Choose a movie",
            filetypes=[("Video files", "*.mkv *.mp4 *.mov *.m4v *.avi *.webm *.ts *.m2ts"), ("All files", "*.*")])
        if name:
            self.load_movie(Path(name))

    def load_movie(self, path):
        try:
            video, self.source_rate, self.duration = probe_movie(path)
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, ZeroDivisionError) as error:
            messagebox.showerror("Could not open movie", str(error))
            return
        self.source = path
        self.video = video
        self.movie.set(path.name)
        self.description.set(
            f"{video['width']} × {video['height']}   ·   {float(self.source_rate):.3f} fps   ·   "
            f"{timecode(self.duration)}   ·   Export as {video['export_color']}"
        )
        self.start.configure(state="normal")
        self.sample.configure(state="normal")
        self.update_estimate()

    def target(self):
        entered = self.rate.get().strip().lower()
        if entered == "60 fps":
            return Fraction(60)
        try:
            multiplier = Fraction(entered.rstrip("x× ").replace(",", "."))
        except (ValueError, ZeroDivisionError):
            raise ValueError(RATE_ERROR) from None
        if not 1 < multiplier <= 32:
            raise ValueError(RATE_ERROR)
        return self.source_rate * multiplier

    @staticmethod
    def number(variable, low, high, error, presets=()):
        entered = variable.get().strip()
        if entered.title() in presets:
            return presets[entered.title()]
        try:
            value = float(entered.replace(",", "."))
        except ValueError:
            raise ValueError(error) from None
        if not low <= value <= high:
            raise ValueError(error)
        return value

    def get_bitrate(self):
        if self.bitrate.get().strip().lower() == "auto":
            return RESOLUTIONS[self.resolution.get()][1]
        return self.number(self.bitrate, 1, 120, BITRATE_ERROR)

    def update_estimate(self, *_):
        if self.source:
            try:
                bitrate = self.get_bitrate()
                size = self.duration * bitrate / 8000
                width, height = output_size(self.video, self.resolution.get())
                self.estimate.set(
                    f"{width} × {height} at {float(self.target()):.3f} fps   ·   "
                    f"{bitrate:g} Mb/s video   ·   About {size:.1f} GB plus audio and subtitles"
                )
            except ValueError as error:
                self.estimate.set(str(error))

    def set_busy(self, busy):
        for control in (self.pick, self.start, self.sample, self.bitrate_entry, self.fps_option,
                        self.grain_option, self.deblock_option, self.parallel_entry):
            control.configure(state="disabled" if busy else "normal")
        for control in (self.resolution_option, self.method_option, self.encoder_option, self.model_option):
            control.configure(state="disabled" if busy else "readonly")
        self.stop.configure(state="normal" if busy else "disabled")

    def export(self, sample):
        try:
            bitrate, target = self.get_bitrate(), self.target()
            model = self.model.get()
            lite_model = model == "Lite"
            if lite_model and not (RUNTIME / "plugins/models/rife_v2/rife_v4.25_lite.onnx").is_file():
                raise ValueError("Run Set Up RIFE.cmd to install the Lite model.")
            if target <= self.source_rate:
                raise ValueError("The output rate must be higher than the source rate. Use a multiplier above 1.")
            grain = self.number(self.grain, 0, 20, GRAIN_ERROR, GRAIN)
            deblock = self.number(self.deblock, 0, 100, DEBLOCK_ERROR, DEBLOCK)
            parallel = self.number(self.parallel, 0.25, 4, PARALLEL_ERROR)
            if self.video.get("field_order") not in (None, "unknown", "progressive"):
                raise ValueError("This app currently supports progressive video. Deinterlace this movie first.")
            if self.video.get("color_error"):
                raise ValueError(self.video["color_error"])
        except ValueError as error:
            messagebox.showerror("Check export settings", str(error))
            return
        resolution = self.resolution.get()
        size_tag = "" if resolution == "Source" else f"{resolution}-"
        suffix = f".RIFE-{size_tag}{float(target):.3f}fps" + (".sample" if sample else "")
        name = filedialog.asksaveasfilename(initialdir=self.source.parent,
            initialfile=f"{self.source.stem}{suffix}.mkv", defaultextension=".mkv",
            filetypes=[("Matroska movie", "*.mkv")], confirmoverwrite=False, title="Save interpolated movie")
        if not name:
            return
        path = Path(name)
        if path.suffix.lower() != ".mkv" or path.exists() or path.with_suffix(".partial.mkv").exists():
            messagebox.showerror("Choose a new MKV name", "Use a new .mkv name. A movie or partial export with that name may already exist.")
            return
        expected = min(self.duration, 30) if sample else self.duration
        if shutil.disk_usage(path.parent).free < (expected * bitrate * 125000 * 1.25 + 100_000_000):
            messagebox.showerror("Disk space", "Choose a drive with more free space for this bitrate.")
            return
        if self.job and self.job.preview:
            self.job.preview.close()
        h, sigma = grain if isinstance(grain, tuple) else (grain, grain)  # a typed number is used as is
        fast = self.grain_method.get() == GRAIN_METHODS[0]
        width, height = output_size(self.video, resolution)
        cleanup = [f"{'NLMeans' if fast else 'BM3D'} {h if fast else sigma:g}"] if h else []
        cleanup += [f"DPIR {deblock:g}"] if deblock else []
        self.live = None
        self.job = Export(self.source, path, self.video, self.source_rate, target,
                          self.duration, bitrate, sample, self.events, resolution=resolution,
                          deblock=deblock, denoise=h if fast else 0, bm3d=0 if fast else sigma,
                          parallel=parallel, encoder=ENCODERS[self.encoder_quality.get()], lite_model=lite_model,
                          fast=2 if model == "Fast" else 0,
                          labels=dict(
                              gpu=self.gpu, output=f"{width}x{height}", parallel=f"{parallel:g}",
                              multiplier=f"{float(target / self.source_rate):.3g}x" + ("" if model == "Standard" else f" {model}"),
                              cleanup=" + ".join(cleanup) or "none"))
        self.percent.set(0)
        self.set_busy(True)
        self.watch.configure(state="disabled")
        self.status.set("Preparing RIFE…")
        self.detail.set("The first export at a resolution can take a few minutes to prepare the GPU engine.")
        threading.Thread(target=self.job.run, daemon=True).start()

    def stop_export(self):
        self.job.cancel_render()
        self.stop.configure(state="disabled")
        self.status.set("Cancelling…")

    def watch_export(self):
        try:
            self.job.watch()
        except OSError as error:
            messagebox.showerror("Could not open player", str(error))

    def poll(self):
        if self.detected:
            if self.encoder_quality.get() == next(iter(ENCODERS)) and not (self.job and not self.job.done.is_set()):
                self.encoder_quality.set(self.detected)
            self.detected = None
        while not self.events.empty():
            event, data = self.events.get_nowait()
            if event == "progress":
                rendered, fps = data
                if stats := self.job.stats:
                    values = [stats[key] for key, _, _ in STAT_COLUMNS]
                    if self.live:
                        self.tree.item(self.live, values=values)
                    else:
                        self.live = self.tree.insert("", 0, values=values)
                self.percent.set(min(99.9, rendered * 100 / self.job.duration))
                self.status.set(f"Exporting   {timecode(rendered)} / {timecode(self.job.duration)}")
                speed = fps / float(self.job.target)
                if rendered >= 5 and self.job.path.is_file():
                    self.watch.configure(state="normal")
                if speed > 0:
                    left = max(0, self.job.duration - rendered)
                    bank = max(0, self.job.duration * (1 - speed))
                    hint = (f"   ·   For uninterrupted playback, wait for about {timecode(bank)} rendered."
                            if bank > rendered else "   ·   Playback can start with the current buffer.")
                    self.detail.set(f"{speed:.2f}× playback speed   ·   {timecode(left / speed)} estimated remaining{hint}")
            else:
                self.set_busy(False)
                if stats := self.job.stats:
                    # Rewrite the whole (small) file so rows from older versions gain any new columns.
                    rows = []
                    if STATS.is_file():
                        with STATS.open(newline="", encoding="utf-8") as file:
                            rows = list(csv.DictReader(file))
                    with STATS.open("w", newline="", encoding="utf-8") as file:
                        writer = csv.DictWriter(file, [key for key, _, _ in STAT_COLUMNS], restval="",
                                                extrasaction="ignore")
                        writer.writeheader()
                        writer.writerows([*rows, stats])
                    self.stats_button.configure(state="normal")
                if event == "complete":
                    self.percent.set(100)
                    self.status.set("Export complete")
                    self.detail.set(f"{data.name}   ·   {data.stat().st_size / 1e9:.2f} GB")
                    self.watch.configure(state="normal")
                elif event == "cancelled":
                    self.status.set("Export cancelled")
                    self.detail.set("Any frames already written are in the .partial.mkv file.")
                else:
                    self.status.set("Export stopped")
                    self.detail.set(data + (" See the export log for details." if self.job.log.exists() else ""))
            if self.job.log.exists():
                self.log_button.configure(state="normal")
        self.after(250, self.poll)

    def close(self):
        if self.job and not self.job.done.is_set():
            if not messagebox.askyesno("Export is running", "Cancel the export and close the app?"):
                return
            self.job.cancel_render()
            self.after(100, self.wait_to_close)
            return
        self.destroy()

    def wait_to_close(self):
        if self.job.done.is_set():
            self.destroy()
        else:
            self.after(100, self.wait_to_close)


if __name__ == "__main__":
    app = App()
    if PREFERRED_MOVIE.is_file():
        movie = Path(PREFERRED_MOVIE.read_text(encoding="utf-8").strip())
        if movie.is_file():
            app.after(100, lambda: app.load_movie(movie))
    app.mainloop()
