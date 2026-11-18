"""RIFE Movie Converter for Windows."""
import hashlib
import json
import math
import msvcrt
import os
import queue
import shutil
import subprocess
import threading
import tkinter as tk
import uuid
from fractions import Fraction
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

RESOLUTIONS = {
    "Source": (None, 15.5),
    "1440p": ((2560, 1440), 12.0),
    "1080p": ((1920, 1080), 8.0),
    "720p": ((1280, 720), 5.0),
}
GRAIN = {"Off": (0, 0), "Light": (1.2, 3), "Medium": (2.5, 5), "Strong": (4.0, 8)}  # (NLMeans h, BM3D sigma)
GRAIN_METHODS = ("Fast (NLMeans)", "Best (BM3D)")
DEBLOCK = {"Off": 0, "Light": 20, "Medium": 35, "Strong": 50}  # DPIR strength
FRAME_RATES = ("2", "3", "4", "6", "10", "60 fps")  # suggestions; any whole multiplier from 2 to 32 works
RATE_ERROR = "Enter a whole frame multiplier from 2 to 32, or 60 fps."
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


def timecode(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def probe_movie(path):
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
    inspect_color(path, video, duration)
    return video, rate, duration


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
                 resolution="Source", denoise=0, bm3d=0, deblock=0):
        self.denoise, self.bm3d, self.deblock = denoise, bm3d, deblock
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
        self.cancel.set()
        if self.encoder and self.encoder.poll() is None:
            self.encoder.terminate()

    def stop_worker(self, worker):
        if worker and worker.poll() is None:
            subprocess.run(["taskkill", "/PID", str(worker.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **HIDDEN)
            worker.wait()

    def run(self):
        worker = None
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
                bm3d=self.bm3d, deblock=self.deblock,
            )
            self.config.write_text(json.dumps(job), encoding="utf-8")
            input_tags = []
            if master := self.video.get("mastering_display"):
                input_tags += ["-mastering_display:v:0", master]
            track_input = ["-i", self.source]
            if self.sample:
                track_input = ["-t", str(self.duration), *track_input]
            args = [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin", "-n",
                    "-progress", "pipe:1", "-stats_period", "1",
                    "-itsoffset", str(self.video["offset"]), *input_tags, "-f", "yuv4mpegpipe", "-i", "pipe:0",
                    *track_input,
                    "-map", "0:v:0", "-map", "1:a?", "-map", "1:s?", "-map", "1:t?",
                    "-map_metadata", "1", "-map_chapters", "-1" if self.sample else "1",
                    "-c:a", "copy", "-c:s", "copy", "-c:t", "copy", "-c:v", "hevc_nvenc", "-profile:v", "main10", "-pix_fmt", "p010le",
                    "-preset", "p5", "-tune", "hq", "-multipass", "disabled", "-spatial-aq", "1", "-temporal-aq", "1",
                    "-rc-lookahead", "32", "-rc", "vbr", "-b:v", f"{self.bitrate}M",
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
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            with self.log.open("wb") as log:
                worker = subprocess.Popen([PYTHON, ROOT / "render.py", self.config],
                                          stdout=subprocess.PIPE, stderr=log, env=env, **HIDDEN)
                with subprocess.Popen(args, stdin=worker.stdout, stdout=subprocess.PIPE,
                                      stderr=log, encoding="utf-8", errors="replace", **HIDDEN) as encoder:
                    self.encoder = encoder
                    worker.stdout.close()
                    if self.cancel.is_set():
                        encoder.terminate()
                    progress = {}
                    for line in encoder.stdout:
                        key, _, val = line.strip().partition("=")
                        progress[key] = val
                        if key == "progress":
                            frame = int(progress.get("frame", "0"))
                            fps = float(progress.get("fps", "0"))
                            self.events.put(("progress", (frame / float(self.target), fps)))
                            progress.clear()
                    encoded = encoder.wait()
                if encoded or self.cancel.is_set():
                    self.stop_worker(worker)
                produced = worker.wait()
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
        self.title("RIFE Movie Converter — RTX 3090")
        self.geometry("920x690")
        self.minsize(880, 650)
        self.source = None
        self.job = None
        self.events = queue.Queue()
        self.rate = tk.StringVar(value="2")
        self.resolution = tk.StringVar(value="Source")
        self.bitrate = tk.StringVar(value="Auto")
        self.grain = tk.StringVar(value="Off")
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
        self.bitrate_entry = ttk.Entry(table, textvariable=self.bitrate, width=17)
        self.grain_option = ttk.Combobox(table, textvariable=self.grain, values=tuple(GRAIN),
                                         state="readonly", width=14)
        self.method_option = ttk.Combobox(table, textvariable=self.grain_method, values=GRAIN_METHODS,
                                          state="readonly", width=14)
        self.deblock_option = ttk.Combobox(table, textvariable=self.deblock, values=tuple(DEBLOCK),
                                           state="readonly", width=14)
        rows = (
            ("Resolution", self.resolution_option,
             "Output size. Source keeps the original size; the others only scale down."),
            ("Frame multiplier", self.fps_option,
             "How many times the frame rate is multiplied. Type any whole number from 2 to 32, or pick 60 fps. "
             "Higher is smoother but slower, and interpolation errors show more."),
            ("Video bitrate (Mb/s)", self.bitrate_entry, BITRATE_NOTE),
            ("Grain reduction", self.grain_option,
             "Removes film grain before interpolation so it does not pulse. Too strong looks waxy."),
            ("Grain method", self.method_option,
             "Fast is NLMeans. Best is BM3D, which keeps more fine detail but is slower."),
            ("Deblocking", self.deblock_option,
             "Repairs blocky compression damage in low-bitrate sources with the DPIR model. Much slower."),
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
        self.log_button = ttk.Button(panel, text="Open export log", state="disabled",
                                     command=lambda: os.startfile(self.job.log))
        self.log_button.pack(anchor="w")

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
            multiplier = int(entered.rstrip("x× "))
        except ValueError:
            raise ValueError(RATE_ERROR) from None
        if not 2 <= multiplier <= 32:
            raise ValueError(RATE_ERROR)
        return self.source_rate * multiplier

    def get_bitrate(self):
        entered = self.bitrate.get().strip()
        if entered.lower() == "auto":
            return RESOLUTIONS[self.resolution.get()][1]
        try:
            bitrate = float(entered.replace(",", "."))
        except ValueError:
            raise ValueError(BITRATE_ERROR) from None
        if not 1 <= bitrate <= 120:
            raise ValueError(BITRATE_ERROR)
        return bitrate

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
        for control in (self.pick, self.start, self.sample, self.bitrate_entry, self.fps_option):
            control.configure(state="disabled" if busy else "normal")
        for control in (self.resolution_option, self.grain_option,
                        self.method_option, self.deblock_option):
            control.configure(state="disabled" if busy else "readonly")
        self.stop.configure(state="normal" if busy else "disabled")

    def export(self, sample):
        try:
            bitrate, target = self.get_bitrate(), self.target()
            if target <= self.source_rate:
                raise ValueError("The output rate must be higher than the source rate. Use a multiplier of 2 or more.")
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
        h, sigma = GRAIN[self.grain.get()]
        fast = self.grain_method.get() == GRAIN_METHODS[0]
        self.job = Export(self.source, path, self.video, self.source_rate, target,
                          self.duration, bitrate, sample, self.events, resolution=resolution,
                          deblock=DEBLOCK[self.deblock.get()],
                          denoise=h if fast else 0, bm3d=0 if fast else sigma)
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
        while not self.events.empty():
            event, data = self.events.get_nowait()
            if event == "progress":
                rendered, fps = data
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
