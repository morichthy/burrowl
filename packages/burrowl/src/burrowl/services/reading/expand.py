r"""联想 = 从关键节点出发、只走跨对象边的一跳图扩展（R19 D-06、§4.4）。

种子与权重 $\sigma(n)$：笔记 `expand_from` 中的锚点为 1.0，笔记的其余出处为 0.3（该笔记没写锚点时为 1.0）；
出处是整条记录时节点取该记录及其结论（不展开全部块），出处是 `MEM-…#块`、`CLM-…`、`SRC-…` 时就是该节点。
块与结论种子另带上所在记录，权重减半且只走**入边**（其他对象指向这条记录的引用）：其他对象通常引用整条记录
而不是某个块，只走块自身的边会漏掉它们；不走记录自己的出边，避免回到"整条记录作种子"的弱关联（R19 走查发现）。

图分数（$w_e$ 查 index/graph.py 的统一边标准，$\deg$ 为节点度数，压低热门节点）：
$$g(m)=\sum_{n\xrightarrow{e}m}\sigma(n)\,\frac{w_e}{\log_2(2+\deg m)}$$
概念与来源节点只作中转：$n \xrightarrow{e_1} c \xrightarrow{e_2} m$ 计为一跳，增益再乘 $w_{e_2}/\log_2(2+k_c)$，
且只经 $k_c \le$ concept_max_degree 的中转节点（热门概念不中转）。$k_c$ 是与 $c$ 相连的不同对象数，不是边数：
同一条记录会从 sources、阶段依据、结论依据各连一条边，记录与它的结论又是不同节点，按边数或节点数都会把
只被两个对象引用的来源误判为热门；"热门"的本意是被很多对象引用。

焦点相关度 $q(m)\in[0,1]$：取 $g$ 最高的 3K 个节点，用重排模型（不可用时用向量相似度，再不行用词重合）
比较节点文本与焦点（AI 写的缺口 / 用户补充的方向 / 会话问题）。对象分数 $s(B)=\sum_{m\in B} g(m)\,q(m)$，
每个对象最多保留 target_nodes 个节点作为关系窗口与定点阅读目标，取 $s\ge$ min_score 的前 expand_owners 个。

只读，不改会话；调用方（ReadingService.recall）负责把结果写进会话。
"""
import math
from typing import Any

from ...core.hashing import canonical_hash
from ...index import graph as graph_projection
from ...index.tokenize import tokens
from ..indexing import REVIEW_FILTERS, IndexService
from . import screening

SUPPORT_WEIGHT, ANCHOR_WEIGHT = 0.3, 1.0
PARENT_FACTOR = 0.5  # 块、结论种子所在记录的权重系数（只走入边）
MEDIATORS = {"concept", "source"}  # 只作中转的节点类型：概念页、已登记来源
EXCLUDED_STATES = {"rejected", "withdrawn", "invalid"}
UNSAFE = graph_projection.UNSAFE
MATURITY_FACTOR = {"established": 1.0, "replicated": 0.8, "preliminary": 0.6, "conjecture": 0.4, None: 0.5}
WINDOW_CHARS = 400


def seeds(session: dict) -> dict[str, float]:
    """会话中尚未扩展过的种子节点 → σ。没写笔记的对象不提供种子。"""
    used = set(session["association"].get("seeded", []))
    out: dict[str, float] = {}
    for state in session["owners"].values():
        note = state.get("note")
        if not note:
            continue
        anchors = {a["ref"] for a in note.get("expand_from", [])}
        for source in note["sources"]:
            sigma = ANCHOR_WEIGHT if source in anchors or not anchors else SUPPORT_WEIGHT
            if source not in used:
                out[source] = max(out.get(source, 0.0), sigma)
    return out


