"""Helpers shared by the provider adapters."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from email.utils import parsedate_to_datetime
from typing import Any

import keyring
import keyring.errors

from kaden.api import (
    JSON,
    KEYCHAIN_SERVICE,
    Block,
    ContextOverflowError,
    CoreError,
    Message,
    ModelInfo,
    OperationResult,
    Role,
    ServerToolUse,
    ToolResult,
    ToolStatus,
    TransientCoreError,
)

ERROR_STATUSES = frozenset(ToolStatus) - {ToolStatus.OK, ToolStatus.BACKGROUNDED}
# What providers say when a request is longer than the model takes; there is no common code.
_OVERFLOW_MESSAGES = ("maximum context length", "context length", "context window",
                      "too many tokens", "prompt is too long", "exceed context limit")


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Per-model adapter configuration. Anything left None comes from the provider."""

    options: Mapping[str, Any] = field(default_factory=dict)  # merged over adapter options
    context_window: int | None = None
    max_output_tokens: int | None = None
    input_usd_per_mtok: float | None = None
    output_usd_per_mtok: float | None = None
    cached_input_usd_per_mtok: float | None = None


def model_configs(models: Mapping[str, ModelConfig | Mapping[str, Any]] | None
                  ) -> dict[str, ModelConfig]:
    return {model_id: c if isinstance(c, ModelConfig) else ModelConfig(**c)
            for model_id, c in (models or {}).items()}


def merged_configs(defaults: Mapping[str, ModelConfig], configs: Mapping[str, ModelConfig]
            ) -> dict[str, ModelConfig]:
    """`configs` over `defaults`, field by field."""
    merged = dict(defaults)
    for model_id, config in configs.items():
        base = merged.get(model_id, ModelConfig())
        changes = {f.name: value for f in fields(config) if f.name != "options"
                   and (value := getattr(config, f.name)) is not None}
        merged[model_id] = replace(base, options={**base.options, **config.options}, **changes)
    return merged


def configured(info: ModelInfo, config: ModelConfig | None) -> ModelInfo:
    """`info` with configured values taking precedence over what the provider reported."""
    if config is None:
        return info
    return replace(
        info,
        context_window=config.context_window or info.context_window,
        max_output_tokens=config.max_output_tokens or info.max_output_tokens,
        input_usd_per_mtok=config.input_usd_per_mtok,
        output_usd_per_mtok=config.output_usd_per_mtok,
        cached_input_usd_per_mtok=config.cached_input_usd_per_mtok,
    )


def request_options(options: Mapping[str, Any], config: ModelConfig | None) -> JSON:
    return {**options, **(config.options if config else {})}


def stored_key(name: str) -> str | None:
    """The key saved in the OS keychain under KEYCHAIN_SERVICE, or None. Blocks briefly:
    Cores call it only at construction."""
    try:
        return keyring.get_password(KEYCHAIN_SERVICE, name)
    except keyring.errors.KeyringError:
        return None


def retry_after(headers: Mapping[str, str] | None) -> float | None:
    """Seconds from `retry-after-ms` or `retry-after` (delta-seconds or HTTP date)."""
    if not headers:
        return None
    if (ms := headers.get("retry-after-ms")) is not None:
        try:
            return float(ms) / 1000
        except ValueError:
            pass
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def status_error(status: int, message: str, *, overflow: bool = False,
                 transient: bool = False, headers: Mapping[str, str] | None = None
                 ) -> CoreError:
    """The CoreError for an HTTP error status. A 400 or 413 whose `message` says the
    request is too long, or that the provider marked so (`overflow`), is a
    ContextOverflowError; 408, 409, 429, 5xx, or what the provider marked `transient`, is
    retried after `headers`' retry-after; anything else is permanent."""
    if status in (400, 413) and (overflow or any(m in message.lower()
                                                 for m in _OVERFLOW_MESSAGES)):
        return ContextOverflowError(message)
    if transient or status in (408, 409, 429) or status >= 500:
        return TransientCoreError(message, retry_after(headers))
    return CoreError(message)


def transport_error(error: Exception) -> CoreError:
    """A failure with no HTTP status: connection failures, timeouts, errors mid-stream."""
    return TransientCoreError(f"{type(error).__name__}: {error}")


def result_text(result: ToolResult, *, mark_errors: bool = False) -> str:
    """Tool result content; runtime-assigned statuses are made visible to the model.
    `mark_errors` also tags ERROR, for formats without an error flag."""
    if result.status is ToolStatus.OK or (result.status is ToolStatus.ERROR and not mark_errors):
        return result.content
    return f"[{result.status}] {result.content}"


def operation_text(result: OperationResult) -> str:
    return (f"[Background operation {result.call_id} finished: {result.status}]\n"
            f"{result.content}")


def parse_arguments(raw: str) -> tuple[JSON, str | None]:
    """(arguments, raw_arguments): raw_arguments is set, with empty arguments, when the
    model's JSON does not parse to an object."""
    if not raw.strip():
        return {}, None
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError:
        return {}, raw
    return (arguments, None) if isinstance(arguments, dict) else ({}, raw)


def assistant_message(blocks: list[Block]) -> Message:
    return Message(uuid.uuid4().hex, Role.ASSISTANT, tuple(blocks), time.time())


def server_tool_text(use: ServerToolUse) -> str:
    """A tool another provider ran, as text: what it did and found."""
    return f"[{use.summary}]\n{use.content}".rstrip()
