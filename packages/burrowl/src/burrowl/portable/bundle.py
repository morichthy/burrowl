"""burrowl-bundle/1：数据目录 + 已登记的外部原件 → zip（逐文件 sha256 清单）→ 新数据目录。

    manifest.json   {format, version, created_at, source_root, files:[{path, sha256, bytes}],
                     excluded:[规则], roots:[{root_id, path}],
                     external_sources:[{source_id, group, relative, original, sha256, packed, bundle_path?, reason?}],
                     bundle_hash}
    data/<相对路径>              数据目录的原样字节
    external/<组>/<相对路径>     数据目录外的已登记原件（组 = 工作根 ID；不在任何工作根下的为 ABS）

打包：排除可重建的派生状态（检索索引、中枢映射）、锁和暂存残留。数据目录外的原件只打包"已登记且字节
     与登记指纹一致"的文件——固定引用指向的是登记时的内容；已变化或缺失的只在清单里如实列出。
     工作目录里未登记的文件（代码仓库其他部分、虚拟环境等）不打包。
恢复：先完整校验清单与每个文件的指纹，再规划每个外部原件的去向，确认没有冲突后才写入：
     1. 有映射（--map 工作根ID=新目录，或 旧路径前缀=新路径前缀）→ 放到新位置；
     2. 原位置仍有同指纹的文件 → 保持不动（同机恢复）；
     3. 否则放到与新数据目录并列的 <新数据目录名>-work/<组>/ 下（原件不混进数据目录），
        之后可整体移动并用 sources.relocate_root 指向新位置。
     目标处已有同指纹文件时直接复用；已有不同内容的文件 → 整个恢复中止，什么都不写。
     最后改写新数据目录中的来源登记（工作根位置 / 绝对路径），记入 relocated_from；固定引用不需要改。
"""
import fnmatch
import json
import os
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ..core.atomic import write_json_atomic
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.hashing import canonical_hash, sha256_bytes, sha256_file

FORMAT = "burrowl-bundle/1"
# 排除可重建或可丢弃的数据：索引、中枢映射、Run 回收站（R14 D-12）、错误日志（R20 D-06）、交换包、锁与暂存
EXCLUDE = [".burrowl/index/*", ".burrowl/hub/*", ".burrowl/trash/*", ".burrowl/logs/*", "exports/*", "*.lock", "*/lock-recovery/*", "*/staging/*", "*.stale-*",
           "*/__pycache__/*", "*.pyc"]
MAX_BUNDLE_BYTES = 8 * 1024 ** 3
ABS = "ABS"
WORK_SUFFIX = "-work"


def _excluded(relative: str) -> bool:
    return any(fnmatch.fnmatch(relative, rule) for rule in EXCLUDE)


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _files(root: Path) -> list[Path]:
    out = []
    for path in sorted(root.rglob("*")):
        if _is_link(path):
            raise BurrowlError("UNSAFE_PATH", "数据目录中有符号链接或联接，拒绝打包", {"path": str(path)})
        if path.is_file() and not _excluded(path.relative_to(root).as_posix()):
            out.append(path)
    return out


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.parts or ".." in path.parts or path.is_absolute() or ":" in path.parts[0]:
        raise BurrowlError("UNSAFE_PATH", "清单中有越界路径", {"path": value})
    return path


def _abs_relative(path: str) -> str:
    """绝对路径 → 包内相对路径：D:/a/b.csv → D/a/b.csv；//server/share/x → server/share/x。"""
    windows = PureWindowsPath(path)
    drive = windows.drive.replace(":", "").replace("\\", "/").strip("/")
    return PurePosixPath(drive, *windows.parts[1:]).as_posix()


