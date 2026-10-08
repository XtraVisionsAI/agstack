#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

"""Token 计数：按模型分派分词器（3.2）

3.1 及之前 ``count_tokens(text, model)`` 对模型名调 ``tiktoken.encoding_for_model``，不认识的名字一律回退
``cl100k_base``，非 OpenAI 模型（Qwen / Claude / DeepSeek …）全按 GPT-4 词表数，中文偏差常见 20% 到 40%。
本模块把计数拆成「分词器实现 + 按模型名的注册表」：

- :class:`Tokenizer` 协议：``name``（诊断）、``exact``（是否真实分词器，决定校准钳制区间）、``count(text)``；
- 三种实现：:class:`TiktokenTokenizer`（按**编码名**构造，不再按模型名猜）、:class:`HFTokenizer`
  （``tokenizers`` 可选依赖，加载本地 ``tokenizer.json``，开放权重模型全覆盖）、:class:`ApproxTokenizer`
  （按 CJK / 其他字符比例近似，拿不到词表的闭源模型兜底，配合 ``harness.metering`` 的实报校准）；
- 注册表：:func:`register_tokenizer` / :func:`tokenizer_for` / :func:`set_default_tokenizer`；
  未注册且未设缺省时沿用 3.1 行为（``encoding_for_model`` → ``cl100k_base``），向后兼容；
- :func:`count_tokens` 签名不变，内部改为 ``tokenizer_for(model).count(text)``，外加按
  ``(分词器, 文本)`` 的小 LRU：装填两遍、折叠反复比较时同一段文本不重复编码。

分词器实例进程内单例由应用保证（注册一次）；``tokenizer.json`` 等文件的交付与路径由应用定。
"""

from __future__ import annotations

import math
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Protocol, runtime_checkable

import tiktoken


#: 3.1 及之前的回退编码
LEGACY_ENCODING = "cl100k_base"

#: 计数缓存容量（条）
COUNT_CACHE_SIZE = 4096

#: 短于此长度的文本不进缓存（编码比查表还便宜）
_CACHE_MIN_CHARS = 64


@runtime_checkable
class Tokenizer(Protocol):
    """分词器：``count(text)`` 返回 token 数"""

    name: str
    exact: bool

    def count(self, text: str) -> int: ...


class TiktokenTokenizer:
    """tiktoken 编码（``o200k_base`` / ``cl100k_base`` …），按编码名构造"""

    exact = True

    def __init__(self, encoding: str = LEGACY_ENCODING):
        self.name = f"tiktoken:{encoding}"
        self._encoding = tiktoken.get_encoding(encoding)

    def count(self, text: str) -> int:
        # 文本里出现 <|endoftext|> 之类特殊串时按普通文本编码，不抛错
        return len(self._encoding.encode(text, disallowed_special=()))


class HFTokenizer:
    """HuggingFace ``tokenizers`` 加载本地 ``tokenizer.json``（Qwen / DeepSeek / GLM / Llama …）

    需要可选依赖 ``agstack[tokenizers]``；只数 ids 长度、不加特殊 token——chat template 与工具声明的固定开销
    由 ``harness.metering.ModelCalibration.overhead`` 吸收。
    """

    exact = True

    def __init__(self, path: str | os.PathLike[str], *, name: str | None = None):
        try:
            from tokenizers import Tokenizer as _HFTok
        except ImportError as exc:  # pragma: no cover - 依赖缺失路径
            raise RuntimeError("HFTokenizer 需要可选依赖 tokenizers：pip install 'agstack[tokenizers]'") from exc
        file = Path(path)
        if file.is_dir():
            file = file / "tokenizer.json"
        self._tokenizer = _HFTok.from_file(str(file))
        self.name = name or f"hf:{file.parent.name or file.stem}"

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)


_CJK = re.compile(r"[　-ヿ㐀-䶿一-鿿가-힯豈-﫿＀-￯]")


class ApproxTokenizer:
    """按字符类别比例近似：CJK 每 token 约 ``cjk_chars_per_token`` 字，其余每 token 约 ``other_chars_per_token`` 字符

    用于拿不到词表的闭源模型；缺省比例偏保守（估算偏大 → 装填少装而非超限），应用按厂商覆盖。
    """

    exact = False

    def __init__(self, *, cjk_chars_per_token: float = 1.3, other_chars_per_token: float = 3.5, name: str = "approx"):
        if cjk_chars_per_token <= 0 or other_chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")
        self.name = name if name.startswith("approx") else f"approx:{name}"
        self._cjk = cjk_chars_per_token
        self._other = other_chars_per_token

    def count(self, text: str) -> int:
        if not text:
            return 0
        cjk = len(_CJK.findall(text))
        other = len(text) - cjk
        return math.ceil(cjk / self._cjk) + math.ceil(other / self._other)


