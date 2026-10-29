from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "TwitchFollows" / "chat_overlay.py"
SPEC = importlib.util.spec_from_file_location("chat_overlay", MODULE_PATH)
assert SPEC and SPEC.loader
chat_overlay = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chat_overlay
SPEC.loader.exec_module(chat_overlay)


class ParseTests(unittest.TestCase):
    def test_privmsg_with_tags(self) -> None:
        line = (
            "@color=#1E90FF;display-name=Big\\sName;emotes= "
            ":bigname!bigname@bigname.tmi.twitch.tv PRIVMSG #chan :hello : world"
        )
        message = chat_overlay.parse_privmsg(line, 5.0)
        self.assertEqual(message.name, "Big Name")
        self.assertEqual(message.color, "#1E90FF")
        self.assertEqual(message.text, "hello : world")
        self.assertFalse(message.action)

    def test_action_without_color_uses_default_palette(self) -> None:
        line = ":someone!someone@x PRIVMSG #chan :\x01ACTION waves\x01"
        message = chat_overlay.parse_privmsg(line, 0.0)
        self.assertTrue(message.action)
        self.assertEqual(message.text, "waves")
        self.assertIn(message.color, chat_overlay.DEFAULT_COLORS)

    def test_ignores_other_commands(self) -> None:
        self.assertIsNone(chat_overlay.parse_privmsg(":tmi JOIN #chan", 0.0))

    def test_twitch_emote_positions_are_sorted_and_bounded(self) -> None:
        line = (
            "@emotes=25:6-10/1902:0-4,12-16,40-44;room-id=123 "
            ":a!a@a PRIVMSG #chan :Kappa Keepo Kappa"
        )
        message = chat_overlay.parse_privmsg(line, 0.0)
        self.assertEqual(
            message.emotes, ((0, 4, "1902"), (6, 10, "25"), (12, 16, "1902"))
        )
        self.assertEqual(chat_overlay.room_id(line), "123")
        self.assertEqual(chat_overlay.room_id(":tmi PRIVMSG #chan :x"), "")


class RenderTests(unittest.TestCase):
    def test_escapes_override_tags_and_colors_name(self) -> None:
        message = chat_overlay.ChatMessage("Ann", "#FF8000", "{\\b1}hi", False, 10.0)
        data = chat_overlay.render([message], 10.0)
        self.assertIn("\\1c&H0080FF&}Ann", data)
        self.assertIn("\\{\\﻿b1\\}hi", data)
        self.assertNotIn("\n", data)

    def test_drops_old_messages_and_keeps_last_lines(self) -> None:
        old = chat_overlay.ChatMessage("Old", "#FFFFFF", "gone", False, 0.0)
        new = [
            chat_overlay.ChatMessage(f"U{i}", "#FFFFFF", "x", False, 500.0)
            for i in range(30)
        ]
        data = chat_overlay.render([old, *new], 500.0)
        self.assertNotIn("Old", data)
        self.assertEqual(data.count("\\N") + 1, chat_overlay.MAX_LINES)
        self.assertIn("U29", data)

    def test_empty_when_nothing_recent(self) -> None:
        self.assertEqual(chat_overlay.render([], 0.0), "")


@unittest.skipUnless(importlib.util.find_spec("PIL"), "needs Pillow")
class ImageLayoutTests(unittest.TestCase):
    def test_narrower_window_rewraps_cached_lines(self) -> None:
        sys.path.insert(0, str(MODULE_PATH.parent))
        self.addCleanup(sys.path.remove, str(MODULE_PATH.parent))
        import chat_image
        import emotes

        renderer = chat_image.ChatRenderer(
            emotes.EmoteCatalog(lambda: None), emotes.EmoteImages(lambda: None)
        )
        message = chat_overlay.ChatMessage(
            "Ann", "#FF8000", "a fairly long chat message " * 3, False, 0.0
        )
        wide = renderer.layout([message], 1280, 720)
        narrow = renderer.layout([message], 300, 720)
        self.assertLessEqual(narrow.width, 300)
        self.assertGreater(narrow.height, wide.height)


if __name__ == "__main__":
    unittest.main()
