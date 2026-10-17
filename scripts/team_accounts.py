"""App-owned team accounts. School addresses are format checked, never ownership verified.

All members using one account have identical access. Profile roles are descriptive.
Passwords, recovery codes and session tokens are stored only as salted hashes/digests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager

ROLES = ("Player", "Captain", "Coach", "Analyst", "Staff", "Other")


class AccountError(ValueError):
    pass


def school_email(value):
    value = str(value).strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+edu", value):
        raise AccountError("Enter a school email address ending in .edu. This checks the format, not ownership.")
    return value


def directory(value):
    if not isinstance(value, list) or len(value) > 100:
        raise AccountError("Use a team directory with at most 100 people.")
    result, ids = [], set()
    for person in value:
        if not isinstance(person, dict):
            raise AccountError("Invalid team member.")
        row = {key: str(person.get(key, "")).strip() for key in ("id", "name", "player", "custom_role")}
        roles = person.get("roles", [])
        if not re.fullmatch(r"[a-f0-9]{32}", row["id"]) or row["id"] in ids:
            raise AccountError("Team members must have unique IDs.")
        if not row["name"] or any(len(row[k]) > 100 for k in ("name", "player", "custom_role")):
            raise AccountError("Names, player usernames and custom roles must be 1–100 characters.")
        if not isinstance(roles, list) or not roles or any(r not in ROLES for r in roles):
            raise AccountError("Choose at least one recognized role.")
        if "Other" in roles and not row["custom_role"]:
            raise AccountError("Name the custom role when choosing Other.")
        row["roles"] = list(dict.fromkeys(roles))
        ids.add(row["id"])
        result.append(row)
    players = [p["player"].casefold() for p in result if p["player"]]
    if len(players) != len(set(players)):
        raise AccountError("Each player username can belong to only one team member.")
    return result


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()


def _password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 256:
        raise AccountError("Use a password with 12–256 characters.")
    return value


class TeamStore:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                  id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                  salt TEXT NOT NULL, password TEXT NOT NULL, recovery TEXT NOT NULL,
                  directory TEXT NOT NULL DEFAULT '[]', revision INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, account TEXT NOT NULL,
                  expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER NOT NULL,
                  until REAL NOT NULL);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.folder / "accounts.db", timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def create(self, email, password, name):
        email, password, name = school_email(email), _password(password), str(name).strip()
        if not name or len(name) > 100:
            raise AccountError("Enter a team name of 1–100 characters.")
        salt, recovery, ident = secrets.token_hex(16), secrets.token_urlsafe(24), secrets.token_hex(16)
        encoded, recovered = _hash(password, salt), _hash(recovery, salt)
        try:
            with self._db() as db:
                db.execute("INSERT INTO accounts(id,email,name,salt,password,recovery) VALUES(?,?,?,?,?,?)",
                           (ident, email, name, salt, encoded, recovered))
        except sqlite3.IntegrityError:
            raise AccountError("This school address already has a team account. Sign in or use its recovery code.") from None
        return recovery

    def _authenticate(self, email, secret, recovery=False, client="local"):
        email = school_email(email)
        if not isinstance(secret, str) or len(secret) > 256:
            raise AccountError("Invalid email or credentials.")
        now = time.time()
        keys = ["email:" + email, "client:" + hashlib.sha256(str(client).encode()).hexdigest()]
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            for key in keys:
                attempt = db.execute("SELECT * FROM attempts WHERE key=?", (key,)).fetchone()
                if attempt and attempt["count"] >= 10 and attempt["until"] > now:
                    raise AccountError("Too many attempts. Try again in 15 minutes.")
            row = db.execute("SELECT * FROM accounts WHERE email=?", (email,)).fetchone()
            # Equal work for unknown addresses; no passwords, codes or tokens in error messages.
            salt = row["salt"] if row else "0" * 32
            valid = hmac.compare_digest(_hash(secret, salt), row["recovery" if recovery else "password"] if row else "0" * 64)
            if not row or not valid:
                for key in keys:
                    db.execute("INSERT INTO attempts VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET "
                               "count=CASE WHEN until < ? THEN 1 ELSE count+1 END, until=?",
                               (key, now + 900, now, now + 900))
                # Commit failed attempts before raising.
                db.commit()
                raise AccountError("Invalid email or credentials.")
            for key in keys:
                db.execute("DELETE FROM attempts WHERE key=?", (key,))
            return dict(row)

    def login(self, email, password, client="local"):
        row = self._authenticate(email, password, client=client)
        token = secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
            inserted = db.execute("INSERT INTO sessions SELECT ?,id,? FROM accounts WHERE id=? AND password=? AND salt=?",
                                 (hashlib.sha256(token.encode()).hexdigest(), time.time() + 12 * 3600,
                                  row["id"], row["password"], row["salt"])).rowcount
            if not inserted:
                raise AccountError("Credentials changed. Sign in again.")
        return token

    def account(self, token):
        if not isinstance(token, str) or len(token) > 200:
            raise AccountError("Sign in to your team account.")
        with self._db() as db:
            row = db.execute("SELECT a.id,a.email,a.name,a.directory,a.revision FROM accounts a JOIN sessions s "
                             "ON a.id=s.account WHERE s.token=? AND s.expires>?",
                             (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        if not row:
            raise AccountError("Your team session expired. Sign in again.")
        result = dict(row)
        result["directory"] = directory(json.loads(result["directory"]))
        return result

    def logout(self, token):
        with self._db() as db:
            db.execute("DELETE FROM sessions WHERE token=?", (hashlib.sha256(token.encode()).hexdigest(),))

    def reset_password(self, email, recovery, password, client="local"):
        _password(password)
        row = self._authenticate(email, recovery, recovery=True, client=client)
        salt, code = secrets.token_hex(16), secrets.token_urlsafe(24)
        with self._db() as db:
            changed = db.execute("UPDATE accounts SET salt=?,password=?,recovery=? WHERE id=? AND recovery=?",
                                (salt, _hash(password, salt), _hash(code, salt), row["id"], row["recovery"])).rowcount
            if not changed:
                raise AccountError("This recovery code has already been used.")
            db.execute("DELETE FROM sessions WHERE account=?", (row["id"],))
        return code

    def save_directory(self, token, people, revision):
        people, account = directory(people), self.account(token)
        with self._db() as db:
            changed = db.execute("UPDATE accounts SET directory=?,revision=revision+1 WHERE id=? AND revision=?",
                                 (json.dumps(people), account["id"], revision)).rowcount
            if not changed:
                raise AccountError("Someone else updated the roster. Refresh before saving your changes.")

    def team_folder(self, token):
        # The account ID always comes from a valid server-side session, never a supplied path.
        folder = self.folder / "teams" / self.account(token)["id"]
        folder.mkdir(parents=True, exist_ok=True)
        return folder
