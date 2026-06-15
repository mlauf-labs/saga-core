"""Langfuse pipeline tracing helpers (FR-33).

Creates one Langfuse *trace* per document-ingestion pipeline run.  Every
pipeline step is exposed as a named *span* nested under the root trace, and
every LLM call inside a step is exposed as a *generation* nested under that
step's span.  This means the Langfuse UI shows a single trace per file with
all stages (convert, classify, extract, summarise, compute_similarity,
place_in_folder, index_chunks) as children — each with its own latency,
token counts, and I/O.

Tracing is **opt-in**: ``build_pipeline_tracer`` returns a no-op object when
Langfuse is not configured (``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY``
are absent), so call-sites never need ``if langfuse_enabled`` guards.

Design
------
The tracer uses the Langfuse 4.x OpenTelemetry-based Python SDK:

* ``build_pipeline_tracer`` creates a root *chain* observation and immediately
  enters its OpenTelemetry context (via ``__enter__``).  From that point every
  ``langfuse.langchain.CallbackHandler()`` created inside the same async task
  automatically inherits this trace as its parent.

* ``_LangfuseTracer.step_span(step_name)`` is a synchronous context manager
  that opens a named *span* observation as a child of the root chain, then
  yields the LangChain callbacks to use inside that step.  All LangChain
  generations produced during the step are therefore grandchildren of the root
  trace, nested under the correct step span.

* ``finish()`` exits the root trace context and flushes buffered events.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from saga.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Generator

    from saga.core.config import LangfuseConfig

_log = get_logger("saga.llm.tracing")


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------


def noop_tracer() -> _BaseTracer:
    """Return a no-op tracer.  Safe to use as a placeholder before the real
    tracer is built (e.g. before the document record is available)."""
    return _NoopTracer()


def build_pipeline_tracer(
    config: LangfuseConfig,
    *,
    document_id: str,
    title: str,
) -> _BaseTracer:
    """Return a :class:`_LangfuseTracer` when configured, else a :class:`_NoopTracer`.

    The returned tracer is already *entered*: the Langfuse root-trace OTel
    context is active.  Call ``finish()`` when the pipeline ends (success or
    failure) to flush buffered events and clean up the context.
    """
    if not config.enabled:
        return _NoopTracer()

    try:
        from langfuse import Langfuse

        lf = Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            host=config.host,
        )
        # start_as_current_observation pushes the span onto the OTel context.
        # "chain" is the root type that represents the full pipeline run.
        # Manually entering the CM keeps the context active for the whole pipeline.
        cm = lf.start_as_current_observation(
            as_type="chain",
            name="ingest-document",
            input={"document_id": document_id, "title": title},
            metadata={"document_id": document_id, "title": title},
        )
        cm.__enter__()

        trace_id = lf.get_current_trace_id() or "unknown"
        _log.info(
            "langfuse_trace_started",
            document_id=document_id,
            trace_id=trace_id,
            host=config.host,
        )
        return _LangfuseTracer(lf, cm)
    except Exception as exc:
        _log.warning(
            "langfuse_init_failed",
            error=str(exc),
            hint="Tracing disabled for this run; check LANGFUSE_* env vars.",
        )
        return _NoopTracer()


# ---------------------------------------------------------------------------
# Tracer implementations
# ---------------------------------------------------------------------------


class _BaseTracer:
    """Common interface for tracers."""

    @contextlib.contextmanager
    def step_span(
        self, step_name: str, *, input: dict[str, Any] | None = None
    ) -> Generator[list[Any], None, None]:
        """Context manager that wraps a pipeline step in a named span.

        Yields a list of LangChain callbacks that should be forwarded to any
        LLM calls made inside the step.  Non-LLM steps can ignore the yielded
        value.

        Usage::

            with tracer.step_span("classify_doc_type", input={...}) as callbacks:
                result = await classify_doc_type(..., trace_callbacks=callbacks)
        """
        raise NotImplementedError

    def finish(self) -> None:
        """Flush buffered Langfuse events.  Safe to call multiple times."""


class _NoopTracer(_BaseTracer):
    """Used when Langfuse is disabled — all methods are no-ops."""

    @contextlib.contextmanager
    def step_span(
        self, step_name: str, *, input: dict[str, Any] | None = None
    ) -> Generator[list[Any], None, None]:
        yield []

    def finish(self) -> None:
        pass


class _LangfuseTracer(_BaseTracer):
    """Active tracer backed by a Langfuse root-trace context.

    The context manager *cm* is already entered; ``finish()`` exits it.
    """

    def __init__(self, lf: Any, cm: Any) -> None:  # noqa: ANN401
        self._lf = lf
        self._cm = cm
        self._finished = False

    @contextlib.contextmanager
    def step_span(
        self, step_name: str, *, input: dict[str, Any] | None = None
    ) -> Generator[list[Any], None, None]:
        """Open a named child span for *step_name*, yield LangChain callbacks.

        The span is opened as a child of the root trace (the OTel context set
        in ``__init__``).  A fresh ``CallbackHandler`` is created *inside* the
        span context so that any LangChain generations automatically appear
        under the correct step span in the Langfuse UI.
        """
        span_cm = self._lf.start_as_current_observation(
            as_type="span",
            name=step_name,
            input=input,
        )
        with span_cm:
            try:
                from langfuse.langchain import CallbackHandler

                callbacks: list[Any] = [CallbackHandler()]
            except Exception as exc:
                _log.warning(
                    "langfuse_step_callback_failed",
                    step=step_name,
                    error=str(exc),
                )
                callbacks = []
            yield callbacks

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        try:
            self._cm.__exit__(None, None, None)
        except Exception as exc:
            _log.warning("langfuse_context_exit_failed", error=str(exc))
        try:
            self._lf.flush()
        except Exception as exc:
            _log.warning("langfuse_flush_failed", error=str(exc))
