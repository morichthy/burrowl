"""R19 D-06、D-07、D-11、D-12：联想 = 从笔记出处出发、只走跨对象边的一跳图扩展；定点阅读；扩大检索审批。

样例（种子对象 A 的 X1 被笔记引用；X2 没有被引用）：
  X1 --references--> B1        X1 的结论 --contradicts--> C1      X1 ==关联== E1
  X1 --input--> SRC-S <--input-- F1（低度数来源中转）
  X1 --input--> SRC-H <--input-- H1、H2（高度数来源，不中转）
  X1 的另一条结论（被标为 disputed）--supports--> G1（失效结论声明的边不扩展）
  X2 --references--> D1（X2 不是种子）        X1 --references--> X2（对象内部，不扩展）
"""
import pytest

from burrowl.core.config import BurrowlConfig, IndexConfig
from burrowl.core.errors import BurrowlError
from burrowl.services.indexing import IndexService
from burrowl.services.memory import associations
from burrowl.services.memory.context import Storage
from burrowl.services.memory.service import MemoryService
from burrowl.services.reading.service import ReadingService
from burrowl.services.review import ReviewService, claim_lookup_factory
from tests.service.helpers import AI, HUMAN, head_of, narrative_draft, register_source, request

QUESTION = "X1 实验"  # 只有 A 的 X1 含这两个词：第一轮只召回 A，其余对象留给扩展


def fixed(memory, owner_id, record_id, relation):
    record = memory.inspect(owner_id, record_id=record_id)["record"]
    return {"target_kind": "record", "target_id": record_id, "revision": record["revision"],
            "sha256": record["record_hash"], "locator": "record", "relation": relation}


def put(memory, owner_id, draft):
    receipt = memory.commit(request(owner_id, head_of(memory, owner_id),
                                    {"op": "put_record", "client_key": "k", "draft": draft}))
    return receipt["record_results"][0]["record_id"]


def narrative(owner_id, title, statement, claim_id, sources, *, claims=None):
    draft = narrative_draft(owner_id, sources[0], claim_id=claim_id, statement=statement)
    draft.update(title=title, body_markdown=statement, sources=sources)
    if claims is not None:
        draft["payload"]["claims"] = claims
    return draft


class Lab:
    def __init__(self, root):
        self.storage = Storage.open(root)
        self.index = IndexService.open(self.storage, IndexConfig(), fake_vectors=True)
        self.memory = MemoryService(self.storage, index=self.index,
                                    claim_lookup_factory=claim_lookup_factory(self.storage))
        self.review = ReviewService(self.storage, self.memory)
        self.config = BurrowlConfig()
        self.config.reading.reranking.mode = "off"
        self.config.reading.association.min_score = 0
        self.config.reading.association.concept_max_degree = 2
        self.reading = ReadingService(self.storage, self.index, lambda: self.config)
        self.ids = {}

    def owner(self, slug, title):
        return self.storage.owners.create("research", slug, title).owner_id

    def other(self, slug, title, statement, *, src=None):
        owner_id = self.owner(slug, title)
        evidence = src or register_source(self.storage, f"SRC-{slug.upper()}", f"work/{slug}.txt", f"{slug}\n")
        self.ids[slug] = put(self.memory, owner_id, narrative(owner_id, title, statement, f"CLM-{slug.upper()}-1",
                                                               [evidence]))
        return owner_id


