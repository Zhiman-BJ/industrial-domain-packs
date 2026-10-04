import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return prefix + uuid4().hex[:16]


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


class Store:
    def __init__(self, root: Path):
        self.root = root / ".eda"
        self.root.mkdir(exist_ok=True)
        (self.root / "objects").mkdir(exist_ok=True)
        (self.root / "runs").mkdir(exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, data TEXT, PRIMARY KEY(kind,id))"
            )
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, data TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "metadata.sqlite", timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def put(self, kind, identity, value, *, mutable=False):
        sql = "INSERT OR REPLACE" if mutable else "INSERT"
        with self.connect() as db:
            db.execute(f"{sql} INTO objects VALUES (?, ?, ?)", (kind, identity, encoded(value).decode()))

    def get(self, kind, identity):
        with self.connect() as db:
            row = db.execute("SELECT data FROM objects WHERE kind=? AND id=?", (kind, identity)).fetchone()
        if row is None:
            raise ValueError(f"Unknown {kind}: {identity}")
        return json.loads(row[0])

    def list(self, kind):
        with self.connect() as db:
            rows = db.execute("SELECT data FROM objects WHERE kind=? ORDER BY rowid", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT data FROM metadata WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, encoded(value).decode()))

    def blob(self, content: bytes):
        hash_ = hashlib.sha256(content).hexdigest()
        target = self.root / "objects" / hash_
        if target.exists():
            self.read_blob(hash_)  # Refuse to bless a corrupted existing CAS object.
        else:
            fd, temp = tempfile.mkstemp(dir=target.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                os.replace(temp, target)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return hash_

    def read_blob(self, hash_):
        if len(hash_) != 64 or any(c not in "0123456789abcdef" for c in hash_):
            raise ValueError("Invalid content hash")
        content = (self.root / "objects" / hash_).read_bytes()
        if hashlib.sha256(content).hexdigest() != hash_:
            raise ValueError(f"Corrupt object: {hash_}")
        return content
