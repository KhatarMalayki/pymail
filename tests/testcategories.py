import tempfile
import unittest
from pathlib import Path

from core import database


class CategoryDatabaseTests(unittest.TestCase):
    def setUp(self):
        self._old_path = database.DB_PATH
        self._tmp = tempfile.TemporaryDirectory()
        database.DB_PATH = str(Path(self._tmp.name) / "mail.db")
        database.init_db()
        self.account_id = database.add_account({
            "name": "Account A", "email": "a@example.com",
        })
        self.other_account_id = database.add_account({
            "name": "Account B", "email": "b@example.com",
        })
        parsed = {
            "uidl": "one", "from": "sender@example.com",
            "to": "a@example.com", "subject": "One",
        }
        self.email_one = database.insert_email(
            self.account_id, "inbox", parsed, []
        )
        parsed = dict(parsed, uidl="two", subject="Two")
        self.email_two = database.insert_email(
            self.account_id, "inbox", parsed, []
        )

    def tearDown(self):
        database.DB_PATH = self._old_path
        self._tmp.cleanup()

    def test_defaults_are_created_once_and_scoped_per_account(self):
        first = database.list_categories(self.account_id)
        second = database.list_categories(self.account_id)
        other = database.list_categories(self.other_account_id)
        self.assertEqual(len(first), 6)
        self.assertEqual([c["id"] for c in first], [c["id"] for c in second])
        self.assertTrue(set(c["id"] for c in first).isdisjoint(
            c["id"] for c in other
        ))

    def test_deleted_defaults_are_not_recreated(self):
        for category in database.list_categories(self.account_id):
            database.delete_category(category["id"])
        self.assertEqual(database.list_categories(self.account_id), [])

    def test_assignment_is_many_to_many_and_idempotent(self):
        category = database.list_categories(self.account_id)[0]
        ids = [self.email_one, self.email_two]
        database.set_category_for_emails(ids, category["id"], True)
        database.set_category_for_emails(ids, category["id"], True)
        self.assertTrue(database.emails_all_have_category(ids, category["id"]))
        mapped = database.get_email_categories_map(ids)
        self.assertEqual(len(mapped[self.email_one]), 1)
        self.assertEqual(len(mapped[self.email_two]), 1)
        database.set_category_for_emails(ids, category["id"], False)
        self.assertFalse(database.emails_all_have_category(ids, category["id"]))

    def test_category_from_other_account_cannot_be_assigned(self):
        foreign = database.list_categories(self.other_account_id)[0]
        database.set_category_for_emails(
            [self.email_one], foreign["id"], True
        )
        self.assertEqual(
            database.get_email_categories_map([self.email_one])[self.email_one],
            [],
        )

    def test_update_and_delete_cascade(self):
        category = database.list_categories(self.account_id)[0]
        database.set_category_for_emails(
            [self.email_one], category["id"], True
        )
        database.update_category(category["id"], "Urgent", "#112233")
        mapped = database.get_email_categories_map([self.email_one])
        self.assertEqual(mapped[self.email_one][0]["name"], "Urgent")
        self.assertEqual(mapped[self.email_one][0]["color"], "#112233")
        database.delete_category(category["id"])
        self.assertEqual(
            database.get_email_categories_map([self.email_one])[self.email_one],
            [],
        )


if __name__ == "__main__":
    unittest.main()