@pytest.fixture
def lab(tmp_path):
    lab = Lab(tmp_path)
    shared = register_source(lab.storage, "SRC-S", "work/shared.csv", "s\n")
    hub = register_source(lab.storage, "SRC-H", "work/hub.csv", "h\n")
    for slug, title, statement in (("b", "对象 B", "B1：求和方法的参考实现"), ("c", "对象 C", "C1：降序求和在大数抵消下更好"),
                                   ("d", "对象 D", "D1：与问题无关的格式说明"), ("e", "对象 E", "E1：相同的抵消问题"),
                                   ("g", "对象 G", "G1：已被质疑的依据")):
        lab.other(slug, title, statement)
    lab.other("f", "对象 F", "F1：同一份数据的另一种分析", src=shared)
    for slug in ("h1", "h2"):
        lab.other(slug, f"对象 {slug.upper()}", f"{slug}：共用热门数据", src=hub)
    a = lab.owner("a", "求和顺序研究")
    m = lab.memory
    x2 = put(m, a, narrative(a, "X2 附带记录", "X2：格式整理", "CLM-A-X2",
                             [register_source(lab.storage, "SRC-A2", "work/a2.txt", "a2\n"),
                              fixed(m, "RES-D", lab.ids["d"], "references")]))
    claims = [{"claim_id": "CLM-A-1", "statement": "升序求和误差更小", "kind": "fact", "scope": "整数输入",
               "evidence_refs": [shared, fixed(m, "RES-C", lab.ids["c"], "contradicts")]},
              {"claim_id": "CLM-A-BAD", "statement": "所有顺序误差相同", "kind": "fact", "scope": "整数输入",
               "evidence_refs": [fixed(m, "RES-G", lab.ids["g"], "supports")]}]
    x1 = put(m, a, narrative(a, "X1 求和顺序实验", "X1：求和顺序对浮点误差的影响", "CLM-A-1",
                             [shared, hub, fixed(m, "RES-B", lab.ids["b"], "references"),
                              fixed(m, a, x2, "references")], claims=claims))
    associations.decide(m, a, {"from": fixed(m, a, x1, "references"), "to": fixed(m, "RES-E", lab.ids["e"], "references"),
                               "explanation": "两者都处理大数抵消"}, actor=AI, reason="读过两端")
    lab.review.submit({"target_claim_id": "CLM-A-BAD", "state": "disputed", "reason": "样本太少", "actor": HUMAN})
    lab.ids.update(a=a, x1=x1, x2=x2)
    lab.index.db.embedder = None  # 只用关键词：第一轮召回可预期；焦点相关度走词重合
    return lab


def read_all(service, sid, owner_id, **kwargs):
    page = 1
    while True:
        result = service.read(sid, owner_id, page, **kwargs)
        if not result["has_more"]:
            return result
        page += 1


def noted_session(lab, *, sources=None, anchors=None):
    """A 判为相关、读完、写笔记（出处默认只有 X1）；其余对象判为无关。"""
    service = lab.reading
    session = service.start(QUESTION, actor=AI, strategy="associative")
    sid = session["session_id"]

    def only_a(state):  # 模拟大库中第一轮只召回到 A（小样例里关键词会命中所有对象），其余对象留给扩展
        state["owners"] = {lab.ids["a"]: state["owners"][lab.ids["a"]]}
        state["queue"] = [lab.ids["a"]]
        return state
    service.sessions.update(sid, only_a)
    while batch := service.status(sid)["batch"]:
        service.assess(sid, [{"owner_id": p["owner_id"], "digest": p["digest"], "reason": "依据窗口",
                              "verdict": "relevant" if p["owner_id"] == lab.ids["a"] else "irrelevant"}
                             for p in batch])
    assert service.view(sid)["owners"][lab.ids["a"]]["verdict"] == "relevant"
    read_all(service, sid, lab.ids["a"])
    sources = sources or [lab.ids["x1"]]
    service.note(sid, lab.ids["a"], "笔记：" + "、".join(sources), sources, expand_from=anchors)
    return sid


def candidates(lab, sid, **kwargs):
    result = lab.reading.recall(sid, "association", **kwargs)
    return [p["owner_id"] for p in result["batch"]], result


def test_expands_only_across_owners_from_note_sources(lab):
    sid = noted_session(lab)
    owners, result = candidates(lab, sid)
    assert {"RES-B", "RES-C", "RES-E", "RES-F"} <= set(owners)
    assert "RES-D" not in owners                       # X2 没被笔记引用，不是种子
    assert lab.ids["a"] not in owners                  # 对象内部的关系不扩展
    assert "RES-G" not in owners                       # 失效结论声明的边不扩展
    assert not {"RES-H1", "RES-H2"} & set(owners)      # 高度数来源不中转
    view = lab.reading.view(sid)
    round_ = view["rounds"][-1]
    assert round_["kind"] == "association" and round_["seeds"] and view["association"]["rounds_used"] == 1
    packet = next(p for p in result["batch"] if p["owner_id"] == "RES-C")
    relation = next(w for w in packet["windows"] if w["kind"] == "relation")
    assert "矛盾" in relation["title"] and relation["record_id"] is None
    assert len([w for w in packet["windows"] if w["kind"] == "relation"]) <= 3
    # 关系窗口不能作为出处：关系窗口没有可引用身份
    assert view["owners"]["RES-C"]["delivered"] == sorted({w["record_id"] for w in packet["windows"]
                                                           if w.get("record_id")})


def test_no_seeds_does_not_consume_a_round(lab):
    session = lab.reading.start(QUESTION, actor=AI, strategy="associative")
    with pytest.raises(BurrowlError) as caught:
        lab.reading.recall(session["session_id"], "association")
    assert caught.value.code == "INVALID_TRANSITION"
    assert lab.reading.view(session["session_id"])["association"]["rounds_used"] == 0