def expand(index: IndexService, session: dict, *, focus: str, settings: dict, reranking: str,
           reranker_path=None, edge_budget: int | None = None) -> dict:
    """返回 {owners, packets, seeds, gaps, cost}；packets 与 screening.packets 同形，另带 origin 与 targets。"""
    index.ensure_graph()
    sigma = seeds(session)
    gaps: list[dict] = []
    if not sigma:
        return {"owners": [], "packets": {}, "seeds": [], "gaps": [{"code": "NO_SEEDS", "message":
                "没有可扩展的种子：先读完相关对象并写笔记（出处与 expand_from 锚点即种子）"}],
                "cost": {"candidates": 0, "rerank_items": 0, "graph_edges": 0}}
    # 概念库对象只作中转，不作候选（R19 §4.4）
    in_session = set(session["owners"]) | set(session.get("skip_owner_ids") or []) | {index.concept_owner()}
    limit = edge_budget if edge_budget is not None else 10**9
    scanned = 0

    def edges_of(nodes: list[str]) -> list[dict]:
        nonlocal scanned
        found = [e for e in index.db.graph_edges(nodes) if e["state"] not in EXCLUDED_STATES]
        if scanned + len(found) > limit:
            found = found[:max(0, limit - scanned)]
            if not any(g["code"] == "GRAPH_BUDGET_TRUNCATED" for g in gaps):
                gaps.append({"code": "GRAPH_BUDGET_TRUNCATED", "message": "扫描边数达到会话预算，扩展结果不完整"})
        scanned += len(found)
        return found

    # 出处为整条记录时，节点取该记录及其结论（记录 → 结论的 contains 边），不展开全部块
    seed_nodes = dict(sigma)
    for edge in edges_of([n for n in sigma if n.startswith("MEM-") and "#" not in n]):
        if edge["type"] == "contains" and edge["dst"].startswith("CLM-") and edge["src"] in sigma:
            seed_nodes[edge["dst"]] = max(seed_nodes.get(edge["dst"], 0.0), sigma[edge["src"]])
    node_rows = {n["node_id"]: n for n in index.db.graph_nodes(list(seed_nodes))}
    seed_owners = {row["owner_id"] for row in node_rows.values()}
    # 块、结论种子的所在记录：权重减半，只走入边（其他对象指向这条记录的引用）
    incoming_only: set[str] = set()
    for node, weight in list(seed_nodes.items()):
        parent = node.split("#", 1)[0] if "#" in node else (
            (node_rows.get(node) or {}).get("record_id") if node.startswith("CLM-") else None)
        if parent and parent != node and parent not in sigma:
            seed_nodes[parent] = max(seed_nodes.get(parent, 0.0), weight * PARENT_FACTOR)
            incoming_only.add(parent)

    # 所在记录只走入边；关联是对称的导航关系，两个方向都算
    first = [e for e in edges_of(list(seed_nodes)) if e["category"] in graph_projection.EXPANDABLE
             and not (e["src"] in incoming_only and e["dst"] not in seed_nodes and e["category"] != "navigation")]
    declared = {e["declared_by"] for e in first if e["declared_by"].startswith("CLM-")}
    others = {(e["dst"] if e["src"] in seed_nodes else e["src"]) for e in first}
    node_rows.update({n["node_id"]: n for n in index.db.graph_nodes(sorted(others | declared))})
    unsafe_claims = {c for c in declared if (node_rows.get(c) or {}).get("review") in UNSAFE}

    # 同一对节点之间常有多条边（sources、阶段依据、结论依据各一条），只取最强的一条，避免重复计分
    direct: dict[tuple[str, str], tuple[float, dict]] = {}
    for edge in first:
        if edge["declared_by"] in unsafe_claims:
            continue
        here, there = (edge["src"], edge["dst"]) if edge["src"] in seed_nodes else (edge["dst"], edge["src"])
        weight = _weight(edge, node_rows)
        if weight <= 0 or there in seed_nodes:
            continue
        gain = seed_nodes[here] * weight
        if (here, there) not in direct or gain > direct[(here, there)][0]:
            direct[(here, there)] = (gain, edge)
    reached: list[tuple[str, float, dict, str | None]] = []  # (目标节点, 增益, 边, 经过的中转节点)
    mediators: dict[str, list[tuple[float, dict]]] = {}
    for (_here, there), (gain, edge) in direct.items():
        if (node_rows.get(there) or {}).get("node_type") in MEDIATORS:
            mediators.setdefault(there, []).append((gain, edge))
        else:
            reached.append((there, gain, edge, None))

    around = [e for e in edges_of(sorted(mediators)) if e["category"] in graph_projection.EXPANDABLE]
    node_rows.update({n["node_id"]: n for n in index.db.graph_nodes(sorted({e["src"] for e in around}
                                                                         | {e["dst"] for e in around}))})
    neighbors: dict[str, set[str]] = {}
    for edge in around:
        for c, other in ((edge["src"], edge["dst"]), (edge["dst"], edge["src"])):
            owner_id = (node_rows.get(other) or {}).get("owner_id")
            if c in mediators and owner_id:
                neighbors.setdefault(c, set()).add(owner_id)
    degrees = {c: len(neighbors.get(c, ())) for c in mediators}
    small = {c for c in mediators if degrees[c] <= settings["concept_max_degree"]}
    hop: dict[tuple[str, str], float] = {}  # (中转节点, 目标节点) → 最强的第二段权重
    for edge in (e for e in around if e["src"] in small or e["dst"] in small):
        mediator = edge["src"] if edge["src"] in small else edge["dst"]
        there = edge["dst"] if mediator == edge["src"] else edge["src"]
        if there in seed_nodes or (node_rows.get(there) or {}).get("node_type") in MEDIATORS:
            continue
        weight = _weight(edge, node_rows) / math.log2(2 + degrees.get(mediator, 0))
        hop[(mediator, there)] = max(hop.get((mediator, there), 0.0), weight)
    for (mediator, there), weight in hop.items():
        for gain, first_edge in mediators[mediator]:
            reached.append((there, gain * weight, first_edge, mediator))

    # 只走跨对象的边：目标对象不能是种子所在对象，也不能已在会话中；范围、可见性、复核过滤与第一轮召回一致
    scope = session.get("scope_owner_ids")
    target_degree = index.db.graph_degrees(sorted({t for t, *_ in reached}))
    scores: dict[str, float] = {}
    best: dict[str, tuple[float, dict, str | None]] = {}
    for there, gain, edge, via in reached:
        owner_id = (node_rows.get(there) or {}).get("owner_id")
        if not owner_id or owner_id in in_session or owner_id in seed_owners or (scope and owner_id not in scope):
            continue
        g = gain / math.log2(2 + target_degree.get(there, 0))
        scores[there] = scores.get(there, 0.0) + g
        if there not in best or g > best[there][0]:
            best[there] = (g, edge, via)
    ranked = sorted(scores, key=lambda n: -scores[n])[:3 * settings["expand_owners"] * settings["target_nodes"]]
    owners = sorted({node_rows[n]["owner_id"] for n in ranked})
    entries = index.db.entries_for_nodes("fulltext", ranked, owners, review=REVIEW_FILTERS[session["review_filter"]],
                                         explicit_owners=bool(scope))
    entry_of: dict[str, dict] = {}
    for entry in entries:
        node = f"{entry['record_id']}#{entry['block_id']}" if entry.get("block_id") else (
            entry["canonical_id"] if entry["kind"] == "claim" else entry["record_id"])
        entry_of.setdefault(node, entry)
    visible = [n for n in ranked if n in entry_of]  # 映射不到条目的节点（不可见、受限、被复核过滤）丢弃

    relevance, rerank_items, diagnostics = _relevance(index, focus, {n: entry_of[n]["text"] for n in visible},
                                                      reranking, reranker_path)
    # 对象汇总：同一条记录（含它的块与结论）只计一次，取其中最好的节点
    by_record: dict[str, tuple[float, str]] = {}
    for node in visible:
        record_id = entry_of[node]["record_id"]
        value = scores[node] * relevance[node]
        if record_id not in by_record or value > by_record[record_id][0]:
            by_record[record_id] = (value, node)
    by_owner: dict[str, list[tuple[float, str]]] = {}
    for value, node in by_record.values():
        by_owner.setdefault(entry_of[node]["owner_id"], []).append((value, node))
    totals = []
    for owner_id, items in by_owner.items():
        items.sort(key=lambda x: (-x[0], x[1]))
        kept = items[:settings["target_nodes"]]
        total = sum(s for s, _ in kept)
        contradicts = any(best[n][1]["type"] == "contradicts" for _s, n in kept)
        if total >= settings["min_score"]:
            totals.append((total, contradicts, owner_id, [n for _s, n in kept]))
    totals.sort(key=lambda x: (-round(x[0], 6), not x[1], x[2]))  # 同分时有矛盾关系的排前
    chosen = totals[:settings["expand_owners"]]

    owner_rows = {n["node_id"]: n for n in index.db.graph_nodes([o for _t, _c, o, _n in chosen])}
    packets = {owner_id: _packet(owner_id, owner_rows.get(owner_id), total, targets, best, node_rows, entry_of, index)
               for total, _c, owner_id, targets in chosen}
    return {"owners": [o for _t, _c, o, _n in chosen], "packets": packets, "seeds": sorted(seed_nodes),
            "gaps": gaps, "diagnostics": diagnostics,
            "cost": {"candidates": len(chosen), "rerank_items": rerank_items, "graph_edges": scanned}}


