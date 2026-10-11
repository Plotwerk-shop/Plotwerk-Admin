"""Owner-only app proxy verification and password/session protection."""
import hashlib
import hmac
import secrets
import sqlite3
import time
from urllib.parse import parse_qs

ADMIN_EMAIL = "info@plotwerk-shop.de"


class Denied(Exception):
    pass


def verify_proxy(query, secret, shop, owner_id, prefix, now=None):
    params = parse_qs(query, keep_blank_values=True, max_num_fields=40)
    for key in ("signature", "shop", "logged_in_customer_id", "timestamp", "path_prefix"):
        if len(params.get(key, [])) != 1:
            raise Denied("Zugriff verweigert.")
    supplied = params.pop("signature")[0]
    message = "".join(sorted(f"{k}={','.join(v)}" for k, v in params.items()))
    expected = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise Denied("Zugriff verweigert.")
    try:
        age = (time.time() if now is None else now) - int(params["timestamp"][0])
    except ValueError:
        raise Denied("Zugriff verweigert.") from None
    if not -30 <= age <= 120:
        raise Denied("Anfrage abgelaufen. Bitte neu laden.")
    if params["shop"][0] != shop or params["path_prefix"][0] != prefix:
        raise Denied("Zugriff verweigert.")
    if not owner_id or params["logged_in_customer_id"][0] != owner_id:
        raise Denied("Zugriff verweigert.")
    return owner_id


def password_hash(password, salt=None):
    if not 16 <= len(password) <= 256:
        raise ValueError("Passwort muss 16 bis 256 Zeichen lang sein.")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def password_matches(password, encoded):
    try:
        kind, salt, digest = encoded.split("$")
        if kind != "scrypt" or len(salt) != 32 or len(digest) != 128:
            return False
        return hmac.compare_digest(password_hash(password, salt), encoded)
    except (ValueError, TypeError):
        return False


class SessionStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, owner TEXT, expires REAL);
                CREATE TABLE IF NOT EXISTS attempts (owner TEXT PRIMARY KEY, count INTEGER, starts REAL);
                CREATE TABLE IF NOT EXISTS audit (at REAL, owner TEXT, action TEXT, resource TEXT);
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=15)

    def login(self, owner, password, encoded, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT count, starts FROM attempts WHERE owner=?", (owner,)).fetchone()
            if row and now - row[1] < 900 and row[0] >= 5:
                raise Denied("Zu viele Versuche. Bitte nach 15 Minuten erneut versuchen.")
            count, starts = (row[0], row[1]) if row and now - row[1] < 900 else (0, now)
            db.execute("INSERT OR REPLACE INTO attempts VALUES (?,?,?)", (owner, count + 1, starts))
        if not password_matches(password, encoded):
            raise Denied("Anmeldung fehlgeschlagen.")
        token = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM attempts WHERE owner=?", (owner,))
            db.execute("DELETE FROM sessions WHERE expires < ? OR owner=?", (now, owner))
            db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), owner, now + 1800))
        return token

    def check(self, token, owner, now=None):
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise Denied("Bitte erneut als Admin anmelden.")
        with self.connect() as db:
            row = db.execute("SELECT owner,expires FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or row[0] != owner or row[1] <= (time.time() if now is None else now):
            raise Denied("Bitte erneut als Admin anmelden.")

    def logout(self, token):
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def audit(self, owner, action, resource):
        with self.connect() as db:
            db.execute("INSERT INTO audit VALUES (?,?,?,?)", (time.time(), owner, action, resource))