def test_expansion_requires_associative_strategy_and_round_limit(lab):
    lab.config.reading.association.max_rounds = 1  # 会话建立时固定上限
    sid = noted_session(lab)
    candidates(lab, sid)
    with pytest.raises(BurrowlError) as caught:
        lab.reading.recall(sid, "association")
    assert caught.value.code == "BUDGET_EXCEEDED"
    standard = lab.reading.start(QUESTION, actor=AI)
    with pytest.raises(BurrowlError) as caught:
        lab.reading.recall(standard["session_id"], "association")
    assert caught.value.code == "INVALID_TRANSITION"


def test_anchor_rules_and_citable_claims(lab):
    with pytest.raises(BurrowlError):                  # 锚点不在出处中
        noted_session(lab, anchors=[{"ref": "MEM-NOPE", "why": "x"}])
    sid = noted_session(lab, sources=["CLM-A-1", lab.ids["x1"]], anchors=[{"ref": "CLM-A-1", "why": "有反例待查"}])
    note = lab.reading.view(sid)["owners"][lab.ids["a"]]["note"]
    assert note["expand_from"] == [{"ref": "CLM-A-1", "why": "有反例待查"}]
    assert any(f.get("target_kind") == "claim" and f["target_id"] == "CLM-A-1" for f in note["fixed_sources"])
    owners, _ = candidates(lab, sid)
    assert owners[0] == "RES-C"                        # 锚点结论的矛盾边带来的候选排在最前
    with pytest.raises(BurrowlError):
        lab.reading.note(sid, lab.ids["a"], "x", ["CLM-A-1"],
                         expand_from=[{"ref": "CLM-A-1", "why": "a"}] * 4)


def test_record_source_does_not_expand_all_blocks_and_focus_reorders(lab):
    sid = noted_session(lab)
    lab.config.reading.association.min_score = 0.0001
    owners, _ = candidates(lab, sid, focus="相同的抵消问题")
    assert owners and owners[0] == "RES-E"
    assert lab.reading.view(sid)["rounds"][-1]["focus"] == "相同的抵消问题"


def test_targeted_reading_and_upgrade(lab):
    sid = noted_session(lab)
    owners, result = candidates(lab, sid)
    packet = next(p for p in result["batch"] if p["owner_id"] == "RES-B")
    lab.reading.assess(sid, [{"owner_id": "RES-B", "verdict": "relevant", "reason": "参考实现",
                              "digest": packet["digest"]}])
    targeted = lab.reading.read(sid, "RES-B", 1)
    assert targeted["scope"] == "targets" and lab.ids["b"] in targeted["ids"]
    with pytest.raises(BurrowlError):                  # 出处只能是已交付节点
        lab.reading.note(sid, "RES-B", "x", ["MEM-NOT-DELIVERED"])
    lab.reading.note(sid, "RES-B", f"参考实现（{lab.ids['b']}）", [lab.ids["b"]])
    full = lab.reading.read(sid, "RES-B", 1, scope="full")
    assert full["scope"] == "full"
    assert any(e["action"] == "read_full" for e in lab.reading.view(sid)["timeline"])


def test_broaden_requires_approval(lab):
    session = lab.reading.start(QUESTION, actor=AI)
    sid = session["session_id"]
    with pytest.raises(BurrowlError) as caught:
        lab.reading.recall(sid, "broaden", question="所有求和方法")
    assert caught.value.code == "APPROVAL_REQUIRED"
    fulltext = lab.reading.ask_user(sid, "clarify", "问题范围？")["request"]
    approval = lab.reading.respond(sid, fulltext["request_id"], decision="approve")["approval_id"]
    with pytest.raises(BurrowlError):                  # 别的请求的批准不能用于扩大检索
        lab.reading.recall(sid, "broaden", approval_id=approval, question="所有求和方法")
    request = lab.reading.ask_user(sid, "broaden", "放宽到所有求和方法")["request"]
    approval = lab.reading.respond(sid, request["request_id"], decision="approve")["approval_id"]
    lab.reading.recall(sid, "broaden", approval_id=approval, question="所有求和方法")
    with pytest.raises(BurrowlError):                  # 批准只能用一次
        lab.reading.recall(sid, "broaden", approval_id=approval, question="所有求和方法")
    with pytest.raises(BurrowlError) as caught:
        lab.reading.recall(sid, "association", association_text="旧写法")
    assert caught.value.code == "INVALID_ARGUMENT"
