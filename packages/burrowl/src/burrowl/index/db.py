"""索引数据库：一个 SQLite 文件，WAL 模式，多进程可同时读、串行写。

    entries        检索条目（三类投影共用一张表，以 corpus 区分）
    entries_fts    FTS5 关键词表（存切分后的词）
    vectors        向量窗口（嵌入 BLOB，按模型指纹隔离），用 sqlite-vec 的 vec_distance_cosine 排序
    index_state    每个 (owner, corpus, channel) 的水位：对应哪个 HEAD、投影版本、模型指纹、状态
    graph_nodes    图投影的节点（R12 §4.10；按投影它的对象分行，来源节点可在多个对象下各有一行）
    graph_edges    图投影的边（由声明它的对象负责写入和删除；另一端缺失时保留，查询时排除并报告）
    link_candidates 待判断的关联与概念链接候选（R12 T-P2.10；派生数据，只是线索）

水位只在该通道全部写入成功后推进；任何失败都如实记录为 failed/pending，不冒充已就绪。
索引是派生数据：结构版本或分词规则不匹配时整库清空重建（R12 T-P1.5），由 reconcile 补齐。
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
from .late import late_windows
from .tokenize import TOKENIZER_VERSION, fts_document, fts_query

SCHEMA_VERSION = 5  # v2：entries.embed_prefix；v3：graph_nodes / graph_edges；v4：link_candidates；v5：graph_nodes.ref
_ENTRY_COLUMNS = ("entry_id", "corpus", "owner_id", "owner_type", "record_id", "canonical_id", "kind", "level",
                  "revision", "content_hash", "block_id", "title", "text", "fixed_ref", "discovery", "sensitivity",
                  "review", "signature", "embed_prefix")
_TABLES = ("entries_fts", "entries", "vectors", "index_state", "graph_nodes", "graph_edges", "link_candidates",
           "link_candidate_state", "meta")
NODE_COLUMNS = ("owner_id", "node_id", "node_type", "record_id", "title", "level", "kind", "domains", "sensitivity",
                "discovery", "review", "maturity", "block_id", "ref")
_JSON_NODE_COLUMNS = {"domains", "ref"}
EDGE_COLUMNS = ("edge_id", "src", "dst", "category", "type", "certainty", "state", "origin", "declared_by",
                "declared_owner", "declared_rev", "field", "derived")
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
CREATE TABLE IF NOT EXISTS graph_nodes (
    owner_id TEXT NOT NULL, node_id TEXT NOT NULL, node_type TEXT NOT NULL, record_id TEXT, title TEXT, level TEXT,
    kind TEXT, domains TEXT, sensitivity TEXT, discovery TEXT, review TEXT, maturity TEXT, block_id TEXT, ref TEXT,
    PRIMARY KEY(owner_id, node_id));
CREATE INDEX IF NOT EXISTS graph_nodes_id ON graph_nodes(node_id);
CREATE TABLE IF NOT EXISTS graph_edges (
    edge_id TEXT PRIMARY KEY, src TEXT NOT NULL, dst TEXT NOT NULL, category TEXT NOT NULL, type TEXT NOT NULL,
    certainty TEXT, state TEXT, origin TEXT, declared_by TEXT NOT NULL, declared_owner TEXT NOT NULL,
    declared_rev INTEGER, field TEXT, derived INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS graph_edges_src ON graph_edges(src);
CREATE INDEX IF NOT EXISTS graph_edges_dst ON graph_edges(dst);
CREATE INDEX IF NOT EXISTS graph_edges_owner ON graph_edges(declared_owner);
CREATE TABLE IF NOT EXISTS link_candidates (
    owner_id TEXT NOT NULL, src TEXT NOT NULL, dst TEXT NOT NULL, kind TEXT NOT NULL, score REAL NOT NULL,
    origin TEXT NOT NULL, computed_at TEXT NOT NULL, PRIMARY KEY(src, dst, kind, origin));
CREATE INDEX IF NOT EXISTS link_candidates_owner ON link_candidates(owner_id);
CREATE TABLE IF NOT EXISTS link_candidate_state (
    record_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, revision INTEGER NOT NULL, signature TEXT NOT NULL);
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
        self.rebuilt_from: dict | None = None  # 本次打开时因版本不匹配而清空的旧版本（供状态报告）
        expected = {"schema_version": str(SCHEMA_VERSION), "tokenizer": TOKENIZER_VERSION}
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            stored = dict(db.execute("SELECT key, value FROM meta").fetchall())
            if stored and stored != expected:
                # 派生数据，不报错要求用户删文件：整库清空，所有对象变为 not_indexed，由 reconcile 重建
                db.execute("BEGIN IMMEDIATE")
                for table in _TABLES:
                    db.execute(f"DROP TABLE IF EXISTS {table}")
                db.execute("COMMIT")
                db.execute("VACUUM")
                self.rebuilt_from = stored
            db.executescript(_SCHEMA)
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
        try:
            if getattr(self.embedder, "late", False):
                windows, vectors = late_windows(self.embedder, changed, list(current.values()))
            else:
                for entry in changed:
                    for window in self.embedder.windows(entry["embed_prefix"] or entry["title"], entry["text"]):
                        windows.append((entry, window))
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
                # 换模型或切窗规则后，旧指纹的窗口不会再被查询，顺带清掉以免索引文件只增不减
                db.execute("DELETE FROM vectors WHERE owner_id=? AND corpus=? AND model_fp!=?", (owner_id, corpus, fp))
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

    def sync_graph(self, owner_id: str, nodes: list[dict], edges: list[dict], *, head: dict | None, version: str,
                   owner_fingerprint: str, signature: str) -> dict:
        """一个事务内替换该对象投影的节点与它声明的边，并推进图水位（corpus=graph，channel=edges）。"""
        with self.connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                db.execute("DELETE FROM graph_nodes WHERE owner_id=?", (owner_id,))
                db.execute("DELETE FROM graph_edges WHERE declared_owner=?", (owner_id,))
                db.executemany(f"INSERT INTO graph_nodes ({','.join(NODE_COLUMNS)}) VALUES "
                               f"({','.join('?' * len(NODE_COLUMNS))})",
                               [[json.dumps(n.get(c) if c == "ref" else (n.get(c) or []), ensure_ascii=False)
                                 if c in _JSON_NODE_COLUMNS else n.get(c) for c in NODE_COLUMNS] for n in nodes])
                db.executemany(f"INSERT OR REPLACE INTO graph_edges ({','.join(EDGE_COLUMNS)}) VALUES "
                               f"({','.join('?' * len(EDGE_COLUMNS))})",
                               [[int(e[c]) if c == "derived" else e.get(c) for c in EDGE_COLUMNS] for e in edges])
                self._set_state(db, owner_id, "graph", "edges", head=json.dumps(head),
                                generation=(head or {}).get("generation", 0), projection_version=version,
                                owner_fingerprint=owner_fingerprint, signature=signature, status="indexed", error=None)
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return {"nodes": len(nodes), "edges": len(edges)}

    def graph_edges(self, node_ids: list[str], *, categories: list[str] | None = None) -> list[dict]:
        """与给定节点相连的边（两个方向）。只返回两端节点都存在的边（悬空边由 graph_dangling 报告）。"""
        if not node_ids:
            return []
        marks = ",".join("?" * len(node_ids))
        where = f"(e.src IN ({marks}) OR e.dst IN ({marks}))"
        params: list[Any] = [*node_ids, *node_ids]
        if categories is not None:
            where += f" AND e.category IN ({','.join('?' * len(categories))})"
            params += categories
        with self.connect() as db:
            rows = db.execute(f"SELECT DISTINCT e.* FROM graph_edges e WHERE {where} "
                              f"AND EXISTS (SELECT 1 FROM graph_nodes a WHERE a.node_id = e.src) "
                              f"AND EXISTS (SELECT 1 FROM graph_nodes b WHERE b.node_id = e.dst)", params).fetchall()
        return [dict(r) | {"derived": bool(r["derived"])} for r in rows]

    def graph_nodes(self, node_ids: list[str] | None = None) -> list[dict]:
        """节点（同一节点在多个对象下有行时只取一行，优先取其归属对象自己投影的那一行）。"""
        with self.connect() as db:
            if node_ids is None:
                rows = db.execute("SELECT * FROM graph_nodes").fetchall()
            elif not node_ids:
                rows = []
            else:
                rows = db.execute(f"SELECT * FROM graph_nodes WHERE node_id IN ({','.join('?' * len(node_ids))})",
                                  node_ids).fetchall()
        found: dict[str, dict] = {}
        for row in rows:
            value = dict(row) | {"domains": json.loads(row["domains"] or "[]"), "ref": json.loads(row["ref"] or "null")}
            if row["node_id"] not in found or row["node_type"] != "source":
                found.setdefault(row["node_id"], value)
        return list(found.values())

    def graph_degrees(self, node_ids: list[str]) -> dict[str, int]:
        if not node_ids:
            return {}
        marks = ",".join("?" * len(node_ids))
        with self.connect() as db:
            rows = db.execute(f"SELECT node, COUNT(*) n FROM (SELECT src AS node FROM graph_edges WHERE src IN ({marks}) "
                              f"UNION ALL SELECT dst FROM graph_edges WHERE dst IN ({marks})) GROUP BY node",
                              [*node_ids, *node_ids]).fetchall()
        return {r["node"]: r["n"] for r in rows}

    def entries_for_nodes(self, corpus: str, node_ids: list[str], owner_ids: list[str], *,
                          levels: list[str] | None = None, kinds: list[str] | None = None,
                          review: list[str] | None = None, explicit_owners: bool = False) -> list[dict]:
        """把图节点映射回当前投影的检索条目，并施加与其他路线相同的过滤（层级、类型、复核、敏感级别、
        owner_only）。记录节点 → 记录级条目；块节点 → 块条目；结论节点 → 结论条目；映射不到的节点丢弃。"""
        if not node_ids or not owner_ids:
            return []
        where, params = self._where(corpus, owner_ids, levels=levels, kinds=kinds, review=review,
                                    explicit_owners=explicit_owners)
        plain = [n for n in node_ids if "#" not in n]
        blocks = [n.split("#", 1) for n in node_ids if "#" in n]
        clauses, values = [], []
        if plain:
            marks = ",".join("?" * len(plain))
            clauses.append(f"((e.record_id IN ({marks}) AND e.block_id IS NULL AND e.kind != 'claim') "
                           f"OR (e.kind = 'claim' AND e.canonical_id IN ({marks})))")
            values += [*plain, *plain]
        for record_id, block_id in blocks:
            clauses.append("(e.record_id = ? AND e.block_id = ?)")
            values += [record_id, block_id]
        with self.connect() as db:
            rows = db.execute(f"SELECT e.* FROM entries e WHERE {where} AND ({' OR '.join(clauses)})",
                              [*params, *values]).fetchall()
        return [_decode(r) for r in rows]

    def graph_all_edges(self) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM graph_edges").fetchall()
        return [dict(r) | {"derived": bool(r["derived"])} for r in rows]

    def graph_dangling(self) -> int:
        """另一端节点不存在（或不可读未投影）的边数。"""
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM graph_edges e WHERE NOT EXISTS "
                              "(SELECT 1 FROM graph_nodes a WHERE a.node_id = e.src) OR NOT EXISTS "
                              "(SELECT 1 FROM graph_nodes b WHERE b.node_id = e.dst)").fetchone()[0]

    def drop_owner(self, owner_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for key, in db.execute("SELECT entry_id FROM entries WHERE owner_id=?", (owner_id,)).fetchall():
                db.execute("DELETE FROM entries_fts WHERE entry_id=?", (key,))
            for table in ("entries", "vectors", "index_state", "graph_nodes"):
                db.execute(f"DELETE FROM {table} WHERE owner_id=?", (owner_id,))
            db.execute("DELETE FROM graph_edges WHERE declared_owner=?", (owner_id,))
            for table in ("link_candidates", "link_candidate_state"):
                db.execute(f"DELETE FROM {table} WHERE owner_id=?", (owner_id,))
            db.execute("COMMIT")

    # ---------- 关联候选（R12 T-P2.10）----------
    def candidate_state(self, owner_id: str) -> dict[str, str]:
        with self.connect() as db:
            return {r["record_id"]: r["signature"] for r in db.execute(
                "SELECT record_id, signature FROM link_candidate_state WHERE owner_id=?", (owner_id,))}

    def replace_candidates(self, owner_id: str, computed: dict[str, tuple[str, list[dict]]], removed: set[str]) -> None:
        """computed：record_id → (签名, 候选列表)。只替换程序自己算出的候选（origin=index），外部回流的保留。"""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for record_id in set(computed) | removed:
                db.execute("DELETE FROM link_candidates WHERE src=? AND origin='index'", (record_id,))
                db.execute("DELETE FROM link_candidate_state WHERE record_id=?", (record_id,))
            now = utc_now()
            for record_id, (signature, rows) in computed.items():
                db.execute("INSERT INTO link_candidate_state VALUES(?,?,?,?)", (record_id, owner_id, 0, signature))
                db.executemany("INSERT OR REPLACE INTO link_candidates VALUES(?,?,?,?,?,?,?)",
                               [(owner_id, r["src"], r["dst"], r["kind"], r["score"], "index", now) for r in rows])
            db.execute("COMMIT")

    def add_candidates(self, owner_id: str, rows: list[dict], origin: str) -> int:
        """外部工具回流的候选（origin=tool:<id>，R12 T-P4.3）；从不直接写成记录。"""
        now = utc_now()
        with self.connect() as db:
            db.executemany("INSERT OR REPLACE INTO link_candidates VALUES(?,?,?,?,?,?,?)",
                           [(owner_id, r["src"], r["dst"], r["kind"], float(r.get("score", 0)), origin, now)
                            for r in rows])
        return len(rows)

    def candidates(self, *, record_ids: list[str] | None = None, owner_id: str | None = None) -> list[dict]:
        clauses, params = [], []
        if record_ids:
            marks = ",".join("?" * len(record_ids))
            clauses.append(f"(src IN ({marks}) OR dst IN ({marks}))")
            params += [*record_ids, *record_ids]
        if owner_id:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        with self.connect() as db:
            rows = db.execute("SELECT * FROM link_candidates" + (" WHERE " + " AND ".join(clauses) if clauses else "")
                              + " ORDER BY score DESC", params).fetchall()
        return [dict(r) for r in rows]

    def nearest_records(self, entry_id: str, *, limit: int = 5, min_similarity: float = 0.75) -> list[tuple[str, float]]:
        """用已保存的向量找最相近的其他记录（不调用模型）：取该条目第一个窗口的向量，与全部记录级条目比较。"""
        if self.embedder is None:
            return []
        fp = self.embedder.fingerprint
        with self.connect() as db:
            if not self.vector_ready:
                return []
            row = db.execute("SELECT embedding FROM vectors WHERE entry_id=? AND model_fp=? ORDER BY start LIMIT 1",
                             (entry_id, fp)).fetchone()
            if row is None:
                return []
            source = db.execute("SELECT record_id FROM entries WHERE entry_id=?", (entry_id,)).fetchone()
            rows = db.execute(
                "SELECT e.record_id, MIN(vec_distance_cosine(v.embedding, ?)) AS distance FROM vectors v "
                "JOIN entries e ON e.entry_id = v.entry_id WHERE v.model_fp=? AND e.corpus='fulltext' "
                "AND e.block_id IS NULL AND e.kind != 'claim' AND e.sensitivity != 'restricted' AND e.record_id != ? "
                "GROUP BY e.record_id ORDER BY distance LIMIT ?",
                (row["embedding"], fp, source["record_id"] if source else "", limit)).fetchall()
        return [(r["record_id"], round(1 - r["distance"], 4)) for r in rows if 1 - r["distance"] >= min_similarity]

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
