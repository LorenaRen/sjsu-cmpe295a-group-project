"""While benchmark scenarios run, compare every recheck with a recheck done in full
(TeamCheckResult.recheck_full), which an incremental recheck must equal: the in-process side of
the equivalence test. Only for checker versions that have recheck_full."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import fleet_checker.adapters.team_check as team_check


def same_result(a: Any, b: Any) -> bool:
    """Equal field by field, and built in the same order (the verdict reads them in order)."""
    return (a == b and all(list(getattr(a.compiled, n)) == list(getattr(b.compiled, n))
                           for n in ("plans", "origins", "timings"))
            and list(a.must_replan) == list(b.must_replan))


@contextmanager
def shadow_rechecks() -> Iterator[dict[str, Any]]:
    """{"rechecks": how many were compared, "mismatches": the plans whose rechecks differed}."""
    cls = team_check.TeamCheckResult
    incremental = cls.recheck
    stats: dict[str, Any] = {"rechecks": 0, "mismatches": []}

    def recheck(self, team_plan):
        result = incremental(self, team_plan)
        if not same_result(result, cls.recheck_full(self, team_plan)):
            stats["mismatches"].append(team_plan.model_dump_json())
        stats["rechecks"] += 1
        return result

    cls.recheck = recheck
    try:
        yield stats
    finally:
        cls.recheck = incremental
