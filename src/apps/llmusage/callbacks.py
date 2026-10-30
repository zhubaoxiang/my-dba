"""
对话模型调用的用量回调

挂在 `build_chat_model()` 返回的模型上，agent 内部的多步调用与流式调用都会经过
这里，业务调用点不需要改动。
"""

import time

from langchain_core.callbacks import BaseCallbackHandler

from apps.llmusage import services
from utils import custom_enum


def _extract_usage(response) -> tuple:
    """
    从 LangChain 的返回中取 token 用量；取不到就返回空，**不估算**
    """
    token_usage = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
    prompt_tokens = token_usage.get("prompt_tokens")
    completion_tokens = token_usage.get("completion_tokens")
    total_tokens = token_usage.get("total_tokens")

    if prompt_tokens is None:
        generations = getattr(response, "generations", None) or []
        if generations and generations[0]:
            message = getattr(generations[0][0], "message", None)
            metadata = getattr(message, "usage_metadata", None) or {}
            prompt_tokens = metadata.get("input_tokens")
            completion_tokens = metadata.get("output_tokens")
            total_tokens = metadata.get("total_tokens")

    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    return prompt_tokens, completion_tokens, total_tokens


class LlmUsageCallbackHandler(BaseCallbackHandler):
    """
    记录一次对话模型调用的耗时、token 与结果
    """

    def __init__(self, provider):
        super().__init__()
        self.provider = provider
        self._started = {}

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs):
        self._mark_started(run_id)

    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs):
        # 对话模型走的是 on_chat_model_start，不是 on_llm_start
        self._mark_started(run_id)

    def on_llm_end(self, response, *, run_id=None, **kwargs):
        latency_ms = self._latency(run_id)
        prompt_tokens, completion_tokens, total_tokens = _extract_usage(response)
        services.record_call(
            call_type=custom_enum.LlmCallTypeEnum.CHAT,
            provider=self.provider,
            latency_ms=latency_ms,
            success=True,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )

    def on_llm_error(self, error, *, run_id=None, **kwargs):
        services.record_call(
            call_type=custom_enum.LlmCallTypeEnum.CHAT,
            provider=self.provider,
            latency_ms=self._latency(run_id),
            success=False,
            error_code=services.classify_error(error),
        )

    def _mark_started(self, run_id):
        if run_id is not None:
            self._started[run_id] = time.perf_counter()

    def _latency(self, run_id) -> int:
        started = self._started.pop(run_id, None)
        return int((time.perf_counter() - started) * 1000) if started is not None else 0
