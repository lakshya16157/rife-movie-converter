"""Feed RIFE frames to FFmpeg as 10-bit YUV4MPEG2."""
import json
import os
import sys
from fractions import Fraction
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parent / "runtime").resolve()
PLUGINS = RUNTIME / "plugins"


def frames(job):
    import vapoursynth as vs

    core = vs.core
    scale = float(job.get("parallel", 1))  # the app's Parallel processing setting
    core.max_cache_size = max(768, round(4096 * scale))  # 4K RGB frames are 36 to 73 MB each
    core.std.LoadPlugin(str(PLUGINS / "ffms2.dll"))
    print("Indexing the movie for frame-accurate decodingâ€¦", file=sys.stderr, flush=True)
    source = core.ffms2.Source(
        job["source"], track=job["track"], threads=8, cachefile=job["index"]
    )
    nominal_rate = Fraction(job["source_rate"])
    # FFMS2 retains exact frame count for CFR inputs. Snap tiny container FPS rounding.
    if abs(float(source.fps - nominal_rate)) > 0.02:
        raise ValueError(f"Source timing differs from its reported rate ({source.fps} vs {nominal_rate}).")
    source = source.std.AssumeFPS(fpsnum=nominal_rate.numerator, fpsden=nominal_rate.denominator)
    if job["sample_seconds"]:
        frame_count = max(2, round(job["sample_seconds"] * nominal_rate))
        source = source[:frame_count]
    if source.num_frames < 2:
        raise ValueError("The source needs at least two frames.")

    if job.get("dovi_map"):
        core.std.LoadPlugin(str(PLUGINS / "vszip.avx2.dll"))
        source = core.vszip.Tonemap(source, src_csp=3, dst_csp=1, dst_max=1000.0,
                                    dst_min=0.005, dynamic_peak_detection=False)

        def normalize_range(n, f):
            frame = f.copy()
            # R73's resize filters read _ColorRange. Tonemap writes the newer _Range.
            frame.props["_ColorRange"] = 1 - int(frame.props["_Range"])
            return frame

        source = core.std.ModifyFrame(source, clips=source, selector=normalize_range)

    matrix = job["matrix"]
    range_in = job["range"]
    chroma = job["chroma"]
    width, height = job.get("size", (source.width, source.height))
    original = source.resize.Spline36(
        width=width, height=height, format=vs.YUV420P10, matrix_in_s=matrix,
        range_in_s=range_in, range_s=range_in, chromaloc_in_s=chroma, chromaloc_s=chroma,
        dither_type="ordered",
    )
    sys.path.insert(0, str(RUNTIME))
    runtime = PLUGINS / "vsmlrt-cuda"
    os.environ["PATH"] = str(runtime) + os.pathsep + os.environ["PATH"]
    with os.add_dll_directory(str(runtime)):
        core.std.LoadPlugin(str(PLUGINS / "vstrt.dll"))

    import vsmlrt
    from vsmlrt import Backend, DPIR, DPIRModel, RIFE, RIFEModel

    vsmlrt.trtexec_path = str(runtime / "trtexec.exe")

    def trt(half_io=False):
        return Backend.TRT(fp16=True, num_streams=max(1, round(2 * scale)), workspace=2048,
                           builder_optimization_level=3, output_format=int(half_io), use_cuda_graph=half_io)

    # Clean up before RIFE so real and generated frames get the same treatment.
    if strength := job.get("deblock"):
        print("Preparing DPIR deblocking (TensorRT)...", file=sys.stderr, flush=True)
        clean = original.resize.Bicubic(format=vs.RGBS, matrix_in_s=matrix, range_in_s=range_in,
                                        chromaloc_in_s=chroma)
        pad_w, pad_h = (-clean.width) % 8, (-clean.height) % 8
        # TensorRT cannot build DPIR for a whole 4K frame in this workspace; split big frames into tiles.
        tiles = (-(-clean.width // 1920), -(-clean.height // 1088))
        clean = DPIR(clean.std.AddBorders(right=pad_w, bottom=pad_h), strength=strength,
                     tiles=None if tiles == (1, 1) else tiles,
                     model=DPIRModel.drunet_deblocking_color, backend=trt())
        original = clean.std.Crop(right=pad_w, bottom=pad_h).resize.Bicubic(
            format=vs.YUV420P10, matrix_s=matrix, range_s=range_in, chromaloc_s=chroma,
            dither_type="ordered")
    if job.get("denoise") or job.get("bm3d"):
        if not hasattr(core, "vszip"):
            core.std.LoadPlugin(str(PLUGINS / "vszip.avx2.dll"))
        if sigma := job.get("bm3d"):
            original = core.vszip.BM3D(original.resize.Point(format=vs.YUV420PS), sigma=sigma, radius=1)
            original = original.resize.Point(format=vs.YUV420P10, dither_type="ordered")
        else:
            original = core.vszip.NLMeans(original, d=1, a=2, s=4, h=job["denoise"])
    following = original[1:] + original[-1]
    scores = core.std.PlaneStats(original, following, plane=0)
    # Half-float frames: the engine is fp16 inside anyway, and they halve the data moved per frame.
    rgb = original.resize.Bicubic(format=vs.RGBH, matrix_in_s=matrix, range_in_s=range_in,
                                  chromaloc_in_s=chroma)

    def mark_cut(n, f):
        frame = f[0].copy()
        frame.props["_SceneChangeNext"] = int(f[1].props["PlaneStatsDiff"] > 0.13)
        return frame

    rgb = core.std.ModifyFrame(rgb, clips=[rgb, scores], selector=mark_cut)

    print("Preparing RIFE 4.25 (TensorRT)...", file=sys.stderr, flush=True)
    ratio = Fraction(job["output_rate"]) / nominal_rate
    if ratio.denominator != 1:
        core.std.LoadPlugin(str(PLUGINS / "akarin.dll"))
    pad_w, pad_h = (-rgb.width) % 64, (-rgb.height) % 64
    padded = rgb.std.AddBorders(right=pad_w, bottom=pad_h)
    result = RIFE(padded, multi=ratio, model=RIFEModel.v4_25,
                  backend=trt(half_io=matrix == "709"), _implementation=2)
    result = result.std.Crop(right=pad_w, bottom=pad_h)
    result = result.resize.Bicubic(format=vs.YUV420P10, matrix_s=matrix, range_s=range_in, chromaloc_s=chroma)
    if ratio.denominator == 1:
        # Preserve the resized original frames at each integer source timestamp.
        step = int(ratio)
        result = core.std.Interleave([original, *(result[i::step] for i in range(1, step))])
    return result


if __name__ == "__main__":
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb") as pipe:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        output = frames(job)
        output.output(pipe, y4m=True, prefetch=max(2, round(8 * float(job.get("parallel", 1)))))
