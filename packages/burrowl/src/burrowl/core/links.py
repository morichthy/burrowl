"""正文链接的语法、规范化与可读短名（R12 D-09、D-28、§4.3）。纯函数，不访问存储。

写法（AI 和人只写可读名，提交时由服务解析并固定版本）：

    [[补偿求和]]                    概念（概念库中按标题、别名匹配）
    [[@第三轮实验]]                 本对象内的记录（按 handle）
    [[@浮点求和/第三轮实验]]        其他对象的记录（斜杠前是对象短名、slug 或 owner_id）
    [[@第三轮实验#kahan]]           记录中的块（只对 L1 有效）
    [[!补偿抵消失效]]               结论（claim_id 以 "-补偿抵消失效" 结尾；也可写完整 CLM-…）
    [[!浮点求和/补偿抵消失效]]      其他对象的结论
    [[…|显示文字]]                  竖线后是显示文字，不参与解析

代码围栏与行内代码里的 [[…]] 不算链接。
"""
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from .records import HANDLE_FORBIDDEN, HANDLE_MAX

_LINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
_FENCE = re.compile(r"^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$", re.M | re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")

LinkKind = Literal["concept", "record", "block", "claim"]


@dataclass(frozen=True)
class Link:
    text: str            # 竖线前的原文（不含方括号），也是"重新固定"规则里比较的键
    kind: LinkKind
    name: str            # 概念名 / 记录 handle / 结论短名
    owner: str | None    # 斜杠前的对象名；None 表示本对象（概念不分对象）
    block: str | None = None


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
    """按出现顺序返回不重复的链接（同一原文只保留一次）。"""
    found: dict[str, Link] = {}
    for match in _LINK.finditer(_blank_code(text or "")):
        link = parse_one(match.group(1))
        if link is not None and link.text not in found:
            found[link.text] = link
    return list(found.values())


def link_texts(record: dict) -> list[str]:
    """一条记录中会被扫描链接的文字：正文、L1 各块、文稿章节的叙述块、结论陈述与结论 relation 的两端。
    payload 中其他字符串不扫描（§4.3）。"""
    payload = record.get("payload") or {}
    texts = [record.get("body_markdown") or ""]
    if record.get("kind") == "detail":
        texts.extend(b.get("markdown", "") for b in payload.get("blocks", []))
    if record.get("kind") == "document_section":
        texts.extend(b.get("markdown", "") for b in payload.get("blocks", []) if b.get("type") == "prose")
    for claim in payload.get("claims", []) or []:
        texts.append(claim.get("statement", ""))
        relation = claim.get("relation") or {}
        texts.extend(relation.get(k, "") for k in ("subject", "object"))
    return [t for t in texts if t]


def collect(record: dict) -> list[Link]:
    found: dict[str, Link] = {}
    for text in link_texts(record):
        for link in parse(text):
            found.setdefault(link.text, link)
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
