"""后切分（R17）：段落切分、逐段池化、L1 记录与块的分组、配置边界、新数据目录的默认模型。"""
from dataclasses import dataclass

import numpy as np
import pytest
from pydantic import ValidationError

from burrowl.app import App
from burrowl.core.config import BurrowlConfig, Embedding
from burrowl.index.embed import late_vectors, segments
from burrowl.index.late import late_windows

CLS, SEP = 1, 2


@dataclass
class _Encoding:
    ids: list[int]
    offsets: list[tuple[int, int]]


class CharTokenizer:
    """每个非空白字符一个 token（id = 码位 + 10），首尾特殊符号偏移为 (0, 0)。"""

    def encode(self, text: str, add_special_tokens: bool = True) -> _Encoding:
        pairs = [(ord(ch) + 10, (i, i + 1)) for i, ch in enumerate(text) if not ch.isspace()]
        ids, offsets = [p[0] for p in pairs], [p[1] for p in pairs]
        if add_special_tokens:
            return _Encoding([CLS, *ids, SEP], [(0, 0), *offsets, (0, 0)])
        return _Encoding(ids, offsets)


TOK = CharTokenizer()


def test_segments_pack_paragraphs_and_split_long_ones():
    text = "aaaa\n\nbbb\n\ncccccccccc\n\n  \n\ndd"
    spans = segments(TOK, text, span_tokens=8)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)                         # 覆盖全文
    assert all(a < b for a, b in spans) and all(x[1] == y[0] for x, y in zip(spans, spans[1:], strict=False))   # 首尾相接
    assert text[spans[0][0]:spans[0][1]].split() == ["aaaa", "bbb"]               # 相邻段落合并到上限内
    sizes = [len(TOK.encode(text[a:b], add_special_tokens=False).ids) for a, b in spans]
    assert max(sizes) <= 8 and sum(sizes) == len(TOK.encode(text, add_special_tokens=False).ids)
    assert segments(TOK, " \n\n ", 8) == []


def test_late_vectors_pool_each_span_with_shared_context_and_batches():
    seen = []

    def run(ids):  # 每个位置的输出 = [token id, 序列长度]：池化结果可以直接核对
        seen.append(ids)
        return np.array([[float(i), float(len(ids))] for i in ids])

    text = "abcdef"
    vectors = late_vectors(TOK, run, "H", text, [(0, 3), (3, 6)], context_tokens=64)
    assert len(seen) == 1 and seen[0][:2] == [CLS, ord("H") + 10] and seen[0][-1] == SEP   # 一次编码，带前缀
    first = np.array([np.mean([ord(c) + 10 for c in "abc"]), 9.0])
    assert np.allclose(vectors[0], first / np.linalg.norm(first))
    seen.clear()
    late_vectors(TOK, run, "H", text, [(0, 2), (2, 4), (4, 6)], context_tokens=7)   # 预算 = 7 - 2 - 1 = 4
    assert len(seen) == 2 and all(ids[1] == ord("H") + 10 for ids in seen)             # 分批，每批都带前缀
    seen.clear()
    late_vectors(TOK, run, "", text, [(4, 6)], context_tokens=64)
    assert seen[0][1:-1] == [ord(c) + 10 for c in text]                                # 区间外的前文也送进模型


class FakeLate:
    late = True

    def __init__(self):
        self.calls = []

    def segments(self, text):
        return segments(TOK, text, span_tokens=1000)

    def late_embed(self, prefix, text, spans):
        self.calls.append((prefix, text, spans))
        return [[float(a), float(b)] for a, b in spans]

    def embed_passages(self, texts):
        return [[0.0, 1.0] for _ in texts]


def _entry(entry_id, text, *, record="MEM-1", block=None, corpus="fulltext"):
    return {"entry_id": entry_id, "record_id": record, "block_id": block, "corpus": corpus, "text": text,
            "embed_prefix": f"P:{entry_id}", "title": entry_id}


