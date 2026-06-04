"""Unit tests for the structured-extraction observability callback."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from docstore.llm import callbacks as callbacks_module
from docstore.llm.callbacks import LlmCallLogger


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    fake = MagicMock()
    monkeypatch.setattr(callbacks_module, "_log", fake)
    return fake


def test_first_call_does_not_log_correction(log: MagicMock) -> None:
    cb = LlmCallLogger("classification")
    convo = [SystemMessage(content="sys"), HumanMessage(content="doc")]
    cb.on_chat_model_start({}, [convo])
    assert cb.calls == 1
    log.info.assert_not_called()
    log.debug.assert_called_once()


def test_retry_call_logs_correction_text(log: MagicMock) -> None:
    cb = LlmCallLogger("value_extraction")
    initial = [SystemMessage(content="sys"), HumanMessage(content="doc")]
    correction = [
        *initial,
        HumanMessage(content="The output does not match the required schema: field X missing"),
    ]
    cb.on_chat_model_start({}, [initial])
    cb.on_chat_model_start({}, [correction])
    assert cb.calls == 2
    log.info.assert_called_once()
    kwargs = log.info.call_args.kwargs
    assert kwargs["step"] == "value_extraction"
    assert kwargs["attempt"] == 2
    assert "field X missing" in kwargs["correction"]


def test_correction_text_is_truncated(log: MagicMock) -> None:
    cb = LlmCallLogger("categorization")
    initial = [SystemMessage(content="sys"), HumanMessage(content="doc")]
    long_correction = [*initial, HumanMessage(content="x" * 5000)]
    cb.on_chat_model_start({}, [initial])
    cb.on_chat_model_start({}, [long_correction])
    correction = log.info.call_args.kwargs["correction"]
    assert len(correction) <= 1500
