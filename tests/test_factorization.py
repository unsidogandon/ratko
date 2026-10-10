"""PQ factorization: cryptg fast path with pure-Python fallback.

cryptg's Rust factorizer only accepts login-sized composites (tiny
inputs make it panic); smaller values transparently fall back to the
pure Python implementation.
"""

import sys
import unittest

from herokutl.crypto.factorization import Factorization

try:
    from cryptg import factorize_pq_pair  # noqa: F401

    HAVE_CRYPTG = True
except ImportError:
    HAVE_CRYPTG = False

# Tiny composites (cryptg panics on these -> pure Python fallback)
SMALL_COMPOSITES = [15, 77, 91, 8, 2 * 97]
# Login-sized composites (cryptg's actual workload)
LARGE_COMPOSITES = [
    104729 * 104659,
    2147483647 * 2147483629,
    2147483647 * 2147483587,
]


class FactorizationTest(unittest.TestCase):
    def test_small_composites(self):
        for pq in SMALL_COMPOSITES:
            with self.subTest(pq=pq):
                p, q = Factorization.factorize(pq)
                self.assertEqual(p * q, pq)
                self.assertLess(p, q)

    def test_login_sized_composites(self):
        for pq in LARGE_COMPOSITES:
            with self.subTest(pq=pq):
                p, q = Factorization.factorize(pq)
                self.assertEqual(p * q, pq)
                self.assertLess(p, q)

    def test_rejects_non_composite(self):
        for value in (3, 1, 0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Factorization.factorize(value)

    def test_pure_python_fallback_when_cryptg_unavailable(self):
        saved = sys.modules.get("cryptg")
        sys.modules["cryptg"] = None
        try:
            for pq in SMALL_COMPOSITES + LARGE_COMPOSITES[:1]:
                with self.subTest(pq=pq):
                    p, q = Factorization.factorize(pq)
                    self.assertEqual(p * q, pq)
                    self.assertLess(p, q)
        finally:
            if saved is None:
                sys.modules.pop("cryptg", None)
            else:
                sys.modules["cryptg"] = saved

    @unittest.skipUnless(HAVE_CRYPTG, "cryptg is not installed")
    def test_cryptg_result_matches_public_api(self):
        for pq in LARGE_COMPOSITES:
            with self.subTest(pq=pq):
                p, q = sorted(factorize_pq_pair(pq))
                self.assertEqual((p, q), Factorization.factorize(pq))


if __name__ == "__main__":
    unittest.main()
