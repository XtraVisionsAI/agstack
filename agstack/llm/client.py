#  Copyright (c) 2020-2026 XtraVisions, All rights reserved.

import logging
import time
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal, overload

from httpx import AsyncClient
from openai import APIError, APITimeoutError, AsyncOpenAI, OpenAI, RateLimitError
from openai.types.chat import ChatCompletionMessageParam

from ..exceptions import AppException


if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion, ChatCompletionChunk

from ..decorators import autoretry


logger = logging.getLogger(__name__)


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

        # 异步客户端
        self._async_client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=60.0,
            max_retries=0,  # 手动控制重试
        )
        self._async_http_client = AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60.0,
        )

        # 同步客户端（延迟初始化）
        self._sync_client: OpenAI | None = None
        self._sync_http_session = None  # requests.Session（延迟初始化）
        self._sync_client_config = {
            "base_url": base_url,
            "api_key": api_key,
            "timeout": 60.0,
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

        try:
            if stream:
                return self._chat_stream(messages, model_name, temperature, max_tokens, start, **kwargs)

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
        **kwargs: Any,
    ) -> AsyncIterator["ChatCompletionChunk"]:
        """流式响应"""
        total_tokens = 0

        try:
            # noinspection PyTypeChecker
            stream = await self._async_client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                stream_options={"include_usage": True},
                **kwargs,
            )

            async for chunk in stream:
                # 收集 token 统计
                if chunk.usage:
                    total_tokens = chunk.usage.total_tokens

                yield chunk

            # 记录指标
            duration_ms = int((time.time() - start_time) * 1000)
            logger.info(f"LLM stream: model={model}, tokens={total_tokens}, duration={duration_ms}ms")

        except APITimeoutError as e:
            logger.error(f"LLM stream timeout: {e}")
            raise LLMTimeoutError("LLM_STREAM_TIMEOUT") from e

        except Exception as e:
            logger.error(f"LLM stream error: {e}")
            raise LLMError("LLM_STREAM_ERROR", {"error": str(e)}) from e

    async def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """文本向量化（异步）

        :param texts: 文本列表
        :param model: 模型名称
        :return: 向量列表
        """

        @autoretry(
            logger,
            retries=3,
            delay=2.0,
            backoff=2.0,
            exceptions=(APITimeoutError, ConnectionError),
        )
        async def _call():
            return await self._async_client.embeddings.create(model=model, input=texts)

        try:
            response = await _call()
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

        try:
            response = client.embeddings.create(model=model, input=texts)
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
            return await self.chat(messages, model=model, stream=True, **kwargs)

        return await self.chat(messages, model=model, stream=False, **kwargs)

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

        return self.chat_sync(messages, model=model, **kwargs)

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
            )

        try:
            response = await _call()
            data = response.json()

            # 解析响应
            results = []
            for item in data.get("results", []):
                index = item.get("index")
                score = item.get("relevance_score")
                doc = item.get("document", {})
                text = doc.get("text", "")
                results.append((index, score, text))

            return results

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
        session = self._get_sync_http_session()

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
                timeout=60.0,
            )
            response.raise_for_status()
            data = response.json()

            results = []
            for item in data.get("results", []):
                index = item.get("index")
                score = item.get("relevance_score")
                doc = item.get("document", {})
                text = doc.get("text", "")
                results.append((index, score, text))

            return results

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