# ── 注册表 ──

_LOCK = threading.Lock()
_REGISTRY: dict[str, Tokenizer] = {}
_DEFAULT: Tokenizer | None = None
_TIKTOKEN_CACHE: dict[str, TiktokenTokenizer] = {}
_LEGACY_BY_MODEL: dict[str, TiktokenTokenizer] = {}


def tiktoken_tokenizer(encoding: str) -> TiktokenTokenizer:
    """按编码名取（或建）tiktoken 分词器，进程内单例"""
    with _LOCK:
        tok = _TIKTOKEN_CACHE.get(encoding)
        if tok is None:
            tok = _TIKTOKEN_CACHE[encoding] = TiktokenTokenizer(encoding)
        return tok


def register_tokenizer(model: str, tokenizer: Tokenizer) -> None:
    """把模型名绑定到分词器（重复注册覆盖）"""
    with _LOCK:
        _REGISTRY[model] = tokenizer
        _COUNT_CACHE.clear()


def set_default_tokenizer(tokenizer: Tokenizer | None) -> None:
    """未注册模型的缺省分词器；None 恢复 3.1 行为（``encoding_for_model`` → ``cl100k_base``）"""
    global _DEFAULT
    with _LOCK:
        _DEFAULT = tokenizer
        _COUNT_CACHE.clear()


def clear_tokenizers() -> None:
    """清空注册表与缺省（测试用）"""
    global _DEFAULT
    with _LOCK:
        _REGISTRY.clear()
        _DEFAULT = None
        _COUNT_CACHE.clear()


def registered_tokenizers() -> dict[str, str]:
    """``{模型名: 分词器名}`` 快照（诊断 / 启动日志）"""
    with _LOCK:
        return {model: tok.name for model, tok in _REGISTRY.items()}


def _legacy_for(model: str) -> TiktokenTokenizer:
    tok = _LEGACY_BY_MODEL.get(model)
    if tok is None:
        try:
            encoding = tiktoken.encoding_for_model(model).name
        except KeyError:
            encoding = LEGACY_ENCODING
        tok = _LEGACY_BY_MODEL[model] = tiktoken_tokenizer(encoding)
    return tok


def tokenizer_for(model: str) -> Tokenizer:
    """模型名 → 分词器：注册表 → 缺省 → 3.1 回退"""
    tok = _REGISTRY.get(model)
    if tok is not None:
        return tok
    if _DEFAULT is not None:
        return _DEFAULT
    return _legacy_for(model)


# ── 计数 ──

_COUNT_CACHE: OrderedDict[tuple[str, int, int], int] = OrderedDict()


def count_with(tokenizer: Tokenizer, text: str) -> int:
    """用给定分词器计数（经 LRU）"""
    if len(text) < _CACHE_MIN_CHARS:
        return tokenizer.count(text)
    key = (tokenizer.name, len(text), hash(text))
    with _LOCK:
        cached = _COUNT_CACHE.get(key)
        if cached is not None:
            _COUNT_CACHE.move_to_end(key)
            return cached
    value = tokenizer.count(text)
    with _LOCK:
        _COUNT_CACHE[key] = value
        while len(_COUNT_CACHE) > COUNT_CACHE_SIZE:
            _COUNT_CACHE.popitem(last=False)
    return value


def count_tokens(text: str, model: str = "gpt-3.5-turbo") -> int:
    """计算文本的 Token 数量（按 ``model`` 绑定的分词器；签名与 3.1 一致）

    :param text: 文本内容
    :param model: 模型名称
    :return: Token 数量
    """
    return count_with(tokenizer_for(model), text)


__all__ = [
    "COUNT_CACHE_SIZE",
    "LEGACY_ENCODING",
    "ApproxTokenizer",
    "HFTokenizer",
    "TiktokenTokenizer",
    "Tokenizer",
    "clear_tokenizers",
    "count_tokens",
    "count_with",
    "register_tokenizer",
    "registered_tokenizers",
    "set_default_tokenizer",
    "tiktoken_tokenizer",
    "tokenizer_for",
]
