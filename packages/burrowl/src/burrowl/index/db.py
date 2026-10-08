"""索引数据库：一个 SQLite 文件，WAL 模式，多进程可同时读、串行写。

    entries        检索条目（三类投影共用一张表，以 corpus 区分）
    entries_fts    FTS5 关键词表（存切分后的词）
    vectors        向量窗口（嵌入 BLOB，按模型指纹隔离），用 sqlite-vec 的 vec_distance_cosine 排序
    index_state    每个 (owner, corpus, channel) 的水位：对应哪个 HEAD、投影版本、模型指纹、状态

水位只在该通道全部写入成功后推进；任何失败都如实记录为 failed/pending，不冒充已就绪。
"""
import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..core.clock import utc_now
from ..core.errors import BurrowlError
from .embed import Embedder, pack
from .tokenize import TOKENIZER_VERSION, fts_document, fts_query

SCHEMA_VERSION = 1
_ENTRY_COLUMNS = ("entry_id", "corpus", "owner_id", "owner_type", "record_id", "canonical_id", "kind", "level",
                  "revision", "content_hash", "block_id", "title", "text", "fixed_ref", "discovery", "sensitivity",
                  "review", "signature")
_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS entries (
    {", ".join(c + (" TEXT PRIMARY KEY" if c == "entry_id" else " INTEGER" if c == "revision" else " TEXT")
               for c in _ENTRY_COLUMNS)});
