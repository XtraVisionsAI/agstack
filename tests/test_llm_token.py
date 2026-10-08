#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""按模型分派分词器（llm.token，3.2）

- 注册表：register → tokenizer_for；缺省分词器；未注册且无缺省沿用 3.1 回退（tiktoken encoding_for_model → cl100k）；
- count_tokens 签名不变、经注册表分派、长文本走 LRU（同一分词器同一文本只编码一次）；
- ApproxTokenizer 按 CJK / 其他字符比例、缺省偏保守；HFTokenizer 加载本地 tokenizer.json（可选依赖，缺失跳过）。
真实 tiktoken 编码表需联网下载，相关断言在拿不到时跳过。
"""

from __future__ import annotations

import pytest

from agstack.genai.llm import token as tok


class _Fake:
    exact = True

    def __init__(self, name: str, per_char: int = 1):
        self.name = name
        self.per_char = per_char
        self.calls = 0

    def count(self, text: str) -> int:
        self.calls += 1
        return len(text) * self.per_char


@pytest.fixture(autouse=True)
def _clean():
    tok.clear_tokenizers()
    yield
    tok.clear_tokenizers()


def test_registry_dispatch_and_default():
    qwen, fallback = _Fake("fake:qwen"), _Fake("fake:default", per_char=2)
    tok.register_tokenizer("qwen3", qwen)
    assert tok.tokenizer_for("qwen3") is qwen
    assert tok.count_tokens("abc", "qwen3") == 3
    assert tok.registered_tokenizers() == {"qwen3": "fake:qwen"}

    tok.set_default_tokenizer(fallback)
    assert tok.tokenizer_for("unknown-model") is fallback
    assert tok.count_tokens("abc", "unknown-model") == 6
    tok.set_default_tokenizer(None)
    assert isinstance(tok.Tokenizer, type) and isinstance(qwen, tok.Tokenizer)


def test_legacy_fallback_without_registration():
    try:
        legacy = tok.tokenizer_for("some-unknown-model")
    except Exception:  # noqa: BLE001 — 无网络拿不到编码表
        pytest.skip("tiktoken encoding unavailable offline")
    assert legacy.name == f"tiktoken:{tok.LEGACY_ENCODING}" and legacy.exact
    assert tok.tokenizer_for("gpt-4o").name == "tiktoken:o200k_base"
    assert tok.tokenizer_for("some-unknown-model") is legacy  # 按模型缓存
    assert tok.count_tokens("hello <|endoftext|> world", "some-unknown-model") > 0  # 特殊串不抛


def test_count_cache_hits_once_per_text():
    fake = _Fake("fake:c")
    tok.register_tokenizer("m", fake)
    long_text = "字" * 200
    assert tok.count_tokens(long_text, "m") == 200
    assert tok.count_tokens(long_text, "m") == 200
    assert fake.calls == 1
    tok.count_tokens("short", "m")
    tok.count_tokens("short", "m")
    assert fake.calls == 3  # 短文本不进缓存
    other = _Fake("fake:other", per_char=3)
    tok.register_tokenizer("m2", other)
    assert tok.count_tokens(long_text, "m2") == 600  # 缓存按分词器名隔离


def test_approx_tokenizer_ratios():
    approx = tok.ApproxTokenizer()
    assert not approx.exact and approx.name == "approx"
    assert approx.count("") == 0
    assert approx.count("中文十个字符的句子啊") == 8  # 10 / 1.3 → ceil 8
    assert approx.count("a" * 35) == 10
    assert approx.count("中文" + "a" * 7) == 2 + 2
    vendor = tok.ApproxTokenizer(cjk_chars_per_token=2.0, other_chars_per_token=4.0, name="claude")
    assert vendor.name == "approx:claude" and vendor.count("中文中文") == 2
    with pytest.raises(ValueError):
        tok.ApproxTokenizer(cjk_chars_per_token=0)


def test_hf_tokenizer_from_local_file(tmp_path):
    pytest.importorskip("tokenizers")
    from tokenizers import Tokenizer as HF
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    vocab = {"[UNK]": 0, "hello": 1, "world": 2, "agstack": 3}
    hf = HF(WordLevel(vocab, unk_token="[UNK]"))
    hf.pre_tokenizer = Whitespace()
    target = tmp_path / "qwen3"
    target.mkdir()
    hf.save(str(target / "tokenizer.json"))

    by_dir = tok.HFTokenizer(target)
    assert by_dir.exact and by_dir.name == "hf:qwen3"
    assert by_dir.count("hello world agstack") == 3
    assert by_dir.count("hello unknown") == 2
    by_file = tok.HFTokenizer(target / "tokenizer.json", name="hf:custom")
    assert by_file.name == "hf:custom"
    tok.register_tokenizer("qwen3", by_dir)
    assert tok.count_tokens("hello world", "qwen3") == 2
