"""知识中枢的发布映射：本地身份 local_key ↔ 外部条目 ID，存于 .burrowl/hub/state.sqlite3。

这是派生状态：丢失后可以通过连接器的 list()（按外部元数据中的 local_key）重新认领，不影响本地记忆。
"""
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from ..core.clock import utc_now
from ..core.errors import BurrowlError
from .layout import Workspace

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mappings(
  connector_id TEXT NOT NULL, local_key TEXT NOT NULL, channel TEXT NOT NULL,
  collection_id TEXT NOT NULL, external_id TEXT NOT NULL, content_hash TEXT NOT NULL,
  fixed_ref TEXT, title TEXT NOT NULL DEFAULT '', state TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
  external_version INTEGER, updated_at TEXT NOT NULL,
  PRIMARY KEY(connector_id, local_key));
CREATE TABLE IF NOT EXISTS runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, connector_id TEXT NOT NULL, plan_hash TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, summary TEXT);
"""
_COLUMNS = ("connector_id", "local_key", "channel", "collection_id", "external_id", "content_hash", "fixed_ref",
            "title", "state", "detail", "external_version", "updated_at")


class HubState:
    def __init__(self, workspace: Workspace):
        self.path = workspace.state("hub/state.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(_SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        try:
            db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        except sqlite3.Error as exc:
            raise BurrowlError("STORAGE_ERROR", "中枢状态库不可用", {"reason": str(exc)}) from exc
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA busy_timeout=5000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["fixed_ref"] = json.loads(out["fixed_ref"]) if out["fixed_ref"] else None
        return out

    def mappings(self, connector_id: str, channel: str | None = None) -> dict[str, dict]:
        sql, args = "SELECT * FROM mappings WHERE connector_id=?", [connector_id]
        if channel:
            sql, args = sql + " AND channel=?", [*args, channel]
        with self.connect() as db:
            return {r["local_key"]: self._row(r) for r in db.execute(sql, args)}

    def by_external(self, connector_id: str, external_ids: list[str]) -> dict[str, dict]:
        if not external_ids:
            return {}
        marks = ",".join("?" * len(external_ids))
        with self.connect() as db:
            rows = db.execute(f"SELECT * FROM mappings WHERE connector_id=? AND external_id IN ({marks})",
                              [connector_id, *external_ids])
            return {r["external_id"]: self._row(r) for r in rows}

    def put(self, connector_id: str, local_key: str, **values: Any) -> None:
        row = {"connector_id": connector_id, "local_key": local_key, "detail": "", "title": "",
               "external_version": None, **values, "updated_at": utc_now()}
        row["fixed_ref"] = json.dumps(row.get("fixed_ref"), ensure_ascii=False) if row.get("fixed_ref") else None
        with self.connect() as db:
            db.execute(f"INSERT OR REPLACE INTO mappings({','.join(_COLUMNS)}) VALUES({','.join('?' * len(_COLUMNS))})",
                       [row.get(c) for c in _COLUMNS])

    def set_state(self, connector_id: str, local_key: str, state: str, detail: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE mappings SET state=?, detail=?, updated_at=? WHERE connector_id=? AND local_key=?",
                       (state, detail, utc_now(), connector_id, local_key))

    def remove(self, connector_id: str, local_key: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM mappings WHERE connector_id=? AND local_key=?", (connector_id, local_key))

    def start_run(self, connector_id: str, plan_hash: str) -> int:
        with self.connect() as db:
            return db.execute("INSERT INTO runs(connector_id, plan_hash, started_at) VALUES(?,?,?)",
                              (connector_id, plan_hash, utc_now())).lastrowid

    def finish_run(self, run_id: int, summary: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE runs SET finished_at=?, summary=? WHERE run_id=?",
                       (utc_now(), json.dumps(summary, ensure_ascii=False), run_id))

    def runs(self, connector_id: str, limit: int = 10) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM runs WHERE connector_id=? ORDER BY run_id DESC LIMIT ?",
                              (connector_id, limit)).fetchall()
        return [{**dict(r), "summary": json.loads(r["summary"]) if r["summary"] else None} for r in rows]