def _weight(edge: dict, node_rows: dict) -> float:
    maturity = (node_rows.get(edge["declared_by"]) or {}).get("maturity")
    return graph_projection.expand_weight(edge, MATURITY_FACTOR.get(maturity, 0.5))


def _relevance(index: IndexService, focus: str, texts: dict[str, str], reranking: str,
               reranker_path) -> tuple[dict[str, float], int, dict[str, Any]]:
    """焦点相关度 q ∈ [0, 1]：重排模型（sigmoid）→ 向量余弦 → 词重合，取第一个可用的。"""
    diagnostics: dict[str, Any] = {"relevance": "lexical"}
    if not texts:
        return {}, 0, diagnostics
    model = screening._load_model(reranking, reranker_path, {})
    if model is not None:
        pairs = [(focus, t[:2000]) for t in texts.values()]
        scores = model.score_pairs(pairs)
        diagnostics["relevance"] = "rerank"
        return {n: 1 / (1 + math.exp(-s)) for n, s in zip(texts, scores, strict=True)}, len(pairs), diagnostics
    embedder = index.db.embedder
    if embedder is not None:
        query = embedder.embed_query(focus)
        vectors = embedder.embed_passages(list(texts.values()))
        diagnostics["relevance"] = "vector"
        return {n: max(0.0, _cosine(query, v)) for n, v in zip(texts, vectors, strict=True)}, 0, diagnostics
    wanted = {t.casefold() for t in tokens(focus)}
    return {n: len(wanted & {t.casefold() for t in tokens(t)}) / max(1, len(wanted)) for n, t in texts.items()}, 0, \
        diagnostics


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def _packet(owner_id: str, owner_row: dict | None, total: float, targets: list[str], best: dict, node_rows: dict,
            entry_of: dict, index: IndexService) -> dict:
    """候选对象的筛选包：每个目标节点一个关系窗口（不可作为笔记出处）+ 一个内容窗口（与普通窗口相同）。"""
    windows = []
    for node in targets:
        _g, edge, via = best[node]
        title = {k: (node_rows.get(k) or {}).get("title") or k for k in (edge["src"], edge["dst"])}
        reading = graph_projection.phrase(edge["type"], title[edge["src"]], title[edge["dst"]],
                                          edge_category=edge["category"])
        if via:
            reading += f"（经 {(node_rows.get(via) or {}).get('title') or via}）"
        windows.append({"entry_id": f"relation:{edge['edge_id']}:{node}", "record_id": None, "kind": "relation",
                        "title": reading, "level": None, "review": None,
                        "text": _relation_text(edge, node_rows, index)[:WINDOW_CHARS],
                        "edge": {"type": edge["type"], "category": edge["category"], "src": edge["src"],
                                 "dst": edge["dst"], "declared_by": edge["declared_by"], "via": via},
                        "target": node, "ref": None})
        entry = entry_of[node]
        windows.append({"entry_id": entry["entry_id"], "record_id": entry["record_id"], "title": entry["title"],
                        "level": entry["level"], "kind": entry["kind"], "review": entry["review"],
                        "text": entry["text"][:WINDOW_CHARS], "ref": entry["fixed_ref"]})
    return {"owner_id": owner_id, "title": (owner_row or {}).get("title") or owner_id, "score": round(total, 6),
            "channels": ["graph"], "windows": windows, "extra_windows": [], "origin": "expansion",
            "targets": targets, "digest": canonical_hash([w["entry_id"] for w in windows])[:16]}


def _relation_text(edge: dict, node_rows: dict, index: IndexService) -> str:
    """关系窗口文字（R19 §4.3）：关联取其说明；结论声明的边取结论条目（陈述 + 适用范围 + 角色读法，R23 D-14），
    条件不能脱离结论；其余取声明记录的标题。"""
    if edge["category"] == "navigation" or edge["declared_by"].startswith("CLM-"):
        ids = [f"{corpus}:{edge['declared_owner']}:{edge['declared_by']}" for corpus in ("discovery", "fulltext")]
        entry = next(iter(index.db.entries(ids)), None)
        if entry:
            return entry["text"]
    declarer = node_rows.get(edge["declared_by"]) or next(iter(index.db.graph_nodes([edge["declared_by"]])), {})
    return declarer.get("title") or edge["declared_by"]
