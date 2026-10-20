#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Literal, overload

import httpx
from httpx import AsyncClient
from httpx import Timeout as HttpxTimeout
from openai import APIError, APITimeoutError, AsyncOpenAI, OpenAI, RateLimitError
from openai import Timeout as OpenAITimeout
from openai.types.chat import ChatCompletionMessageParam

from ...contexts import get_request_id
from ...exceptions import AppException
from .hooks import CallMeta, StreamSummary, has_llm_hooks, run_after_call, run_before_call
from .token import count_with, tokenizer_for


if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

from ...decorators import autoretry


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UsageEvent:
    """单次 LLM 调用的用量事件

    :param model: 模型名称
    :param kind: 调用类型（chat / chat_stream / vision / embedding / rerank）
    :param prompt_tokens: 输入 token 数（后端未返回 usage 时为 0）
    :param completion_tokens: 输出 token 数
    :param total_tokens: 总 token 数
    :param duration_ms: 调用耗时（毫秒）
    :param estimated: 分量为本地分词器估算而非后端实报（流式被中断、未收到末尾 usage 块时）
    """

    model: str
    kind: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    duration_ms: int
    estimated: bool = False


UsageCallback = Callable[[UsageEvent], None]
"""usage 回调类型：必须同步、快速返回且自行捕获所有异常（不阻塞、不影响主调用链）"""

_usage_callback: UsageCallback | None = None


def set_usage_callback(callback: UsageCallback | None) -> None:
    """注册全局 usage 回调（进程级单例，传 None 注销）

    每次 LLM 调用（chat/chat_stream/vision/embed/rerank，含同步变体）完成后触发一次。
    回调实现方应自行兜底异常；此处仍会捕获并告警，绝不向主调用链抛出。
    """
    global _usage_callback
    _usage_callback = callback


def _usage_field(usage: Any, key: str) -> int:
    """从 usage（对象/字典/None）中容错提取整数字段"""
    if usage is None:
        return 0
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    return int(value or 0)


def _emit_usage(model: str, kind: str, usage: Any, duration_ms: int, *, estimated: bool = False) -> None:
    """触发 usage 回调；usage 缺失时各分量记 0（保留 model/duration 供监控）"""
    callback = _usage_callback
    if callback is None:
        return
    try:
        callback(
            UsageEvent(
                model=model,
                kind=kind,
                prompt_tokens=_usage_field(usage, "prompt_tokens"),
                completion_tokens=_usage_field(usage, "completion_tokens"),
                total_tokens=_usage_field(usage, "total_tokens"),
                duration_ms=duration_ms,
                estimated=estimated,
            )
        )
    except Exception:
        logger.warning("Usage callback failed", exc_info=True)


