"""Outgoing payload compression behavior: upload parts are never
gzip-attempted (their bodies are raw media bytes), everything else
keeps the gzip-if-smaller semantics."""

import inspect
import random
import unittest

from herokutl.extensions.messagepacker import _NO_COMPRESS_REQUESTS
from herokutl.network.mtprotostate import MTProtoState
from herokutl.tl.core.gzippacked import GzipPacked
from herokutl.tl.functions.help import GetConfigRequest
from herokutl.tl.functions.upload import SaveBigFilePartRequest, SaveFilePartRequest


class GzipIfSmallerTest(unittest.TestCase):
    def test_compress_false_skips_compression(self):
        data = b"a" * 2048
        self.assertEqual(GzipPacked.gzip_if_smaller(True, data, compress=False), data)

    def test_compressible_data_still_gzipped_by_default(self):
        data = b"a" * 2048
        result = GzipPacked.gzip_if_smaller(True, data)
        self.assertNotEqual(result, data)
        self.assertLess(len(result), len(data))

    def test_incompressible_data_returned_unchanged(self):
        data = random.Random(0xC0FFEE).randbytes(2048)
        self.assertEqual(GzipPacked.gzip_if_smaller(True, data), data)

    def test_small_or_non_content_data_not_gzipped(self):
        self.assertEqual(GzipPacked.gzip_if_smaller(True, b"abc"), b"abc")
        self.assertEqual(
            GzipPacked.gzip_if_smaller(False, b"a" * 2048), b"a" * 2048
        )


class UploadNoCompressTest(unittest.TestCase):
    def test_upload_requests_are_flagged(self):
        requests = (
            SaveFilePartRequest(file_id=1, file_part=0, bytes=b"x"),
            SaveBigFilePartRequest(
                file_id=1, file_part=0, file_total_parts=1, bytes=b"x"
            ),
        )
        for request in requests:
            self.assertIsInstance(request, _NO_COMPRESS_REQUESTS)

    def test_regular_requests_are_not_flagged(self):
        self.assertNotIsInstance(GetConfigRequest(), _NO_COMPRESS_REQUESTS)

    def test_write_data_as_message_accepts_compress_flag(self):
        parameters = inspect.signature(
            MTProtoState.write_data_as_message
        ).parameters
        self.assertIn("compress", parameters)
        self.assertTrue(parameters["compress"].default)


if __name__ == "__main__":
    unittest.main()

