"""Dispatcher reaction handling: reaction-only message updates must not
re-execute commands; real text edits must still be processed."""

import unittest
from types import SimpleNamespace

# inline.core participates in a pre-existing import cycle that is only
# resolved when heroku.main is imported first (as the entry point does)
import heroku.main  # noqa: F401
from heroku.dispatcher import CommandDispatcher, MESSAGE_STATES_LIMIT


def make_dispatcher(keep_state=True):
    dispatcher = object.__new__(CommandDispatcher)
    dispatcher._message_states = __import__("collections").OrderedDict()
    dispatcher._modules = SimpleNamespace(
        lookup=lambda name: SimpleNamespace(
            config={"keep_message_state": keep_state}
        )
    )
    return dispatcher


def make_event(text=".ping", chat_id=123, message_id=1, reactions=True):
    message = SimpleNamespace(chat_id=chat_id, id=message_id, message=text)
    return SimpleNamespace(
        message=message,
        reactions=reactions,
    )


class ReactionHandlingTest(unittest.TestCase):
    def test_event_without_reactions_is_not_a_reaction_update(self):
        dispatcher = make_dispatcher()
        self.assertFalse(dispatcher._is_reaction_update(make_event(reactions=False)))

    def test_unknown_message_with_reactions_is_skipped(self):
        dispatcher = make_dispatcher()
        self.assertTrue(dispatcher._is_reaction_update(make_event()))

    def test_remembered_message_with_same_text_is_a_reaction_update(self):
        dispatcher = make_dispatcher()
        event = make_event()
        dispatcher._remember_state(event.message)
        self.assertTrue(dispatcher._is_reaction_update(event))

    def test_remembered_message_with_changed_text_is_not_a_reaction_update(self):
        dispatcher = make_dispatcher()
        event = make_event()
        dispatcher._remember_state(event.message)
        event.message.message = ".ping changed"
        self.assertFalse(dispatcher._is_reaction_update(event))

    def test_other_messages_do_not_match(self):
        dispatcher = make_dispatcher()
        dispatcher._remember_state(make_event(message_id=1).message)
        other = make_event(message_id=2)
        self.assertTrue(dispatcher._is_reaction_update(other))

    def test_disabled_state_means_all_reaction_updates_are_skipped(self):
        dispatcher = make_dispatcher(keep_state=False)
        event = make_event()
        dispatcher._remember_state(event.message)
        self.assertEqual(len(dispatcher._message_states), 0)
        self.assertTrue(dispatcher._is_reaction_update(event))

    def test_state_limit_is_bounded(self):
        dispatcher = make_dispatcher()
        for index in range(MESSAGE_STATES_LIMIT + 25):
            dispatcher._remember_state(make_event(message_id=index).message)
        self.assertEqual(len(dispatcher._message_states), MESSAGE_STATES_LIMIT)
        # The oldest entries are evicted, the newest are kept
        self.assertNotIn((123, 0), dispatcher._message_states)
        self.assertIn(
            (123, MESSAGE_STATES_LIMIT + 24), dispatcher._message_states
        )


if __name__ == "__main__":
    unittest.main()
