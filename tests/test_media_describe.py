import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from ai_test_agent.__main__ import main


class MediaProvider:
    provider_id = "fake-media"
    model_id = "fake-vision"
    supports_images = True

    def __init__(self):
        self.calls = 0
        self.images = None

    def generate(self, prompt, schema, images, work_dir):
        self.calls += 1
        self.images = images
        return {
            "payload": {
                "summary": "演示了升级流程",
                "scenes": [{"frame": 1, "description": "点击升级按钮"}],
                "uncertain": [],
            },
            "metadata": {"attached_image_ids": [image["image_id"] for image in images]},
        }


class NoImagesProvider(MediaProvider):
    supports_images = False


class MediaDescribeCliTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / ".local" / ("media-test-" + uuid4().hex)
        self.frames = self.root / "frames"
        self.frames.mkdir(parents=True)
        for index in (1, 2):
            (self.frames / f"frame-{index}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        self.config = self.root / "provider.json"
        self.config.write_text('{"provider":"codex-cli","model":"test"}', encoding="utf-8")
        self.output = self.root / "out" / "media.json"

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_cli(self, frames=None, allow_model=True):
        argv = [
            "describe-media",
            "--frames", str(frames or self.frames),
            "--provider-config", str(self.config),
            "--output", str(self.output),
        ]
        if allow_model:
            argv.append("--allow-model")
        return main(argv)

    def test_requires_allow_model(self):
        provider = MediaProvider()
        with patch("ai_test_agent.media_describe.load_provider", return_value=provider):
            self.assertEqual(self.run_cli(allow_model=False), 2)
        self.assertEqual(provider.calls, 0)
        self.assertFalse(self.output.exists())

    def test_describes_frames_into_json(self):
        provider = MediaProvider()
        with patch("ai_test_agent.media_describe.load_provider", return_value=provider):
            self.assertEqual(self.run_cli(), 0)
        data = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"], "演示了升级流程")
        self.assertEqual(data["frames"], ["frame-1", "frame-2"])
        self.assertEqual(provider.calls, 1)

    def test_empty_frames_are_rejected(self):
        empty = self.root / "empty"
        empty.mkdir()
        with patch("ai_test_agent.media_describe.load_provider", return_value=MediaProvider()):
            self.assertEqual(self.run_cli(frames=empty), 2)

    def test_provider_without_image_support_is_rejected(self):
        provider = NoImagesProvider()
        with patch("ai_test_agent.media_describe.load_provider", return_value=provider):
            self.assertEqual(self.run_cli(), 2)
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()
