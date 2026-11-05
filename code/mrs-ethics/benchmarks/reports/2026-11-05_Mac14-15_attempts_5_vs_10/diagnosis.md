# Out of attempts: not enough attempts, or no way through?

The repair benchmark's 1350 scenarios, with the fake planner allowed 5 attempts and 10, on checker `c17bc6b`. Written by `diagnose_attempts.py` from the runs beside it.

## Check: rounds 1–5 do not depend on the limit

Of the 816 runs that ended within 5 rounds, 0 ended differently with 10 attempts.

## All scenarios

| outcome | 5 attempts | 10 attempts |
|---|---|---|
| approved in round 1 | 28.0% (378) | 28.0% (378) |
| repaired, targets kept | 17.1% (231) | 17.6% (237) |
| repaired, a target moved | 11.9% (161) | 19.6% (265) |
| stuck | 3.4% (46) | 7.0% (95) |
| out of attempts | 39.6% (534) | 27.8% (375) |

## The 534 runs out of attempts at 5, with 10 attempts

| with 10 attempts | runs | share |
|---|---|---|
| repaired, targets kept | 6 | 1.1% |
| repaired, a target moved | 104 | 19.5% |
| stuck | 49 | 9.2% |
| out of attempts | 375 | 70.2% |

Approved in round: 6: 45, 7: 22, 8: 21, 9: 14, 10: 8.
Stuck in round: 6: 13, 7: 18, 8: 14, 9: 3, 10: 1.

## By map and fleet size

Runs out of attempts at 5, and what they became with 10 attempts.

| map | robots | out of attempts at 5 | approved by 10 | stuck by 10 | still out at 10 |
|---|---|---|---|---|---|
| bench_grid | 2 | 1 | 1 | 0 | 0 |
| bench_grid | 4 | 5 | 5 | 0 | 0 |
| bench_grid | 8 | 40 | 34 | 0 | 6 |
| bench_grid | 16 | 116 | 61 | 0 | 55 |
| bench_grid | 32 | 150 | 0 | 0 | 150 |
| narrow_aisle | 2 | 5 | 0 | 0 | 5 |
| narrow_aisle | 4 | 130 | 0 | 27 | 103 |
| samples_world | 2 | 6 | 6 | 0 | 0 |
| samples_world | 4 | 81 | 3 | 22 | 56 |

## The 375 runs still out of attempts at 10

Constraints in each round's verdict (a second, identical pass over these scenarios).

|  | round 1 | round 5 | round 10 |
|---|---|---|---|
| median | 16 | 4 | 4 |
| mean | 27.1 | 7.8 | 6.0 |

Round 10 against round 5: fewer constraints in 177, as many in 116, more in 82.

## Answer

- **Not enough attempts:** 110 of the 534 runs out of attempts at 5 (20.6%) were approved within 10 attempts.
- **Blocked:** 49 (9.2%) got stuck by round 10: a REJECT with nothing the fake planner could apply.
- **Not settled by 10 attempts:** 375 (70.2%) were still rejected after 10. Whether more attempts would do is not measured; the fake planner never proposes the same plan twice, so these are not loops, but nothing here shows they would converge.
