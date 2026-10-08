"""Rich inline responses retain their payload and replace only outgoing commands."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import test_runtime_lifecycle as lifecycle
from test_runtime_performance import load_definition


class RichInlineResponseTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        lifecycle.InlineConstructorTest.setUp(self)
        self.namespace.update({
            "InlineCall": type("InlineCall", (), {}),
            "BotInlineMessage": type("BotInlineMessage", (), {}),
            "BotInlineCall": type("BotInlineCall", (), {}),
            "UpdateBotInlineSend": type("UpdateBotInlineSend", (), {}),
            "make_button": lambda **kwargs: kwargs,
        })
        load_definition("heroku/inline/types.py", "InlineMessage", self.namespace)
        self.answer = load_definition("heroku/utils/messages.py", "answer", self.namespace)
        for name in ("_send_rich_message", "_edit_rich_message"):
            load_definition("heroku/utils/messages.py", name, self.namespace)
        lifecycle.bind_methods(
            type(self.manager), "heroku/inline/utils.py", "Utils", ("_generate_markup",),
            self.namespace,
        )
        lifecycle.bind_methods(
            type(self.manager), "heroku/inline/events.py", "Events", ("_chosen_inline_handler",),
            self.namespace,
        )
        lifecycle.bind_methods(
            type(self.manager), "heroku/inline/form.py", "Form", ("_form_inline_handler",),
            self.namespace,
        )
        self.manager.generate_markup = self.manager._generate_markup
        self.manager._get_button_style = lambda button: None
        self.manager._get_button_emoji_id = lambda button: None
        self.namespace["utils"].array_sum = lambda rows: sum(rows, [])
        self.namespace["utils"].check_url = lambda url: url.startswith("https://")
        self.namespace["get_chat_id"] = lambda message: message.chat_id
        self.namespace["get_topic"] = lambda message: message.topic_id
        self.manager._me = 1
        self.manager._edit_unit = AsyncMock(return_value=True)
        self.manager.bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        self.client = SimpleNamespace(
            heroku_me=SimpleNamespace(premium=False),
            loader=SimpleNamespace(inline=self.manager),
            edit_rich_message=AsyncMock(return_value="native edit"),
            send_rich_message=AsyncMock(return_value="native send"),
        )
        self.message = self.namespace["Message"]()
        self.message.client = self.client
        self.message.id = 10
        self.message.out = True
        self.message.chat_id = self.message.peer_id = 123
        self.message.reply_to_msg_id = None
        self.message.topic_id = None
        self.message.via_bot_id = self.message.fwd_from = None
        self.message.delete = AsyncMock()
        self.message.edit = AsyncMock()
        self.message.respond = AsyncMock()
        self.message.raw_text = "1ratko"
        self.rich = '<h1>ratko</h1><details><summary>test</summary>value</details>'
        self.operations = []
        self.queries = []
        self.invocations = []

        async def delete():
            self.operations.append("delete command")

        async def remove_markup(**kwargs):
            self.operations.append("remove temporary markup")

        self.message.delete.side_effect = delete
        self.manager.bot.edit_message_reply_markup.side_effect = remove_markup

        async def invoke(unit_id, message, **kwargs):
            self.invocations.append((message, kwargs))
            query = SimpleNamespace(
                query=unit_id, from_user=SimpleNamespace(id=self.manager._me),
                rich_article=AsyncMock(),
                builder=SimpleNamespace(article=AsyncMock()),
                answer=AsyncMock(),
            )
            self.queries.append(query)
            await self.manager._form_inline_handler(query)
            query.answer.assert_awaited_once()
            result = query.rich_article if query.rich_article.await_count else query.builder.article
            # Telegram supplies msg_id only for inline results with a keyboard.
            markup = result.call_args.kwargs["buttons"]
            chosen = self.namespace["UpdateBotInlineSend"]()
            chosen.query = unit_id
            chosen.user_id = self.manager._me
            chosen.msg_id = "inline-id" if markup else None
            await self.manager._chosen_inline_handler(chosen)
            self.operations.append("send inline result")
            return SimpleNamespace(chat_id=123, id=20)

        self.manager._invoke_unit = invoke

    async def test_nonpremium_buttonless_rich_replaces_the_command(self):
        result = await self.answer(self.message, rich_message=self.rich)
        self.assertIsInstance(result, self.namespace["InlineMessage"])
        self.message.delete.assert_awaited_once_with()
        self.assertEqual(self.operations, [
            "send inline result", "remove temporary markup", "delete command",
        ])
        self.assertEqual(self.queries[0].rich_article.call_args.kwargs["html"], self.rich)
        self.assertTrue(self.queries[0].rich_article.call_args.kwargs["buttons"])
        self.assertEqual(self.manager._units[result.unit_id]["buttons"], [])
        self.manager._edit_unit.assert_not_awaited()
        self.manager.bot.edit_message_reply_markup.assert_awaited_once_with(
            inline_message_id="inline-id", reply_markup=None,
        )

    async def test_images_and_rich_blocks_are_not_resent_or_replaced_with_plain_text(self):
        rich = '<figure><img src="https://example.invalid/banner.jpg"/></figure>' + self.rich
        result = await self.answer(self.message, rich_message=rich)
        self.assertTrue(result)
        self.queries[0].rich_article.assert_awaited_once()
        self.assertEqual(self.queries[0].rich_article.call_args.kwargs["html"], rich)
        self.manager._edit_unit.assert_not_awaited()
        self.client.send_rich_message.assert_not_awaited()

    async def test_rich_true_uses_the_same_cleanup(self):
        self.assertTrue(await self.answer(self.message, self.rich, rich=True))
        self.message.delete.assert_awaited_once()
        self.assertEqual(self.queries[0].rich_article.call_args.kwargs["html"], self.rich)

    async def test_explicit_form_ttl_does_not_skip_temporary_markup(self):
        result = await self.manager.form(
            text="fallback", message=self.message, rich_message=self.rich, ttl=600,
            silent=True,
        )
        self.assertTrue(result)
        self.message.delete.assert_awaited_once()
        self.manager.bot.edit_message_reply_markup.assert_awaited_once()

    async def test_rich_without_explicit_ttl_does_not_edit_to_fallback_text(self):
        result = await self.manager.form(
            text="fallback", message=self.message, rich_message=self.rich, silent=True,
        )
        self.assertTrue(result)
        self.manager._edit_unit.assert_not_awaited()
        self.assertEqual(self.manager._units[result.unit_id]["rich_message"], self.rich)

    async def test_existing_rich_buttons_are_preserved(self):
        buttons = [[{"text": "keep", "data": "test-callback"}]]
        result = await self.answer(self.message, rich_message=self.rich, reply_markup=buttons)
        self.assertTrue(result)
        self.message.delete.assert_awaited_once()
        self.manager.bot.edit_message_reply_markup.assert_not_awaited()
        self.assertEqual(self.manager._units[result.unit_id]["buttons"], buttons)
        self.assertEqual(self.queries[0].rich_article.call_args.kwargs["buttons"], buttons)

    async def test_incoming_messages_are_never_deleted(self):
        self.message.out = False
        result = await self.answer(self.message, rich_message=self.rich)
        self.assertTrue(result)
        self.message.delete.assert_not_awaited()
        self.assertEqual(self.invocations[0][0], self.message.chat_id)

    async def test_reply_and_topic_are_preserved(self):
        self.message.reply_to_msg_id = 30
        await self.answer(self.message, rich_message=self.rich)
        self.assertEqual(self.invocations[0][1]["reply_to"], 30)
        await self.answer(self.message, rich_message=self.rich, reply_to=40)
        self.assertEqual(self.invocations[1][1]["reply_to"], 40)
        self.message.reply_to_msg_id = None
        self.message.topic_id = 50
        await self.answer(self.message, rich_message=self.rich)
        self.assertEqual(self.invocations[2][1]["reply_to"], 50)

    async def test_premium_native_edit_does_not_delete_the_command(self):
        self.client.heroku_me.premium = True
        self.assertEqual(await self.answer(self.message, rich_message=self.rich), "native edit")
        self.message.delete.assert_not_awaited()
        self.client.edit_rich_message.assert_awaited_once()
        self.assertEqual(self.queries, [])

    async def test_rich_with_premium_fallback_emojis_is_not_edited_as_plain_text(self):
        self.client.heroku_me.premium = True
        self.manager._needs_premium_emoji_pre_edit = Mock(return_value=True)
        result = await self.manager.form(
            text='<tg-emoji emoji-id="123">test</tg-emoji>', message=self.message,
            rich_message=self.rich, silent=True,
        )
        self.assertTrue(result)
        self.manager._edit_unit.assert_not_awaited()
        self.message.delete.assert_awaited_once()

    async def test_failed_send_keeps_the_command(self):
        self.manager._invoke_unit = AsyncMock(side_effect=RuntimeError("synthetic send failure"))
        self.assertFalse(await self.answer(self.message, rich_message=self.rich))
        self.message.delete.assert_not_awaited()
        self.assertEqual(self.manager._units, {})

    async def test_cancelled_send_keeps_the_command(self):
        self.manager._invoke_unit = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.answer(self.message, rich_message=self.rich)
        self.message.delete.assert_not_awaited()
        self.assertEqual(self.manager._units, {})

    async def test_failed_markup_cleanup_keeps_command_and_releases_unit(self):
        self.manager.bot.edit_message_reply_markup.side_effect = RuntimeError("synthetic markup failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic markup failure"):
            await self.answer(self.message, rich_message=self.rich)
        self.message.delete.assert_not_awaited()
        self.assertEqual(self.manager._units, {})

    async def test_cancelled_markup_cleanup_keeps_command_and_releases_unit(self):
        self.manager.bot.edit_message_reply_markup.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.answer(self.message, rich_message=self.rich)
        self.message.delete.assert_not_awaited()
        self.assertEqual(self.manager._units, {})

    async def test_plain_form_with_ttl_still_cleans_up_the_command(self):
        result = await self.manager.form(
            text="plain", message=self.message, ttl=600, silent=True,
        )
        self.assertTrue(result)
        self.message.delete.assert_awaited_once()
        self.manager._edit_unit.assert_awaited_once()
        self.manager.bot.edit_message_reply_markup.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
