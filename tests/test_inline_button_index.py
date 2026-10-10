"""Inline button callback-data index: lookup order, liveness
verification and invalidation semantics of Events._iter_callback_buttons."""

import time
import unittest

from heroku.inline.events import Events


def button(callback_data):
    return {
        "_callback_data": callback_data,
        "callback": lambda *args, **kwargs: None,
    }


def make_manager(units):
    manager = object.__new__(Events)
    manager._units = {
        unit_id: {"type": "form", "buttons": rows} for unit_id, rows in units
    }
    manager._callback_index = None
    manager._callback_index_built_at = 0.0
    return manager


class ButtonIndexTest(unittest.TestCase):
    def test_lookup_order_matches_scan_order(self):
        manager = make_manager(
            [
                ("unit-1", [[button("a"), button("b")], [button("a")]]),
                ("unit-2", [[button("a")], [button("c")]]),
            ]
        )
        found = [
            unit_id for unit_id, _, _ in manager._iter_callback_buttons("a")
        ]
        self.assertEqual(found, ["unit-1", "unit-1", "unit-2"])

        found = [
            unit_id for unit_id, _, _ in manager._iter_callback_buttons("c")
        ]
        self.assertEqual(found, ["unit-2"])

    def test_no_match_yields_nothing(self):
        manager = make_manager([("unit-1", [[button("a")]])])
        self.assertEqual(list(manager._iter_callback_buttons("zzz")), [])

    def test_empty_units(self):
        manager = make_manager([])
        self.assertEqual(list(manager._iter_callback_buttons("a")), [])

    def test_stale_button_not_yielded_without_invalidation(self):
        manager = make_manager([("unit-1", [[button("a")]])])
        # build the index
        self.assertEqual(len(list(manager._iter_callback_buttons("a"))), 1)
        # raw mutation without invalidation (simulates a missed hook)
        manager._units["unit-1"]["buttons"] = []
        self.assertEqual(list(manager._iter_callback_buttons("a")), [])

    def test_removed_unit_not_yielded_without_invalidation(self):
        manager = make_manager([("unit-1", [[button("a")]])])
        self.assertEqual(len(list(manager._iter_callback_buttons("a"))), 1)
        manager._units.pop("unit-1")
        self.assertEqual(list(manager._iter_callback_buttons("a")), [])

    def test_replaced_unit_not_yielded(self):
        manager = make_manager([("unit-1", [[button("a")]])])
        self.assertEqual(len(list(manager._iter_callback_buttons("a"))), 1)
        manager._units["unit-1"] = {"type": "form", "buttons": [[button("a")]]}
        self.assertEqual(list(manager._iter_callback_buttons("a")), [])

    def test_invalidation_rebuilds_and_finds_new_button(self):
        manager = make_manager([("unit-1", [[button("a")]])])
        self.assertEqual(len(list(manager._iter_callback_buttons("a"))), 1)
        manager._units["unit-1"]["buttons"] = [[button("b")]]
        manager._invalidate_button_index()
        self.assertEqual(list(manager._iter_callback_buttons("a")), [])
        found = [unit_id for unit_id, _, _ in manager._iter_callback_buttons("b")]
        self.assertEqual(found, ["unit-1"])

    def test_index_miss_rescan_is_bounded(self):
        manager = make_manager([("unit-1", [[button("a")]])])
        # First lookup builds a fresh index
        self.assertEqual(len(list(manager._iter_callback_buttons("a"))), 1)
        # A button added without invalidation is not found while the
        # index is fresh
        manager._units["unit-1"]["buttons"].append([button("fresh")])
        self.assertEqual(list(manager._iter_callback_buttons("fresh")), [])
        # ... but after the rescan interval the index self-heals
        manager._callback_index_built_at = time.monotonic() - 400
        found = [
            unit_id for unit_id, _, _ in manager._iter_callback_buttons("fresh")
        ]
        self.assertEqual(found, ["unit-1"])

    def test_corrupted_buttons_are_skipped(self):
        manager = make_manager(
            [("unit-1", [["corrupted"], [button("a")]])]
        )
        found = [
            unit_id for unit_id, _, _ in manager._iter_callback_buttons("a")
        ]
        self.assertEqual(found, ["unit-1"])

    def test_corrupted_row_does_not_crash_liveness(self):
        # A non-list in place of a row (before the row holding the
        # matching button) used to raise TypeError from the liveness
        # check (`dict in str`), killing the whole callback handler
        manager = make_manager([("unit-1", ["corrupted", [button("a")]])])
        found = [
            unit_id for unit_id, _, _ in manager._iter_callback_buttons("a")
        ]
        self.assertEqual(found, ["unit-1"])

    def test_invalidation_clears_index(self):
        manager = make_manager([("unit-1", [[button("a")]])])
        manager._build_button_index()
        self.assertIsNotNone(manager._callback_index)
        manager._invalidate_button_index()
        self.assertIsNone(manager._callback_index)


if __name__ == "__main__":
    unittest.main()
