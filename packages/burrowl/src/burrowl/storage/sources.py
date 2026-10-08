"""已登记来源（`.burrowl/sources.json`）：file 类型引用唯一的授权入口。

正文里出现的路径从不授予读取权限；只有在这里登记、启用且未限制的条目才能被读取。
登记的外部原件保持只读，拒绝经由符号链接或 junction 重定向。

    {"schema_version": 1,
     "roots": [{"root_id": "ROOT-PROJ", "path": "D:/工作/proj", "title": "proj"}],
     "sources": [
        {"source_id": "SRC-…", "path": "research/x/data.csv",              数据目录内：相对数据目录
         "enabled": true, "sensitivity": "internal", "sha256": "…", "title": "…"},
        {"source_id": "SRC-…", "root": "ROOT-PROJ", "path": "data/a.csv", …}, 工作目录内：相对工作根
        {"source_id": "SRC-…", "path": "E:/其他/原件.pdf", …}]}                其他位置：绝对路径

工作根（root）是用户或 agent 的工作目录（原始数据与代码所在）。登记绝对路径时，落在某个工作根下的
文件自动记成"根 + 相对路径"；整个工作目录搬家或迁移到别的机器时，只需改根的位置（relocate_root），
所有固定引用不变。固定引用只记 source_id 与指纹，从不记路径。
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.atomic import exclusive_lock, read_json, write_json_atomic
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.hashing import canonical_hash, sha256_file
from ..core.pathsafe import safe_path
from .layout import Workspace

FILE = "sources.json"
RANK = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}


@dataclass(frozen=True)
class Source:
    source_id: str
    path: Path
    entry: dict[str, Any]

    @property
    def sensitivity(self) -> str:
        return self.entry.get("sensitivity", "internal")


def _checked_absolute(raw: str | Path, what: str) -> Path:
    """外部绝对路径：路径本身及其各级父目录都不能是符号链接或联接。"""
    if not Path(raw).is_absolute():
        raise BurrowlError("INVALID_ARGUMENT", f"{what}必须是绝对路径", {"path": str(raw)})
    absolute = Path(Path(raw).absolute())
    if absolute.resolve() != absolute or any(p.is_symlink() or p.is_junction() for p in [absolute, *absolute.parents]):
        raise BurrowlError("UNSAFE_PATH", f"{what}含符号链接或联接", {"path": str(raw)})
    return absolute


def relative_under(path: Path, base: Path) -> str | None:
    """path 位于 base 下时返回相对路径（按 Windows 规则不区分大小写比较，保留实际拼写），否则 None。"""
    parts, prefix = path.parts, base.parts
    if len(parts) <= len(prefix) or any(os.path.normcase(a) != os.path.normcase(b) for a, b in zip(parts, prefix, strict=False)):
        return None
    return PurePosixPath(*parts[len(prefix):]).as_posix()


class SourceRegistry:
    def __init__(self, workspace: Workspace):
        self.ws = workspace

    def _load(self) -> dict[str, Any]:
        path = self.ws.state(FILE)
        if not path.exists():
            return {"schema_version": 1, "roots": [], "sources": []}
        value = read_json(path)
        if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
            raise BurrowlError("INTEGRITY_ERROR", "来源登记格式错误")
        value.setdefault("roots", [])
        return value

    def entries(self) -> list[dict[str, Any]]:
        return list(self._load()["sources"])

    def roots(self) -> list[dict[str, Any]]:
        return list(self._load()["roots"])

    def normalize(self, path: str) -> str:
        """统一分隔符；指向数据目录内的绝对路径改写为相对数据目录的路径（以便按对象检查访问权限）。"""
        raw = path.replace("\\", "/")
        if Path(raw).is_absolute():
            relative = relative_under(Path(Path(raw).absolute()), self.ws.root)
            if relative:
                return relative
        return raw

    def describe(self) -> list[dict[str, Any]]:
        """登记条目加上当前解析出的位置（location），供列表展示；不检查文件是否存在。"""
        value = self._load()
        out = []
        for entry in value["sources"]:
            try:
                location = str(self._locate(entry, value["roots"]))
            except BurrowlError as exc:
                location = f"<{exc.code}>"
            out.append({**entry, "location": location})
        return out

    def _root_path(self, roots: list[dict], root_id: str) -> Path:
        match = next((r for r in roots if r.get("root_id") == root_id), None)
        if match is None:
            raise BurrowlError("UNRESOLVED_REFERENCE", "工作根不存在", {"root_id": root_id})
        return _checked_absolute(match["path"], "工作根")

    def _locate(self, entry: dict, roots: list[dict] | None = None) -> Path:
        if entry.get("root"):
            roots = self._load()["roots"] if roots is None else roots
            return safe_path(self._root_path(roots, entry["root"]), entry["path"])
        raw = Path(entry["path"])
        if raw.is_absolute():
            return _checked_absolute(raw, "来源")
        return safe_path(self.ws.root, entry["path"])

    def resolve(self, source_id: str) -> Source:
        value = self._load()
        matches = [e for e in value["sources"] if e.get("source_id") == source_id]
        if len(matches) != 1:
            raise BurrowlError("UNRESOLVED_REFERENCE", "来源 ID 不存在或重复", {"source_id": source_id})
        entry = matches[0]
        if not entry.get("enabled", True) or entry.get("sensitivity") == "restricted":
            raise BurrowlError("ACCESS_DENIED", "来源未启用或不允许读取", {"source_id": source_id})
        path = self._locate(entry, value["roots"])
        if not path.is_file():
            raise BurrowlError("UNRESOLVED_REFERENCE", "已登记来源不存在", {"source_id": source_id})
        return Source(source_id, path, entry)

    def _write(self, mutate) -> dict[str, Any]:
        state = self.ws.state()
        state.mkdir(exist_ok=True)
        with exclusive_lock(state / "sources.lock", "sources-register"):
            value = self._load()
            result = mutate(value)
            write_json_atomic(self.ws.state(FILE), value, pretty=True)
            return result

    @staticmethod
    def _find(value: dict, source_id: str) -> dict | None:
        return next((e for e in value["sources"] if e.get("source_id") == source_id), None)

    # ---------- 工作根 ----------
    @staticmethod
    def _new_root(value: dict, path: Path, title: str = "") -> dict:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", path.name).strip("-").upper()[:40]
        base = f"ROOT-{slug}" if slug else f"ROOT-{canonical_hash(str(path))[:8].upper()}"
        used = {r["root_id"] for r in value["roots"]}
        root_id, n = base, 2
        while root_id in used:
            root_id, n = f"{base}-{n}", n + 1
        root = {"root_id": root_id, "path": path.as_posix(), "title": title or path.name, "created_at": utc_now()}
        value["roots"].append(root)
        return root

    def _place(self, value: dict, absolute: Path, work_dir: Path | None) -> dict:
        """为外部绝对路径选择记法：已有工作根 → 当前工作目录（自动建根）→ 绝对路径。"""
        best = None
        for root in value["roots"]:
            relative = relative_under(absolute, Path(root["path"]))
            if relative and (best is None or len(Path(root["path"]).parts) > best[0]):
                best = (len(Path(root["path"]).parts), root["root_id"], relative)
        if best:
            return {"root": best[1], "path": best[2]}
        if work_dir is not None:
            relative = relative_under(absolute, work_dir)
            if relative:
                return {"root": self._new_root(value, work_dir)["root_id"], "path": relative}
        return {"path": absolute.as_posix()}

    def add_root(self, path: str, *, title: str = "") -> dict[str, Any]:
        """登记一个工作根（工作目录）。同一位置重复登记返回已有的根。"""
        absolute = _checked_absolute(path, "工作根")
        if not absolute.is_dir():
            raise BurrowlError("NOT_FOUND", "工作根必须是已存在的目录", {"path": path})
        if absolute == self.ws.root or relative_under(absolute, self.ws.root) or relative_under(self.ws.root, absolute):
            raise BurrowlError("INVALID_ARGUMENT", "工作根不能是数据目录本身、在数据目录内或包含数据目录",
                           {"path": path})

        def mutate(value: dict) -> dict:
            for root in value["roots"]:
                if os.path.normcase(str(Path(root["path"]))) == os.path.normcase(str(absolute)):
                    return root
            return self._new_root(value, absolute, title)
        return self._write(mutate)

    def relocate_root(self, root_id: str, new_path: str, *, allow_missing: bool = False) -> dict[str, Any]:
        """工作目录整体搬家：新位置下每个已登记文件的字节指纹必须与登记时一致。

        有文件内容不同 → 拒绝（那是另一份目录，不是搬家）；有文件缺失 → 默认拒绝，allow_missing 时照常改根并列出缺失。
        """
        target = _checked_absolute(new_path, "新位置")
        if not target.is_dir():
            raise BurrowlError("NOT_FOUND", "新位置必须是已存在的目录", {"path": new_path})

        def mutate(value: dict) -> dict:
            root = next((r for r in value["roots"] if r.get("root_id") == root_id), None)
            if root is None:
                raise BurrowlError("NOT_FOUND", "工作根不存在", {"root_id": root_id})
            missing, changed, checked = [], [], 0
            for entry in (e for e in value["sources"] if e.get("root") == root_id):
                candidate = safe_path(target, entry["path"])
                if not candidate.is_file():
                    missing.append(entry["source_id"])
                elif sha256_file(candidate) != entry.get("sha256"):
                    changed.append(entry["source_id"])
                else:
                    checked += 1
            if changed or (missing and not allow_missing):
                raise BurrowlError("STALE_BASIS", "新位置的文件与登记时不一致，不能视为同一工作目录",
                               {"root_id": root_id, "changed": changed, "missing": missing})
            root["relocated_from"] = [*root.get("relocated_from", []), root["path"]]
            root["path"] = target.as_posix()
            return {**root, "verified": checked, "missing": missing}
        return self._write(mutate)

    # ---------- 来源 ----------
    def register(self, source_id: str, path: str, *, title: str = "", sensitivity: str = "internal",
                 work_dir: Path | None = None) -> dict[str, Any]:
        """登记一个新来源并记录当前字节指纹。

        path：数据目录内的相对路径，或外部绝对路径（落在工作根下时自动记为根 + 相对路径；
        work_dir 是当前会话的工作目录，文件在其中而尚无根时自动为它建根）。
        已登记的 ID 不能被改写：同一文件、同一指纹的重复登记视为幂等（只允许提高敏感级别）；
        内容变了要用新 ID 登记（旧的固定引用继续指向旧指纹）；文件搬家用 relocate / relocate_root；
        启用/停用、降低敏感级别只能由用户经 update 修改。
        """
        if not source_id.startswith("SRC-"):
            raise BurrowlError("INVALID_ARGUMENT", "来源 ID 必须以 SRC- 开头")
        if sensitivity not in RANK:
            raise BurrowlError("INVALID_ARGUMENT", "未知的敏感级别", {"sensitivity": sensitivity})
        raw = self.normalize(path)
        external = Path(raw).is_absolute()
        location = _checked_absolute(raw, "来源") if external else self._locate({"path": raw})
        if not location.is_file():
            raise BurrowlError("NOT_FOUND", "登记需要实际文件", {"path": path})
        digest = sha256_file(location)

        def mutate(value: dict) -> dict:
            existing = self._find(value, source_id)
            if existing is not None:
                if self._locate(existing, value["roots"]) != location or existing.get("sha256") != digest:
                    raise BurrowlError("CONFLICT", "该来源 ID 已登记为另一个文件或另一版内容；内容变化请用新 ID，"
                                   "文件搬家请用 sources.relocate", {"source_id": source_id, "registered": existing})
                if not existing.get("enabled", True):
                    raise BurrowlError("ACCESS_DENIED", "该来源已被停用；重新启用需由用户在工作台操作",
                                   {"source_id": source_id})
                if RANK[sensitivity] > RANK.get(existing.get("sensitivity", "internal"), 3):
                    existing["sensitivity"] = sensitivity
                return existing
            placement = self._place(value, location, work_dir) if external else {"path": raw}
            entry = {"source_id": source_id, **placement, "enabled": True, "sensitivity": sensitivity,
                     "title": title or location.name, "sha256": digest}
            value["sources"].append(entry)
            return entry
        return self._write(mutate)

    def relocate(self, source_id: str, new_path: str, *, work_dir: Path | None = None) -> dict[str, Any]:
        """单个原件搬到新位置：新位置的字节指纹必须与登记时一致；旧位置保留在 relocated_from 中。"""
        raw = self.normalize(new_path)
        external = Path(raw).is_absolute()

        def mutate(value: dict) -> dict:
            existing = self._find(value, source_id)
            if existing is None:
                raise BurrowlError("NOT_FOUND", "来源未登记", {"source_id": source_id})
            location = _checked_absolute(raw, "新位置") if external else self._locate({"path": raw})
            if not location.is_file() or sha256_file(location) != existing.get("sha256"):
                raise BurrowlError("STALE_BASIS", "新位置的文件与登记时的内容不一致，不能视为同一来源",
                               {"source_id": source_id})
            old = (f"{existing['root']}:{existing['path']}" if existing.get("root") else existing["path"])
            existing["relocated_from"] = [*existing.get("relocated_from", []), old]
            existing.pop("root", None)
            existing.update(self._place(value, location, work_dir) if external else {"path": raw})
            return existing
        return self._write(mutate)

    def update(self, source_id: str, *, enabled: bool | None = None, sensitivity: str | None = None) -> dict[str, Any]:
        """用户操作：启用/停用来源，或调整敏感级别（包括降低）。"""
        if sensitivity is not None and sensitivity not in RANK:
            raise BurrowlError("INVALID_ARGUMENT", "未知的敏感级别", {"sensitivity": sensitivity})

        def mutate(value: dict) -> dict:
            existing = self._find(value, source_id)
            if existing is None:
                raise BurrowlError("NOT_FOUND", "来源未登记", {"source_id": source_id})
            if enabled is not None:
                existing["enabled"] = enabled
            if sensitivity is not None:
                existing["sensitivity"] = sensitivity
            return existing
        return self._write(mutate)
