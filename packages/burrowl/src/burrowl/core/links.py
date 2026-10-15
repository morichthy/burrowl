"""正文链接的语法、规范化与可读短名（R12 D-09、D-28、§4.3）。纯函数，不访问存储。

写法（AI 和人只写可读名，提交时由服务解析并固定版本）：

    [[补偿求和]]                    概念（概念库中按标题、别名匹配）
    [[@第三轮实验]]                 本对象内的记录（按 handle）
    [[@浮点求和/第三轮实验]]        其他对象的记录（斜杠前是对象短名、slug 或 owner_id）
    [[@第三轮实验#kahan]]           记录中的块（只对 L1 有效）
    [[!补偿抵消失效]]               结论（claim_id 以 "-补偿抵消失效" 结尾；也可写完整 CLM-…）
    [[!浮点求和/补偿抵消失效]]      其他对象的结论
    [[@第三轮实验#fig-1]]           记录中的图（R24：图与块共用 # 后的锚点名）
    ![[@第三轮实验#fig-1]]          在此处显示被引用的图（前缀 !，只能指向图）
    ![[#fig-1]]                     图的归属记录在原位置显示自己的图（不是引用，不进 links）
    [[…|显示文字]]                  竖线后是显示文字，不参与解析

代码围栏与行内代码里的 [[…]] 不算链接。
"""
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any, Literal

from .records import HANDLE_FORBIDDEN, HANDLE_MAX

_LINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
_FENCE = re.compile(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$", re.M | re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")

LinkKind = Literal["concept", "record", "block", "claim", "anchor"]

# 给 AI 的引用写法（R24 D-03、D-10）：报错与提示统一引用这一处
WRITING_RULES = ("引用写法：① 记录、块或图写 [[@短名]]、[[@短名#块或图]]，其他对象前加“对象/”；"
                 "② 结论写 [[!短名]]；③ 在此处显示一张图在引用前加 !：![[@短名#图]]，"
                 "图的归属记录显示自己的图写 ![[#图]]；④ 提到概念写 [[名称]]，概念不算出处")


@dataclass(frozen=True)
class Link:
    text: str            # 竖线前的原文（不含方括号），也是"重新固定"规则里比较的键
    kind: LinkKind
    name: str            # 概念名 / 记录 handle / 结论短名 / 本记录锚点名
    owner: str | None    # 斜杠前的对象名；None 表示本对象（概念不分对象）
    block: str | None = None  # # 后的锚点名：块号或图的锚点名（R24 D-01）
    embed: bool = False  # 前面紧跟 !：在此处显示（R24 D-05）


def normalize(name: str) -> str:
    """匹配用的规范形：全角转半角（NFKC）、忽略大小写、空白折叠为一个空格。"""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def _blank_code(text: str) -> str:
    """把代码围栏和行内代码替换为等长空白，保持其余位置不变。"""
    def blank(match: re.Match) -> str:
        return re.sub(r"[^\n]", " ", match.group(0))
    return _INLINE_CODE.sub(blank, _FENCE.sub(blank, text))


def parse_one(inner: str) -> Link | None:
    """解析 [[ ]] 内部的文字；写法不合法（如空名）返回 None。"""
    text = inner.split("|", 1)[0].strip()
    if not text:
        return None
    if text[0] == "#":  # 本记录锚点（R24）
        name = text[1:].strip()
        return Link(text, "anchor", name, None, name) if name else None
    if text[0] in "@!":
        sigil, body = text[0], text[1:].strip()
        owner, _, name = body.rpartition("/") if "/" in body else (None, "", body)
        owner = owner.strip() if owner else None
        block = None
        if sigil == "@" and "#" in name:
            name, _, block = name.partition("#")
            block = block.strip() or None
        name = name.strip()
        if not name or (owner is not None and not owner):
            return None
        if sigil == "!":
            return Link(text, "claim", name, owner)
        return Link(text, "block" if block else "record", name, owner, block)
    return Link(text, "concept", text, None)


def parse(text: str) -> list[Link]:
    """按出现顺序返回不重复的链接（同一原文只保留一次；任一处带 ! 前缀即记为嵌入）。"""
    found: dict[str, Link] = {}
    visible = _blank_code(text or "")
    for match in _LINK.finditer(visible):
        link = parse_one(match.group(1))
        if link is None:
            continue
        embed = match.start() > 0 and visible[match.start() - 1] == "!"
        _merge(found, replace(link, embed=embed))
    return list(found.values())


def _merge(found: dict[str, Link], link: Link) -> None:
    old = found.get(link.text)
    if old is None:
        found[link.text] = link
    elif link.embed and not old.embed:
        found[link.text] = replace(old, embed=True)