def _registry(root: Path) -> dict:
    path = root / ".burrowl" / "sources.json"
    if not path.is_file():
        return {"roots": [], "sources": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    value.setdefault("roots", [])
    return value


def _external(root: Path) -> tuple[list[dict], list[dict]]:
    """数据目录外的已登记来源：(工作根列表, 来源列表)。数据目录内的来源已在 data/ 中。"""
    value = _registry(root)
    roots = {r["root_id"]: r for r in value["roots"]}
    out = []
    for entry in value["sources"]:
        if entry.get("root"):
            base = roots.get(entry["root"])
            original = str(Path(base["path"]) / PurePosixPath(entry["path"])) if base else None
            item = {"group": entry["root"], "relative": entry["path"], "original": original}
        elif Path(entry["path"]).is_absolute():
            item = {"group": ABS, "relative": _abs_relative(entry["path"]), "original": entry["path"]}
        else:
            continue
        out.append({"source_id": entry["source_id"], **item, "sha256": entry.get("sha256")})
    return [{"root_id": r["root_id"], "path": r["path"]} for r in value["roots"]], out


def pack(root: str | Path, target: str | Path, *, include_external: bool = True) -> dict[str, Any]:
    """把数据目录（默认连同已登记的外部原件）打成 burrowl-bundle。target 必须是尚不存在的 .zip 文件。"""
    root, target = Path(root).resolve(), Path(target).resolve()
    if target.exists():
        raise BurrowlError("CONFLICT", "目标文件已存在，不覆盖", {"path": str(target)})
    if target.is_relative_to(root):
        raise BurrowlError("INVALID_ARGUMENT", "备份文件不能放在数据目录内")
    roots, external = _external(root)
    files, total, written = [], 0, set()

    def add(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
        nonlocal total
        total += len(data)
        if total > MAX_BUNDLE_BYTES:
            raise BurrowlError("BUDGET_EXCEEDED", "数据目录与原件超过可打包上限", {"limit_bytes": MAX_BUNDLE_BYTES})
        archive.writestr(name, data)

    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", zipfile.ZIP_DEFLATED) as archive:
        for path in _files(root):
            data = path.read_bytes()
            relative = path.relative_to(root).as_posix()
            add(archive, f"data/{relative}", data)
            files.append({"path": relative, "sha256": sha256_bytes(data), "bytes": len(data)})
        for item in external:
            item["packed"] = False
            if not include_external:
                item["reason"] = "未选择打包外部原件"
                continue
            location = Path(item["original"]) if item["original"] else None
            if location is None or not location.is_file() or any(_is_link(p) for p in [location, *location.parents]):
                item["reason"] = "原件缺失、工作根未登记或路径含链接"
                continue
            data = location.read_bytes()
            if sha256_bytes(data) != item["sha256"]:
                item["reason"] = "原件已在登记后改变；固定引用指向旧内容，不打包新内容"
                continue
            name = f"external/{item['group']}/{_safe_relative(item['relative']).as_posix()}"
            if name not in written:
                add(archive, name, data)
                written.add(name)
            item.update(packed=True, bundle_path=name, bytes=len(data))
        manifest = {"format": FORMAT, "version": 1, "created_at": utc_now(), "source_root": str(root),
                    "files": files, "excluded": EXCLUDE, "roots": roots, "external_sources": external}
        manifest["bundle_hash"] = canonical_hash({k: v for k, v in manifest.items() if k != "created_at"})
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    skipped = [{"source_id": i["source_id"], "original": i["original"], "reason": i["reason"]}
               for i in external if not i["packed"]]
    return {"bundle": str(target), "files": len(files), "bytes": total, "bundle_hash": manifest["bundle_hash"],
            "external_packed": sum(i["packed"] for i in external), "external_skipped": skipped,
            "note": "检索索引与中枢映射未打包；恢复后运行 burrowl call index.reconcile 重建索引"}


def _read_manifest(archive: zipfile.ZipFile) -> dict:
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (KeyError, ValueError) as exc:
        raise BurrowlError("INTEGRITY_ERROR", "不是有效的 burrowl-bundle（缺少或损坏 manifest.json）") from exc
    if manifest.get("format") != FORMAT:
        raise BurrowlError("INTEGRITY_ERROR", "不支持的包格式", {"format": manifest.get("format")})
    expected = canonical_hash({k: v for k, v in manifest.items() if k not in {"created_at", "bundle_hash"}})
    if manifest.get("bundle_hash") != expected:
        raise BurrowlError("INTEGRITY_ERROR", "清单指纹不匹配，包可能被修改")
    manifest.setdefault("roots", [])
    return manifest


def verify(bundle: str | Path) -> dict[str, Any]:
    """只校验，不写任何文件：清单完整、每个文件指纹一致、没有越界路径或多余文件。"""
    with zipfile.ZipFile(bundle) as archive:
        manifest = _read_manifest(archive)
        expected = {f"data/{f['path']}": f["sha256"] for f in manifest["files"]}
        for item in manifest["external_sources"]:
            if item.get("packed"):
                expected[item["bundle_path"]] = item["sha256"]
        extra = sorted(n for n in archive.namelist() if n != "manifest.json" and n not in expected)
        if extra:
            raise BurrowlError("INTEGRITY_ERROR", "包内有清单之外的文件", {"files": extra[:20]})
        for name, digest in expected.items():
            _safe_relative(name)
            if sha256_bytes(archive.read(name)) != digest:
                raise BurrowlError("INTEGRITY_ERROR", "文件指纹不一致", {"path": name})
    packed = [i for i in manifest["external_sources"] if i.get("packed")]
    return {"ok": True, "files": len(manifest["files"]), "bundle_hash": manifest["bundle_hash"],
            "created_at": manifest["created_at"], "source_root": manifest["source_root"],
            "roots": manifest["roots"], "external_packed": len(packed),
            "external_not_packed": [i for i in manifest["external_sources"] if not i.get("packed")]}


def _key(path: str | Path) -> str:
    return os.path.normcase(str(PureWindowsPath(str(path)))).rstrip("\\/")


def _map_prefix(original: str, mapping: dict[str, str]) -> str | None:
    """按最长的旧路径前缀映射绝对路径。"""
    best = None
    for old, new in mapping.items():
        prefix = _key(old)
        candidate = _key(original)
        if candidate == prefix or candidate.startswith(prefix + "\\"):
            if best is None or len(prefix) > len(best[0]):
                best = (prefix, new)
    if best is None:
        return None
    remainder = PureWindowsPath(original).parts[len(PureWindowsPath(best[0]).parts):]
    return str(PureWindowsPath(best[1], *remainder))


def _matches(path: Path, digest: str | None) -> bool:
    return path.is_file() and not _is_link(path) and sha256_file(path) == digest


def _default_dir(target: Path) -> Path:
    """没有映射、原位置也不可用时外部原件的落点：与新数据目录并列，原件不混进数据目录。"""
    return target.parent / f"{target.name}{WORK_SUFFIX}"


def _plan(manifest: dict, target: Path, mapping: dict[str, str]) -> tuple[dict, list[dict]]:
    """为每个工作根与每个绝对路径来源决定新位置。返回 (根 → 新目录, 逐来源计划)。"""
    by_group: dict[str, list[dict]] = {}
    for item in manifest["external_sources"]:
        by_group.setdefault(item["group"], []).append(item)
    root_paths = {r["root_id"]: r["path"] for r in manifest["roots"]}
    new_roots, plan = {}, []
    for root_id, items in by_group.items():
        if root_id == ABS:
            continue
        old = root_paths.get(root_id)
        mapped = mapping.get(root_id) or (_map_prefix(old, mapping) if old else None)
        packed = [i for i in items if i.get("packed")]
        if mapped:
            new_roots[root_id] = (Path(mapped), "mapped")
        elif old and Path(old).is_dir() and all(_matches(Path(old) / PurePosixPath(i["relative"]), i["sha256"])
                                                for i in packed):
            new_roots[root_id] = (Path(old), "kept")
        else:
            new_roots[root_id] = (_default_dir(target) / root_id, "default")
        base = new_roots[root_id][0]
        plan += [{**i, "destination": base / PurePosixPath(_safe_relative(i["relative"]))} for i in items]
    for item in by_group.get(ABS, []):
        mapped = _map_prefix(item["original"], mapping)
        if mapped:
            destination, action = Path(mapped), "mapped"
        elif _matches(Path(item["original"]), item["sha256"]):
            destination, action = Path(item["original"]), "kept"
        else:
            destination = _default_dir(target) / ABS / PurePosixPath(_safe_relative(item["relative"]))
            action = "default"
        plan.append({**item, "destination": destination, "action": action})
    return new_roots, plan


def unpack(bundle: str | Path, target: str | Path, *, mapping: dict[str, str] | None = None) -> dict[str, Any]:
    """校验后恢复到 target（不存在或为空的目录），连同外部原件一起迁移并重定向来源登记。"""
    target = Path(target).resolve()
    if target.exists() and any(target.iterdir()):
        raise BurrowlError("CONFLICT", "目标目录不为空，拒绝覆盖", {"path": str(target)})
    checked = verify(bundle)
    with zipfile.ZipFile(bundle) as archive:
        manifest = _read_manifest(archive)
        new_roots, plan = _plan(manifest, target, mapping or {})
        conflicts, to_write, reused, unresolved = [], [], 0, []
        for item in plan:
            destination = item["destination"]
            if _matches(destination, item["sha256"]):
                reused += 1
            elif item.get("packed"):
                if destination.exists() or _is_link(destination):
                    conflicts.append({"source_id": item["source_id"], "path": str(destination)})
                else:
                    to_write.append(item)
            else:  # 打包时已缺失或已改变：原位置的状态照旧，由 burrowl validate 继续报告
                unresolved.append({"source_id": item["source_id"], "expected_at": str(destination),
                                   "reason": item.get("reason", "未打包")})
        if conflicts:
            raise BurrowlError("CONFLICT", "外部原件的目标位置已有不同内容的文件；换一个 --map 目标后重试（未写入任何文件）",
                           {"conflicts": conflicts[:50]})
        for item in manifest["files"]:
            destination = target.joinpath(*_safe_relative(item["path"]).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(archive.read(f"data/{item['path']}"))
        written = set()
        for item in to_write:
            if item["destination"] in written:
                continue
            item["destination"].parent.mkdir(parents=True, exist_ok=True)
            with item["destination"].open("xb") as stream:
                stream.write(archive.read(item["bundle_path"]))
            written.add(item["destination"])
    _redirect(target, new_roots, [i for i in plan if i["group"] == ABS])
    report = [{"root_id": r, "from": next((x["path"] for x in manifest["roots"] if x["root_id"] == r), None),
               "to": p.as_posix(), "action": a} for r, (p, a) in new_roots.items()]
    defaulted = any(a == "default" for _, a in new_roots.values()) or any(
        i.get("action") == "default" for i in plan if i["group"] == ABS)
    return {**checked, "restored_to": str(target), "roots": report, "external_written": len(written),
            "external_reused": reused, "external_unresolved": unresolved,
            "next": "运行 burrowl call index.reconcile 重建检索索引" + (
                f"；没有指定去向的外部原件放在 {_default_dir(target)}，可整体移动后用 sources.relocate_root 指向新位置"
                if defaulted else "")}


def _redirect(target: Path, new_roots: dict, absolute: list[dict]) -> None:
    """改写新数据目录的来源登记：工作根指向新目录，绝对路径来源指向新位置；旧位置记入 relocated_from。"""
    path = target / ".burrowl" / "sources.json"
    if not path.is_file():
        return
    value = json.loads(path.read_text(encoding="utf-8"))
    for root in value.get("roots", []):
        if root["root_id"] in new_roots:
            new = new_roots[root["root_id"]][0].as_posix()
            if new != root["path"]:
                root["relocated_from"] = [*root.get("relocated_from", []), root["path"]]
                root["path"] = new
    moved = {i["source_id"]: i["destination"].as_posix() for i in absolute}
    for entry in value["sources"]:
        new = moved.get(entry["source_id"])
        if new and not entry.get("root") and new != entry["path"]:
            entry["relocated_from"] = [*entry.get("relocated_from", []), entry["path"]]
            entry["path"] = new
    write_json_atomic(path, value, pretty=True)
