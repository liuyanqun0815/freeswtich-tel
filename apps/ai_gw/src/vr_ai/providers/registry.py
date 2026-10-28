"""Pick a vendor adapter from a scene's provider config."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vr_ai.providers.base import Asr, Llm, ProviderError, Tts
from vr_ai.providers.openai_compat import OpenAiAsr, OpenAiLlm, OpenAiTts
from vr_ai.providers.tencent import TencentAsr

_ASR: dict[str, Callable[[dict[str, Any]], Asr]] = {"openai": OpenAiAsr, "tencent": TencentAsr}
_TTS: dict[str, Callable[[dict[str, Any]], Tts]] = {"openai": OpenAiTts}
_LLM: dict[str, Callable[[dict[str, Any]], Llm]] = {"openai": OpenAiLlm}


def _build(table: dict[str, Callable[[dict[str, Any]], Any]], kind: str, config: dict[str, Any]) -> Any:
    vendor = str(config.get("vendor") or "")
    factory = table.get(vendor)
    if factory is None:
        raise ProviderError(f"{kind} vendor not supported: {vendor or '-'}")
    return factory(config)


def build_asr(config: dict[str, Any]) -> Asr:
    return _build(_ASR, "asr", config)


def build_tts(config: dict[str, Any]) -> Tts:
    return _build(_TTS, "tts", config)


def build_llm(config: dict[str, Any]) -> Llm:
    return _build(_LLM, "llm", config)
