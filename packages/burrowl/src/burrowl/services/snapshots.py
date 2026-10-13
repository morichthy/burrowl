"""原文快照、图片附件、覆盖报告与原文展开（R21）。

快照 `.burrowl/snapshots/<SRC>/<原件sha256>.json`：导入时对原件抽取的分段文本与指纹，不保存原件本身。
它是数据目录的状态而不是缓存：原件一改就无法再生成旧版，所以随备份打包，原件变化后用来逐段对比。
附件 `attachments/<sha256>.<ext>`：从原件中提取的图片（或已登记的图片文件）按内容寻址复制进数据目录，
登记为来源（`SRC-ATT-<sha 前 16 位>`），供 L1 的 figures 固定引用；迁移时随数据目录一起走。
覆盖报告与展开视图只读，从快照与对象的当前记录现算，不保存。
"""
import re
from pathlib import Path
from typing import Any

from ..core import segments as seg
from ..core.atomic import read_json, write_json_atomic
from ..core.clock import utc_now
from ..core.errors import BurrowlError
from ..core.hashing import sha256_bytes, sha256_file
from ..core.refs import make_ref
from ..index import extract
from .memory.context import Storage
from .memory.reader import Reader

SNAPSHOT_VERSION = 1
MAX_SOURCE_BYTES = 50 * 1024 * 1024
IMAGE_TYPES = {b"\x89PNG\r\n\x1a\n": ".png", b"\xff\xd8\xff": ".jpg"}


def _image_suffix(raw: bytes) -> str | None:
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return next((suffix for magic, suffix in IMAGE_TYPES.items() if raw.startswith(magic)), None)


