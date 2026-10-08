"""多通道结果融合：先在每个通道内按记录折叠，再用 RRF 合并排名。

折叠避免同一记录的多个块/窗口挤掉其他记录；RRF 只看名次，同一通道重复命中不会重复加分。
排名是相关性，不是可信度。
"""
from collections.abc import Callable

RRF_K = 60


def collapse(rows: list[dict], key: Callable[[dict], str]) -> list[dict]:
    """按 key 保留每组首个（名次最高）命中，并把同组的块命中记为 matched_blocks。"""
    seen: dict[str, dict] = {}
    for row in rows:
        group = key(row)
        if group not in seen:
            seen[group] = dict(row, matched_blocks=[row["block_id"]] if row.get("block_id") else [])
        elif row.get("block_id") and row["block_id"] not in seen[group]["matched_blocks"]:
            seen[group]["matched_blocks"].append(row["block_id"])
    return list(seen.values())


def record_key(row: dict) -> str:
    return row["canonical_id"]


def owner_key(row: dict) -> str:
    return row["owner_id"]


def fuse_routes(batches: list[tuple[dict, list[dict]]], key: Callable[[dict], str] = record_key, *,
                by_family: bool = False) -> list[dict]:
    """多路线融合。by_family=True（按 Owner 汇总时）：同一信号族（如所有关键词路线）
    对同一 Owner 只投一票、取最好名次——同义改写丰富出处，但不能重复加分。"""
    merged: dict[str, dict] = {}
    votes: dict[tuple[str, str], tuple[int, float]] = {}
    for route, rows in batches:
        family = f"{route['kind']}:{route['channel']}"
        for rank, row in enumerate(collapse(rows, key), start=1):
            group = key(row)
            item = merged.setdefault(group, dict(row, score=0.0, query_sources=[], matched_blocks=[], hits=[]))
            item["query_sources"].append({"route": route["id"], "rank": rank, "channel": route["channel"]})
            if len(item["hits"]) < 8:
                item["hits"].append({"entry_id": row["entry_id"], "record_id": row["record_id"],
                                     "block_id": row.get("block_id"), "route": route["id"]})
            if by_family:
                previous = votes.get((group, family))
                if previous is None or rank < previous[0]:
                    votes[(group, family)] = (rank, route["weight"])
            else:
                item["score"] += route["weight"] / (RRF_K + rank)
    if by_family:
        for (group, _family), (rank, weight) in votes.items():
            merged[group]["score"] += weight / (RRF_K + rank)
    return sorted(merged.values(), key=lambda r: (-r["score"], key(r)))


def rrf(channels: dict[str, list[dict]], key: Callable[[dict], str] = record_key, *,
        weights: dict[str, float] | None = None) -> list[dict]:
    """返回融合后的列表；每项带 score、各通道名次 ranks 与原始分 channel_scores。"""
    merged: dict[str, dict] = {}
    for channel, rows in channels.items():
        weight = (weights or {}).get(channel, 1.0)
        for rank, row in enumerate(collapse(rows, key), start=1):
            item = merged.setdefault(key(row), dict(row, score=0.0, ranks={}, channel_scores={}, matched_blocks=[]))
            item["score"] += weight / (RRF_K + rank)
            item["ranks"][channel] = rank
            item["channel_scores"][channel] = row.get("raw_score")
            for block in row.get("matched_blocks", []):
                if block not in item["matched_blocks"]:
                    item["matched_blocks"].append(block)
    return sorted(merged.values(), key=lambda r: (-r["score"], r["canonical_id"]))
