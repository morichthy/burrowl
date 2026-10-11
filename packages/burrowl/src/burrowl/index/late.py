"""后切分的条目分组（R17，设计见 docs/design/R17_LATE_CHUNKING.md）。

技术记录（L1）的记录级条目 = 检索说明 + 全部块正文（+ 其余字段与关键词），块另有块级条目。
后切分时整条记录只编码一次：块级条目的向量取自这次编码里该块所在的区间（看过全文上下文），
记录级条目的向量取块以外的区间（检索说明等），不再把多个块压进同一个向量。
其他条目各自编码一次，按段落切成不超过 span_tokens 的区间。
"""
from .embed import Window


def _norm(vector: list[float]) -> list[float]:
    size = sum(v * v for v in vector) ** 0.5 or 1.0
    return [v / size for v in vector]


def late_windows(embedder, changed: list[dict], current: list[dict]) -> tuple[list[tuple[dict, Window]], list[list[float]]]:
    """为变化的条目生成 [(条目, 区间)] 与对应向量。只输出 changed 中的条目（调用方只替换这些条目的向量）。"""
    wanted = {e["entry_id"] for e in changed}
    blocks: dict[str, list[dict]] = {}
    for entry in current:
        if entry["corpus"] == "fulltext" and entry.get("block_id"):
            blocks.setdefault(entry["record_id"], []).append(entry)
    pairs: list[tuple[dict, Window]] = []
    vectors: list[list[float]] = []
    done: set[str] = set()

    for record in changed:
        if record["corpus"] != "fulltext" or record.get("block_id") or record["record_id"] not in blocks:
            continue
        text, cursor, located = record["text"], 0, []
        for block in blocks[record["record_id"]]:  # 投影按块的顺序拼接记录正文
            at = text.find(block["text"], cursor) if block["text"] else -1
            if at < 0:
                located = []  # 对不上就退回各自编码，不猜位置
                break
            located.append((block, at))
            cursor = at + len(block["text"])
        if not located:
            continue
        spans, owners, position = [], [], 0
        for block, at in [*located, (None, len(text))]:
            for a, b in embedder.segments(text[position:at]):  # 块之间（检索说明、其余字段）归记录本身
                spans.append((position + a, position + b))
                owners.append((record, 0))
            if block is not None:
                for a, b in embedder.segments(block["text"]):
                    spans.append((at + a, at + b))
                    owners.append((block, at))
                position = at + len(block["text"])
        result = embedder.late_embed(record["embed_prefix"] or record["title"], text, spans)
        own = []
        for (owner, base), (a, b), vector in zip(owners, spans, result, strict=True):
            if owner is record:
                own.append(vector)
            if owner["entry_id"] in wanted:
                pairs.append((owner, Window(a - base, b - base, text[a:b])))
                vectors.append(vector)
        if not own and record["entry_id"] in wanted:  # 没有块外文字：记录向量取各块向量的平均
            pairs.append((record, Window(0, len(text), "")))
            vectors.append(_norm([sum(column) for column in zip(*result, strict=True)]))
        done |= {record["entry_id"], *(block["entry_id"] for block, _at in located)}

    for entry in changed:
        if entry["entry_id"] in done:
            continue
        prefix = entry["embed_prefix"] or entry["title"]
        spans = embedder.segments(entry["text"])
        if not spans:  # 没有正文（如只有标题的关联记录）：只编码前缀
            pairs.append((entry, Window(0, 0, prefix)))
            vectors.extend(embedder.embed_passages([prefix]))
            continue
        for (a, b), vector in zip(spans, embedder.late_embed(prefix, entry["text"], spans), strict=True):
            pairs.append((entry, Window(a, b, entry["text"][a:b])))
            vectors.append(vector)
    return pairs, vectors
