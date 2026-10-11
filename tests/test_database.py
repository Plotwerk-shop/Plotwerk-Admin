"""Database boundary checks; a real Neon connection needs deployment credentials."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import App
from security import SessionStore, password_hash


class DatabaseTests(unittest.TestCase):
    def test_free_deployment_cannot_fall_back_to_local_database(self):
        with self.assertRaises(RuntimeError):
            App({"REQUIRE_POSTGRES": "true"})

    def test_postgres_verifies_tls_and_binds_values(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch("psycopg.connect", return_value=connection) as connect:
            store = SessionStore("unused.sqlite3", "postgresql://example.invalid/db")
            with store.connect() as db:
                store.lock_owner(db, "42")
                db.execute("SELECT owner FROM sessions WHERE digest=?", ("untrusted' value",))
            self.assertEqual(connect.call_args.kwargs["sslmode"], "verify-full")
            self.assertEqual(connect.call_args.kwargs["sslrootcert"], "system")
            connection.execute.assert_any_call("SELECT pg_advisory_xact_lock(hashtext(%s))", ("42",))
            connection.execute.assert_any_call("SELECT owner FROM sessions WHERE digest=%s", ("untrusted' value",))

    def test_database_failure_does_not_create_sqlite_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fallback.sqlite3"
            with patch("psycopg.connect", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    SessionStore(str(path), "postgresql://example.invalid/db")
            self.assertFalse(path.exists())

    def test_valid_session_survives_reopen_and_new_login_revokes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "sessions.sqlite3")
            store = SessionStore(path)
            password = "long-test-password-only"
            encoded = password_hash(password)
            first = store.login("42", password, encoded, now=1000)
            reopened = SessionStore(path)
            reopened.check(first, "42", now=1001)
            second = reopened.login("42", password, encoded, now=1002)
            from security import Denied
            with self.assertRaises(Denied):
                reopened.check(first, "42", now=1003)
            reopened.check(second, "42", now=1003)
