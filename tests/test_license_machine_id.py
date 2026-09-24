import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import license as licmod


class MachineIdStabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.machine_file = Path(self.tmp.name) / "machine_id"
        self.file_patch = patch.object(licmod, "MACHINE_ID_FILE", self.machine_file)
        self.file_patch.start()

    def tearDown(self):
        self.file_patch.stop()
        self.tmp.cleanup()

    def test_machine_id_is_persisted_across_identity_source_changes(self):
        with patch.object(licmod, "_native_machine_identity", return_value="guid-a"), \
             patch.object(licmod, "_legacy_fallback_identity", return_value="fallback"):
            first = licmod.get_machine_id()

        with patch.object(licmod, "_native_machine_identity", return_value="guid-b"), \
             patch.object(licmod, "_legacy_fallback_identity", return_value="other"):
            self.assertEqual(licmod.get_machine_id(), first)

    def test_candidates_include_native_and_legacy_ids(self):
        with patch.object(licmod, "_native_machine_identity", return_value="guid"), \
             patch.object(licmod, "_legacy_fallback_identity", return_value="legacy"):
            candidates = licmod.get_machine_id_candidates()
        self.assertIn(licmod._hash_machine_identity("guid"), candidates)
        self.assertIn(licmod._hash_machine_identity("legacy"), candidates)

    def test_bound_legacy_id_is_accepted_and_adopted(self):
        legacy_id = licmod._hash_machine_identity("legacy")
        payload = {"machine_id_hash": legacy_id, "expires_at": "", "license_id": "x"}
        with patch.object(licmod, "verify_signature", return_value=payload), \
             patch.object(licmod, "get_machine_id_candidates", return_value=(
                 licmod._hash_machine_identity("guid"), legacy_id,
             )):
            result = licmod.validate_license({"payload": {}}, check_blacklist=False)
        self.assertEqual(result, payload)
        self.assertEqual(self.machine_file.read_text(encoding="ascii"), legacy_id)

    def test_unrelated_machine_is_still_rejected(self):
        payload = {"machine_id_hash": "f" * 32, "expires_at": "", "license_id": "x"}
        with patch.object(licmod, "verify_signature", return_value=payload), \
             patch.object(licmod, "get_machine_id_candidates", return_value=("a" * 32,)):
            with self.assertRaises(licmod.LicenseError):
                licmod.validate_license({"payload": {}}, check_blacklist=False)

    def test_floating_license_skips_machine_binding(self):
        payload = {"machine_id_hash": "", "expires_at": "", "license_id": "x"}
        with patch.object(licmod, "verify_signature", return_value=payload):
            self.assertEqual(
                licmod.validate_license({"payload": {}}, check_blacklist=False),
                payload,
            )


if __name__ == "__main__":
    unittest.main()
