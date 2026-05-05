# R6 Master Plan Algorithm Hardening

## Background

The company's master production plan is the R6 plan: a monthly commitment for wafer starts by product. For this workflow, the planning result must be suitable for three decisions:

- formal wafer-start decision
- capacity commitment
- weekly plan freeze

## Implemented Scope

1. R6 monthly capacity bucket
   - Monthly plan windows are treated as monthly buckets.
   - Effective capacity uses machines, calendar hours, availability, and performance.

2. WIP time-phased load
   - WIP downstream route load is no longer only available as one flat remaining-load number.
   - The new period bucket function spreads WIP load into week or month buckets.
   - R6 planning consumes the WIP load assigned to the selected monthly bucket.

3. Unified WIP-aware decision chain
   - RCCP, LP optimizer, and Production Plan now use the same WIP base-load concept.
   - Frontend full analysis sends WIP to RCCP, LP, and Production Plan by default.

4. Storage-fab route semantics
   - `visit_count` participates in capacity matrix construction.
   - Multiple paths without explicit path mix are balanced by product instead of being double-counted.

5. Decision readiness signal
   - Production Plan now returns `decision_readiness`.
   - `commit_ready_optimal` means an optimal solver result supports final commitment.
   - `solver_required_for_final_commit` means the plan is capacity-feasible but solved by fallback heuristic.

## Validation

Added `capacity_agent/test_r6_algorithm_readiness.py` covering:

- route `visit_count` and default path mix behavior
- monthly WIP load spreading
- WIP-aware RCCP feasibility change
- R6 production plan convergence from infeasible demand to WIP-constrained commitment

## Remaining Control Gate

For formal commitment, the environment should run an optimization solver such as HiGHS or CBC through Pyomo. If the system falls back to heuristic, it can provide a feasible recommendation, but it should not be treated as the final frozen commitment without solver validation.
