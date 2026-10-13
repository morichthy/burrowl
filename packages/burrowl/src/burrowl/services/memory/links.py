"""正文链接的解析：把 [[…]] 可读名解析为固定引用，写入记录外壳的 links（R12 D-09、§4.3）。

解析目标：
- 概念：概念库对象（配置 graph.concept_owner_id）中 page_type 为 concept / entity 的 wiki_page，
  按标题和别名的规范形精确匹配；已合并（status=merged）的页面转向 merged_into。
  找不到不阻塞提交，记为 unresolved_concept，等收口时新建或合并概念。
- 记录 / 块 / 结论：本对象（含同批新建的记录）或斜杠前指定的对象。找不到或有歧义时提交失败
  UNRESOLVED_LINK，并在 errors[].candidates 中给出候选，AI 改写后重新提交。

记录的 handle：已保存的 handle 优先；旧记录没有 handle 时，用"按当前标题生成的 handle"参与匹配
（只用于解析，不写回记录；该记录下次修订时服务会把它固定下来）。
"""
import difflib
from typing import Any

from ...core import links as syntax
from ...core.errors import BurrowlError
from ...core.records import claim_hash
from ...storage.owners import Owner, OwnerIndex
from .concepts import CONCEPT_PAGE_TYPES
from .context import Storage


def stored_or_derived_handle(record: dict) -> str:
    return record.get("handle") or syntax.make_handle(record["title"])


def record_ref(record: dict, *, pending: bool, block: str | None = None) -> dict:
    """同批草案还没有 record_hash，按 RefResolver 的约定用 sha256=None 固定修订号。"""
    return {"target_kind": "record", "target_id": record["record_id"], "revision": record["revision"],
            "sha256": None if pending else record["record_hash"],
            "locator": f"block:{block}" if block else "record", "relation": "references"}


def claim_ref(claim: dict) -> dict:
    return {"target_kind": "claim", "target_id": claim["claim_id"], "revision": None,
            "sha256": claim_hash(claim), "locator": "statement", "relation": "references"}


