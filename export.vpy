"""Give FFmpeg ordered frames from the app's VapourSynth graph."""
import json
import os
import sys
from pathlib import Path

import vapoursynth as vs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import frames, lookahead

job = json.loads(Path(os.environ["RIFE_JOB"]).read_text(encoding="utf-8"))
clip = frames(job)
depth = lookahead(job)
pending = {}


def provide_frame(n, f):
    # FFmpeg reads in order. Keep future GPU requests running while it encodes.
    if n not in pending:
        pending.clear()
    for index in range(n, min(n + depth, clip.num_frames)):
        if index not in pending:
            pending[index] = clip.get_frame_async(index)
    return pending.pop(n).result()


if vs.core.num_threads > 1:
    blank = clip.std.BlankClip(keep=True)
    blank.std.ModifyFrame(blank, provide_frame).set_output()
else:
    clip.set_output()
