"""is_serializable fast path for trivially-serializable exact types."""

import unittest

from heroku.utils.messages import is_serializable


class IsSerializableTest(unittest.TestCase):
    def test_trivial_types(self):
        for value in ("x", "", 0, 1, -5, 1.5, float("nan"), True, False, None):
            with self.subTest(value=value):
                self.assertTrue(is_serializable(value))

    def test_containers(self):
        self.assertTrue(is_serializable({"a": [1, "b", None]}))
        self.assertTrue(is_serializable([1, 2, 3]))

    def test_non_serializable(self):
        self.assertFalse(is_serializable({1, 2}))
        self.assertFalse(is_serializable(object()))

    def test_subclasses_still_fall_back_to_dumps(self):
        class MyStr(str):
            pass

        class MyInt(int):
            pass

        self.assertTrue(is_serializable(MyStr("x")))
        self.assertTrue(is_serializable(MyInt(3)))


if __name__ == "__main__":
    unittest.main()
