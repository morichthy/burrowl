"""L0 原始材料视图：列出某对象（及其显式关联的 Run）声明过的原件，按需回读单个文件。

只是已有登记的只读投影，不是第二份来源存储。列表不读原件正文；
回读时重新核对授权与固定字节。文件指纹匹配不等于科学结论正确。
"""
import base64
from collections import deque
from pathlib import Path

from ...core.errors import BurrowlError
from ...core.hashing import canonical_hash, is_sha256, sha256_bytes
from ...core.pathsafe import safe_path
from .claims import iter_refs
from .context import Storage
from .reader import Reader

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 2000


class _Collector:
    def __init__(self, storage: Storage, owner_id: str):
        self.storage = storage
        self.reader = Reader(storage)
        self.root = self.reader.owner(owner_id)
        self.items: dict[str, dict] = {}
        self.paths: dict[str, Path] = {}
        self.missing: list[dict] = []
        self.seen_runs: set[str] = set()

    def gap(self, target: str, code: str) -> None:
        if (issue := {"target_id": target, "code": code}) not in self.missing:
            self.missing.append(issue)

    def _locate(self, locator: str, source_ref: dict | None) -> Path:
        if source_ref:
            return self.storage.sources.resolve(source_ref["target_id"]).path
        if locator.startswith("SRC-"):
            return self.storage.sources.resolve(locator).path
        path = safe_path(self.storage.ws.root, locator)
        for owner in self.reader.index.owners:  # 受限对象目录下的文件不能经由其他声明读取
            if not owner.readable and path.is_relative_to(self.storage.ws.path(owner.native_path).parent):
                raise BurrowlError("ACCESS_DENIED", "原始材料所属对象已限制访问")
        return path

    def add(self, locator: str | None, digest: str | None, origin: dict, source_ref: dict | None = None) -> None:
        try:
            path = self._locate(locator or "", source_ref)
        except BurrowlError as exc:
            self.gap(origin.get("record_id") or origin["owner_id"], exc.code)
            return
        pinned = digest if is_sha256(digest) else None
        identity = "RAW-" + canonical_hash({"path": str(path), "sha256": pinned})[:32]
        display = path.relative_to(self.storage.ws.root).as_posix() if path.is_relative_to(self.storage.ws.root) \
            else str(path)
        row = self.items.setdefault(identity, {
            "material_id": identity, "title": path.name, "path": display, "sha256": pinned, "level": "L0",
            "origins": [], "source_refs": [],
            "status": "missing" if not path.is_file() else "registered" if pinned else "unversioned"})
        if origin not in row["origins"]:
            row["origins"].append(origin)
        if source_ref and source_ref not in row["source_refs"]:
            row["source_refs"].append(source_ref)
        self.paths[identity] = path

    def run(self, run_id: str, fixed: dict | None = None) -> list[dict]:
        try:
            run = self.reader.owner(run_id)
        except BurrowlError as exc:
            self.gap(run_id, exc.code)
            return []
        if run.owner_type != "run" or run_id in self.seen_runs:
            return []
        if fixed and fixed.get("sha256") and fixed["sha256"] != run.fingerprint:
            self.gap(run_id, "STALE_BASIS")  # 引用的旧 Run 版本不能静默绑定当前元数据
            return []
        self.seen_runs.add(run_id)
        origin = {"owner_id": run_id, "role": "run"}
        for key in ("inputs", "artifacts"):
            for entry in run.native.get(key, []):
                if isinstance(entry, dict) and entry.get("path"):
                    self.add(entry["path"], entry.get("sha256"), origin | {"role": key})
        self.add(run.native_path, None, origin | {"role": "run_card"})
        return self.records(run_id)

    def records(self, owner_id: str) -> list[dict]:
        if not self.reader.index.get(owner_id).persisted:
            return []
        return [{"target_kind": "record", "target_id": r["record_id"], "revision": r["revision"],
                 "sha256": r["record_hash"]} for r in self.reader.state(owner_id).records.values()
                if r["sensitivity"] != "restricted"]

    def collect(self) -> dict:
        owner_id = self.root.owner_id
        pending = deque(self.records(owner_id))
        if self.root.owner_type == "run":
            pending.extend(self.run(owner_id))
        for run in self.reader.index.readable("run"):
            if run.native.get("owner_id") == owner_id:
                pending.extend(self.run(run.owner_id))
        seen: set[tuple] = set()
        while pending:
            ref = pending.popleft()
            key = (ref["target_id"], ref.get("revision"), ref.get("sha256"))
            if key in seen:
                continue
            if len(seen) >= MAX_RECORDS:
                self.gap(owner_id, "REFERENCE_LIMIT")
                break
            seen.add(key)
            try:
                record = self.reader.record(ref)
            except BurrowlError as exc:
                self.gap(ref["target_id"], exc.code)
                continue
            origin = {"owner_id": record["owner_id"], "record_id": record["record_id"],
                      "revision": record["revision"], "role": record["kind"], "title": record["title"]}
            for child in iter_refs({"sources": record["sources"], "payload": record["payload"]}):
                if child["target_kind"] == "file":
                    self.add(None, child.get("sha256"), origin, child)
                elif child["target_kind"] == "record":
                    pending.append(child)
                elif child["target_kind"] == "owner":
                    pending.extend(self.run(child["target_id"], child))
        self.reader.recheck()
        rows = sorted(self.items.values(), key=lambda r: (r["path"], r["sha256"] or ""))
        return {"owner_id": owner_id, "items": rows, "total": len(rows), "missing": self.missing,
                "basis_heads": self.reader.heads(), "content_loaded": False, "writes": 0}


def listing(storage: Storage, owner_id: str) -> dict:
    return _Collector(storage, owner_id).collect()


def read(storage: Storage, owner_id: str, material_id: str, *, max_chars: int = 20000) -> dict:
    """按清单中的不透明 ID 回读；从不接受客户端给出的任意路径。"""
    if not 1 <= max_chars <= 64000:
        raise BurrowlError("INVALID_ARGUMENT", "max_chars 范围为 1..64000")
    collector = _Collector(storage, owner_id)
    listing_result = collector.collect()
    row = next((i for i in listing_result["items"] if i["material_id"] == material_id), None)
    if row is None:
        raise BurrowlError("NOT_FOUND", "材料不在当前对象的可读登记中，请刷新清单")
    if row["status"] != "registered":
        raise BurrowlError("UNRESOLVED_REFERENCE", "原始材料缺失或没有固定指纹", {"status": row["status"]})
    path = collector.paths[material_id]
    if path.stat().st_size > MAX_FILE_BYTES:
        raise BurrowlError("INVALID_ARGUMENT", "文件超过 64 MiB 预览上限；请用本地工具读取原件")
    data = path.read_bytes()
    if sha256_bytes(data) != row["sha256"]:
        raise BurrowlError("STALE_BASIS", "原始材料与登记版本不一致，未返回变更后的内容")
    result = {"material": row, "verified_sha256": row["sha256"], "bytes": len(data), "writes": 0}
    mime = "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else \
        "image/jpeg" if data.startswith(b"\xff\xd8\xff") else None
    if mime and len(data) <= 2 * 1024 * 1024:
        return result | {"media_type": mime, "image_data": f"data:{mime};base64,{base64.b64encode(data).decode()}"}
    try:
        text = data.decode("utf-8-sig")
        if "\x00" in text:
            raise UnicodeError
    except UnicodeError:
        return result | {"media_type": "application/octet-stream", "message": "二进制或非 UTF-8 材料，请用对应本地工具查看"}
    return result | {"media_type": "text/plain", "text": text[:max_chars], "truncated": len(text) > max_chars}
