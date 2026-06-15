"""Distributed locks for pipeline steps that must not run concurrently (NFR-11).

The folder-placement step reads the live folder tree, calls the LLM (which may
create new folders via tool calls), then writes the result.  Running this step
concurrently for multiple documents causes the LLMs to make independent decisions
on the same stale snapshot of the folder tree, leading to semantically duplicate
folders with slightly different names ("Finance" vs "Finances" vs "Finanzen").

Serialising placement through a Redis lock ensures each run sees the folders
created by the previous run, so the LLM can reuse them rather than recreate them.
The lock is store-scoped so multiple independent store instances sharing the same
Redis instance do not block each other.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from arq.connections import ArqRedis

# Generous timeout: folder placement can take 30-60 s with LLM tool-call loops.
# The lock is force-released after this many seconds even if the worker crashes.
_LOCK_TIMEOUT_S = 180

# How often to retry acquiring the lock while another worker holds it.
_POLL_INTERVAL_S = 0.5


@asynccontextmanager
async def folder_placement_lock(redis: ArqRedis, store_name: str) -> AsyncIterator[None]:
    """Serialise folder-placement across concurrent ARQ workers.

    Acquires a Redis ``SET NX EX`` lock before yielding and unconditionally
    releases it on exit (including on exceptions).  Callers spin-wait until
    the lock is available — no fairness guarantee, first-come-first-served.

    Args:
        redis:      The ARQ Redis connection from the worker context (``ctx["redis"]``).
        store_name: The store name (``config.name``); used as the lock key prefix so
                    multiple stores on the same Redis instance do not block each other.
    """
    key = f"{store_name}:lock:folder_placement"
    while True:
        # SET key value NX EX timeout — returns True on success, None if key exists.
        acquired = await redis.set(key, "1", nx=True, ex=_LOCK_TIMEOUT_S)
        if acquired:
            break
        await asyncio.sleep(_POLL_INTERVAL_S)
    try:
        yield
    finally:
        await redis.delete(key)
