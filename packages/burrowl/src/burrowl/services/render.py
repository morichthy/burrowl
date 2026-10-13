"""把规范记录渲染成 Markdown 页面（R12 T-P3.1、§4.12）：交换包、Markdown 库连接器与工作台共用一个渲染器。

页面 = YAML 头 + 正文 + 自动生成区块：
- YAML 头写身份（burrowl_id、local_key=记录ID@修订、handle、owner、kind、level、revision、sha256）和
  渲染时才算出的状态（结论的复核与成熟度）。复核、成熟度、统计从不写进记录正文（D-18）。
- 正文里的 [[…]] 链接改写为库内相对路径（不带 .md）；目标不在本次渲染范围内时只保留显示文字。
- L1 的每个块以 "## <block_id>" 标题输出，块内原有标题降一级，使 Obsidian 的标题嵌入
  ![[records/<对象>/<handle>#<block_id>]] 能取到完整的块。文稿页只嵌入技术块，不复制正文。
- 结论锚点用 ^c-<结论 ID 哈希的前 8 位>（Obsidian 的块 ID 只能用 ASCII）。
- 自动生成区块在 <!-- burrowl:generated:start --> 与 <!-- burrowl:generated:end --> 之间：反链、关联（带确定度）、
  结论状态。外部工具不要编辑这一段。
"""
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..core import links as syntax
from ..core.errors import BurrowlError
from .memory.claims import ClaimGraph
from .memory.context import Storage

GENERATED_START = "<!-- burrowl:generated:start -->"
GENERATED_END = "<!-- burrowl:generated:end -->"
SKIP_KINDS = {"review", "policy", "feedback", "association", "representation", "consolidation", "document_section"}
_UNSAFE_FILE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+')
_LINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
_HEADING = re.compile(r"^( {0,3})(#{1,5})(\s)", re.M)
_FENCE = re.compile(r"^ {0,3}(```|~~~)")
LEVEL_NAMES = {"L0": "L0 原始材料", "L1": "L1 技术单元", "L2": "L2 研究经过", "L3": "L3 经验", "L4": "L4 概览"}
RELATION_NAMES = {"related_to": "相关", "same_problem": "同一问题", "same_failure_mode": "同一失败模式",
                  "analogous_to": "类比", "similar_structure": "结构相似", "same_entity": "同一实体"}
CERTAINTY_NAMES = {"speculative": "推测", "plausible": "可能", "supported": "有依据"}


def safe_name(name: str, fallback: str = "untitled") -> str:
    """文件名安全化：去掉 Windows 与 Obsidian 链接语法不允许的字符，保留中文。"""
    value = _UNSAFE_FILE.sub("-", name or "").strip(" .-")
    return value[:80] or fallback


def claim_anchor(claim_id: str) -> str:
    return "c-" + hashlib.sha256(claim_id.encode("utf-8")).hexdigest()[:8]


def yaml_front(fields: dict[str, Any]) -> str:
    """JSON 是 YAML 1.2 的子集：值一律用 JSON 写，避免手写 YAML 转义出错。"""
    lines = ["---", *(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in fields.items()), "---"]
    return "\n".join(lines) + "\n"


def demote_headings(markdown: str) -> str:
    """块内标题降一级（代码围栏内不动），使块标题 "## <block_id>" 成为唯一的二级标题。"""
    out, fence = [], None
    for line in markdown.splitlines():
        if match := _FENCE.match(line):
            fence = None if fence == match.group(1) else (fence or match.group(1))
        elif fence is None:
            line = _HEADING.sub(lambda m: f"{m.group(1)}#{m.group(2)}{m.group(3)}", line)
        out.append(line)
    return "\n".join(out)