CREATE INDEX IF NOT EXISTS entries_owner ON entries(owner_id, corpus);
CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(entry_id UNINDEXED, title, text);
CREATE TABLE IF NOT EXISTS vectors (
    window_id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL, model_fp TEXT NOT NULL, owner_id TEXT NOT NULL,
    corpus TEXT NOT NULL, signature TEXT NOT NULL, start INTEGER NOT NULL, "end" INTEGER NOT NULL,
    window_count INTEGER NOT NULL, embedding BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS vectors_entry ON vectors(entry_id, model_fp);
CREATE INDEX IF NOT EXISTS vectors_scope ON vectors(model_fp, corpus, owner_id);
CREATE TABLE IF NOT EXISTS index_state (
    owner_id TEXT NOT NULL, corpus TEXT NOT NULL, channel TEXT NOT NULL, head TEXT, generation INTEGER,
    projection_version TEXT, model_fp TEXT, owner_fingerprint TEXT, signature TEXT,
    status TEXT NOT NULL, error TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(owner_id, corpus, channel));
"""


def _load_vec(db: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec  # noqa: PLC0415 - 可选扩展；缺失时只关闭向量通道
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        return True
    except (ImportError, AttributeError, sqlite3.Error):
        return False


class IndexDB:
    def __init__(self, path: Path, *, embedder: Embedder | None = None):
        self.path = Path(path)
        self.embedder = embedder
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(_SCHEMA)
            stored = dict(db.execute("SELECT key, value FROM meta").fetchall())
            expected = {"schema_version": str(SCHEMA_VERSION), "tokenizer": TOKENIZER_VERSION}
            if stored and stored != expected:
                raise BurrowlError("INTEGRITY_ERROR", "索引文件版本不匹配；删除后重建即可", {"path": str(self.path)})
            db.executemany("INSERT OR IGNORE INTO meta VALUES(?, ?)", expected.items())

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        except sqlite3.Error as exc:
            raise BurrowlError("STORAGE_ERROR", "索引数据库不可用", {"reason": str(exc)}) from exc
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            self.vector_ready = _load_vec(db)
            yield db
        except sqlite3.OperationalError as exc:
            code = "LOCKED" if "locked" in str(exc).lower() or "busy" in str(exc).lower() else "STORAGE_ERROR"
            raise BurrowlError(code, "索引数据库操作失败", {"reason": str(exc)}) from exc
        finally:
            db.close()

    # ---------- 水位 ----------
    def _set_state(self, db: sqlite3.Connection, owner_id: str, corpus: str, channel: str, **values: Any) -> None:
        current = db.execute("SELECT * FROM index_state WHERE owner_id=? AND corpus=? AND channel=?",
                             (owner_id, corpus, channel)).fetchone()
        row = dict(current) if current else {"owner_id": owner_id, "corpus": corpus, "channel": channel,
                                             "status": "pending"}
        row.update(values, updated_at=utc_now())
        columns = list(row)
        db.execute(f"INSERT OR REPLACE INTO index_state ({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
                   [row[c] for c in columns])

    def mark_pending(self, owner_id: str, head: dict | None) -> None:
        """提交发布后立即调用：旧投影不再被视为就绪（即使随后的同步失败）。"""
        with self.connect() as db:
            db.execute("UPDATE index_state SET status='pending', updated_at=? WHERE owner_id=?", (utc_now(), owner_id))

    def states(self, owner_ids: Iterable[str] | None = None) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM index_state").fetchall()
        wanted = set(owner_ids) if owner_ids is not None else None
        return [dict(r) for r in rows if wanted is None or r["owner_id"] in wanted]

    # ---------- 同步 ----------
    def sync_lexical(self, owner_id: str, corpus: str, entries: list[dict], *, head: dict | None,
                     version: str, owner_fingerprint: str, signature: str) -> dict:
        """一个事务内替换该对象该投影的条目与 FTS 行，并推进关键词水位。"""
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                previous = {r["entry_id"]: r["signature"] for r in db.execute(
                    "SELECT entry_id, signature FROM entries WHERE owner_id=? AND corpus=?", (owner_id, corpus))}
                current = {e["entry_id"]: e for e in entries}
                changed = [e for key, e in current.items() if previous.get(key) != e["signature"]]
                removed = set(previous) - set(current)
                for key in removed | {e["entry_id"] for e in changed}:
                    db.execute("DELETE FROM entries_fts WHERE entry_id=?", (key,))
                    db.execute("DELETE FROM entries WHERE entry_id=?", (key,))
                for entry in changed:
                    values = [json.dumps(entry[c], ensure_ascii=False) if c == "fixed_ref" else entry[c]
                              for c in _ENTRY_COLUMNS]
                    db.execute(f"INSERT INTO entries ({','.join(_ENTRY_COLUMNS)}) VALUES "
                               f"({','.join('?' * len(_ENTRY_COLUMNS))})", values)
                    db.execute("INSERT INTO entries_fts(entry_id, title, text) VALUES(?,?,?)",
                               (entry["entry_id"], fts_document(entry["title"]), fts_document(entry["text"])))
                self._set_state(db, owner_id, corpus, "lexical", head=json.dumps(head),
                                generation=(head or {}).get("generation", 0), projection_version=version,
                                owner_fingerprint=owner_fingerprint, signature=signature, status="indexed", error=None)
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return {"changed": len(changed), "removed": len(removed)}

    def sync_vectors(self, owner_id: str, corpus: str, entries: list[dict], *, head: dict | None,
                     version: str, signature: str) -> dict:
        """只重新编码变化的条目；编码在写事务之外完成，写入与水位在同一事务。"""
        if self.embedder is None:
            with self.connect() as db:
                self._set_state(db, owner_id, corpus, "vector", status="disabled", error=None,
                                head=json.dumps(head), projection_version=version)
            return {"status": "disabled"}
        fp = self.embedder.fingerprint
        with self.connect() as db:
            if not self.vector_ready:
                self._set_state(db, owner_id, corpus, "vector", status="unavailable", model_fp=fp,
                                error="sqlite-vec 扩展不可用", head=json.dumps(head))
                return {"status": "unavailable"}
            existing = {}
            for row in db.execute("SELECT entry_id, signature, window_count, COUNT(*) n FROM vectors "
                                  "WHERE owner_id=? AND corpus=? AND model_fp=? GROUP BY entry_id, signature",
                                  (owner_id, corpus, fp)):
                existing[row["entry_id"]] = (row["signature"], row["window_count"] == row["n"])
        current = {e["entry_id"]: e for e in entries}
        changed = [e for key, e in current.items() if existing.get(key) != (e["signature"], True)]
        removed = set(existing) - set(current)
        windows = []
        for entry in changed:
            for window in self.embedder.windows(entry["title"], entry["text"]):
                windows.append((entry, window))
        try:
            vectors = self.embedder.embed_passages([w.text for _e, w in windows]) if windows else []
        except Exception as exc:
            with self.connect() as db:
                self._set_state(db, owner_id, corpus, "vector", status="failed", model_fp=fp,
                                error=f"{type(exc).__name__}: {exc}"[:500], head=json.dumps(head))
            raise
        counts: dict[str, int] = {}
        for entry, _window in windows:
            counts[entry["entry_id"]] = counts.get(entry["entry_id"], 0) + 1
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                for key in removed | {e["entry_id"] for e in changed}:
                    db.execute("DELETE FROM vectors WHERE entry_id=? AND model_fp=?", (key, fp))
                db.executemany(
                    'INSERT INTO vectors(entry_id, model_fp, owner_id, corpus, signature, start, "end", window_count, '
                    "embedding) VALUES(?,?,?,?,?,?,?,?,?)",
                    [(e["entry_id"], fp, owner_id, corpus, e["signature"], w.start, w.end, counts[e["entry_id"]],
                      pack(v)) for (e, w), v in zip(windows, vectors, strict=True)])
                self._set_state(db, owner_id, corpus, "vector", head=json.dumps(head),
                                generation=(head or {}).get("generation", 0), projection_version=version,
                                model_fp=fp, signature=signature, status="indexed", error=None)
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return {"status": "indexed", "encoded_entries": len(changed), "windows": len(windows), "removed": len(removed)}

    def drop_owner(self, owner_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, in db.execute("SELECT entry_id FROM entries WHERE owner_id=?", (owner_id,)).fetchall():
                db.execute("DELETE FROM entries_fts WHERE entry_id=?", (key,))
            for table in ("entries", "vectors", "index_state"):
                db.execute(f"DELETE FROM {table} WHERE owner_id=?", (owner_id,))
            db.execute("COMMIT")

    # ---------- 查询 ----------
    @staticmethod
    def _where(corpus: str, owner_ids: list[str], *, levels: list[str] | None, kinds: list[str] | None,
               review: list[str] | None, explicit_owners: bool) -> tuple[str, list]:
        clauses = ["e.corpus = ?", f"e.owner_id IN ({','.join('?' * len(owner_ids))})", "e.sensitivity != 'restricted'"]
        params: list[Any] = [corpus, *owner_ids]
        if not explicit_owners:  # 未显式指定对象时，owner_only 记录不参与跨对象检索
            clauses.append("e.discovery != 'owner_only'")
        for column, values in (("level", levels), ("kind", kinds), ("review", review)):
            if values is not None:
                clauses.append(f"e.{column} IN ({','.join('?' * len(values))})")
                params.extend(values)
        return " AND ".join(clauses), params

    def search_lexical(self, corpus: str, query: str, owner_ids: list[str], *, limit: int = 100,
                       levels: list[str] | None = None, kinds: list[str] | None = None,
                       review: list[str] | None = None, explicit_owners: bool = False) -> list[dict]:
        expression = fts_query(query)
        if not expression or not owner_ids or limit <= 0:
            return []
        where, params = self._where(corpus, owner_ids, levels=levels, kinds=kinds, review=review,
                                    explicit_owners=explicit_owners)
        with self.connect() as db:
            rows = db.execute(f"SELECT e.*, bm25(entries_fts) AS score FROM entries_fts f "
                              f"JOIN entries e ON e.entry_id = f.entry_id WHERE entries_fts MATCH ? AND {where} "
                              f"ORDER BY score LIMIT ?", [expression, *params, limit]).fetchall()
        return [_decode(r) | {"channel": "lexical", "raw_score": -r["score"]} for r in rows]

    def search_vector(self, corpus: str, query: str, owner_ids: list[str], *, limit: int = 100,
                      levels: list[str] | None = None, kinds: list[str] | None = None,
                      review: list[str] | None = None, explicit_owners: bool = False) -> list[dict]:
        if self.embedder is None or not owner_ids or limit <= 0:
            return []
        vector = pack(self.embedder.embed_query(query))
        where, params = self._where(corpus, owner_ids, levels=levels, kinds=kinds, review=review,
                                    explicit_owners=explicit_owners)
        with self.connect() as db:
            if not self.vector_ready:
                raise BurrowlError("CAPABILITY_UNAVAILABLE", "sqlite-vec 扩展不可用")
            rows = db.execute(
                f"SELECT e.*, MIN(vec_distance_cosine(v.embedding, ?)) AS distance FROM vectors v "
                f"JOIN entries e ON e.entry_id = v.entry_id WHERE v.model_fp = ? AND v.signature = e.signature "
                f"AND {where} GROUP BY e.entry_id ORDER BY distance LIMIT ?",
                [vector, self.embedder.fingerprint, *params, limit]).fetchall()
        return [_decode(r) | {"channel": "dense", "raw_score": 1 - r["distance"]} for r in rows]

    def search_identity(self, corpus: str, identifiers: list[str], owner_ids: list[str]) -> list[dict]:
        if not identifiers or not owner_ids:
            return []
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM entries e WHERE e.corpus=? AND e.block_id IS NULL AND e.sensitivity!='restricted' "
                f"AND e.owner_id IN ({','.join('?' * len(owner_ids))}) "
                f"AND (e.canonical_id IN ({','.join('?' * len(identifiers))}) "
                f"OR e.record_id IN ({','.join('?' * len(identifiers))}))",
                [corpus, *owner_ids, *identifiers, *identifiers]).fetchall()
        return [_decode(r) | {"channel": "identity", "raw_score": 1.0} for r in rows]

    def entries(self, entry_ids: list[str]) -> list[dict]:
        if not entry_ids:
            return []
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM entries WHERE entry_id IN ({','.join('?' * len(entry_ids))})",
                              entry_ids).fetchall()
        return [_decode(r) for r in rows]


def _decode(row: sqlite3.Row) -> dict:
    value = {c: row[c] for c in _ENTRY_COLUMNS}
    value["fixed_ref"] = json.loads(value["fixed_ref"])
    return value
