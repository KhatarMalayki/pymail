import unittest

from PyQt5.QtWidgets import QApplication

from core.sig_templates import TEMPLATES, render_tunas, render_tunas_logistic
from core.smtp_client import _data_uris_to_cid
from ui.template_form_dialog import TunasTemplateForm


class SignatureTemplateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_logistic_form_builds_without_crashing(self):
        form = TunasTemplateForm(
            default_name="Yulia Sarasati",
            default_email="administration.staff@mitraanantamegah.com",
            default_role="Finance & Billing Staff",
            template_label="Tunas Logistic",
        )

        self.assertEqual(
            "Tunas Logistic Signature Template",
            form.windowTitle(),
        )
        self.assertEqual("Yulia Sarasati", form.name_edit.text())
        self.assertEqual("Finance & Billing Staff", form.role_edit.text())
        form.close()

    def test_tunas_logistic_template_is_registered_and_self_contained(self):
        html = render_tunas_logistic(
            name="Yulia Sarasati",
            role="Finance & Billing Staff",
            phone="021-7486 1000",
            email="administration.staff@mitraanantamegah.com",
            address="Bintaro Test Address",
        )

        self.assertIn("tunas_logistic", TEMPLATES)
        self.assertIn("Yulia Sarasati", html)
        self.assertIn("Finance & Billing Staff", html)
        self.assertIn("administration.staff@mitraanantamegah.com", html)
        self.assertIn("Bintaro Test Address", html)
        self.assertIn('alt="Tunas Logistic"', html)
        self.assertGreaterEqual(html.count("data:image/png;base64,"), 6)
        self.assertNotRegex(html, r'<img[^>]+src="https?://')

    def test_logistic_images_convert_to_related_content_ids(self):
        html = render_tunas_logistic(name="Test User")
        cid_html, images = _data_uris_to_cid(html)

        self.assertNotIn("data:image/", cid_html)
        self.assertEqual(6, len(images))
        self.assertEqual(6, cid_html.count("cid:"))
        self.assertTrue(all(payload for _cid, _kind, payload in images))

    def test_existing_tunas_rent_template_remains_available(self):
        html = render_tunas(name="Test User")
        self.assertIn("Tunas Rent", TEMPLATES["tunas"]["label"])
        self.assertIn('alt="Tunas Rent"', html)


if __name__ == "__main__":
    unittest.main()
