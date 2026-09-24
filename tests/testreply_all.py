import unittest

from core.recipient_utils import build_reply_all_recipients, parse_recipients


class ReplyAllRecipientTests(unittest.TestCase):
    def test_sender_and_original_to_are_in_to_except_self(self):
        to_value, cc_value = build_reply_all_recipients(
            "Anna <anna@example.com>",
            "Me <me@example.com>, Ayak <ayak@example.com>",
            "Hadi <hadi@example.com>",
            "ME@example.com",
        )
        self.assertEqual(
            to_value, "Anna <anna@example.com>, Ayak <ayak@example.com>"
        )
        self.assertEqual(cc_value, "Hadi <hadi@example.com>")

    def test_to_wins_over_cc_and_duplicates_are_case_insensitive(self):
        to_value, cc_value = build_reply_all_recipients(
            "anna@example.com",
            "ANNA@example.com, shared@example.com",
            "Shared <SHARED@example.com>, other@example.com",
            "me@example.com",
        )
        self.assertEqual(to_value, "anna@example.com, shared@example.com")
        self.assertEqual(cc_value, "other@example.com")

    def test_sent_message_excludes_own_sender(self):
        to_value, cc_value = build_reply_all_recipients(
            "Me <me@example.com>",
            "One <one@example.com>, Two <two@example.com>",
            "",
            "me@example.com",
        )
        self.assertEqual(
            to_value, "One <one@example.com>, Two <two@example.com>"
        )
        self.assertEqual(cc_value, "")

    def test_quoted_name_and_outlook_separator_are_preserved(self):
        parsed = parse_recipients(
            '"Last, First" <first@example.com>; Plain <plain@example.com>'
        )
        self.assertEqual(
            parsed,
            [("Last, First", "first@example.com"),
             ("Plain", "plain@example.com")],
        )

    def test_apostrophe_name_and_legacy_quoted_address(self):
        parsed = parse_recipients(
            "O'Connor <oconnor@example.com>; 'legacy@example.com'"
        )
        self.assertEqual(
            parsed,
            [("O'Connor", "oconnor@example.com"),
             ("", "legacy@example.com")],
        )

    def test_lenient_parser_keeps_valid_addresses_from_malformed_header(self):
        parsed = parse_recipients(
            "Valid <valid@example.com>, broken <broken@example.com, "
            "Other <other@example.com>"
        )
        self.assertIn(("Valid", "valid@example.com"), parsed)
        self.assertIn(("Other", "other@example.com"), parsed)


if __name__ == "__main__":
    unittest.main()
