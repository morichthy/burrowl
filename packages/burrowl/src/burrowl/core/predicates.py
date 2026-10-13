"""结论 relation 的谓词框架（R23 D-03、D-04、§4.1）。纯函数，唯一定义处。

relation = {predicate, negated?, participants: [{role, value}]}。每个谓词预先定义必填角色、可选角色与方向读法
（PropBank / FrameNet 的做法），角色不在每条结论里临时命名；框架外的角色与自定义谓词用 "x-" 前缀。
同一角色可以出现多次，表示共同作用或多个结果。条件不是角色，写在 scope 里（D-06）；
矛盾不是谓词，写在 evidence_refs 的 contradicts 或复核里（D-05）。

relation 是 AI 写结论时自动生成的结构标注，不计入内容指纹（D-02，见 records.content_hash）。
"""
import re
from dataclasses import dataclass

_CUSTOM = re.compile(r"x-[a-z0-9_-]+")


@dataclass(frozen=True)
class Frame:
    label: str                           # 谓词的中文读法
    required: tuple[str, ...]            # 必须出现的角色
    optional: tuple[str, ...] = ()       # 可选角色（限定层，D-01）
    direction: tuple[str, str] | None = None   # 方向读法：from 角色 → to 角色；None 表示对称或无方向
    min_count: dict[str, int] | None = None    # 角色至少出现的次数（默认 1）


FRAMES: dict[str, Frame] = {
    "improves": Frame("改善", ("agent", "target"), ("baseline", "metric"), ("agent", "target")),
    "degrades": Frame("恶化", ("agent", "target"), ("baseline", "metric"), ("agent", "target")),
    "causes": Frame("导致", ("cause", "effect"), ("mechanism",), ("cause", "effect")),
    "prevents": Frame("阻止", ("agent", "target"), ("mechanism",), ("agent", "target")),
    "fails_on": Frame("失效于", ("method", "case"), ("metric",), ("method", "case")),
    "applies_to": Frame("适用于", ("method", "domain"), (), ("method", "domain")),
    "requires": Frame("需要", ("dependent", "requirement"), (), ("dependent", "requirement")),
    "equivalent_to": Frame("等价于", ("item",), (), None, {"item": 2}),
    "part_of": Frame("组成", ("part", "whole"), (), ("part", "whole")),
}
ROLE_LABELS = {
    "agent": "作用方", "target": "作用对象", "baseline": "基准", "metric": "指标",
    "cause": "原因", "effect": "结果", "mechanism": "机制",
    "method": "方法", "case": "失效情形", "domain": "适用对象",
    "dependent": "需求方", "requirement": "前提", "item": "比较项", "part": "部分", "whole": "整体",
    "condition": "条件",   # 只由 scope 中的链接生成（D-06），不能写进 participants
}
CONDITION = "condition"
CUSTOM_MIN_PARTICIPANTS = 2


def is_custom(name: str) -> bool:
    return bool(_CUSTOM.fullmatch(name))


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role) or (role[2:] if is_custom(role) else role)


def predicate_label(predicate: str) -> str:
    frame = FRAMES.get(predicate)
    return frame.label if frame else (predicate[2:] if is_custom(predicate) else predicate)