@dataclass
class Page:
    """一个渲染范围内的页面目录：节点 → 库内路径（不带 .md），以及反链等派生信息。"""
    storage: Storage
    records: dict[str, dict]                     # 参与渲染的记录（record_id → 当前记录）
    owners: dict[str, Any]                        # owner_id → Owner
    concept_owner: str | None = None
    paths: dict[str, str] = field(default_factory=dict)
    claims: dict[str, tuple[dict, dict]] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    graph: ClaimGraph | None = None

    @classmethod
    def build(cls, storage: Storage, records: list[dict], *, concept_owner: str | None = None,
              edges: list[dict] | None = None, path_for=None) -> "Page":
        """path_for：可选的路径覆盖（record → 路径或 None），供连接器把某些页放到自己的位置（如 Wiki 目录）。"""
        index = storage.owners.scan()
        page = cls(storage, {r["record_id"]: r for r in records}, {o.owner_id: o for o in index.owners},
                   concept_owner=concept_owner, edges=list(edges or []), graph=ClaimGraph(storage, index))
        taken: set[str] = set()
        for record in sorted(records, key=lambda r: (r["owner_id"], r.get("created_at", ""), r["record_id"])):
            path = (path_for(record) if path_for else None) or page._path_for(record)
            base, number = path, 2
            while syntax.normalize(path) in taken:  # 同名（如旧记录按标题生成的 handle 相同）追加序号
                path, number = f"{base}-{number}", number + 1
            taken.add(syntax.normalize(path))
            page.paths[record["record_id"]] = path
            for claim in record["payload"].get("claims", []) or []:
                page.claims[claim["claim_id"]] = (claim, record)
        return page

    def owner_short(self, owner_id: str) -> str:
        owner = self.owners.get(owner_id)
        native = owner.native if owner else {}
        return safe_name(native.get("short_name") or native.get("slug") or owner_id, owner_id)

    def _path_for(self, record: dict) -> str:
        payload = record.get("payload") or {}
        if record["kind"] == "wiki_page" and payload.get("page_type") in {"concept", "entity"} \
                and (self.concept_owner is None or record["owner_id"] == self.concept_owner):
            return f"concepts/{safe_name(record['title'])}"
        folder = "documents" if record["kind"] == "document" else "records"
        handle = record.get("handle") or syntax.make_handle(record["title"])
        return f"{folder}/{self.owner_short(record['owner_id'])}/{safe_name(handle)}"

    # ---------- 链接改写 ----------
    def target_path(self, ref: dict | None) -> str | None:
        if not ref:
            return None
        if ref["target_kind"] == "claim":
            claim = self.claims.get(ref["target_id"])
            return f"{self.paths[claim[1]['record_id']]}#^{claim_anchor(ref['target_id'])}" if claim else None
        base = self.paths.get(ref["target_id"])
        locator = str(ref.get("locator") or "")
        if base and locator.startswith("block:"):
            return f"{base}#{locator[6:]}"
        return base

    def rewrite_links(self, text: str, record: dict) -> str:
        resolved = {x["text"]: x for x in record.get("links", [])}

        def replace(match: re.Match) -> str:
            inner = match.group(1)
            link = syntax.parse_one(inner)
            display = inner.split("|", 1)[1].strip() if "|" in inner else (link.name if link else inner)
            target = self.target_path((resolved.get(link.text) or {}).get("ref")) if link else None
            return f"[[{target}|{display}]]" if target else display
        return _LINK.sub(replace, text or "")

    # ---------- 页面 ----------
    def front(self, record: dict) -> dict[str, Any]:
        payload = record.get("payload") or {}
        fields: dict[str, Any] = {
            "burrowl_id": record["record_id"], "local_key": f"{record['record_id']}@{record['revision']}",
            "handle": record.get("handle") or syntax.make_handle(record["title"]), "owner": record["owner_id"],
            "kind": record["kind"], "level": record.get("level"), "revision": record["revision"],
            "sha256": record["record_hash"], "aliases": list(payload.get("aliases", [])),
            "domains": list(payload.get("domains", [])), "tags": list(record.get("keywords", []))}
        claims = payload.get("claims", []) or []
        if claims and self.graph is not None:
            fields["claims"] = [{"id": c["claim_id"], "review": self._badge(c["claim_id"]),
                                 "maturity": self.graph.maturity(c["claim_id"])["maturity"]} for c in claims]
        return fields

    def _badge(self, claim_id: str) -> str:
        try:
            return self.graph.badge(claim_id) if self.graph else "not-reviewed"
        except KeyError:
            return "not-reviewed"

    def body(self, record: dict) -> str:
        payload = record.get("payload") or {}
        parts = [f"# {record['title']}"]
        if record["kind"] == "detail":
            question = payload["retrieval_description"].get("question")
            if question:
                parts.append(f"> 问题：{question}")
            for block in payload.get("blocks", []):
                parts.append(f"## {block['block_id']}\n\n{demote_headings(self.rewrite_links(block['markdown'], record))}")
        elif record["kind"] == "document":
            parts.extend(self._document_sections(record))
        elif record.get("body_markdown"):
            parts.append(self.rewrite_links(record["body_markdown"], record))
        claims = payload.get("claims", []) or []
        if claims:
            lines = ["## 结论"]
            for claim in claims:
                lines.append(f"- {self.rewrite_links(claim['statement'], record)}（适用范围：{claim['scope']}）"
                             f" ^{claim_anchor(claim['claim_id'])}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def _document_sections(self, record: dict) -> list[str]:
        """文稿页：章节标题 + 作者写的衔接文字 + 技术块嵌入（![[…#块]]），不复制技术块正文。"""
        out = []
        for ref in record["payload"].get("section_refs", []):
            section = self.records.get(ref["target_id"]) or self._load(ref)
            if section is None:
                out.append(f"## （章节不可读：{ref['target_id']}）")
                continue
            out.append(f"## {section['payload'].get('title') or section['title']}")
            for block in section["payload"].get("blocks", []):
                if block["type"] == "prose":
                    out.append(demote_headings(self.rewrite_links(block["markdown"], section)))
                    continue
                unit_path = self.paths.get(block["ref"]["target_id"])
                unit = self.records.get(block["ref"]["target_id"])
                ids = block.get("block_ids") or [b["block_id"] for b in (unit or {}).get("payload", {}).get("blocks", [])]
                if unit_path:
                    out.extend(f"![[{unit_path}#{block_id}]]" for block_id in ids)
                else:
                    out.append(f"（技术单元 {block['ref']['target_id']} 不在导出范围内）")
        return out

    def _load(self, ref: dict) -> dict | None:
        for owner in self.owners.values():
            if owner.persisted and owner.readable:
                found = self.storage.store.read_snapshot(owner).records.get(ref["target_id"])
                if found is not None:
                    return found
        return None

    def generated(self, record: dict) -> str:
        """自动生成区块：反链、关联、结论状态（从图投影的边和复核图算出）。"""
        rid = record["record_id"]
        node_ids = {rid, *(c["claim_id"] for c in record["payload"].get("claims", []) or [])}
        lines = [GENERATED_START, "## 关联与引用（自动生成）"]
        incoming = sorted({e["declared_by"] for e in self.edges if e["dst"] in node_ids
                           and e["category"] in {"reference", "evidence", "mention"} and e["declared_by"] in self.paths
                           and e["declared_by"] != rid})
        if incoming:
            lines.append("**被引用**：" + "、".join(f"[[{self.paths[r]}|{self.records[r]['title']}]]" for r in incoming))
        navigation = [e for e in self.edges if e["category"] == "navigation" and rid in {e["src"], e["dst"]}]
        for edge in navigation:
            other = edge["dst"] if edge["src"] == rid else edge["src"]
            if other in self.paths:
                lines.append(f"- {RELATION_NAMES.get(edge['type'], edge['type'])}"
                             f"（{CERTAINTY_NAMES.get(edge['certainty'] or 'plausible', edge['certainty'])}）："
                             f"[[{self.paths[other]}|{self.records[other]['title']}]]")
        for claim in record["payload"].get("claims", []) or []:
            maturity = self.graph.maturity(claim["claim_id"]) if self.graph else {}
            lines.append(f"- 结论 {claim['claim_id']}：复核 {self._badge(claim['claim_id'])}，"
                         f"成熟度 {maturity.get('maturity') or '未登记'}")
        if len(lines) == 2:
            lines.append("（暂无）")
        lines.append(GENERATED_END)
        return "\n".join(lines)

    def content(self, record: dict) -> str:
        """不带 YAML 头的页面内容（正文 + 自动生成区块），供自己写头的连接器使用。"""
        return self.body(record) + "\n\n" + self.generated(record)

    def render(self, record: dict) -> str:
        return yaml_front(self.front(record)) + self.content(record) + "\n"


def render_one(storage: Storage, owner_id: str, record_id: str, *, concept_owner: str | None = None,
               edges: list[dict] | None = None) -> dict:
    """工作台用：渲染一条记录（链接只能指向同一对象中的记录，其他目标保留显示文字）。"""
    owner = storage.owners.scan().get(owner_id).require_readable()
    records = [r for r in storage.store.read_snapshot(owner).records.values()
               if r["kind"] not in SKIP_KINDS and r["sensitivity"] != "restricted"]
    page = Page.build(storage, records, concept_owner=concept_owner, edges=edges)
    if record_id not in page.records:
        raise BurrowlError("NOT_FOUND", "记录不存在或不可渲染", {"record_id": record_id})
    return {"record_id": record_id, "path": page.paths[record_id] + ".md", "markdown": page.render(page.records[record_id])}
