"""Differential test for the optimized markdown parser.

The optimized ``herokutl.extensions.markdown.parse`` (regex
search-jumps instead of per-character probing) must produce results
identical to the original per-character algorithm, which is kept
below verbatim as the reference.
"""

import random
import re
import unittest

from herokutl.extensions.markdown import DEFAULT_DELIMITERS, DEFAULT_URL_RE, parse
from herokutl.helpers import add_surrogate, del_surrogate, strip_text
from herokutl.tl.types import (
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityItalic,
    MessageEntityPre,
    MessageEntityStrike,
    MessageEntityTextUrl,
)


def _reference_parse(message, delimiters=None, url_re=None):
    """The pre-optimization per-character parsing algorithm (reference)."""
    if not message:
        return message, []

    if url_re is None:
        url_re = DEFAULT_URL_RE
    elif isinstance(url_re, str):
        url_re = re.compile(url_re)

    if not delimiters:
        if delimiters is not None:
            return message, []
        delimiters = DEFAULT_DELIMITERS

    delim_re = re.compile(
        "|".join(
            "({})".format(re.escape(k))
            for k in sorted(delimiters, key=len, reverse=True)
        )
    )

    i = 0
    result = []

    message = add_surrogate(message)
    while i < len(message):
        m = delim_re.match(message, pos=i)
        if m:
            delim = next(filter(None, m.groups()))
            end = message.find(delim, i + len(delim) + 1)
            if end != -1:
                message = "".join(
                    (
                        message[:i],
                        message[i + len(delim) : end],
                        message[end + len(delim) :],
                    )
                )

                for ent in result:
                    if ent.offset + ent.length > i:
                        if ent.offset <= i and ent.offset + ent.length >= end + len(
                            delim
                        ):
                            ent.length -= len(delim) * 2
                        else:
                            ent.length -= len(delim)

                ent = delimiters[delim]
                if ent == MessageEntityPre:
                    result.append(ent(i, end - i - len(delim), ""))
                else:
                    result.append(ent(i, end - i - len(delim)))

                if ent in (MessageEntityCode, MessageEntityPre):
                    i = end - len(delim)

                continue

        elif url_re:
            m = url_re.match(message, pos=i)
            if m:
                message = "".join(
                    (message[: m.start()], m.group(1), message[m.end() :])
                )

                delim_size = m.end() - m.start() - len(m.group(1))
                for ent in result:
                    if ent.offset + ent.length > m.start():
                        ent.length -= delim_size

                result.append(
                    MessageEntityTextUrl(
                        offset=m.start(),
                        length=len(m.group(1)),
                        url=del_surrogate(m.group(2)),
                    )
                )
                i += len(m.group(1))
                continue

        i += 1

    message = strip_text(message, result)
    return del_surrogate(message), result


def _signature(entities):
    return [
        (
            type(entity).__name__,
            entity.offset,
            entity.length,
            getattr(entity, "url", None),
            getattr(entity, "language", None),
        )
        for entity in entities
    ]


class MarkdownParseDifferentialTest(unittest.TestCase):
    CASES = [
        "",
        "hello",
        "hello world",
        "**bold**",
        "__italic__",
        "~~strike~~",
        "`code`",
        "```pre```",
        "**bold __italic__**",
        "**bold `code`**",
        "~~**strike bold**~~",
        # unclosed / degenerate delimiters
        "**bold",
        "*bold",
        "****",
        "___",
        "``",
        "**a*",
        "******",
        "****bold****",
        "*__`~~`__*",
        # urls
        "[text](url)",
        "[**bold**](url)",
        "a [b](c) d",
        "**[x](y)**",
        "[x",
        "[x](y",
        "[x](y)",
        "[a](https://x.y/z?w=1)",
        "[a](b(c))",
        "[](url)",
        "[![nested](img)](outer)",
        # emoji / surrogates
        "🔥 **bold** 🔥",
        "**🔥🔥**",
        "🔥**🔥**🔥",
        "**bold**🔥**bold**",
        "`🔥`",
        # mixed
        "**bold** [link](url) `code` **__nested__**",
        "```**code**```",
        "**`code in bold`**",
        "**bold**plain**bold2**",
        "line1\n**bold**\nline2",
        "prefix ```code``` suffix **bold**",
        "text with ` `` ` backticks",
        "**[x](y)** [z](w)",
        "[bold __in__ link](url)",
        "**bold [link](url) more**",
        "plain text with no entities at all",
    ]

    def _compare(self, message, delimiters=None, url_re=None):
        expected_message, expected_entities = _reference_parse(
            message, delimiters, url_re
        )
        actual_message, actual_entities = parse(message, delimiters, url_re)
        self.assertEqual(
            actual_message,
            expected_message,
            f"message mismatch for {message!r}",
        )
        self.assertEqual(
            _signature(actual_entities),
            _signature(expected_entities),
            f"entities mismatch for {message!r}",
        )

    def test_battery(self):
        for case in self.CASES:
            with self.subTest(case=case):
                self._compare(case)

    def test_custom_delimiters(self):
        delimiters = {"*": MessageEntityBold, "~": MessageEntityItalic}
        for case in ("*bold*", "~italic~", "**bold**", "*~mix~*", "*unclosed", "~~x~~"):
            with self.subTest(case=case):
                self._compare(case, delimiters=delimiters)

    def test_custom_url_re(self):
        url_re = r"<(\w*)\|([^>]*)>"
        for case in ("<text|url>", "a <b|c> d", "<unclosed", "**<x|y>**", "<|>"):
            with self.subTest(case=case):
                self._compare(case, url_re=url_re)

    def test_no_delimiters(self):
        self._compare("**bold**", delimiters={})
        self.assertEqual(parse("**bold**", {}), ("**bold**", []))

    def test_fuzz_random_messages(self):
        rng = random.Random(0xC0FFEE)
        alphabet = list("ab *_~`[]()\n🔥 .") + [
            "**",
            "__",
            "~~",
            "```",
            "[x](y)",
        ]
        for _ in range(600):
            message = "".join(
                rng.choice(alphabet) for _ in range(rng.randrange(0, 24))
            )
            with self.subTest(msg=message):
                self._compare(message)

    def test_fuzz_with_custom_delimiters(self):
        rng = random.Random(0xBEEF)
        delimiters = {"*": MessageEntityBold, "~": MessageEntityItalic}
        alphabet = list("ab *~x[]()") + ["*", "~", "**", "~~"]
        for _ in range(400):
            message = "".join(
                rng.choice(alphabet) for _ in range(rng.randrange(0, 20))
            )
            with self.subTest(msg=message):
                self._compare(message, delimiters=delimiters)

    def test_long_message_entities_offsets(self):
        message = "word " * 400 + "**bold** " * 10 + "[link](url) " * 5
        self._compare(message)


if __name__ == "__main__":
    unittest.main()
