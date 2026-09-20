import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "feishu"))

import feishu_bridge  # noqa: E402


class _Response:
    def __init__(self, data, start, end, total):
        self._data = data
        self.status = 206
        self.headers = {"Content-Range": f"bytes {start}-{end}/{total}"}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self._data


class AttachmentRangeDownloadTests(unittest.TestCase):
    def test_audio_and_video_use_file_resource_type(self):
        self.assertEqual(feishu_bridge._message_resource_type("file"), "file")
        self.assertEqual(feishu_bridge._message_resource_type("audio"), "file")
        self.assertEqual(feishu_bridge._message_resource_type("video"), "file")
        self.assertEqual(feishu_bridge._message_resource_type("image"), "image")

    def test_downloads_checked_ranges_then_atomically_exposes_file(self):
        payload = b"0123456789abcdefghij"
        requested = []

        def open_range(request, timeout):
            self.assertEqual(timeout, 30)
            header = request.get_header("Range")
            requested.append(header)
            start, end = map(int, header.removeprefix("bytes=").split("-"))
            return _Response(payload[start:end + 1], start, end, len(payload))

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(feishu_bridge, "_tenant_token", return_value="token"):
            path = feishu_bridge._download_message_resource(
                "app", "secret", "om_message", "file_key", resource_type="audio",
                dest_dir=tmp, file_name="recording.m4a", chunk_size=8,
                urlopen=open_range,
            )
            self.assertEqual(path.read_bytes(), payload)
            self.assertFalse(Path(tmp, "recording.m4a.part").exists())

        self.assertEqual(requested, ["bytes=0-0", "bytes=1-8", "bytes=9-16", "bytes=17-19"])

    def test_bad_chunk_leaves_no_completed_or_partial_file(self):
        calls = 0

        def short_second_chunk(request, timeout):
            nonlocal calls
            calls += 1
            start, end = map(int, request.get_header("Range").removeprefix("bytes=").split("-"))
            data = b"x" if calls == 1 else b"short"
            return _Response(data, start, end, 20)

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(feishu_bridge, "_tenant_token", return_value="token"):
            with self.assertRaisesRegex(RuntimeError, "分片不完整"):
                feishu_bridge._download_message_resource(
                    "app", "secret", "om_message", "file_key", resource_type="file",
                    dest_dir=tmp, file_name="recording.m4a", chunk_size=8,
                    urlopen=short_second_chunk,
                )
            self.assertFalse(Path(tmp, "recording.m4a").exists())
            self.assertFalse(Path(tmp, "recording.m4a.part").exists())


if __name__ == "__main__":
    unittest.main()