def validate(relation: dict) -> list[tuple[str, str]]:
    """返回 [(相对 relation 的 JSON 指针, 说明)]；结构（字段与类型）由 schema 检查。"""
    errors: list[tuple[str, str]] = []
    predicate = relation["predicate"]
    participants = relation["participants"]
    if predicate == "contradicts":
        return [("/predicate", "矛盾不是结论谓词：反例写进 evidence_refs（relation=contradicts，并写 note），"
                               "两条结论冲突用复核标 disputed（R23 D-05）")]
    frame = FRAMES.get(predicate)
    if frame is None and not is_custom(predicate):
        return [("/predicate", f"未知谓词：用 {'、'.join(FRAMES)}，或以 x- 开头自定义")]
    allowed = set(frame.required + frame.optional) if frame else set(ROLE_LABELS) - {CONDITION}
    for i, participant in enumerate(participants):
        role = participant["role"]
        if role == CONDITION:
            errors.append((f"/participants/{i}/role", "条件写在 scope 里（其中的链接会生成条件边），不写成参与者（R23 D-06）"))
        elif role not in allowed and not is_custom(role):
            hint = f"{predicate} 的角色是 {'、'.join(sorted(allowed))}" if frame else "自定义谓词的角色"
            errors.append((f"/participants/{i}/role", f"未知角色 {role}：{hint}；框架外的角色以 x- 开头"))
    roles = [p["role"] for p in participants]
    if frame:
        for role in frame.required:
            need = (frame.min_count or {}).get(role, 1)
            if roles.count(role) < need:
                errors.append(("/participants", f"{predicate} 需要角色 {role}（{ROLE_LABELS[role]}）至少 {need} 个"))
    elif len(participants) < CUSTOM_MIN_PARTICIPANTS:
        errors.append(("/participants", f"自定义谓词至少要有 {CUSTOM_MIN_PARTICIPANTS} 个参与者"))
    return errors


def display(value: str) -> str:
    """参与者的显示文字：链接取竖线后的原文写法，没有竖线取链接名；纯文字原样返回。"""
    text = value.strip()
    if text.startswith("[[") and text.endswith("]]"):
        inner = text[2:-2]
        name, _, shown = inner.partition("|")
        return (shown or name).strip().lstrip("@!")
    return text


def link_inner(value: str) -> str | None:
    """参与者是单个链接时返回竖线前的原文（与 links.Link.text 相同，用于查找解析结果）；纯文字返回 None。"""
    text = value.strip()
    if text.startswith("[[") and text.endswith("]]") and "[[" not in text[2:-2]:
        return text[2:-2].split("|", 1)[0].strip() or None
    return None


def reading(relation: dict, show=display) -> str:
    """一句话读法，例如"导致｜原因：温度漂移、基准噪声；结果：瞬态误差"。用于检索条目与关系窗口（D-14）。"""
    predicate = relation["predicate"]
    frame = FRAMES.get(predicate)
    order = list(frame.required + frame.optional) if frame else []
    grouped: dict[str, list[str]] = {}
    for participant in relation["participants"]:
        grouped.setdefault(participant["role"], []).append(show(participant["value"]))
    roles = sorted(grouped, key=lambda r: (order.index(r) if r in order else len(order), r))
    head = ("不" if relation.get("negated") else "") + predicate_label(predicate)
    return head + "｜" + "；".join(f"{role_label(r)}：{'、'.join(grouped[r])}" for r in roles)


def guide() -> dict:
    """给 AI 的谓词框架说明（memory_contract 返回，RECORDING 引用）。"""
    return {
        "shape": "relation = {predicate, negated（可选，否定结论写 true）, participants: [{role, value}]}；"
                 "value 是链接（[[概念]]、[[@记录]]、[[!结论]]，概念页不存在也照写）或纯文字（只是描述时）",
        "rules": "同一角色可以出现多次（共同作用、多个结果），不要拆成多条二元结论；条件写在 scope，不是角色；"
                 "矛盾写进 evidence_refs（contradicts），不是谓词；框架外的角色与自定义谓词以 x- 开头；"
                 "relation 是陈述的结构标注，拿不准就不写，陈述与 scope 才是权威内容",
        "frames": {name: {"读法": f.label, "必填角色": {r: ROLE_LABELS[r] for r in f.required},
                          "可选角色": {r: ROLE_LABELS[r] for r in f.optional},
                          **({"至少": f.min_count} if f.min_count else {}),
                          "方向": f"{ROLE_LABELS[f.direction[0]]} → {ROLE_LABELS[f.direction[1]]}" if f.direction
                          else "无方向"}
                   for name, f in FRAMES.items()},
    }