def _collect_delta_text(delta: Any, out: list[str]) -> None:
    """收集流式增量中的产出文本（正文 / 推理内容 / 工具调用参数），供中断估算"""
    if delta is None:
        return
    for key in ("content", "reasoning_content"):
        value = getattr(delta, key, None)
        if isinstance(value, str) and value:
            out.append(value)
    for call in getattr(delta, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        if fn is not None:
            out.append(f"{getattr(fn, 'name', None) or ''}{getattr(fn, 'arguments', None) or ''}")


def _messages_text(messages: list[Any]) -> str:
    """拼接消息中可计数的文本（content 字符串 / text 分片 / tool_calls 参数；图片分片不计）"""
    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if isinstance(fn, dict):
                parts.append(f"{fn.get('name') or ''} {fn.get('arguments') or ''}")
    return "\n".join(parts)


def _estimate_stream_usage(model: str, messages: list[Any], completion_text: str) -> dict[str, int]:
    """流式被中断且无实报 usage 时，按模型分词器估算已消耗分量（宁可略估也不记 0）"""
    tokenizer = tokenizer_for(model)
    prompt = count_with(tokenizer, _messages_text(messages)) if messages else 0
    completion = count_with(tokenizer, completion_text) if completion_text else 0
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


class LLMError(AppException):
    """LLM 调用错误基类"""

    def __init__(self, error_key: str, error_args: dict | None = None, http_status: int = 500):
        super().__init__(error_key, http_status, error_args)


class LLMTimeoutError(LLMError):
    """超时错误"""

    def __init__(self, error_key: str, error_args: dict | None = None):
        super().__init__(error_key, error_args, http_status=504)


class LLMRateLimitError(LLMError):
    """限流错误"""

    def __init__(self, error_key: str, error_args: dict | None = None):
        super().__init__(error_key, error_args, http_status=429)


class LLMClient:
    """LLM 客户端：只负责与 AI 的交互"""

    def __init__(self, base_url: str, api_key: str):
        """
        :param base_url: API 地址
        :param api_key: API 密钥
        """
        self._base_url = base_url

        # 只限制连接超时，读取不限（推理时间不可预估）
        _openai_timeout = OpenAITimeout(connect=10.0, read=None, write=None, pool=None)
        _httpx_timeout = HttpxTimeout(connect=10.0, read=None, write=None, pool=None)

        # 异步客户端
        self._async_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=_openai_timeout,
            max_retries=0,  # 手动控制重试
        )
        self._async_http_client = AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_httpx_timeout,
        )

        # 同步客户端（延迟初始化）
        self._sync_client: OpenAI | None = None
        self._sync_http_session = None  # requests.Session（延迟初始化）
        self._sync_client_config = {
            "base_url": base_url,
            "api_key": api_key,
            "timeout": _openai_timeout,
            "max_retries": 0,
        }

    def _get_sync_client(self) -> OpenAI:
        """获取同步客户端（延迟初始化）"""
        if self._sync_client is None:
            self._sync_client = OpenAI(**self._sync_client_config)
        return self._sync_client

    def _get_sync_http_session(self):
        """获取同步 HTTP Session（延迟初始化）"""
        if self._sync_http_session is None:
            import requests

            self._sync_http_session = requests.Session()
            self._sync_http_session.headers.update({"Authorization": f"Bearer {self._sync_client_config['api_key']}"})
        return self._sync_http_session

    @overload
    async def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        *,
        stream: Literal[False] = False,
        **kwargs: Any,
    ) -> "ChatCompletion": ...

    @overload
    async def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        *,
        stream: Literal[True],
        **kwargs: Any,
    ) -> "AsyncIterator[ChatCompletionChunk]": ...

    async def chat(
        self,
        messages: list[ChatCompletionMessageParam],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> "ChatCompletion | AsyncIterator[ChatCompletionChunk]":
        """聊天接口（异步）

        :param messages: 消息列表
        :param model: 模型名称
        :param temperature: 温度参数
        :param max_tokens: 最大 token 数
        :param stream: 是否流式响应
        :return: 响应对象或流式迭代器
        """
        start = time.time()
        model_name = model
        # 内部调用类型标记（vision 经由 chat 转发时传入，不透传给推理后端）
        usage_kind = kwargs.pop("usage_kind", "chat")
        # 调用方附带给 LLM 钩子的上下文（Agent 传 agent / turn / retry / context），不透传给推理后端
        hook_extra: dict[str, Any] = kwargs.pop("hook_meta", None) or {}
        stream_kind = "chat_stream" if usage_kind == "chat" else usage_kind
        meta = CallMeta(
            model=model_name,
            kind=stream_kind if stream else usage_kind,
            stream=stream,
            request_id=get_request_id(),
            extra=hook_extra,
        )
        # F6 before 链：请求发出前改写消息；钩子异常即失败（fail closed），不包进 LLMError 以保留原异常类型
        if has_llm_hooks():
            messages = await run_before_call(messages, kwargs.get("tools"), meta)

        try:
            if stream:
                return self._chat_stream(
                    messages, model_name, temperature, max_tokens, start, usage_kind=stream_kind, meta=meta, **kwargs
                )

            @autoretry(
                logger,
                retries=3,
                delay=2.0,
                backoff=2.0,
                exceptions=(APITimeoutError, ConnectionError),
            )
            async def _call():
                return await self._async_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    extra_headers={"X-Request-ID": get_request_id()},
                    **kwargs,
                )

            response = await _call()

            # 记录指标
            duration_ms = int((time.time() - start) * 1000)
            usage = getattr(response, "usage", None)
            if usage:
                logger.info(f"LLM: model={model_name}, tokens={usage.total_tokens}, duration={duration_ms}ms")
            else:
                logger.info(f"LLM: model={model_name}, duration={duration_ms}ms")
            _emit_usage(model_name, usage_kind, usage, duration_ms)
            if has_llm_hooks():
                await run_after_call(response, meta)

            return response

        except APITimeoutError as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.error(f"LLM timeout after {duration_ms}ms: {e}")
            raise LLMTimeoutError("LLM_TIMEOUT", {"duration_ms": duration_ms}) from e

        except RateLimitError as e:
            logger.error(f"LLM rate limit: {e}")
            raise LLMRateLimitError("LLM_RATE_LIMIT") from e

        except APIError as e:
            logger.error(f"LLM API error: {e}")
            raise LLMError("LLM_API_ERROR", {"message": e.message}) from e

        except Exception as e:
            logger.error(f"Unexpected LLM error: {e}")
            raise LLMError("LLM_UNEXPECTED_ERROR", {"error": str(e)}) from e

    def chat_sync(
        self,
        messages: list[ChatCompletionMessageParam],
        model: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> "ChatCompletion":
        """聊天接口（同步，不支持流式）

        :param messages: 消息列表
        :param model: 模型名称
        :param temperature: 温度参数
        :param max_tokens: 最大 token 数
        :return: 响应对象
        """
        client = self._get_sync_client()
        start = time.time()
        model_name = model
        usage_kind = kwargs.pop("usage_kind", "chat")

        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            duration_ms = int((time.time() - start) * 1000)
            usage = getattr(response, "usage", None)
            if usage:
                logger.info(f"LLM (sync): model={model_name}, tokens={usage.total_tokens}, duration={duration_ms}ms")
            else:
                logger.info(f"LLM (sync): model={model_name}, duration={duration_ms}ms")
            _emit_usage(model_name, usage_kind, usage, duration_ms)

            return response

        except APITimeoutError as e:
            duration_ms = int((time.time() - start) * 1000)
            logger.error(f"LLM timeout after {duration_ms}ms: {e}")
            raise LLMTimeoutError("LLM_TIMEOUT", {"duration_ms": duration_ms}) from e
        except RateLimitError as e:
            logger.error(f"LLM rate limit: {e}")
            raise LLMRateLimitError("LLM_RATE_LIMIT") from e
        except APIError as e:
            logger.error(f"LLM API error: {e}")
            raise LLMError("LLM_API_ERROR", {"message": e.message}) from e
        except Exception as e:
            logger.error(f"Unexpected LLM error: {e}")
            raise LLMError("LLM_UNEXPECTED_ERROR", {"error": str(e)}) from e

    async def _chat_stream(
        self,
        messages: list[ChatCompletionMessageParam],
        model: str,
        temperature: float,
        max_tokens: int | None,
        start_time: float,
        usage_kind: str = "chat_stream",
        meta: CallMeta | None = None,
        **kwargs: Any,
    ) -> AsyncIterator["ChatCompletionChunk"]:
        """流式响应

        usage 在 finally 中保证恰好上报一次：正常结束或已收到末尾 usage 块按实报；
        被取消 / 消费方提前关闭 / 中途异常且无实报时，按已发送的 prompt 与已产出
        的增量文本估算（``estimated=True``）——上游已计费的消耗不能因中断而漏账。
        """
        final_usage = None
        finish_reason: str | None = None
        started = False
        completed = False
        produced: list[str] = []

        try:
            # noinspection PyTypeChecker
            stream = await self._async_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                extra_headers={"X-Request-ID": get_request_id()},
                **kwargs,
            )
            started = True

            async for chunk in stream:
                # 收集 token 统计（usage 通常在末尾 chunk 返回）
                if chunk.usage:
                    final_usage = chunk.usage
                if chunk.choices:
                    choice = chunk.choices[0]
                    if choice.finish_reason:
                        finish_reason = choice.finish_reason
                    if final_usage is None:  # 仅为中断估算留底，已有实报即不再累积
                        _collect_delta_text(choice.delta, produced)

                yield chunk

            # 记录指标
            completed = True
            duration_ms = int((time.time() - start_time) * 1000)
            total_tokens = final_usage.total_tokens if final_usage else 0
            logger.info(f"LLM stream: model={model}, tokens={total_tokens}, duration={duration_ms}ms")
            _emit_usage(model, usage_kind, final_usage, duration_ms)
            if meta is not None and has_llm_hooks():
                await run_after_call(StreamSummary(usage=final_usage, finish_reason=finish_reason), meta)

        except APITimeoutError as e:
            logger.error(f"LLM stream timeout: {e}")
            raise LLMTimeoutError("LLM_STREAM_TIMEOUT") from e

        except Exception as e:
            logger.error(f"LLM stream error: {e}")
            raise LLMError("LLM_STREAM_ERROR", {"error": str(e)}) from e

        finally:
            # 流已建立（请求已被后端受理）但未走到正常收尾（取消 / aclose / 异常）：
            # 此处只做同步上报，不 await；建流前即失败的请求未产生消耗，与非流式失败口径一致不记账
            if started and not completed:
                duration_ms = int((time.time() - start_time) * 1000)
                if final_usage is not None:
                    _emit_usage(model, usage_kind, final_usage, duration_ms)
                else:
                    try:
                        estimate = _estimate_stream_usage(model, list(messages), "".join(produced))
                    except Exception:
                        logger.warning("Stream usage estimation failed", exc_info=True)
                        estimate = None
                    logger.info(
                        f"LLM stream interrupted: model={model}, estimated={estimate}, duration={duration_ms}ms"
                    )
                    _emit_usage(model, usage_kind, estimate, duration_ms, estimated=estimate is not None)

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """文本向量化（异步）

        :param texts: 文本列表
        :param model: 模型名称
        :return: 向量列表
        """
        start = time.time()

        @autoretry(
            logger,
            retries=3,
            delay=2.0,
            backoff=2.0,
            exceptions=(APITimeoutError, ConnectionError),
        )
        async def _call():
            return await self._async_client.embeddings.create(
                model=model, input=texts, extra_headers={"X-Request-ID": get_request_id()}
            )

        try:
            response = await _call()
            _emit_usage(model, "embedding", getattr(response, "usage", None), int((time.time() - start) * 1000))
            data = getattr(response, "data", None)
            if data:
                return [item.embedding for item in data]
            return []

        except Exception as e:
            logger.error(f"Embedding error: {e}")
            raise LLMError("LLM_EMBED_ERROR", {"error": str(e)}) from e

    def embed_sync(self, texts: list[str], model: str) -> list[list[float]]:
        """文本向量化（同步）

        :param texts: 文本列表
        :param model: 模型名称
        :return: 向量列表
        """
        client = self._get_sync_client()
        start = time.time()

        try:
            response = client.embeddings.create(model=model, input=texts)
            _emit_usage(model, "embedding", getattr(response, "usage", None), int((time.time() - start) * 1000))
            data = getattr(response, "data", None)
            if data:
                return [item.embedding for item in data]
            return []
        except Exception as e:
            logger.error(f"Embedding error (sync): {e}")
            raise LLMError("LLM_EMBED_ERROR", {"error": str(e)}) from e

    @overload
    async def vision(
        self,
        text: str,
        images: list[bytes],
        model: str,
        *,
        stream: Literal[False] = False,
        **kwargs: Any,
    ) -> "ChatCompletion": ...

    @overload
    async def vision(
        self,
        text: str,
        images: list[bytes],
        model: str,
        *,
        stream: Literal[True],
        **kwargs: Any,
    ) -> "AsyncIterator[ChatCompletionChunk]": ...

    async def vision(
        self,
        text: str,
        images: list[bytes],
        model: str,
        stream: bool = False,
        **kwargs: Any,
    ) -> "ChatCompletion | AsyncIterator[ChatCompletionChunk]":
        """多模态处理（异步）

        :param text: 文本指令
        :param images: 图片字节数据列表
        :param model: 模型名称
        :param stream: 是否流式响应
        :return: 响应对象或流式迭代器
        """
        import base64

        content: list[dict[str, Any]] = [{"type": "text", "text": text}]

        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )

        # noinspection PyTypeChecker
        messages: list[ChatCompletionMessageParam] = [
            {"role": "user", "content": content}  # type: ignore[list-item]
        ]

        # bypass type check of Literal param `stream`
        if stream:
            return await self.chat(messages, model=model, stream=True, usage_kind="vision", **kwargs)

        return await self.chat(messages, model=model, stream=False, usage_kind="vision", **kwargs)

    def vision_sync(
        self,
        text: str,
        images: list[bytes],
        model: str,
        **kwargs: Any,
    ) -> "ChatCompletion":
        """多模态处理（同步）

        :param text: 文本指令
        :param images: 图片字节数据列表
        :param model: 模型名称
        :return: 响应对象
        """
        import base64

        content: list[dict[str, Any]] = [{"type": "text", "text": text}]

        for img_bytes in images:
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                }
            )

        # noinspection PyTypeChecker
        messages: list[ChatCompletionMessageParam] = [
            {"role": "user", "content": content}  # type: ignore[list-item]
        ]

        return self.chat_sync(messages, model=model, usage_kind="vision", **kwargs)

    async def rerank(
        self,
        query: str,
        documents: list[str],
        model: str,
        top_n: int = 10,
    ) -> list[tuple[int, float, str]]:
        """文档重排序（异步）

        :param query: 查询文本
        :param documents: 文档列表
        :param top_n: 返回 Top N 结果
        :param model: 模型名称
        :return: [(index, score, text), ...] 按相关性降序排列
        """
        from httpx import ConnectTimeout, TimeoutException

        start = time.time()

        @autoretry(
            logger,
            retries=3,
            delay=2.0,
            backoff=2.0,
            exceptions=(ConnectTimeout, ConnectionError),
        )
        async def _call():
            return await self._async_http_client.post(
                f"{self._base_url}/reranks",
                json={
                    "model": model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                    "return_documents": True,
                },
                headers={"X-Request-ID": get_request_id()},
            )

        try:
            response = await _call()
            response.raise_for_status()
            data = response.json()

            # 上报用量：后端不返回 usage 时也上报（token 记 0），次数是按次计价的依据
            _emit_usage(model, "rerank", data.get("usage"), int((time.time() - start) * 1000))

            # 解析响应
            results = []
            for item in data.get("results", []):
                index = item.get("index")
                score = item.get("relevance_score")
                doc = item.get("document", {})
                text = doc.get("text", "")
                results.append((index, score, text))

            return results

        except TimeoutException as e:
            logger.error(f"Rerank timeout: {e}")
            raise LLMTimeoutError("LLM_RERANK_TIMEOUT") from e

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.error(f"Rerank rate limit: {e}")
                raise LLMRateLimitError("LLM_RERANK_RATE_LIMIT") from e
            logger.error(f"Rerank HTTP error {e.response.status_code}: {e}")
            raise LLMError("LLM_RERANK_ERROR", {"error": str(e)}, http_status=e.response.status_code) from e

        except Exception as e:
            logger.error(f"Rerank error: {e}")
            raise LLMError("LLM_RERANK_ERROR", {"error": str(e)}) from e

    def rerank_sync(
        self,
        query: str,
        documents: list[str],
        model: str,
        top_n: int = 10,
    ) -> list[tuple[int, float, str]]:
        """文档重排序（同步）

        :param query: 查询文本
        :param documents: 文档列表
        :param top_n: 返回 Top N 结果
        :param model: 模型名称
        :return: [(index, score, text), ...] 按相关性降序排列
        """
        import requests

        session = self._get_sync_http_session()
        start = time.time()

        try:
            response = session.post(
                f"{self._base_url}/reranks",
                json={
                    "model": model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                    "return_documents": True,
                },
                timeout=None,
            )
            response.raise_for_status()
            data = response.json()

            # 上报用量：后端不返回 usage 时也上报（token 记 0），次数是按次计价的依据
            _emit_usage(model, "rerank", data.get("usage"), int((time.time() - start) * 1000))

            results = []
            for item in data.get("results", []):
                index = item.get("index")
                score = item.get("relevance_score")
                doc = item.get("document", {})
                text = doc.get("text", "")
                results.append((index, score, text))

            return results

        except requests.Timeout as e:
            logger.error(f"Rerank timeout (sync): {e}")
            raise LLMTimeoutError("LLM_RERANK_TIMEOUT") from e

        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 500
            if status == 429:
                logger.error(f"Rerank rate limit (sync): {e}")
                raise LLMRateLimitError("LLM_RERANK_RATE_LIMIT") from e
            logger.error(f"Rerank HTTP error {status} (sync): {e}")
            raise LLMError("LLM_RERANK_ERROR", {"error": str(e)}, http_status=status) from e

        except Exception as e:
            logger.error(f"Rerank error (sync): {e}")
            raise LLMError("LLM_RERANK_ERROR", {"error": str(e)}) from e


_client: LLMClient | None = None


def setup_llm_client(base_url: str, api_key: str):
    """初始化 LLM 客户端"""
    global _client
    _client = LLMClient(base_url=base_url, api_key=api_key)


def get_llm_client() -> LLMClient:
    """获取 LLM 客户端实例"""
    if _client is None:
        raise RuntimeError("LLM client not initialized. Call `setup_llm_client` first.")
    return _client
