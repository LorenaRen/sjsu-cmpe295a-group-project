"""Identifiers that join telemetry together (Module 1).

transaction_id : minted once when a plan is first proposed; every later message about
                 that plan (verdict, validated plan, task status) carries the same id.
episode_id     : one per simulation run; readable so result folders sort nicely.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone


def new_transaction_id() -> str:
    return str(uuid.uuid4())


def new_episode_id(scenario: str = "dev", seed: int | None = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", scenario.lower()).strip("-") or "dev"
    seed_part = f"-s{seed}" if seed is not None else ""
    return f"{slug}{seed_part}-{stamp}-{uuid.uuid4().hex[:6]}"


def is_transaction_id(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, TypeError, AttributeError):
        return False
