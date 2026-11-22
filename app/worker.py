"""Process-local tenant ownership. This is workload isolation, not authentication."""
import os
from uuid import uuid4

from fastapi import HTTPException


GROUP = os.getenv("TENANT_GROUP", "small")
if GROUP not in {"small", "whale", "all"}:
    raise RuntimeError("TENANT_GROUP must be small, whale, or all (benchmark only)")
INSTANCE = uuid4().hex


def require_tenant(tenant_id: int) -> int:
    if not 1 <= tenant_id <= 301:
        raise HTTPException(status_code=404, detail="unknown tenant")
    if (GROUP == "small" and tenant_id == 1) or (GROUP == "whale" and tenant_id != 1):
        raise HTTPException(status_code=403, detail="tenant belongs to a different application worker")
    return tenant_id


def require_whale() -> None:
    require_tenant(1)


def info() -> dict:
    return {"group": GROUP, "instance": INSTANCE, "pid": os.getpid()}
