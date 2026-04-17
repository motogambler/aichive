import sqlite3
import json
import zlib
import hashlib
import os
import time
from typing import Optional, List, Tuple
try:
    from cryptography.fernet import Fernet
    _HAS_CRYPTO = True
except Exception:
    Fernet = None
    _HAS_CRYPTO = False
import logging
 

DB_PATH = os.environ.get("INTERCEPTOR_DB", "./interceptor_storage.db")
_FERNET_KEY = os.environ.get("INTERCEPTOR_KEY")
import logging

# warn when user has provided an INTERCEPTOR_KEY but cryptography isn't installed
_logger = logging.getLogger(__name__)
if _FERNET_KEY and not _HAS_CRYPTO:
    _logger.warning("INTERCEPTOR_KEY is set but 'cryptography' package is not installed; encryption is disabled. Install 'cryptography' to enable encryption.")


def _get_fernet():
    if not _FERNET_KEY:
        return None
    if Fernet is None:
        return None
    try:
        return Fernet(_FERNET_KEY)
    except Exception:
        return None


class DB:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        self._conn = None
        # create DB file and schema on first use
        self._init()
        self._fernet = _get_fernet()

    def _init(self):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            # reduce on-disk locking by using in-memory journaling for tests and
            # small local DBs; this avoids persistent WAL/SHM files on Windows
            try:
                conn.execute('PRAGMA journal_mode=MEMORY')
                conn.execute('PRAGMA synchronous=OFF')
            except Exception:
                pass
            c = conn.cursor()
            c.execute(
            """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            direction TEXT,
            url TEXT,
            method TEXT,
            ts REAL,
            headers TEXT,
            compressed BLOB,
            hash TEXT,
            metadata TEXT
        )
        """
        )
            c.execute(
            """
        CREATE TABLE IF NOT EXISTS embeddings (
            id INTEGER PRIMARY KEY,
            message_id INTEGER UNIQUE,
            vector BLOB,
            FOREIGN KEY(message_id) REFERENCES messages(id)
        )
        """
            )
            c.execute(
            """
        CREATE TABLE IF NOT EXISTS tokenized (
            id INTEGER PRIMARY KEY,
            message_id INTEGER UNIQUE,
            encoding TEXT,
            compressed_tokens BLOB,
            token_count INTEGER,
            FOREIGN KEY(message_id) REFERENCES messages(id)
        )
        """
            )
            c.execute(
            """
        CREATE TABLE IF NOT EXISTS compactions (
            id INTEGER PRIMARY KEY,
            ts REAL,
            summary TEXT,
            message_ids TEXT
        )
        """
            )
            # chunk store: content-addressed chunks and mapping to messages
            c.execute(
                """
        CREATE TABLE IF NOT EXISTS chunks (
            hash TEXT PRIMARY KEY,
            compressed BLOB,
            size INTEGER,
            refs INTEGER DEFAULT 0
        )
        """
            )
            c.execute(
                """
        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            id INTEGER PRIMARY KEY,
            chunk_hash TEXT UNIQUE,
            vector BLOB,
            FOREIGN KEY(chunk_hash) REFERENCES chunks(hash)
        )
        """
            )
            c.execute(
                """
        CREATE TABLE IF NOT EXISTS chunk_tokenized (
            chunk_hash TEXT PRIMARY KEY,
            encoding TEXT,
            compressed_tokens BLOB,
            token_count INTEGER,
            FOREIGN KEY(chunk_hash) REFERENCES chunks(hash)
        )
        """
            )
            c.execute(
                """
        CREATE TABLE IF NOT EXISTS message_chunks (
            message_id INTEGER,
            chunk_hash TEXT,
            ord INTEGER,
            FOREIGN KEY(message_id) REFERENCES messages(id),
            FOREIGN KEY(chunk_hash) REFERENCES chunks(hash),
            PRIMARY KEY(message_id, ord)
        )
        """
            )
            # index to speed up chunk -> message lookups
            try:
                c.execute("CREATE INDEX IF NOT EXISTS idx_message_chunks_hash ON message_chunks(chunk_hash)")
            except Exception:
                pass
            conn.commit()

    def _maybe_encrypt(self, data: bytes) -> bytes:
        if not data:
            return b""
        if self._fernet:
            return self._fernet.encrypt(data)
        return data

    def _maybe_decrypt(self, data: bytes) -> bytes:
        if not data:
            return b""
        if self._fernet:
            try:
                return self._fernet.decrypt(data)
            except Exception:
                return data
        return data

    def store_message(self, direction: str, url: str, method: str, headers: dict, content: bytes, metadata: Optional[dict] = None) -> int:
        compressed = zlib.compress(content or b"")
        compressed = self._maybe_encrypt(compressed)
        h = hashlib.sha256(content or b"").hexdigest()
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO messages (direction, url, method, ts, headers, compressed, hash, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (direction, url, method, time.time(), json.dumps(headers or {}), compressed, h, json.dumps(metadata or {})),
            )
            conn.commit()
            return c.lastrowid

    def get_all_messages(self) -> List[Tuple]:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT id, direction, url, method, ts, headers, compressed, hash, metadata FROM messages ORDER BY ts DESC")
            return c.fetchall()

    def get_message_content(self, message_id: int) -> bytes:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT compressed FROM messages WHERE id=?", (message_id,))
            row = c.fetchone()
        if not row:
            return b""
        data = row[0]
        data = self._maybe_decrypt(data)
        return zlib.decompress(data)

    # Embedding helpers
    def add_embedding(self, message_id: int, vector: bytes):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO embeddings (message_id, vector) VALUES (?, ?)", (message_id, vector))
            conn.commit()

    def add_compaction(self, summary: str, message_ids: list):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO compactions (ts, summary, message_ids) VALUES (?, ?, ?)", (time.time(), summary, json.dumps(message_ids)))
            conn.commit()
            return c.lastrowid

    def get_compactions(self):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT id, ts, summary, message_ids FROM compactions ORDER BY ts DESC")
            rows = c.fetchall()
        out = []
        for r in rows:
            out.append((r[0], r[1], r[2], json.loads(r[3] or '[]')))
        return out

    def delete_messages_by_ids(self, ids: list) -> int:
        if not ids:
            return 0
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.executemany("DELETE FROM embeddings WHERE message_id = ?", [(i,) for i in ids])
            c.executemany("DELETE FROM tokenized WHERE message_id = ?", [(i,) for i in ids])
            c.executemany("DELETE FROM messages WHERE id = ?", [(i,) for i in ids])
            conn.commit()
            return len(ids)

    def get_all_embeddings(self) -> List[Tuple[int, bytes]]:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT message_id, vector FROM embeddings")
            return c.fetchall()

    def get_unembedded_message_ids(self, limit: int = 1000) -> List[int]:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT m.id FROM messages m LEFT JOIN embeddings e ON m.id=e.message_id WHERE e.message_id IS NULL ORDER BY m.ts ASC LIMIT ?",
                (limit,)
            )
            return [r[0] for r in c.fetchall()]

    def get_untokenized_message_ids(self, limit: int = 1000) -> List[int]:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT m.id FROM messages m LEFT JOIN tokenized t ON m.id=t.message_id WHERE t.message_id IS NULL ORDER BY m.ts ASC LIMIT ?",
                (limit,)
            )
            return [r[0] for r in c.fetchall()]

    def purge_older_than(self, days: int) -> int:
        """Delete messages older than `days`. Returns number of rows deleted."""
        cutoff = time.time() - days * 24 * 3600
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            # find message ids to delete
            c.execute("SELECT id FROM messages WHERE ts < ?", (cutoff,))
            rows = c.fetchall()
            ids = [r[0] for r in rows]
            if not ids:
                return 0
            # delete embeddings and tokenized first
            c.executemany("DELETE FROM embeddings WHERE message_id = ?", [(i,) for i in ids])
            c.executemany("DELETE FROM tokenized WHERE message_id = ?", [(i,) for i in ids])
            c.executemany("DELETE FROM messages WHERE id = ?", [(i,) for i in ids])
            conn.commit()
            return len(ids)

    def store_tokenized(self, message_id: int, encoding: str, compressed_tokens: bytes, token_count: int):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO tokenized (message_id, encoding, compressed_tokens, token_count) VALUES (?, ?, ?, ?)",
                (message_id, encoding, compressed_tokens, token_count),
            )
            conn.commit()

    def get_tokenized(self, message_id: int):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT encoding, compressed_tokens, token_count FROM tokenized WHERE message_id=?", (message_id,))
            row = c.fetchone()
        if not row:
            return None
        return row[0], row[1], row[2]

    # Chunk helpers
    def store_chunk(self, chunk_hash: str, chunk_bytes: bytes) -> None:
        """Store compressed chunk if not exists and increment refs."""
        compressed = zlib.compress(chunk_bytes or b"")
        compressed = self._maybe_encrypt(compressed)
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            try:
                c.execute("INSERT INTO chunks (hash, compressed, size, refs) VALUES (?, ?, ?, ?)",
                          (chunk_hash, compressed, len(chunk_bytes), 1))
            except Exception:
                c.execute("UPDATE chunks SET refs = refs + 1 WHERE hash=?", (chunk_hash,))
            conn.commit()

    def link_message_chunks(self, message_id: int, chunk_hashes: list) -> None:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            for i, h in enumerate(chunk_hashes):
                c.execute("INSERT OR REPLACE INTO message_chunks (message_id, chunk_hash, ord) VALUES (?, ?, ?)",
                          (message_id, h, i))
            conn.commit()

    def get_message_chunks(self, message_id: int) -> list:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT chunk_hash FROM message_chunks WHERE message_id=? ORDER BY ord ASC", (message_id,))
            return [r[0] for r in c.fetchall()]

    def get_messages_for_chunk(self, chunk_hash: str) -> list:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT message_id FROM message_chunks WHERE chunk_hash=? ORDER BY ord ASC", (chunk_hash,))
            return [r[0] for r in c.fetchall()]

    def get_chunk_bytes(self, chunk_hash: str) -> bytes:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT compressed FROM chunks WHERE hash=?", (chunk_hash,))
            row = c.fetchone()
        if not row:
            return b""
        data = row[0]
        data = self._maybe_decrypt(data)
        return zlib.decompress(data)

    def add_chunk_embedding(self, chunk_hash: str, vector: bytes):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO chunk_embeddings (chunk_hash, vector) VALUES (?, ?)", (chunk_hash, vector))
            conn.commit()

    def get_unembedded_chunk_hashes(self, limit: int = 1000) -> list:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT c.hash FROM chunks c LEFT JOIN chunk_embeddings e ON c.hash=e.chunk_hash WHERE e.chunk_hash IS NULL ORDER BY c.hash ASC LIMIT ?",
                (limit,)
            )
            return [r[0] for r in c.fetchall()]

    def get_all_chunk_embeddings(self) -> list:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT chunk_hash, vector FROM chunk_embeddings")
            return c.fetchall()

    def store_chunk_tokenized(self, chunk_hash: str, encoding: str, compressed_tokens: bytes, token_count: int):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO chunk_tokenized (chunk_hash, encoding, compressed_tokens, token_count) VALUES (?, ?, ?, ?)",
                (chunk_hash, encoding, compressed_tokens, token_count),
            )
            conn.commit()

    def get_un_tokenized_chunk_hashes(self, limit: int = 1000) -> list:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT c.hash FROM chunks c LEFT JOIN chunk_tokenized t ON c.hash=t.chunk_hash WHERE t.chunk_hash IS NULL ORDER BY c.hash ASC LIMIT ?",
                (limit,)
            )
            return [r[0] for r in c.fetchall()]

    def get_chunk_tokenized(self, chunk_hash: str):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT encoding, compressed_tokens, token_count FROM chunk_tokenized WHERE chunk_hash=?", (chunk_hash,))
            row = c.fetchone()
        if not row:
            return None
        return row[0], row[1], row[2]

    def rehydrate_message(self, message_id: int) -> bytes:
        hashes = self.get_message_chunks(message_id)
        if not hashes:
            return self.get_message_content(message_id)
        parts = []
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            for h in hashes:
                c.execute("SELECT compressed FROM chunks WHERE hash=?", (h,))
                row = c.fetchone()
                if not row:
                    continue
                data = row[0]
                data = self._maybe_decrypt(data)
                parts.append(zlib.decompress(data))
        return b"".join(parts)

    def dec_ref_chunk(self, chunk_hash: str) -> None:
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("UPDATE chunks SET refs = refs - 1 WHERE hash=?", (chunk_hash,))
            conn.commit()

    def gc_chunks(self) -> int:
        """Delete chunks with refs <= 0. Returns number deleted."""
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
            c = conn.cursor()
            c.execute("SELECT hash FROM chunks WHERE refs <= 0")
            rows = c.fetchall()
            hashes = [r[0] for r in rows]
            if not hashes:
                return 0
            c.executemany("DELETE FROM chunks WHERE hash = ?", [(h,) for h in hashes])
            conn.commit()
            return len(hashes)

    def close(self):
        # no-op: connections are short-lived per-operation
        return

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

