"""LangChain callback for observability of structured-extraction LLM calls.

The structured-output library re-prompts the model with a field-level correction
message when validation fails. This callback surfaces, per LLM call, how many calls
a step has made and the exact correction text sent back to the model on retries — so
operators can see why a step is retrying (NFR-16).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.callbacks.base import BaseCallbackHandler

from saga.core.logging import get_logger

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

_log = get_logger("saga.llm.analyzer")

# A retry conversation contains the original system + human messages plus one or more
# appended correction messages; more than this baseline means a retry is in progress.
_BASELINE_MESSAGE_COUNT = 2
_MAX_LOGGED_CHARS = 1500


class LlmCallLogger(BaseCallbackHandler):
    """Logs each chat-model invocation for one analysis step and any correction text."""

    def __init__(self, step: str) -> None:
        self.step = step
        self.calls = 0

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        **kwargs: Any,  # noqa: ANN401 - LangChain passes run_id/tags/etc.
    ) -> None:
        self.calls += 1
        conversation = messages[0] if messages else []
        # The first call is the initial prompt; later calls carry a correction message
        # the library appended after a validation/tool-call error.
        if self.calls > 1 and len(conversation) > _BASELINE_MESSAGE_COUNT:
            correction = str(getattr(conversation[-1], "content", ""))[:_MAX_LOGGED_CHARS]
            _log.info(
                "llm_correction_sent",
                step=self.step,
                attempt=self.calls,
                correction=correction,
            )
        else:
            _log.debug("llm_call", step=self.step, attempt=self.calls)
