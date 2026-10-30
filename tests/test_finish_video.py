import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class FinishingTests(unittest.TestCase):
    def test_local_preview_preserves_timing_audio_and_original(self):
        with tempfile.TemporaryDirectory(prefix="flow-finish-test-") as folder:
            source = Path(folder) / "source.mp4"
            output = Path(folder) / "preview.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "smptebars=size=160x90:rate=24:duration=2,eq=brightness=-0.04:enable='gte(t,1)'",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-c:a", "aac", str(source)
            ], check=True)
            original = source.read_bytes()
            command = [sys.executable, "scripts/finish-video.py", str(source), str(output),
                       "--clip-seconds", "1", "--ramp-seconds", ".2", "--motion-fps", "48"]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(original, source.read_bytes())
            metadata = json.loads(subprocess.check_output([
                "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)
            ]))
            video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
            self.assertEqual(video["avg_frame_rate"], "48/1")
            self.assertEqual(int(video["nb_frames"]), 96)
            self.assertAlmostEqual(float(metadata["format"]["duration"]), 2, places=3)

            def audio_hash(path):
                return subprocess.check_output([
                    "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                    "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"
                ])

            self.assertEqual(audio_hash(source), audio_hash(output))
            report = json.loads(output.with_suffix(".json").read_text())
            join = report["joins"][0]
            self.assertTrue(join["accepted"])
            self.assertLess(join["after_mae"], join["before_mae"])
            finished = output.read_bytes()
            refused = subprocess.run(command, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(finished, output.read_bytes())