def link_texts(record: dict, *, claim_parts: bool = True) -> list[str]:
    """一条记录中会被扫描链接的文字：正文、L1 各块、文稿章节的叙述块、结论的陈述与适用范围、结论 relation 的参与者。
    payload 中其他字符串不扫描（§4.3）。适用范围中的链接生成条件边（R23 D-06）。
    claim_parts=False 时不含适用范围与参与者（图投影中它们由结论要素边表达，不再从记录连提及边）。"""
    payload = record.get("payload") or {}
    texts = [record.get("body_markdown") or ""]
    if record.get("kind") == "detail":
        texts.extend(b.get("markdown", "") for b in payload.get("blocks", []))
    if record.get("kind") == "document_section":
        texts.extend(b.get("markdown", "") for b in payload.get("blocks", []) if b.get("type") == "prose")
    for claim in payload.get("claims", []) or []:
        texts.append(claim.get("statement", ""))
        if claim_parts:
            texts.append(claim.get("scope", ""))
            texts.extend(p.get("value", "") for p in (claim.get("relation") or {}).get("participants", []))
    return [t for t in texts if t]


# R21 D-10：正文里不写服务生成的长编号与指纹，引用用 [[@短名]]；代码与链接内部不检查
_RAW_ID = re.compile(r"(?<![\w-])([A-Z]{2,5})-[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?![\w-])")
_RAW_HASH = re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
RAW_ID_KINDS = frozenset({"detail", "narrative", "experience", "overview", "document", "document_section"})


def _text_fields(record: dict) -> list[tuple[str, str]]:
    """与 link_texts 相同的扫描范围，带字段路径（相对草案）。"""
    payload = record.get("payload") or {}
    fields = [("/body_markdown", record.get("body_markdown") or "")]
    if record.get("kind") in {"detail", "document_section"}:
        fields.extend((f"/payload/blocks/{i}/markdown", b.get("markdown", ""))
                      for i, b in enumerate(payload.get("blocks", []))
                      if record.get("kind") == "detail" or b.get("type") == "prose")
    for i, c in enumerate(payload.get("claims", []) or []):
        fields.extend(((f"/payload/claims/{i}/statement", c.get("statement", "")),
                       (f"/payload/claims/{i}/scope", c.get("scope", ""))))
    return [(path, text) for path, text in fields if text]


def raw_identifiers(record: dict) -> list[dict]:
    """正文中出现的长编号（XXX-<uuid>）与 64 位十六进制指纹：[{path, text, prefix}]。只检查 RAW_ID_KINDS。"""
    if record.get("kind") not in RAW_ID_KINDS:
        return []
    found = []
    for path, text in _text_fields(record):
        visible = _LINK.sub(lambda m: " " * len(m.group(0)), _blank_code(text))
        for match in _RAW_ID.finditer(visible):
            found.append({"path": path, "text": match.group(0), "prefix": match.group(1)})
        for match in _RAW_HASH.finditer(visible):
            found.append({"path": path, "text": match.group(0), "prefix": "sha256"})
    return found


def collect(record: dict) -> list[Link]:
    found: dict[str, Link] = {}
    for text in link_texts(record):
        for link in parse(text):
            _merge(found, link)
    return list(found.values())


def make_handle(title: str) -> str:
    """按标题生成 handle：保留中文，空白改为 "-"，去掉链接语法字符，最长 64 字符。"""
    value = unicodedata.normalize("NFKC", title or "").strip()
    value = "".join("-" if ch.isspace() else ch for ch in value if ch not in HANDLE_FORBIDDEN)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value[:HANDLE_MAX].rstrip("-") or "record"


def unique_handle(base: str, taken: set[str]) -> str:
    """同一对象内冲突时追加 -2、-3……（比较时用规范形）。"""
    folded = {normalize(t) for t in taken}
    return pick_handle(base, folded.__contains__)


def pick_handle(base: str, is_taken: Callable[[str], bool], counters: dict[str, int] | None = None) -> str:
    """unique_handle 的批量版：is_taken 接收规范形，调用方维护已占用的规范形，不必每次重新规范化全部 handle。

    counters 记录每个 base 上次用到的后缀编号，同一批里大量同名时从那里继续试，避免逐个重试 -2、-3……。
    只有在占用集合只增不减时才可传入（批量分配 handle 时成立），结果与不传相同。
    """
    if not is_taken(normalize(base)):
        return base
    number = (counters or {}).get(base, 2)
    while True:
        suffix = f"-{number}"
        candidate = base[:HANDLE_MAX - len(suffix)] + suffix
        if not is_taken(normalize(candidate)):
            if counters is not None:
                counters[base] = number + 1
            return candidate
        number += 1


def claim_short_name(claim_id: str, short: str) -> bool:
    """[[!短名]] 是否指向这条结论：完整 ID 相同，或 ID 以 "-短名" 结尾（比较规范形）。"""
    cid, name = normalize(claim_id), normalize(short)
    return cid == name or cid.endswith("-" + name)


def as_dict(link: Link) -> dict[str, Any]:
    return {"text": link.text, "kind": link.kind, "name": link.name, "owner": link.owner, "block": link.block}
