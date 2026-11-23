"""Limit concurrent whale reports within one application process."""

import asyncio
import os

from fastapi import HTTPException

# Set to 0 to disable the limiter for the before/after comparison.
LIMIT = int(os.getenv("WHALE_REPORT_CONCURRENCY", "2"))
MAX_WAITING = 80
WAIT_SECONDS = 25

if LIMIT < 0:
    raise ValueError("WHALE_REPORT_CONCURRENCY cannot be negative")

_slots = asyncio.Semaphore(LIMIT) if LIMIT else None
_waiting = 0


async def report_slot(tenant_id: int):
    """Wait without occupying a request thread or database connection."""
    global _waiting

    if tenant_id != 1 or _slots is None:
        yield
        return

    if _waiting >= MAX_WAITING:
        raise HTTPException(
            status_code=503,
            detail="Report queue is full. Please retry later.",
            headers={"Retry-After": "5"},
        )

    _waiting += 1
    try:
        try:
            await asyncio.wait_for(
                _slots.acquire(),
                timeout=WAIT_SECONDS,
            )
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=503,
                detail="Report queue wait timed out. Please retry later.",
                headers={"Retry-After": "5"},
            )
    finally:
        _waiting -= 1

    try:
        yield
    finally:
        _slots.release()