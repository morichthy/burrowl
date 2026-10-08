"""只读监测：对比各对象记忆 HEAD 与记录所引用来源文件的字节指纹，产生"观察"和维护候选。

只覆盖对象记录与记忆 HEAD（不扫描 Run 发布的压缩包等范围外产物）。
观察不改变任何证据或复核状态；不完整的一次扫描不会覆盖旧基线。
    .burrowl/monitor/baseline.json      上一次完整扫描
    .burrowl/monitor/observations.jsonl 追加的观察记录
"""
import json
from typing import Any

from ..core.atomic import read_json, write_json_atomic
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.hashing import sha256_file
from .memory import provenance
from .memory.claims import iter_refs
from .memory.context import Storage

MAX_FILE_BYTES = 200 * 1024 * 1024


def scan(storage: Storage) -> dict[str, Any]:
    heads, files, errors = {}, {}, []
    for owner in storage.owners.scan().readable():
        if not owner.persisted:
            continue
        snapshot = storage.store.read_snapshot(owner)
        heads[owner.owner_id] = snapshot.commit_id
        for record in snapshot.records.values():
            for ref in iter_refs({"sources": record["sources"], "payload": record["payload"]}):
                if ref["target_kind"] != "file" or ref["target_id"] in files:
                    continue
                try:
                    source = storage.sources.resolve(ref["target_id"])
                    if source.path.stat().st_size > MAX_FILE_BYTES:
                        raise BurrowlError("INVALID_ARGUMENT", "文件超过监测上限")
                    files[ref["target_id"]] = sha256_file(source.path)
                except (BurrowlError, OSError) as exc:
                    files[ref["target_id"]] = None
                    errors.append({"target_id": ref["target_id"], "code": getattr(exc, "code", "STORAGE_ERROR")})
    return {"at": utc_now(), "heads": heads, "files": files, "errors": errors, "complete": not errors}


class Monitor:
    def __init__(self, storage: Storage):
        self.storage = storage

    def _path(self, name: str):
        path = self.storage.ws.state(f"monitor/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def baseline(self) -> dict | None:
        path = self._path("baseline.json")
        return read_json(path) if path.exists() else None

    def run(self, *, dry_run: bool = False) -> dict:
        """扫描一次并与基线比较。首次运行只建立基线。完整扫描才更新基线。

        dry_run：只比较、不写观察记录、不更新基线、不登记候选。
        """
        current, previous = scan(self.storage), self.baseline()
        changes: list[dict] = []
        if previous:
            for owner_id, head in current["heads"].items():
                if previous["heads"].get(owner_id) != head:
                    changes.append({"kind": "head", "target_id": owner_id, "before": previous["heads"].get(owner_id),
                                    "after": head})
            for source_id, digest in current["files"].items():
                before = previous["files"].get(source_id)
                if before is not None and digest != before:
                    changes.append({"kind": "file", "target_id": source_id, "before": before, "after": digest})
        candidates = []
        file_changes = [c["target_id"] for c in changes if c["kind"] == "file"]
        if file_changes:
            candidates = provenance.impact(self.storage, file_changes)["affected"]
        observation = {"at": current["at"], "changes": changes, "errors": current["errors"],
                       "maintenance_candidates": [c["canonical_id"] for c in candidates],
                       "baseline_updated": current["complete"] and not dry_run, "dry_run": dry_run}
        if dry_run:
            return observation
        with self._path("observations.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(observation, ensure_ascii=False) + "\n")
        if current["complete"]:
            write_json_atomic(self._path("baseline.json"), current)
        if candidates:
            self._register_candidates(candidates, observation["at"])
        return observation

    # ---------- 维护候选（用户处置：open → planned / dismissed） ----------
    def _candidates_doc(self) -> dict:
        path = self._path("candidates.json")
        return read_json(path) if path.exists() else {"schema_version": 1, "items": {}}

    def _register_candidates(self, affected: list[dict], at: str) -> None:
        doc = self._candidates_doc()
        for item in affected:
            key = item["canonical_id"]
            existing = doc["items"].get(key)
            if existing is None or existing["status"] != "open":
                doc["items"][key] = {"target_id": key, "status": "open", "first_seen": at, "last_seen": at,
                                     "reasons": item.get("reasons", item.get("paths", []))[:5]}
            else:
                existing["last_seen"] = at
        write_json_atomic(self._path("candidates.json"), doc)

    def candidates(self, status: str | None = "open") -> list[dict]:
        items = self._candidates_doc()["items"].values()
        return sorted((i for i in items if status is None or i["status"] == status),
                      key=lambda i: i["last_seen"], reverse=True)

    def resolve(self, target_id: str, status: str, note: str = "") -> dict:
        """（用户）处置候选：planned=已纳入维护计划；dismissed=确认无需处理；open=重新打开。"""
        if status not in {"open", "planned", "dismissed"}:
            raise BurrowlError("INVALID_ARGUMENT", "status 只能是 open / planned / dismissed")
        doc = self._candidates_doc()
        item = doc["items"].get(target_id)
        if item is None:
            raise BurrowlError("NOT_FOUND", "没有这个维护候选", {"target_id": target_id})
        item.update(status=status, note=note, resolved_at=utc_now())
        write_json_atomic(self._path("candidates.json"), doc)
        return item

    def observations(self, limit: int = 50) -> list[dict]:
        path = self._path("observations.jsonl")
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in reversed(lines) if line.strip()]
