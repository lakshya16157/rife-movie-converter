import io
import unittest
from contextlib import redirect_stderr
from fractions import Fraction

import vapoursynth as vs

from render import PLUGINS, fast_model, protect_lite_frames


def pixels(clip, index):
    with clip.get_frame(index) as frame:
        return tuple(memoryview(frame[plane]).tobytes() for plane in range(frame.format.num_planes))


class LiteFrameProtectionTests(unittest.TestCase):
    def setUp(self):
        self.black = vs.core.std.BlankClip(width=128, height=72, length=1, format=vs.YUV420P10,
                                           color=[0, 512, 512])
        self.green = self.black.std.BlankClip(color=[690, 166, 104])
        self.invalid = self.black.std.BlankClip(color=[0, 0, 0])
        self.patch = vs.core.std.StackHorizontal([self.invalid.std.Crop(right=96),
                                                   self.green.std.Crop(left=32)])

    def test_repairs_only_invalid_interpolated_frames(self):
        original = self.black + self.green + self.black
        candidate = self.black + self.invalid + self.green + self.patch + self.black + self.green
        protected = protect_lite_frames(candidate, original, Fraction(2))
        expected = self.black + self.black + self.green + self.green + self.black + self.green
        log = io.StringIO()
        with redirect_stderr(log):
            for index in range(expected.num_frames):
                self.assertEqual(pixels(protected, index), pixels(expected, index))
        self.assertIn("output frame 1", log.getvalue())
        self.assertIn("output frame 3", log.getvalue())

    def test_fractional_mapping_keeps_exact_source_timestamps(self):
        original = self.black + self.green + self.black
        candidate = self.black + self.green + self.invalid + self.green + self.patch + self.invalid
        protected = protect_lite_frames(candidate, original, Fraction(5, 2))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(pixels(protected, 2), pixels(original, 0))
            self.assertEqual(pixels(protected, 4), pixels(original, 1))
            self.assertEqual(pixels(protected, 5), pixels(candidate, 5))


@unittest.skipUnless((PLUGINS / "models/rife_v2/rife_v4.25.onnx").is_file(), "Run Set Up RIFE.cmd first.")
class FastModelTests(unittest.TestCase):
    def test_writes_a_valid_model_for_each_input_layout(self):
        import onnx

        for times, channels in ((None, (7, 3)), ([0.25, 0.5, 0.75], (6, 9))):
            path = fast_model(PLUGINS / "models/rife_v2/rife_v4.25.onnx", 256, 128, 2, times)
            try:
                onnx.checker.check_model(str(path))
                graph = onnx.load(path).graph
                shape = lambda value: value.type.tensor_type.shape.dim[1].dim_value
                self.assertEqual((shape(graph.input[0]), shape(graph.output[0])), channels)
                self.assertNotIn("/GridSample_4", [node.name for node in graph.node])  # small-picture warp removed
            finally:
                path.unlink()

    def test_rejects_sizes_the_small_picture_cannot_cover(self):
        with self.assertRaises(ValueError):
            fast_model(PLUGINS / "models/rife_v2/rife_v4.25.onnx", 255, 128, 2)


if __name__ == "__main__":
    unittest.main()
