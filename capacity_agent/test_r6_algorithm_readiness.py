import sys

import pandas as pd

sys.path.insert(0, "/Users/workspace/Capacity_agent_output/Capacity_agent/capacity_agent")

from engines.output_rccp import compute_wip_remaining_load_by_period
from engines.production_plan import ProductionPlanInput, generate_production_plan
from engines.rccp import build_capacity_matrix, compute_rccp, RCCPInput


def test_capacity_matrix_uses_visit_count_and_balances_missing_path_mix():
    routes = pd.DataFrame(
        [
            {"product_id": "P1", "path_id": "A", "step_seq": 1, "tool_group_id": "LITHO", "run_time_hr": 2.0, "batch_size": 1, "visit_count": 2},
            {"product_id": "P1", "path_id": "B", "step_seq": 1, "tool_group_id": "LITHO", "run_time_hr": 4.0, "batch_size": 1, "visit_count": 1},
        ]
    )

    matrix = build_capacity_matrix(routes)

    assert matrix.loc["P1", "LITHO"] == 4.0


def test_wip_remaining_load_is_spread_across_r6_month_buckets():
    routes = pd.DataFrame(
        [
            {"product_id": "P1", "step_seq": 10, "tool_group_id": "A", "run_time_hr": 24.0, "batch_size": 1, "visit_count": 1},
            {"product_id": "P1", "step_seq": 20, "tool_group_id": "B", "run_time_hr": 24.0 * 35, "batch_size": 1, "visit_count": 1},
        ]
    )
    wip = pd.DataFrame(
        [{"lot_id": "L1", "product_id": "P1", "current_step_seq": 10, "wafer_count": 1, "percent_complete": 40}]
    )

    period_load = compute_wip_remaining_load_by_period(
        wip,
        routes,
        current_week="2026-W17",
        granularity="monthly",
    )

    assert "2026-04" in period_load
    assert "2026-05" in period_load
    assert period_load["2026-04"]["A"]["P1"] == 24.0
    assert round(period_load["2026-04"]["B"]["P1"], 2) == 240.0
    assert round(period_load["2026-05"]["B"]["P1"], 2) == 600.0


def test_hold_wip_is_shifted_out_of_current_week_bucket():
    routes = pd.DataFrame(
        [{"product_id": "P1", "step_seq": 10, "tool_group_id": "A", "run_time_hr": 24.0, "batch_size": 1, "visit_count": 1}]
    )
    wip = pd.DataFrame(
        [{"lot_id": "L1", "product_id": "P1", "current_step_seq": 10, "wafer_count": 1, "percent_complete": 40, "lot_status": "HOLD"}]
    )

    period_load = compute_wip_remaining_load_by_period(
        wip,
        routes,
        current_week="2026-W17",
        granularity="weekly",
    )

    assert "2026-W17" not in period_load
    assert period_load["2026-W18"]["A"]["P1"] == 24.0


def test_effective_available_hours_sums_r6_month_capacity_calendar():
    try:
        from engines.server import _effective_available_hours
    except ModuleNotFoundError:
        print("test_effective_available_hours_sums_r6_month_capacity_calendar: skipped (fastapi unavailable)")
        return

    class Bundle:
        pass

    bundle = Bundle()
    bundle.tool_groups = pd.DataFrame([{"tool_group_id": "LITHO", "n_machines": 2}])
    bundle.oee = pd.DataFrame(
        [
            {"fact_date": "2026-05-01", "tool_group_id": "LITHO", "availability": 0.5, "performance": 1.0, "available_hours": 0},
            {"fact_date": "2026-05-02", "tool_group_id": "LITHO", "availability": 0.75, "performance": 1.0, "available_hours": 0},
            {"fact_date": "2026-06-01", "tool_group_id": "LITHO", "availability": 1.0, "performance": 1.0, "available_hours": 999},
        ]
    )

    assert _effective_available_hours(bundle, "R6-2026-05") == {"LITHO": 60.0}


def test_wip_aware_rccp_changes_r6_commitment_feasibility():
    capacity_matrix = pd.DataFrame({"LITHO": {"P1": 1.0}})
    base = compute_rccp(
        RCCPInput(
            demand_plan={"P1": 80},
            capacity_matrix=capacity_matrix,
            available_hours={"LITHO": 100},
        )
    )
    with_wip = compute_rccp(
        RCCPInput(
            demand_plan={"P1": 80},
            capacity_matrix=capacity_matrix,
            available_hours={"LITHO": 100},
            wip_remaining_hours={"LITHO": 30},
        )
    )

    assert base.feasible is True
    assert with_wip.feasible is False


def test_r6_production_plan_converges_to_wip_constrained_commitment():
    routes = pd.DataFrame(
        [{"product_id": "P1", "path_id": "default", "step_seq": 1, "tool_group_id": "LITHO", "run_time_hr": 1.0, "batch_size": 1, "visit_count": 1}]
    )
    wip = pd.DataFrame(
        [{"lot_id": "L1", "product_id": "P1", "current_step_seq": 1, "wafer_count": 30, "percent_complete": 10}]
    )

    result = generate_production_plan(
        ProductionPlanInput(
            demand_plan={"P1": 120},
            capacity_matrix=build_capacity_matrix(routes),
            available_hours={"LITHO": 100},
            routes=routes,
            wip_lot_detail=wip,
            enable_wip_adjustment=True,
            demand_max={"P1": 120},
            lp_enabled=True,
            lp_objective="max_output",
            lp_solver="highs",
            time_window="R6-2026-05",
            planning_granularity="monthly",
        )
    ).to_dict()

    assert result["feasible"] is False
    assert result["metadata"]["capacity_feasible"] is True
    assert result["metadata"]["commit_feasible"] is False
    assert result["metadata"]["decision_readiness"] == "solver_required_for_final_commit"
    assert result["metadata"]["wip_total_hours"] == 30.0
    assert result["metadata"]["adjusted_demand_total"] == 70.0
    assert result["metadata"]["wip_adjusted_overall_loading_pct"] == 100.0
