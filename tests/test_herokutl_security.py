"""SecurityGate serialized-bytes scan (fork 2.3.3).

Upload payloads are skipped when scanning for forbidden TL constructors
(media bytes may randomly contain a forbidden id — that used to block
legitimate uploads), while upload headers, inner messages and wallet
constructors are still rejected.
"""

import struct
import unittest

from herokutl._security import SecurityGate, SecurityViolation

WALLET = 0xD9E5D9E2  # wallet.sendTransfer
SAVE_FILE = 0xB304A621
SAVE_BIG = 0xDE7B673D
INVOKE_AFTER = 0xCB9F372D
CONTAINER = 0x73F1F8DC


def tl_bytes(data: bytes) -> bytes:
    if len(data) < 254:
        prefix = bytes([len(data)])
    else:
        prefix = b"\xfe" + len(data).to_bytes(3, "little")
    out = prefix + data
    return out + b"\x00" * ((-len(out)) % 4)


def message(body: bytes, msg_id: int = 1, seqno: int = 1) -> bytes:
    return (
        struct.pack("<Q", msg_id)
        + struct.pack("<i", seqno)
        + struct.pack("<i", len(body))
        + body
    )


def save_file_body(payload: bytes, file_id: bytes = b"\x00" * 8) -> bytes:
    return struct.pack("<I", SAVE_FILE) + file_id + struct.pack("<i", 0) + tl_bytes(payload)


def save_big_body(payload: bytes, file_id: bytes = b"\x00" * 8) -> bytes:
    return (
        struct.pack("<I", SAVE_BIG)
        + file_id
        + struct.pack("<i", 0)
        + struct.pack("<i", 1)
        + tl_bytes(payload)
    )


DIRTY_PAYLOAD = struct.pack("<I", WALLET) + b"\x00" * 12
POISON_HEADER = struct.pack("<I", WALLET) + b"\x00" * 4
WALLET_BODY = struct.pack("<I", WALLET) + b"\x00" * 4


class SecurityGateTest(unittest.TestCase):
    def test_upload_payload_with_forbidden_id_is_not_flagged(self):
        SecurityGate.check_serialized_batch(message(save_file_body(DIRTY_PAYLOAD)))

    def test_big_upload_payload_with_forbidden_id_is_not_flagged(self):
        SecurityGate.check_serialized_batch(message(save_big_body(DIRTY_PAYLOAD)))

    def test_upload_header_is_still_scanned(self):
        with self.assertRaises(SecurityViolation):
            SecurityGate.check_serialized_batch(
                message(save_file_body(b"clean", file_id=POISON_HEADER))
            )

    def test_wallet_constructor_is_rejected(self):
        with self.assertRaises(SecurityViolation):
            SecurityGate.check_serialized_batch(message(WALLET_BODY))

    def test_container_inner_wallet_is_rejected(self):
        inner = message(WALLET_BODY)
        container = struct.pack("<I", CONTAINER) + struct.pack("<i", 1) + inner
        with self.assertRaises(SecurityViolation):
            SecurityGate.check_serialized_batch(message(container))

    def test_invoke_after_upload_with_dirty_payload_is_not_flagged(self):
        wrapped = (
            struct.pack("<I", INVOKE_AFTER)
            + struct.pack("<q", 0)
            + save_file_body(DIRTY_PAYLOAD)
        )
        SecurityGate.check_serialized_batch(message(wrapped))

    def test_clean_upload_is_not_flagged(self):
        SecurityGate.check_serialized_batch(message(save_file_body(b"hello world")))

    def test_body_level_check_rejects_wallet(self):
        with self.assertRaises(SecurityViolation):
            SecurityGate.check_serialized(WALLET_BODY)

    def test_body_level_check_skips_upload_payload(self):
        SecurityGate.check_serialized(save_file_body(DIRTY_PAYLOAD))


if __name__ == "__main__":
    unittest.main()
