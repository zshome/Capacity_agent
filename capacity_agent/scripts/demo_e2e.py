"""
End-to-End Demo (No LLM Required)
=================================

走一遍完整的产能现况计算流水线,验证所有引擎正常工作。
不依赖 vLLM/LLM,直接调用 L2 引擎。

Usage:
  python scripts/demo_e2e.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 加项目根到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from engines.bottleneck_analyzer import (
    BottleneckInput,
    analyze_bottleneck,
)
from engines.des_validator import (
    DESInput,
    DESJobArrival,
    DESToolGroup,
    run_des,
)
from engines.lp_optimizer import LPInput, Objective, optimize
from engines.rccp import RCCPInput, build_capacity_matrix, compute_rccp


def main():
    print("=" * 70)
    print("Capacity Agent - End-to-End Demo")
    print("=" * 70)

    # 1. 加载样本数据
    sample_dir = Path(__file__).parent.parent / "data" / "sample"
    if not (sample_dir / "dim_route.parquet").exists():
        print("Sample data not found. Running load_sample_data.py first...")
        import subprocess
        subprocess.run([sys.executable, str(Path(__file__).parent / "load_sample_data.py")], check=True)

    routes = pd.read_parquet(sample_dir / "dim_route.parquet")
    tool_groups = pd.read_parquet(sample_dir / "dim_tool_group.parquet")
    oee = pd.read_parquet(sample_dir / "fact_oee_daily.parquet")
    demand = pd.read_parquet(sample_dir / "fact_demand_plan.parquet")

    # ============================================================
    # Step 1: Build Capacity Matrix
    # ============================================================
    print("\n[Step 1] Building Capacity Matrix from Route Master...")
    cm = build_capacity_matrix(routes)
    print(f"  Capacity matrix shape: {cm.shape} (products × tool_groups)")
    print(f"  Sample top-left 5×5:")
    print(cm.iloc[:5, :5].round(3).to_string())

    # ============================================================
    # Step 2: Aggregate available hours (last 7 days)
    # ============================================================
    print("\n[Step 2] Aggregating available hours from OEE...")
    recent_oee = oee[oee["fact_date"] >= oee["fact_date"].max() - pd.Timedelta(days=7)]
    available_hours = recent_oee.groupby("tool_group_id")["available_hours"].sum().to_dict()
    print(f"  Total available hours across {len(available_hours)} tool groups: "
          f"{sum(available_hours.values()):.0f}h")

    # ============================================================
    # Step 3: Get current week demand
    # ============================================================
    current_week = demand["time_window"].min()
    week_demand = demand[demand["time_window"] == current_week]
    demand_plan = dict(zip(week_demand["product_id"], week_demand["wafer_count"]))
    print(f"\n[Step 3] Demand for {current_week}: {len(demand_plan)} products, "
          f"{sum(demand_plan.values()):.0f} total wafers")

    # ============================================================
    # Step 4: Run RCCP
    # ============================================================
    print("\n[Step 4] Running RCCP...")
    rccp_in = RCCPInput(
        demand_plan=demand_plan,
        capacity_matrix=cm,
        available_hours=available_hours,
    )
    rccp_out = compute_rccp(rccp_in)
    print(f"  Feasible:           {rccp_out.feasible}")
    print(f"  Overall loading:    {rccp_out.overall_loading_pct:.1f}%")
    print(f"  Critical groups:    {len(rccp_out.critical_groups)}")
    print(f"\n  Top 10 hotspots:")
    print(f"  {'Tool Group':<15}{'Avail':>8}{'Demand':>8}{'Load%':>8}  Status")
    print("  " + "-" * 55)
    for tg in rccp_out.loading_table[:10]:
        print(f"  {tg.tool_group_id:<15}{tg.available_hours:>8.0f}{tg.demand_hours:>8.0f}"
              f"{tg.loading_pct:>8.1f}  {tg.status}")

    # ============================================================
    # Step 5: Bottleneck Analysis
    # ============================================================
    print("\n[Step 5] Bottleneck Analysis (with Kingman queue approx)...")

    # 模拟历史 (从 oee 派生)
    historical = {}
    for tg_id in available_hours.keys():
        # 用噪声模拟近 5 周的 loading
        base = next((tg.loading_pct for tg in rccp_out.loading_table if tg.tool_group_id == tg_id), 50.0)
        history = [max(0, base + (i - 5) * 1.5 + (i % 2) * 2) for i in range(5)]
        historical[tg_id] = history

    n_servers_map = dict(zip(tool_groups["tool_group_id"], tool_groups["n_machines"].astype(int)))
    service_rates_map = dict(zip(tool_groups["tool_group_id"],
                                  tool_groups["nameplate_throughput_wph"].astype(float)))

    bn_in = BottleneckInput(
        loading_table=[tg.to_dict() for tg in rccp_out.loading_table],
        historical_loading=historical,
        service_rates=service_rates_map,
        n_servers=n_servers_map,
    )
    bn_out = analyze_bottleneck(bn_in)
    print(f"  Primary bottleneck:   {bn_out.primary_bottleneck}")
    print(f"  Secondary:            {bn_out.secondary_bottlenecks[:3]}")
    print(f"  Emerging (drift):     {bn_out.emerging_bottlenecks[:3]}")
    print(f"\n  Top 5 by composite score:")
    for b in bn_out.bottlenecks[:5]:
        print(f"    {b.rank}. {b.tool_group_id:<15} score={b.composite_score:5.1f}  "
              f"load={b.loading_pct:5.1f}%  wait={b.expected_wait_hours:5.1f}h  [{b.severity}]")

    # ============================================================
    # Step 6: LP Optimize (if infeasible)
    # ============================================================
    if not rccp_out.feasible:
        print("\n[Step 6] RCCP infeasible — running LP optimizer to find best mix...")
        unit_profit = dict(zip(week_demand["product_id"], week_demand["unit_profit"]))
        d_min = dict(zip(week_demand["product_id"], week_demand["contract_min"]))
        d_max = dict(zip(week_demand["product_id"], week_demand["market_max"]))

        # 只取在 capacity matrix 中的产品
        valid = [p for p in demand_plan.keys() if p in cm.index]

        lp_in = LPInput(
            products=valid,
            tool_groups=list(cm.columns),
            capacity_matrix=cm.loc[valid],
            available_hours=available_hours,
            demand_min={p: d_min.get(p, 0) for p in valid},
            demand_max={p: d_max.get(p, 99999) for p in valid},
            unit_profit={p: unit_profit.get(p, 100) for p in valid},
            objective=Objective.MAX_PROFIT,
        )
        lp_out = optimize(lp_in)
        print(f"  Status:           {lp_out.status}")
        print(f"  Total profit:     {lp_out.objective_value:.0f}")
        print(f"  Solve time:       {lp_out.solve_time_seconds:.2f}s")
        print(f"  Binding (full)tool groups: {len(lp_out.binding_constraints)}")
        print(f"\n  Top 5 plan changes (vs original):")
        deltas = sorted(
            [(p, lp_out.optimal_plan.get(p, 0) - demand_plan.get(p, 0)) for p in valid],
            key=lambda x: abs(x[1]), reverse=True,
        )[:5]
        for p, d in deltas:
            sign = "+" if d >= 0 else ""
            print(f"    {p:<25} {sign}{d:.0f} wafers")
    else:
        print("\n[Step 6] RCCP feasible — no LP optimization needed.")

    # ============================================================
    # Step 7: DES validation on top bottleneck
    # ============================================================
    if bn_out.primary_bottleneck:
        print(f"\n[Step 7] DES validation on primary bottleneck '{bn_out.primary_bottleneck}'...")
        tg_id = bn_out.primary_bottleneck

        # 估算到达率: 把所有产品对该 tg 的需求加起来,除以一周小时数
        tg_arrival_rate = sum(
            demand_plan.get(p, 0) * cm.loc[p, tg_id]
            for p in demand_plan if p in cm.index
        ) / 168.0 * (1.0 / cm.loc[cm.index[0], tg_id] if tg_id in cm.columns and len(cm.index) > 0 else 1.0)

        # 简化: 用主要产品的总到达率
        main_product = max(demand_plan, key=demand_plan.get)
        rate = demand_plan[main_product] / 168.0   # wafers/hour

        des_in = DESInput(
            tool_groups=[
                DESToolGroup(
                    tool_group_id=tg_id,
                    n_machines=n_servers_map.get(tg_id, 4),
                    service_rate=service_rates_map.get(tg_id, 2.0),
                    service_cv=0.5,
                    availability=0.85,
                ),
            ],
            arrivals=[
                DESJobArrival(
                    product_id=main_product,
                    arrival_rate=rate,
                    arrival_cv=1.0,
                    target_tool_groups=[tg_id],
                    service_time_per_tg={tg_id: float(cm.loc[main_product, tg_id]) if main_product in cm.index else 0.5},
                ),
            ],
            sim_duration_hours=168.0,
            n_replications=2,
        )
        des_out = run_des(des_in)
        s = des_out.tool_group_stats[0]
        print(f"  DES utilization:   {s.avg_utilization*100:.1f}%")
        print(f"  Avg/p95 wait:      {s.avg_wait_hours:.1f}h / {s.p95_wait_hours:.1f}h")
        print(f"  Max queue length:  {s.max_queue_length}")
        print(f"  Feasible (DES):    {des_out.feasible}")
        if des_out.risk_flags:
            for r in des_out.risk_flags:
                print(f"  ⚠ {r}")

    print("\n" + "=" * 70)
    print("End-to-End Demo Complete")
    print("=" * 70)


if __name__ == "__main__":
    main()