class LinkResolver:
    """一次提交内有效。pending 是本批记录（record_id → 带 revision 的草案/记录）。"""

    def __init__(self, storage: Storage, index: OwnerIndex, owner: Owner, records: dict[str, dict],
                 pending: dict[str, dict], concept_owner_id: str | None):
        self.storage, self.index, self.owner = storage, index, owner
        self.pending = pending
        self.concept_owner_id = concept_owner_id
        self._records = {owner.owner_id: {**records, **pending}}
        self._concepts: dict[str, list[dict]] | None = None

    # ---------- 目标集合 ----------
    def _owner_records(self, owner: Owner) -> dict[str, dict]:
        if owner.owner_id not in self._records:
            owner.require_readable()
            snapshot = self.storage.store.read_snapshot(owner) if owner.persisted else None
            records = dict(snapshot.records) if snapshot else {}
            self._records[owner.owner_id] = {**records, **{k: v for k, v in self.pending.items()
                                                           if v.get("owner_id") == owner.owner_id}}
        return self._records[owner.owner_id]

    def _find_owner(self, name: str) -> Owner:
        wanted = syntax.normalize(name)
        matches = [o for o in self.index.owners
                   if wanted in {syntax.normalize(v) for v in (o.owner_id, o.native.get("short_name") or "",
                                                               o.native.get("slug") or "") if v}]
        if len(matches) != 1:
            names = [o.native.get("short_name") or o.owner_id for o in self.index.readable()]
            raise _unresolved(name, "对象不存在或名称有歧义", difflib.get_close_matches(name, names, n=5, cutoff=0.3))
        return matches[0]

    def concepts(self) -> dict[str, list[dict]]:
        """规范化名称 → 概念页（含已合并页；解析时再转向）。"""
        if self._concepts is None:
            self._concepts = {}
            owner = self.index.find(self.concept_owner_id) if self.concept_owner_id else None
            if owner is not None and owner.readable:
                for record in self._owner_records(owner).values():
                    payload = record.get("payload") or {}
                    if record.get("kind") != "wiki_page" or payload.get("page_type") not in CONCEPT_PAGE_TYPES \
                            or record.get("sensitivity") == "restricted":
                        continue
                    for name in [record["title"], *payload.get("aliases", [])]:
                        self._concepts.setdefault(syntax.normalize(name), []).append(record)
        return self._concepts

    # ---------- 单个链接 ----------
    def _concept(self, link: syntax.Link) -> dict:
        pages = self.concepts().get(syntax.normalize(link.name), [])
        active = [p for p in pages if (p["payload"].get("status") or "active") == "active"]
        page = active[0] if len(active) == 1 else (pages[0] if len(pages) == 1 else None)
        seen = set()
        while page is not None and page["payload"].get("status") == "merged":  # 转向保留页（D-13）
            target = page["payload"]["merged_into"]["target_id"]
            if target in seen:
                break
            seen.add(target)
            page = self._owner_records(self.index.get(self.concept_owner_id)).get(target)
        if page is None or (page["payload"].get("status") or "active") != "active":
            return {"text": link.text, "ref": None, "status": "unresolved_concept"}
        ref = record_ref(page, pending=page["record_id"] in self.pending)
        return {"text": link.text, "ref": ref | {"relation": "mentions"}, "status": "resolved"}

    def _record(self, link: syntax.Link, self_id: str) -> dict:
        owner = self._find_owner(link.owner) if link.owner else self.owner
        records = self._owner_records(owner)
        wanted = syntax.normalize(link.name)
        exact = [r for r in records.values() if r["record_id"] == link.name]
        stored = [r for r in records.values() if r.get("handle") and syntax.normalize(r["handle"]) == wanted]
        derived = [r for r in records.values() if not r.get("handle")
                   and syntax.normalize(syntax.make_handle(r["title"])) == wanted]
        matches = exact or stored or derived
        if len(matches) != 1:
            names = sorted({stored_or_derived_handle(r) for r in records.values()
                            if r.get("kind") not in {"review", "policy"}})
            reason = "记录 handle 有歧义" if matches else "找不到这个 handle 的记录"
            raise _unresolved(link.text, reason, difflib.get_close_matches(link.name, names, n=5, cutoff=0.3))
        target = matches[0]
        if target["record_id"] == self_id:
            raise _unresolved(link.text, "记录不能链接自己", [])
        if link.block:
            blocks = [b["block_id"] for b in (target.get("payload") or {}).get("blocks", [])]
            if target.get("kind") != "detail" or link.block not in blocks:
                raise _unresolved(link.text, "块不存在（块链接只对 L1 技术单元有效）",
                                  difflib.get_close_matches(link.block, blocks, n=5, cutoff=0.0))
        ref = record_ref(target, pending=target["record_id"] in self.pending, block=link.block)
        return {"text": link.text, "ref": ref, "status": "resolved"}

    def _claim(self, link: syntax.Link, own_claims: list[dict]) -> dict:
        owner = self._find_owner(link.owner) if link.owner else self.owner
        claims = [c for r in self._owner_records(owner).values() for c in (r.get("payload") or {}).get("claims", [])]
        if owner.owner_id == self.owner.owner_id:
            claims = own_claims + [c for c in claims if c["claim_id"] not in {x["claim_id"] for x in own_claims}]
        matches = {c["claim_id"]: c for c in claims if syntax.claim_short_name(c["claim_id"], link.name)}
        if len(matches) != 1:
            names = sorted({c["claim_id"] for c in claims})
            reason = "结论短名有歧义" if matches else "找不到这条结论"
            raise _unresolved(link.text, reason, sorted(matches) or difflib.get_close_matches(
                "CLM-" + link.name, names, n=5, cutoff=0.3))
        return {"text": link.text, "ref": claim_ref(next(iter(matches.values()))), "status": "resolved"}

    # ---------- 正文中的长编号（R21 D-10） ----------
    def _owner_name(self, owner: Owner) -> str:
        return owner.native.get("short_name") or owner.native.get("slug") or owner.owner_id

    def _link_to(self, record: dict) -> str:
        handle = stored_or_derived_handle(record)
        if record.get("owner_id", self.owner.owner_id) == self.owner.owner_id:
            return f"[[@{handle}]]"
        owner = self.index.find(record["owner_id"])
        return f"[[@{self._owner_name(owner) if owner else record['owner_id']}/{handle}]]"

    def _find_records(self, predicate) -> list[dict]:
        """先找本对象（含同批草案），找不到再找其他可读对象。只在报错时调用。"""
        found = [r for r in self._owner_records(self.owner).values() if predicate(r)]
        if found:
            return found
        for owner in self.index.readable():
            if owner.owner_id != self.owner.owner_id and owner.persisted:
                found.extend(r for r in self._owner_records(owner).values() if predicate(r))
        return found

    def replacement(self, item: dict) -> str | None:
        """能机械替换的长编号（记录、有 L0 登记的原件）→ 短名链接；其他返回 None。"""
        raw, prefix = item["text"], item["prefix"]
        if prefix == "MEM":
            hits = self._find_records(lambda r: r.get("record_id") == raw)
        elif prefix == "SRC":
            hits = self._find_records(lambda r: r.get("kind") == "source"
                                      and (r.get("payload") or {}).get("source_ref", {}).get("target_id") == raw)
        else:
            return None
        return self._link_to(hits[0]) if len(hits) == 1 else None

    def suggest(self, item: dict) -> str:
        prefix = item["prefix"]
        if prefix in {"MEM", "SRC"}:
            if link := self.replacement(item):
                return f"改写为 {link}" + ("（登记这个原件的 L0 记录）" if prefix == "SRC" else "")
            return ("找不到这条记录；正文用 [[@记录短名]] 链接，固定版本由服务写进 links" if prefix == "MEM"
                    else "先为这个原件写一条 L0 登记记录，再用 [[@L0短名]] 链接；固定引用写在 sources")
        if prefix == "RUN":
            return "Run 不能写进正文：实验类 L1 用 run_ref、L2 阶段用 evidence_refs 引用，正文用 Run 的标题描述"
        if prefix == "sha256":
            return "指纹只写在固定引用（sources、evidence_refs、figures 等）里，正文删去"
        return "这是服务内部编号，正文不要写；需要引用记录、块或结论时用 [[@短名]]、[[@短名#块]]、[[!结论短名]]"

    def raw_id_errors(self, record: dict, previous: dict | None = None) -> list[dict]:
        """只拦新写进正文的长编号：上一修订里已有的不拦（只改别的字段、程序生成的修订不被旧内容卡住），
        它们由 system_validate 扫描后经"引用改写"维护计划处理。"""
        existing = {item["text"] for item in syntax.raw_identifiers(previous)} if previous else set()
        return [{"path": item["path"], "code": "UNREADABLE_REF", "text": item["text"],
                 "message": "正文里不要写长编号或指纹，读者看不懂；改用可读的短名链接",
                 "hint": self.suggest(item)} for item in syntax.raw_identifiers(record) if item["text"] not in existing]

    # ---------- 整条记录 ----------
    def resolve(self, record: dict, *, record_id: str, previous: list[dict] | None = None,
                repin: bool = False) -> list[dict]:
        """返回 links 列表（无链接时为空列表，调用方据此省略字段）。

        重新固定规则：修订时链接原文没有变化的，沿用上一修订固定的版本；新增的链接或 repin=True 才固定当前版本。
        所有记录/块/结论链接的错误一次性报告。"""
        old = {item["text"]: item for item in previous or []} if not repin else {}
        own_claims = list((record.get("payload") or {}).get("claims", []) or [])
        out, errors = [], []
        for link in syntax.collect(record):
            if link.text in old and old[link.text]["status"] == "resolved":
                out.append(old[link.text])
                continue
            try:
                if link.kind == "concept":
                    out.append(self._concept(link))
                elif link.kind == "claim":
                    out.append(self._claim(link, own_claims))
                else:
                    out.append(self._record(link, record_id))
            except BurrowlError as exc:
                errors.extend(exc.errors)
        if errors:
            raise BurrowlError("UNRESOLVED_LINK", "正文中有无法解析的链接；按候选改写后重新提交", errors=errors)
        return out


def _unresolved(text: str, message: str, candidates: list[str]) -> BurrowlError:
    error: dict[str, Any] = {"path": "/links", "code": "UNRESOLVED_LINK", "link": f"[[{text}]]", "message": message,
                             "candidates": candidates}
    return BurrowlError("UNRESOLVED_LINK", message, errors=[error])
