"""R12 T-P0.1：向量窗口按模型真实上限切分（E6 截断缺陷的回归测试）。"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from burrowl.index.embed import FastEmbedEmbedder, token_windows, window_plan


class CharTokenizer:
    """桩分词器：每个非空白字符一个 token；编码时加首尾两个特殊符号。"""

    def encode(self, text: str, add_special_tokens: bool = True):
        offsets = [(i, i + 1) for i, ch in enumerate(text) if not ch.isspace()]
        ids = [ord(text[s]) for s, _e in offsets]
        if add_special_tokens:
            ids = [0, *ids, 0]
        return SimpleNamespace(ids=ids, offsets=offsets)

    def decode(self, ids: list[int]) -> str:
        return "".join(chr(i) for i in ids)


def test_windows_fit_limit_and_cover_all_text():
    tok = CharTokenizer()
    text = "".join(chr(0x4E00 + i % 500) for i in range(1000))
    windows = token_windows(tok, 32, "很长的标题" * 5, text)
    assert len(windows) > 1
    for window in windows:
        assert len(tok.encode(window.text).ids) <= 32
    covered = set()
    for window in windows:
        covered.update(range(window.start, window.end))
    assert covered == set(range(len(text)))


def test_title_prefix_capped_to_quarter_of_limit():
    tok = CharTokenizer()
    windows = token_windows(tok, 32, "标" * 100, "正文")
    assert windows[0].text.split("\n")[0] == "标" * 8  # min(40, 32 // 4)


def test_empty_text_yields_prefix_only_window():
    windows = token_windows(CharTokenizer(), 32, "标题", "")
    assert [(w.start, w.end, w.text) for w in windows] == [(0, 0, "标题")]


def test_window_plan_leaves_room_for_specials():
    size, stride = window_plan(128, 10)
    assert size == 128 - 2 - 1 - 10
    assert 0 < stride < size


MODEL_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "burrowl" / "models" / "multilingual-minilm"


@pytest.mark.real_model
@pytest.mark.skipif(not (MODEL_DIR / "tokenizer.json").is_file(), reason="本机没有嵌入模型")
def test_real_model_windows_do_not_truncate():
    """复现 R12 §3.1：前缀相同、后文不同的两段文本，修复后得到不同的窗口向量。"""
    model = FastEmbedEmbedder(MODEL_DIR, "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    assert model.max_tokens == 128
    shared = "浮点数累加时舍入误差会逐步积累。" * 12
    a = model.windows("标题", shared + "补偿求和通过记录被舍弃的低位来修正结果。" * 6)
    b = model.windows("标题", shared + "今天的天气晴朗，适合在公园散步和野餐。" * 6)
    for window in a + b:
        assert model.count_tokens(window.text) <= model.max_tokens
    va = model.embed_passages([a[-1].text])[0]
    vb = model.embed_passages([b[-1].text])[0]
    dot = sum(x * y for x, y in zip(va, vb, strict=True))
    cosine = dot / (sum(x * x for x in va) ** 0.5 * sum(y * y for y in vb) ** 0.5)
    assert cosine < 0.99
