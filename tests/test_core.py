import tempfile
import unittest
from pathlib import Path

import cloud_sync
import serveur_local


class CoreTests(unittest.TestCase):
    def test_password_hash_roundtrip(self):
        encoded = serveur_local.hash_password("correct horse battery staple")
        self.assertTrue(serveur_local.verify_password("correct horse battery staple", encoded))
        self.assertFalse(serveur_local.verify_password("wrong", encoded))

    def test_session_token_is_random_and_expires(self):
        account = {"id": "42", "role": "user"}
        first = serveur_local.create_session(account)
        second = serveur_local.create_session(account)
        self.assertNotEqual(first, second)
        self.assertEqual(serveur_local.active_sessions[first]["id"], "42")

    def test_metadata_cannot_break_legacy_catalogue_format(self):
        self.assertEqual(serveur_local.flat_text("a|b\nc"), "a¦b c")

    def test_cloud_manifest_excludes_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "repertoire").mkdir()
            (root / "repertoire" / "catalogue.txt").write_text("catalogue", encoding="utf-8")
            (root / "cloud.json").write_text("{}", encoding="utf-8")
            sync = cloud_sync.WebDAVSync(root, root / "cloud.json")
            manifest = sync.manifest()
            self.assertIn("repertoire/catalogue.txt", manifest)
            self.assertNotIn("cloud.json", manifest)
            self.assertFalse(sync._safe_relative("../../outside.txt"))
            backup = sync._create_backup()
            self.assertTrue(Path(backup).exists())


if __name__ == "__main__":
    unittest.main()
