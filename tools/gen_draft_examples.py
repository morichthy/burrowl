"""生成 core/schema/examples/<kind>.json：每类记录一份最小但真实的草案示例（浮点求和样例的固定引用）。

这些示例由 memory.contract 返回给 AI，并由 tests/service/test_draft_examples.py 用真实校验器逐条预检，
保证示例与契约同步。改了记录契约后：uv run python tools/gen_draft_examples.py && uv run pytest tests/service/test_draft_examples.py
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "packages/burrowl/src/burrowl/core/schema/examples"
OWNER = "RES-FLOATING-POINT-SUMMATION"


def ref(kind, tid, rev, sha, rel, loc=""):
    return {"target_kind": kind, "target_id": tid, "revision": rev, "sha256": sha, "locator": loc, "relation": rel}


FILE_R01 = ref("file", "SRC-SUMMATION-R01", None,
               "cdb1f9e4f0580d5baaea4a09416f75801e6c5d01eab96c601ded00a28e31134d", "input", "全文")
DETAIL = ref("record", "MEM-4acc73bb-59c7-560d-9415-fd42e9dcaa2d", 1,
             "ba12342c6ca8afa0a1760dc517fd7bff2d1b60c7f082ee9113e419572834df51", "references", "record")
NARR = ref("record", "MEM-77b35d77-f6c1-5d05-80a1-7c3b9d5e9d09", 4,
           "9918032b4a47d068d2056472853db83cee83c46cbb13501bac91b32458d203ed", "references", "record")
EXP = ref("record", "MEM-58fd845a-05a5-516c-8bbe-c88d887cbb91", 6,
          "e5fc7cb2327f0b0412166c545c6ae9e862dad0692c0038775dcb4deb0766e3c2", "references", "record")
GOAL = ref("record", "MEM-3090dc56-e320-5db7-8489-e0bff4d9a153", 1,
           "942b428430701fe15176250bd73d682b6c10379ba2ae1ef07afc5d8e0410c4f7", "references", "record")
SECTION = ref("record", "MEM-240055fb-b318-5e3e-85a0-dadf88507a18", 1,
              "e73d93cd8e625924ae01ddd9ae80476c3652311335ecbfcfd47c914d8fa9be41", "contains", "record")


def rel(r, relation):
    return {**r, "relation": relation}


def claim(cid, statement, scope, refs, kind="fact"):
    return {"claim_id": cid, "statement": statement, "kind": kind, "scope": scope, "evidence_refs": refs}


def draft(kind, title, body, payload, sources, reason, keywords=(), gap=None):
    return {"owner_id": OWNER, "kind": kind, "title": title, "keywords": list(keywords), "body_markdown": body,
            "record_reason": reason, "sensitivity": "internal", "sources": sources, "provenance_gap": gap,
            "payload": payload}


EXAMPLES = {
    "source": (
        "L0 原始依据：先用 sources.register 登记文件（得到 SRC-ID 与 sha256），再写这条登记说明。"
        "body_markdown 说明材料是什么、谁产生、怎么解释（列、单位、限制）；原件不改，未展开原件不能声称已核验全文。",
        draft("source", "第一轮原始结果：单位增量与抵消（登记说明）",
              "第一轮实验脚本输出的原始结果表：顺序求和与 Kahan 补偿求和在三项抵消输入上的逐步累加值。"
              "列依次为步号、累加值、精确参考值，数值无量纲。本条只登记原件，不给结论。",
              {"source_ref": FILE_R01, "acquisition": "original_link", "completeness": "complete",
               "acquired_at": None, "missing_refs": []},
              [FILE_R01], "登记第一轮原始结果，供技术单元固定引用", ["浮点求和", "原始结果"])),
    "detail": (
        "L1 技术单元：完整正文只放 payload.blocks，body_markdown 必须为空字符串；retrieval_description 是检索用摘要，"
        "不是正文。unit_type=experiment 时 run_ref 必须是真实 Run；结果块用 requires_block_ids 声明依赖的定义块。",
        draft("detail", "顺序求和误差的精确基准计算方法", "",
              {"unit_type": "method", "run_ref": None, "evidence_refs": [rel(FILE_R01, "supports")], "figures": [],
               "missing_refs": [],
               "retrieval_description": {
                   "question": "如何为浮点求和误差建立精确基准",
                   "method": "用有理数逐项累加程序实际收到的 binary64 输入",
                   "key_findings": ["精确基准把输入表示误差与求和误差分开"],
                   "applicable": ["有限 binary64 输入、无量纲数列"],
                   "not_applicable": ["分析十进制实数到 binary64 的表示误差"],
                   "limitations": ["只覆盖已保存的三组构造输入"]},
               "blocks": [
                   {"block_id": "definitions", "role": "definitions", "requires_block_ids": [],
                    "markdown": "## 定义\n\n$x_i$ 为程序实际收到的 binary64 数，$\\widehat S$ 为浮点求和结果，"
                                "精确参考 $S_{\\mathrm{ref}}=\\sum_i \\operatorname{Fraction}(x_i)$，"
                                "绝对误差 $e=|\\operatorname{Fraction}(\\widehat S)-S_{\\mathrm{ref}}|$，均无量纲。"},
                   {"block_id": "method", "role": "methods", "requires_block_ids": ["definitions"],
                    "markdown": "## 方法\n\n逐项读取输入，分别用顺序求和与 Kahan 补偿求和得到 $\\widehat S$，"
                                "再与 $S_{\\mathrm{ref}}$ 比较。依据：第一轮原始结果 [SRC-SUMMATION-R01]。"}]},
              [FILE_R01], "保存可复用的基准计算方法", ["浮点求和", "精确基准"])),
    "narrative": (
        "L2 经过：body_markdown 写 背景 → 实际尝试、选择理由、观察与转折 → 简单结论和未决问题；"
        "stages 至少一段（situation/action/reason/outcome/evidence_refs）。需要复核的结论写进 claims。",
        draft("narrative", "从单位增量丢失到补偿比较的第一轮经过",
              "## 背景\n\n研究要比较求和顺序对抵消输入的影响。\n\n## 经过\n\n先用顺序求和处理三项抵消输入，"
              "单位增量完全丢失；随后加入 Kahan 补偿作对照，补偿后仍丢失，说明补偿不能覆盖所有抵消情形。\n\n"
              "## 结论与未决\n\n在这组三项输入上，顺序与补偿都未保住单位增量；更多排列需要后续实验。"
              "依据：[SRC-SUMMATION-R01]。",
              {"question": "求和顺序与补偿能否保住被大数抵消的单位增量", "limitations": ["只有三项构造输入"],
               "stages": [
                   {"situation": "三项抵消输入", "action": "顺序求和", "reason": "建立基线",
                    "outcome": "单位增量丢失", "evidence_refs": [rel(FILE_R01, "supports")]},
                   {"situation": "基线丢失", "action": "加入 Kahan 补偿对照", "reason": "检验补偿能否恢复",
                    "outcome": "补偿后仍丢失", "evidence_refs": [rel(FILE_R01, "supports")]}],
               "claims": [claim("CLM-EXAMPLE-NARR-1", "在三项抵消输入上 Kahan 补偿未能保住单位增量",
                                "三项构造的 binary64 抵消输入", [rel(FILE_R01, "supports")])],
               "experience_refs": [], "process_refs": [], "technical_refs": [DETAIL]},
              [FILE_R01, DETAIL], "记录第一轮实际经过", ["浮点求和", "Kahan"])),
    "experience": (
        "L3 可复用认识：写 适用问题 → 认识/做法 → 依据 → 反例与不适用边界。knowledge_type 区分 "
        "observation/conclusion/hypothesis/recommendation，它不是复核状态；假设不能写成结论。",
        draft("experience", "补偿求和不能替代抵消场景的精确基准",
              "## 适用问题\n\n判断浮点求和结果在大数抵消输入上是否可信时。\n\n## 认识\n\n补偿求和能减小一般舍入误差，"
              "但在大数抵消、小量夹在中间的输入上仍可能丢失小量；下结论前应先用有理数精确基准核对。\n\n"
              "## 依据与边界\n\n依据第一轮与第三轮实验经过。只在有限 binary64 抵消输入上观察到；"
              "不能推出补偿求和在一般输入上无效。",
              {"problem_structure": "大数抵消导致小量丢失",
               "recommendation": "抵消场景先用精确基准核对，再判断补偿是否足够",
               "applicable": ["有限 binary64 输入", "存在大数抵消"], "prohibited": ["推广到无抵消的一般输入"],
               "failure_modes": ["补偿项本身被抵消吞没"], "retry_conditions": ["换用更多排列或更高精度累加"],
               "knowledge_type": "recommendation", "claim_refs": [],
               "claims": [claim("CLM-EXAMPLE-EXP-1", "抵消场景下应先用精确基准核对补偿求和结果",
                                "有限 binary64 抵消输入", [rel(NARR, "supports")], kind="recommendation")],
               "process_refs": [NARR], "technical_refs": [DETAIL]},
              [NARR, DETAIL], "提炼可迁移到其他求和问题的经验", ["浮点求和", "补偿求和"])),
    "overview": (
        "L4 概览：对象的发现入口，不读下层也能知道 用途与范围 → 主要方法 → 当前结果/状态 → 限制与未决。"
        "长期复用的 Owner 至少维护一条；短经验可以只有一条自足的 L4。",
        draft("overview", "浮点求和数值稳定性研究概览",
              "## 用途与范围\n\n研究有限 binary64 数列求和时顺序与补偿对误差的影响，重点是大数抵消场景。\n\n"
              "## 当前结果\n\n顺序与 Kahan 补偿在构造的抵消输入上都可能丢失小量；以有理数精确基准为判断依据。\n\n"
              "## 限制与未决\n\n只覆盖构造输入；随机输入与成对求和尚未研究。",
              {"question": "求和顺序与补偿如何影响抵消场景的误差", "methods": ["有理数精确基准", "顺序与补偿对照"],
               "results": ["抵消输入上补偿仍可能丢失小量"], "current_stage": "三轮实验已完成，扩展研究未开始",
               "limitations": ["仅构造输入"], "open_questions": ["成对求和是否更稳健"], "claims": [],
               "experience_refs": [EXP], "process_refs": [NARR], "technical_refs": [DETAIL]},
              [EXP, NARR, DETAIL], "维护对象的顶层发现入口", ["浮点求和", "概览"])),
    "document_section": (
        "文稿章节（level=null）：blocks 由 prose（作者写的衔接文字，附 evidence_refs）和 unit（整块收录技术单元，"
        "block_ids 选块，自动补必需定义）组成；body_markdown 为空。",
        draft("document_section", "方法：精确基准", "",
              {"section_key": "methods-baseline", "title": "方法：精确基准", "role": "methods",
               "watch_refs": [], "missing_refs": [],
               "blocks": [{"type": "prose", "markdown": "本节说明误差的计算基准。",
                           "evidence_refs": [rel(DETAIL, "supports")]},
                          {"type": "unit", "ref": rel(DETAIL, "contains"), "block_ids": ["definitions", "reference"]}]},
              [DETAIL], "为文稿组织方法章节")),
    "document": (
        "文稿根（level=null）：document_type 为 research_process（完整过程稿）或 research_report（精简报告）；"
        "section_refs 按阅读顺序列章节；先保存章节再保存文稿根。",
        draft("document", "浮点求和研究：精简报告（示例）", "",
              {"document_type": "research_report", "purpose": "概括方法、结果与边界", "audience": "需要快速了解结论的读者",
               "scope": "已完成的三轮实验", "common_refs": [DETAIL], "section_refs": [SECTION], "watch_refs": [],
               "missing_refs": []},
              [DETAIL], "编排面向读者的报告")),
    "goal": (
        "目标（level=null）：持续工作的目标、约束与成功标准；修改目标时引用上一目标并写 change_impact。",
        draft("goal", "扩展到随机输入的求和误差研究", "",
              {"objective": "在随机抵消输入上比较顺序、补偿与成对求和", "constraints": ["只用有限 binary64 输入"],
               "success_criteria": ["每种方法给出误差分布与反例"], "previous_goal_ref": GOAL,
               "change_impact": "在原目标基础上扩大输入范围", "missing_refs": []},
              [GOAL], "登记扩展研究目标")),
    "question": (
        "问题（level=null）：未决问题、它影响的决策和缺失证据；解决时 status=resolved 并给出 resolution_refs。",
        draft("question", "成对求和在抵消输入上是否更稳健", "",
              {"question": "成对求和在抵消输入上的误差是否显著小于顺序与补偿", "status": "open",
               "decision_affected": "扩展研究选用哪种求和作对照", "missing_evidence": ["成对求和实验结果"],
               "resolution_refs": [], "replacement_ref": None, "reopen_reason": None, "missing_refs": []},
              [EXP], "记录未决问题")),
    "route": (
        "路线（level=null）：为某个目标采取的一条假设与做法；受阻时写 blocker 与 reopen_condition。",
        draft("route", "用成对求和作为新对照", "",
              {"goal_ref": GOAL, "hypothesis": "成对求和能减少抵消场景的小量丢失", "status": "planned",
               "attempt_refs": [], "blocker": None, "next_step": "实现成对求和并复用精确基准",
               "reopen_condition": "无", "replacement_ref": None, "missing_refs": []},
              [GOAL], "规划研究路线")),
    "checkpoint": (
        "检查点（level=null）：交接时点的状态：已完成什么、下一步、前提与停止原因。续接时 memory.resume 会读它。",
        draft("checkpoint", "三轮实验完成，扩展研究待开始", "",
              {"goal_ref": GOAL, "route_refs": [], "completed_refs": [NARR, DETAIL], "question_refs": [],
               "next_step": "设计随机抵消输入的生成方法", "prerequisites": ["确认随机种子与样本量"],
               "stop_reason": "阶段收口",
               "budget_remaining": {"value": None, "reason": "not_applicable", "note": "无预算约束"},
               "missing_refs": []},
              [GOAL, NARR, DETAIL], "保存交接检查点")),
    "association": (
        "关联（level=null）：相似、对照、依赖等导航关系，不是证据。analogous_to 必须写 shared_structure 与 "
        "transfer_limits。通常先由 relations.associations 产生候选，再用 relations.decide 处理；手写时 status=candidate。",
        draft("association", "补偿经验与第三轮经过的同一失败模式", "",
              {"from": rel(EXP, "same_failure_mode"), "to": rel(NARR, "same_failure_mode"),
               "relation": "same_failure_mode", "explanation": "两者都记录了补偿项被抵消吞没的失败",
               "shared_structure": None, "transfer_limits": [], "differences": [], "basis_refs": [EXP, NARR],
               "status": "candidate", "missing_refs": []},
              [EXP, NARR], "登记导航关联")),
    "wiki_page": (
        "Wiki 页面（level=null）：宿主 AI 编写、经 hub.plan/hub.apply 发布到知识中枢；slug 为 <类型>/<名称>，"
        "source_refs 必须是固定引用。",
        draft("wiki_page", "补偿求和",
              "补偿求和（Kahan 求和）在每一步保存舍入误差并在下一步补回，能减小一般输入的舍入误差；"
              "在大数抵消输入上仍可能丢失小量，需要用精确基准核对。",
              {"slug": "concept/compensated-summation", "page_type": "concept", "aliases": ["Kahan 求和"],
               "source_refs": [EXP, DETAIL], "missing_refs": []},
              [EXP, DETAIL], "整理概念页")),
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for kind, (note, body) in EXAMPLES.items():
        doc = {"kind": kind, "note": note,
               "operation": {"op": "put_record", "client_key": f"new-{kind}", "draft": body}}
        (OUT / f"{kind}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8", newline="\n")
    print(f"wrote {len(EXAMPLES)} examples to {OUT}")


if __name__ == "__main__":
    main()
