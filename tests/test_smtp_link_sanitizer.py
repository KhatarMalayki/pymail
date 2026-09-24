import unittest

from core.smtp_client import build_message, _strip_hyperlinks, _strip_plaintext_urls


class SMTPLinkSanitizerTests(unittest.TestCase):
    def test_anchor_is_unwrapped_and_visible_text_is_kept(self):
        html = '<p>Open <a href="https://bad.example/x">the report</a>.</p>'
        cleaned = _strip_hyperlinks(html)
        self.assertIn("the report", cleaned)
        self.assertNotIn("<a", cleaned.lower())
        self.assertNotIn("https://", cleaned.lower())

    def test_quoted_links_and_external_attributes_are_removed(self):
        html = (
            '<blockquote><a href="https://old.example">old</a>'
            '<img src="https://tracker.example/pixel" '
            'srcset="https://tracker.example/2x 2x">'
            '</blockquote>'
        )
        cleaned = _strip_hyperlinks(html)
        self.assertIn("old", cleaned)
        self.assertNotIn("http", cleaned.lower())
        self.assertNotIn("srcset", cleaned.lower())

    def test_embedded_cid_image_is_preserved(self):
        cleaned = _strip_hyperlinks('<img src="cid:logo@runlabmail.local">')
        self.assertIn('src="cid:logo@runlabmail.local"', cleaned)

    def test_plain_text_url_is_removed_but_email_address_is_preserved(self):
        cleaned = _strip_plaintext_urls(
            "Visit https://example.com/a or www.example.org. Mail a@example.com"
        )
        self.assertEqual(cleaned.count("[link removed]"), 2)
        self.assertIn("a@example.com", cleaned)

    def test_message_has_no_url_in_plain_or_html_parts(self):
        msg = build_message(
            from_addr="sender@example.com",
            to_list=["recipient@example.com"],
            cc_list=[],
            bcc_list=[],
            subject="test",
            body_text="See https://example.com",
            body_html='<p>See <a href="https://example.com">site</a></p>',
        )
        rendered = msg.as_string()
        self.assertNotIn("https://example.com", rendered)
        self.assertNotIn("href=", rendered.lower())


if __name__ == "__main__":
    unittest.main()