def test_l1_blocks_take_vectors_from_one_record_pass():
    blocks = [_entry("b1", "块一正文", block="b1"), _entry("b2", "块二正文", block="b2")]
    record = _entry("r", "检索说明\n\n块一正文\n\n块二正文\n关键词")
    embedder = FakeLate()
    pairs, vectors = late_windows(embedder, [record, *blocks], [record, *blocks])
    assert len(embedder.calls) == 1 and embedder.calls[0][0] == "P:r"                 # 整条记录只编码一次
    by_entry = {}
    for (entry, window), vector in zip(pairs, vectors, strict=True):
        by_entry.setdefault(entry["entry_id"], []).append((window, vector))
    assert set(by_entry) == {"r", "b1", "b2"}
    (b1_window, b1_vector), = by_entry["b1"]
    assert (b1_window.start, b1_window.end) == (0, 4) and b1_vector == [6.0, 10.0]    # 块内偏移；向量来自记录中的区间
    assert [w.text.strip() for w, _v in by_entry["r"]] == ["检索说明", "关键词"]        # 记录向量只取块外文字


def test_only_changed_entries_are_emitted_and_fallbacks():
    record = _entry("r", "块一正文")
    block = _entry("b1", "块一正文", block="b1")
    embedder = FakeLate()
    pairs, _ = late_windows(embedder, [record], [record, block])
    assert [e["entry_id"] for e, _w in pairs] == ["r"]                                  # 未变化的块不重复写入
    empty = _entry("a", "", record="MEM-2")
    pairs, vectors = late_windows(embedder, [empty], [empty])
    assert pairs[0][1].text == "P:a" and vectors == [[0.0, 1.0]]                        # 没有正文只编码前缀


def test_late_sizes_are_validated():
    with pytest.raises(ValidationError):
        Embedding(late_context_tokens=512, span_tokens=512)
    assert Embedding().chunking == "auto"


def test_new_data_dir_pins_default_model_but_old_dirs_keep_minilm(tmp_path):
    fresh = App.open(tmp_path / "new", create=True, fake_vectors=True)
    assert fresh.config.current().index.embedding.model == "jinaai/jina-embeddings-v2-base-zh"
    old = tmp_path / "old"
    (old / ".burrowl").mkdir(parents=True)                                              # 已有目录、没有配置文件
    app = App.open(old, create=True, fake_vectors=True)
    assert app.config.current().index.embedding.model == BurrowlConfig().index.embedding.model
    assert "MiniLM" in app.config.current().index.embedding.model


JINA_DIR = __import__("burrowl.index.model_store", fromlist=["x"]).default_models_dir() / "jina-embeddings-v2-base-zh"


@pytest.mark.real_model
@pytest.mark.skipif(not (JINA_DIR / "tokenizer.json").is_file(), reason="本机没有 jina-v2-base-zh")
def test_real_jina_late_chunking_matches_native_pooling_and_uses_context():
    """后切分覆盖整段时与模型原生向量一致；同一段文字放在不同上下文中，向量不同（确实看到了上下文）。"""
    from burrowl.index.embed import FastEmbedEmbedder
    model = FastEmbedEmbedder(JINA_DIR, "jinaai/jina-embeddings-v2-base-zh", 768, chunking="auto")
    assert model.late and model.late_context_tokens == 2048
    text = "温度漂移补偿实验的结论是二阶多项式足够。"
    native = np.array(model.embed_passages([text])[0])
    session_native = np.array(model.late_embed("", text, [(0, len(text))])[0])
    assert float(native @ session_native) > 0.98      # 只差首尾特殊符号是否计入平均
    a = model.late_embed("", "背景：传感器在高温下输出偏移。\n\n" + text, [(17, 17 + len(text))])[0]
    b = model.late_embed("", "背景：今天天气很好适合散步。\n\n" + text, [(16, 16 + len(text))])[0]
    assert float(np.dot(a, b)) < 0.999