class SnapshotService:
    def __init__(self, storage: Storage, *, ocr_enabled=lambda: True):
        self.storage = storage
        self.ocr_enabled = ocr_enabled

    # ---------- 快照 ----------
    def _path(self, source_id: str, sha: str) -> Path:
        return self.storage.ws.state(f"snapshots/{source_id}/{sha}.json")

    def _read_source(self, source_id: str) -> tuple[Any, bytes]:
        source = self.storage.sources.resolve(source_id)
        if source.path.stat().st_size > MAX_SOURCE_BYTES:
            raise BurrowlError("INVALID_ARGUMENT", "原件超过 50 MiB，无法生成快照", {"source_id": source_id})
        return source, source.path.read_bytes()

    def _extract(self, name: str, raw: bytes, *, ocr_enabled: bool | None = None) -> dict:
        # 检索投影可限制视觉数量；导入保全不能沿用默认的 40 张截断。
        # 原件仍受 50 MiB 限制，抽取失败与不可展示的格式必须通过警告或报错显式交付。
        return extract.extract(name, raw, cache_dir=self.storage.ws.state("index/assets"),
                               ocr_enabled=self.ocr_enabled() if ocr_enabled is None else ocr_enabled,
                               max_assets=None, preserve_assets=True)

    def load(self, source_id: str, sha: str | None = None) -> dict | None:
        """读取快照；sha 不给时取登记指纹对应的那一份。"""
        if sha is None:
            sha = self.storage.sources.resolve(source_id).entry["sha256"]
        path = self._path(source_id, sha)
        return read_json(path) if path.is_file() else None

    def take(self, source_id: str) -> dict:
        """为登记的原件生成快照（已有则直接返回）。原件当前字节必须与登记指纹一致。"""
        source, raw = self._read_source(source_id)
        sha = sha256_bytes(raw)
        if sha != source.entry["sha256"]:
            raise BurrowlError("STALE_BASIS", "原件已在登记后变化；请用新的来源 ID 登记新内容后再生成快照",
                               {"source_id": source_id})
        existing = self.load(source_id, sha)
        if existing is not None:
            return existing
        extracted = self._extract(source.path.name, raw)
        segments = seg.build_segments(extracted["units"])
        unit_to_key = {label: s["key"] for s in segments for label in s["labels"]}
        assets = [{"label": a["label"], "segment": unit_to_key.get(a["unit"], a["unit"]),
                   "ocr_status": a["ocr_status"], "has_text": a["has_text"]} for a in extracted["assets"]]
        snapshot = {"schema_version": SNAPSHOT_VERSION, "source_id": source_id, "sha256": sha,
                    "file_name": source.path.name, "taken_at": utc_now(), "segments": segments,
                    "assets": assets, "warnings": extracted["warnings"]}
        path = self._path(source_id, sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, snapshot)
        return snapshot

    def outline(self, snapshot: dict, keys: list[str] | None = None, *, max_chars: int = 20000) -> dict:
        """给 AI 的快照视图：全部分段的 key 与字数；keys 给出时附这些分段的全文（按顺序，受字数上限）。"""
        known = {s["key"] for s in snapshot["segments"]}
        if keys and (unknown := sorted(set(keys) - known)):
            raise BurrowlError("INVALID_ARGUMENT", "快照中没有这些分段", {"unknown": unknown[:20]})
        texts, used, truncated = [], 0, []
        for segment in snapshot["segments"]:
            if keys and segment["key"] in keys:
                if used + len(segment["text"]) > max_chars and texts:
                    truncated.append(segment["key"])
                    continue
                texts.append({"key": segment["key"], "text": segment["text"]})
                used += len(segment["text"])
        out = {"source_id": snapshot["source_id"], "sha256": snapshot["sha256"], "file_name": snapshot["file_name"],
               "segments": [{"key": s["key"], "chars": s["chars"]} for s in snapshot["segments"]],
               "assets": snapshot["assets"], "warnings": snapshot["warnings"]}
        if keys:
            out["texts"] = texts
            if truncated:
                out["truncated_keys"] = truncated
        return out

    def compare(self, source_id: str) -> dict:
        """原件当前内容与登记时快照逐段对比（D-09）。没有快照时如实说明，不猜。"""
        entry = next((e for e in self.storage.sources.entries() if e.get("source_id") == source_id), None)
        if entry is None:
            raise BurrowlError("UNRESOLVED_REFERENCE", "来源 ID 不存在", {"source_id": source_id})
        old = self.load(source_id, entry["sha256"])
        if old is None:
            return {"source_id": source_id, "status": "no_snapshot"}
        source, raw = self._read_source(source_id)
        sha = sha256_bytes(raw)
        if sha == old["sha256"]:
            return {"source_id": source_id, "status": "unchanged"}
        new = seg.build_segments(self._extract(source.path.name, raw)["units"])
        return {"source_id": source_id, "status": "changed", "old_sha256": old["sha256"], "new_sha256": sha,
                "diff": seg.diff_segments(old["segments"], new)}

    # ---------- 附件 ----------
    def attach(self, source_id: str, asset_label: str | None = None, *, title: str = "") -> dict:
        """把原件中提取的一张图片（asset_label），或本身就是图片的已登记来源，复制为受控附件并登记。"""
        source, raw = self._read_source(source_id)
        if sha256_bytes(raw) != source.entry["sha256"]:
            raise BurrowlError("STALE_BASIS", "原件已在登记后变化", {"source_id": source_id})
        if asset_label is None:
            data = raw
        else:
            # 附加只需要图片字节，文字已在快照阶段抽取；避免逐张保存时反复执行全文件 OCR。
            extracted = self._extract(source.path.name, raw, ocr_enabled=False)
            match = [a for a in extracted["assets"] if a["label"] == asset_label]
            if not match:
                raise BurrowlError("NOT_FOUND", "原件中没有这张图片；可选的标签见 sources_snapshot 返回的 assets",
                                   {"asset_label": asset_label,
                                    "available": [a["label"] for a in extracted["assets"]][:40]})
            data = Path(match[0]["original_path"]).read_bytes()
        suffix = _image_suffix(data)
        if suffix is None:
            raise BurrowlError("INVALID_ARGUMENT", "只能附加 PNG / JPEG / WebP 原图；其他格式需显式转换并说明缺口",
                               {"source_id": source_id})
        sha = sha256_bytes(data)
        relative = f"attachments/{sha}{suffix}"
        target = self.storage.ws.path(relative)
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            write_tmp = target.with_suffix(target.suffix + ".tmp")
            write_tmp.write_bytes(data)
            write_tmp.replace(target)
        attachment_id = f"SRC-ATT-{sha[:16]}"
        label = asset_label or source.path.name
        entry = self.storage.sources.register(attachment_id, relative, sensitivity=source.sensitivity,
                                              title=title or f"{source.entry.get('title') or source.path.name} · {label}")
        return {"source_id": attachment_id, "sha256": entry["sha256"], "path": relative,
                "ref": make_ref("file", attachment_id, sha256=entry["sha256"], locator=label),
                "use": "写进 L1 的 payload.figures：{caption, ref}；块正文用 ![图注](figure:序号) 在原位置引用"}

    # ---------- 覆盖与展开（只读） ----------
    def _owner_material(self, owner_id: str, source_id: str) -> tuple[dict, list[dict], list[dict]]:
        snapshot = self.load(source_id)
        if snapshot is None:
            raise BurrowlError("NOT_FOUND", "这个原件还没有快照；先调用 sources_snapshot", {"source_id": source_id})
        reader = Reader(self.storage)
        records = list(reader.state(owner_id).records.values())
        details = [r for r in records if r["kind"] == "detail" and r["sensitivity"] != "restricted"]
        omitted = [item for r in records if r["kind"] == "source"
                   and r["payload"]["source_ref"]["target_id"] == source_id
                   for item in r["payload"].get("omitted", [])]
        reader.recheck()
        return snapshot, details, omitted

    def transcriptions(self, owner_id: str) -> list[dict]:
        """本对象转写过（L1 的 origin）或登记过（L0）的原件，附是否有快照与覆盖计数。"""
        reader = Reader(self.storage)
        records = reader.state(owner_id).records.values()
        ids = {r["payload"]["origin"]["source_ref"]["target_id"] for r in records
               if r["kind"] == "detail" and r["payload"].get("origin")}
        ids |= {r["payload"]["source_ref"]["target_id"] for r in records if r["kind"] == "source"}
        entries = {e["source_id"]: e for e in self.storage.sources.entries()}
        out = []
        for source_id in sorted(ids):
            entry = entries.get(source_id)
            if entry is None or not entry.get("enabled", True) or entry.get("sensitivity") == "restricted":
                continue
            row: dict[str, Any] = {"source_id": source_id, "title": entry.get("title") or source_id,
                                   "has_snapshot": self._path(source_id, entry["sha256"]).is_file()}
            if row["has_snapshot"]:
                row["counts"] = self.coverage(owner_id, source_id)["counts"]
            out.append(row)
        reader.recheck()
        return out

    def coverage(self, owner_id: str, source_id: str) -> dict:
        snapshot, details, omitted = self._owner_material(owner_id, source_id)
        report = seg.coverage(snapshot["segments"], details, source_id, omitted)
        assets = self._asset_coverage(snapshot["assets"], details, source_id, omitted)
        return {"owner_id": owner_id, "source_id": source_id, "sha256": snapshot["sha256"], **report,
                "assets": assets,
                "asset_counts": {status: sum(a["status"] == status for a in assets)
                                 for status in ("linked", "unreferenced", "missing", "unavailable", "omitted")},
                "warnings": snapshot.get("warnings", []),
                "meaning": "覆盖度是提示不是判定：uncovered = 没有 L1 认领也没登记省略；thin = 认领了但正文明显偏短"
                           "或与原文差异较大（字数比或字符 4-gram 重合度低于 0.9）。"
                           "图片按原文标签核对附件与所在分段的正文引用，linked 不等于视觉内容已核验。"
                           "报告不包含抽取时漏掉的内容；对照原件逐段逐图验收后才能声明完整导入"}

    def _asset_coverage(self, assets: list[dict], records: list[dict], source_id: str, omitted: list[dict]) -> list[dict]:
        """只读现算图片缺口，不另存完成状态；旧快照只有标签时也能检查。

        标签只能关联附件，不能证明视觉内容与原图相同。用同一原件的 L1、分段定位与图号
        三者一起匹配，避免另一章节的一次引用掩盖原位置遗漏；附件还需可读且固定指纹有效。
        """
        claims = seg.claims(records, source_id)
        reasons = {key: item["reason"] for item in omitted for key in item["locators"]}
        health: dict[tuple, str | None] = {}
        rows = []
        for asset in assets:
            matches = []
            for hit in claims.get(asset["segment"], []):
                figures = hit["record"]["payload"].get("figures", [])
                mentioned = {int(m[1]) for m in re.finditer(r"!\[[^\]]*\]\(figure:(\d+)\)", hit["block"]["markdown"])}
                for index, figure in enumerate(figures):
                    ref = figure["ref"]
                    if ref.get("target_kind") != "file" or ref.get("locator") != asset["label"]:
                        continue
                    key = (ref["target_id"], ref.get("sha256"))
                    if key not in health:
                        health[key] = self._attachment_problem(ref)
                    matches.append({"record_id": hit["record"]["record_id"], "index": index,
                                    "in_body": index in mentioned, "problem": health[key]})
            row = {"label": asset["label"], "segment": asset["segment"]}
            valid = [m for m in matches if m["problem"] is None]
            if any(m["in_body"] for m in valid):
                row["status"] = "linked"
            elif valid:
                row["status"] = "unreferenced"
            elif matches:
                row |= {"status": "unavailable", "problems": sorted({m["problem"] for m in matches})}
            elif asset["segment"] in reasons:
                row |= {"status": "omitted", "reason": reasons[asset["segment"]]}
            else:
                row["status"] = "missing"
            rows.append(row)
        return rows

    def _attachment_problem(self, ref: dict) -> str | None:
        """与图片展示边界一致地检查附件；只回传错误类别，不泄露受限来源的位置或内容。"""
        try:
            source = self.storage.sources.resolve(ref["target_id"])
            if not source.path.is_relative_to(self.storage.ws.root):
                return "不是数据目录内的受控附件"
            with source.path.open("rb") as stream:
                if _image_suffix(stream.read(16)) is None:
                    return "不是可展示的 PNG / JPEG / WebP"
            if sha256_file(source.path) != ref.get("sha256"):
                return "固定图片指纹不匹配"
            if source.path.stat().st_size > 8 * 1024 * 1024:
                return "原图已保存，但超过 8 MiB 展示上限"
        except BurrowlError as exc:
            return exc.code
        except OSError:
            return "图片文件不可读"
        return None

    def expansion(self, owner_id: str, source_id: str) -> dict:
        snapshot, details, omitted = self._owner_material(owner_id, source_id)
        return {"owner_id": owner_id, "source_id": source_id, "file_name": snapshot["file_name"],
                "sha256": snapshot["sha256"],
                "sections": seg.expansion(snapshot["segments"], details, source_id, omitted)}
