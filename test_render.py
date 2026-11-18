import io
import unittest
from contextlib import redirect_stderr
from fractions import Fraction

import vapoursynth as vs

from render import protect_lite_frames


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


if __name__ == "__main__":
    unittest.main()
