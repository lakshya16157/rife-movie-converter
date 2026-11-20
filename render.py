"""Feed RIFE frames to FFmpeg as 10-bit YUV4MPEG2."""
import json
import os
import sys
from fractions import Fraction
from pathlib import Path

RUNTIME = (Path(__file__).resolve().parent / "runtime").resolve()
PLUGINS = RUNTIME / "plugins"


def protect_lite_frames(result, original, ratio):
    import vapoursynth as vs

    # A failed Lite frame can contain patches of zeroed Y, U and V samples.
    # Sample a small grid so checking for those patches does not copy the 4K frame.
    sample = result.resize.Point(width=128, height=72)
    planes = [sample.std.ShufflePlanes(planes=plane, colorfamily=vs.GRAY) for plane in range(3)]
    planes[0] = planes[0].resize.Point(width=64, height=36)
    invalid = vs.core.std.Expr(planes, expr="x 8 <= y 8 <= and z 8 <= and 1023 0 ?")
    scores = invalid.std.PlaneStats()

    def select(n, f):
        tick = n * ratio.denominator
        if tick % ratio.numerator and f.props["PlaneStatsAverage"] >= 0.01:
            source_index = min(tick // ratio.numerator, original.num_frames - 1)
            print(f"RIFE Lite output frame {n} has zeroed color samples. Using source frame {source_index}.",
                  file=sys.stderr, flush=True)
            return original[source_index]
        return result

    return result.std.FrameEval(select, prop_src=scores, clip_src=[result, original])


# RIFE's warp coordinates need full precision in an fp16 engine. The first two lines are vsmlrt's own list:
# https://github.com/AmusementClub/vs-mlrt/issues/66#issuecomment-1791986979
FAST_PRECISION = ("/Cast_2:fp32,/Cast_3:fp32,/Cast_5:fp32,/Cast_7:fp32,/Reciprocal:fp32,/Reciprocal_1:fp32,"
                  "/Mul:fp32,/Mul_1:fp32,/Mul_8:fp32,/Mul_10:fp32,/Sub_5:fp32,/Sub_6:fp32,ONNXTRT_Broadcast_*:fp32,"
                  "fast/scaled:fp32,fast/shifted_x:fp32,fast/shifted:fp32,fast/grid:fp32,fast/grid0:fp32")


def fast_model(model, width, height, shrink, times=None):
    """Write a RIFE variant that estimates motion on a 1/shrink-size picture and warps the full-size frames.

    Televisions interpolate this way: cheap low-resolution motion, full-resolution pixels.
    With times, the input is one frame pair and the output holds an in-between frame for every time.
    Without, the input carries its own time plane, as RIFE's does.
    """
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    count = len(times) if times else 1
    path = model.with_name(f"{model.stem}_fast{shrink}_{count + 1 if times else 'any'}x_{width}x{height}.onnx")
    if path.is_file():
        return path
    if width % shrink or height % shrink:
        raise ValueError(f"{width}x{height} is not divisible by {shrink}.")
    network = onnx.load(model)
    graph = network.graph
    for item in graph.node:
        item.input[:] = ["small" if name == "input" else name for name in item.input]
        item.output[:] = ["small_output" if name == "output" else name for name in item.output]
    original = list(graph.node)

    def const(name, values, kind=np.int64):
        graph.initializer.append(numpy_helper.from_array(np.asarray(values, kind), name))
        return name

    def node(kind, inputs, outputs, **attributes):
        outputs = [outputs] if isinstance(outputs, str) else outputs
        graph.node.append(helper.make_node(kind, inputs, outputs, name="fast/" + outputs[0], **attributes))

    made = {name: item for item in original for name in item.output}
    for item in original:  # the model's reshapes assume one frame pair per call: [2, 3, h, w], [1, 8, h, w], ...
        shape = made.get(item.input[1]) if item.op_type == "Reshape" else None
        if shape is not None and shape.op_type == "Concat" and len(shape.input) == 4:
            lead = numpy_helper.to_array(made[shape.input[0]].attribute[0].t)
            shape.input[0] = const(f"fast_lead_{item.name}", lead * count)

    small_w, small_h = width // shrink, height // shrink
    pool = dict(kernel_shape=[shrink, shrink], strides=[shrink, shrink])
    if times:
        node("AveragePool", ["input"], "small_pair", **pool)
        node("Expand", ["small_pair", const("fast_batch", [count, 6, small_h, small_w])], "small_pairs")
        moments = np.broadcast_to(np.reshape(times, (count, 1, 1, 1)), (count, 1, small_h, small_w))
        node("Concat", ["small_pairs", const("fast_times", moments, np.float32)], "small", axis=1)
    else:
        node("AveragePool", ["input"], "small", **pool)
    head = list(graph.node)[len(original):]

    axes = const("fast_axes", [2, 3])
    origin = const("fast_origin", [0, 0])
    small = const("fast_small", [small_h, small_w])
    # The model pads its input on the right and bottom. Drop that margin from the motion and the blend mask.
    node("Slice", ["/Add_7_output_0", origin, small, axes], "small_flow")
    node("Slice", ["/Sigmoid_output_0", origin, small, axes], "small_mask")
    node("Concat", ["small_flow", "small_mask"], "small_motion", axis=1)
    node("Resize", ["small_motion", "", "", const("fast_size", [count, 5, height, width])], "motion",
         mode="linear", coordinate_transformation_mode="half_pixel")
    node("Split", ["motion", const("fast_parts", [4, 1])], ["flow", "mask"], axis=1)
    node("Reshape", ["flow", const("fast_pairs", [2 * count, 2, height, width])], "flows")
    # Sampling positions run from -1 to 1 across the picture, so a small-picture pixel is 2 / small size.
    node("Mul", ["flows", const("fast_step", np.reshape([2 / small_w, 2 / small_h], (1, 2, 1, 1)), np.float32)],
         "scaled")
    centers_x = np.zeros((1, 2, 1, width), np.float32)
    centers_x[0, 0, 0] = (2 * np.arange(width) + 1) / width - 1
    centers_y = np.zeros((1, 2, height, 1), np.float32)
    centers_y[0, 1, :, 0] = (2 * np.arange(height) + 1) / height - 1
    node("Add", ["scaled", const("fast_x", centers_x, np.float32)], "shifted_x")
    node("Add", ["shifted_x", const("fast_y", centers_y, np.float32)], "shifted")
    node("Transpose", ["shifted"], "grid", perm=[0, 2, 3, 1])
    node("Split", ["grid", const("fast_grids", [2] * count)], [f"grid{i}" for i in range(count)], axis=0)
    node("Split", ["mask", const("fast_masks", [1] * count)], [f"mask{i}" for i in range(count)], axis=0)
    node("Slice", ["input", const("fast_first", [0]), const("fast_last", [6]), const("fast_channel", [1])],
         "pictures")
    node("Reshape", ["pictures", const("fast_pair", [2, 3, height, width])], "pair")
    each = const("fast_each", [1, 1])
    for i in range(count):
        node("GridSample", ["pair", f"grid{i}"], f"warped{i}", mode="bilinear", padding_mode="border",
             align_corners=0)
        node("Split", [f"warped{i}", each], [f"before{i}", f"after{i}"], axis=0)
        node("Sub", [f"before{i}", f"after{i}"], f"difference{i}")
        node("Mul", [f"difference{i}", f"mask{i}"], f"weighted{i}")
        node("Add", [f"after{i}", f"weighted{i}"], f"frame{i}")
    node("Concat", [f"frame{i}" for i in range(count)], "output", axis=1)

    ordered = [*head, *original, *list(graph.node)[len(original) + len(head):]]
    needed, kept = {"output"}, []
    for item in reversed(ordered):  # drop the small-picture warp and everything else the output no longer uses
        if needed.intersection(item.output):
            kept.append(item)
            needed.update(item.input)
    del graph.node[:], graph.value_info[:], graph.input[:], graph.output[:]
    graph.node.extend(reversed(kept))
    # trtexec is given the frame size on its command line and refuses it for a model that declares one.
    graph.input.append(helper.make_tensor_value_info(
        "input", TensorProto.FLOAT, [1, 6 if times else 7, "height", "width"]))
    graph.output.append(helper.make_tensor_value_info(
        "output", TensorProto.FLOAT, [1, 3 * count, "height", "width"]))
    onnx.save(network, path)
    return path


def lookahead(job):
    """Output frames requested ahead of the encoder."""
    # A Fast engine call returns several 4K frames at once; with fewer than 16 in flight the GPU idles.
    return max(2, round((16 if job.get("fast") else 8) * float(job.get("parallel", 1))))


def frames(job):
    import vapoursynth as vs

    core = vs.core
    scale = float(job.get("parallel", 1))  # the app's Parallel processing setting
    # 4K RGB frames are 36 to 73 MB each. Fast keeps every in-between frame of a pair until all are encoded;
    # with the smaller budget those were evicted and computed again.
    core.max_cache_size = max(768, round((8192 if job.get("fast") else 4096) * scale))
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

    lite_model = job.get("lite_model", False)
    model = RIFEModel.v4_25_lite if lite_model else RIFEModel.v4_25
    print(f"Preparing RIFE 4.25{' Lite' if lite_model else ''} (TensorRT)...", file=sys.stderr, flush=True)
    ratio = Fraction(job["output_rate"]) / nominal_rate
    if ratio.denominator != 1:
        core.std.LoadPlugin(str(PLUGINS / "akarin.dll"))
    pad_w, pad_h = (-rgb.width) % 64, (-rgb.height) % 64
    padded = rgb.std.AddBorders(right=pad_w, bottom=pad_h)
    times = None
    if shrink := job.get("fast"):
        # Fast runs the Standard model. Lite returns invalid frames in fp16 on some cards.
        base = PLUGINS / "models/rife_v2/rife_v4.25.onnx"
        # shortcut: one call returns at most five frames; above 6x the engine and its 4K output get too big,
        # so those rates run one frame per call like the fractional ones. Raise the limit if VRAM allows.
        if ratio.denominator == 1 and ratio <= 6:
            times = [i / ratio.numerator for i in range(1, ratio.numerator)]
        fast = trt(half_io=matrix == "709")
        fast.custom_args = ["--precisionConstraints=obey", "--layerPrecisions=" + FAST_PRECISION + "".join(
            f",fast/warped{i}:fp32" for i in range(len(times or [0])))]
        network = str(fast_model(base, padded.width, padded.height, shrink, times))
    if times:
        # One engine call per source pair returns every in-between frame, so each pair is uploaded once.
        planes = vsmlrt.flexible_inference([padded, padded[1:] + padded[-1]], network, backend=fast)
        between = [core.std.ShufflePlanes(planes[3 * i:3 * i + 3], [0, 0, 0], vs.RGB) for i in range(len(times))]
        padded = vsmlrt.bits_as(padded, between[0])
        between = [clip.std.FrameEval(lambda n, f, clip=clip: padded if f.props["_SceneChangeNext"] else clip,
                                      prop_src=padded) for clip in between]
        result = core.std.Interleave([padded, *between])
    else:
        if shrink:
            # RIFE() keeps its fractional frame scheduling; only the network behind it changes.
            vsmlrt.RIFEMerge = lambda clipa, clipb, mask, **_: vsmlrt.inference(
                [clipa, clipb, mask], network, backend=fast)
        result = RIFE(padded, multi=ratio, model=model,
                      backend=trt(half_io=matrix == "709"), _implementation=2)
    result = result.std.Crop(right=pad_w, bottom=pad_h)
    result = result.resize.Bicubic(format=vs.YUV420P10, matrix_s=matrix, range_s=range_in, chromaloc_s=chroma)
    if lite_model:
        result = protect_lite_frames(result, original, ratio)
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
        output.output(pipe, y4m=True, prefetch=lookahead(job))
