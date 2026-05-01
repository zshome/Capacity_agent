"""
RCCP (Rough-Cut Capacity Planning) Engine
==========================================

核心算法: Capacity Matrix × 产品计划 = Tool Group Loading

输入:
  - demand_plan: Dict[product_id, wafer_count]
  - capacity_matrix: DataFrame [product × tool_group] 单位 hours/wafer
  - available_hours: Dict[tool_group_id, hours]

输出:
  - loading_table: 每个 tool_group 的 demand/available/loading/gap
  - feasibility: 是否可行
  - critical_groups: loading > threshold 的 tool group 列表

性能: 200 tool groups × 50 products 在毫秒级完成
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Data classes
# ============================================================
@dataclass
class RCCPInput:
    """RCCP 输入"""
    demand_plan: dict[str, float]              # {product_id: wafer_count}
    capacity_matrix: pd.DataFrame              # index=product_id, columns=tool_group_id, values=hours/wafer
    available_hours: dict[str, float]          # {tool_group_id: hours}
    wip_remaining_hours: dict[str, float] | None = None  # {tool_group_id: wip_hours}
    time_window: str = "weekly"                # weekly | monthly
    path_mix: dict[str, dict[str, float]] | None = None  # {product: {path: alpha}}, optional


@dataclass
class ToolGroupLoading:
    """单个 tool group 的产能现况"""
    tool_group_id: str
    available_hours: float
    new_plan_hours: float
    wip_hours: float
    demand_hours: float
    loading_pct: float        # 0-100+
    gap_hours: float          # >0 表示缺口, <0 表示余量
    status: str               # "healthy" | "warning" | "critical" | "overload"
    contributing_products: dict[str, float] = field(default_factory=dict)  # 各产品贡献的小时数

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_group_id": self.tool_group_id,
            "available_hours": round(self.available_hours, 2),
            "new_plan_hours": round(self.new_plan_hours, 2),
            "wip_hours": round(self.wip_hours, 2),
            "demand_hours": round(self.demand_hours, 2),
            "loading_pct": round(self.loading_pct, 2),
            "gap_hours": round(self.gap_hours, 2),
            "status": self.status,
            "contributing_products": {k: round(v, 2) for k, v in self.contributing_products.items()},
        }


@dataclass
class RCCPResult:
    """RCCP 输出"""
    loading_table: list[ToolGroupLoading]
    feasible: bool
    overall_loading_pct: float
    critical_groups: list[str]
    warning_groups: list[str]
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "loading_table": [tg.to_dict() for tg in self.loading_table],
            "feasible": self.feasible,
            "overall_loading_pct": round(self.overall_loading_pct, 2),
            "critical_groups": self.critical_groups,
            "warning_groups": self.warning_groups,
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


# ============================================================
# Status thresholds (configurable)
# ============================================================
THRESHOLDS = {
    "healthy_max": 75.0,       # < 75% 健康
    "warning_max": 85.0,       # 75-85% 关注
    "critical_max": 100.0,     # 85-100% 瓶颈
    # > 100% 过载
}


def classify_status(loading_pct: float) -> str:
    """根据 loading 率分类状态"""
    if loading_pct >= THRESHOLDS["critical_max"]:
        return "overload"
    elif loading_pct >= THRESHOLDS["warning_max"]:
        return "critical"
    elif loading_pct >= THRESHOLDS["healthy_max"]:
        return "warning"
    else:
        return "healthy"


# ============================================================
# Core RCCP algorithm
# ============================================================
def compute_rccp(inp: RCCPInput) -> RCCPResult:
    """
    核心 RCCP 计算

    数学表达:
        D[j] = Σ_i  P[i] * C[i,j]       # 需求小时
        L[j] = D[j] / H[j] * 100        # 利用率
        gap[j] = D[j] - H[j]            # 缺口

    其中:
        i = product index
        j = tool_group index
        P[i] = 产品 i 的产量计划 (wafers)
        C[i,j] = 产品 i 在 tool group j 的单位 wafer 占用小时
        H[j] = tool group j 的可用小时
    """
    # 1. 数据校验
    products = list(inp.demand_plan.keys())
    if not products:
        raise ValueError("demand_plan is empty")

    # Capacity matrix: 取产品计划中存在的产品
    missing = set(products) - set(inp.capacity_matrix.index)
    if missing:
        logger.warning(f"Products missing in capacity matrix: {missing}")

    valid_products = [p for p in products if p in inp.capacity_matrix.index]
    C = inp.capacity_matrix.loc[valid_products].apply(pd.to_numeric, errors="coerce")
    C = C.replace([np.inf, -np.inf], 0.0).fillna(0.0)
    P = pd.Series([inp.demand_plan[p] for p in valid_products], index=valid_products, dtype="float64")

    # 2. 逐列聚合需求小时,避免在部分环境下 numpy matmul 的误报警告
    contribution_matrix = C.multiply(P, axis=0)  # shape: (n_products, n_tool_groups)
    new_plan_hours = contribution_matrix.sum(axis=0)
    wip_remaining_hours = inp.wip_remaining_hours or {}

    # 4. 构建 loading table
    loading_table: list[ToolGroupLoading] = []
    overall_demand = 0.0
    overall_available = 0.0

    all_tool_groups = set(C.columns) | set(inp.available_hours.keys()) | set(wip_remaining_hours.keys())

    for tg_id in all_tool_groups:
        new_d = float(new_plan_hours.get(tg_id, 0.0))
        wip_d = float(wip_remaining_hours.get(tg_id, 0.0))
        d = new_d + wip_d
        h = float(inp.available_hours.get(tg_id, 0.0))

        if h <= 0:
            logger.warning(f"Tool group {tg_id} has zero or missing available hours, skipping")
            continue

        loading = (d / h) * 100.0
        gap = d - h

        # 找出该 tool group 上的主要贡献产品 (Top 5)
        if tg_id in contribution_matrix.columns:
            contrib = contribution_matrix[tg_id].sort_values(ascending=False)
            top_contributors = {p: float(v) for p, v in contrib.head(5).items() if v > 0}
        else:
            top_contributors = {}

        tg_loading = ToolGroupLoading(
            tool_group_id=tg_id,
            available_hours=h,
            new_plan_hours=new_d,
            wip_hours=wip_d,
            demand_hours=d,
            loading_pct=loading,
            gap_hours=gap,
            status=classify_status(loading),
            contributing_products=top_contributors,
        )
        loading_table.append(tg_loading)

        overall_demand += d
        overall_available += h

    # 5. 排序 (按 loading 降序)
    loading_table.sort(key=lambda x: x.loading_pct, reverse=True)

    # 6. 汇总指标
    overall_loading = (overall_demand / overall_available * 100.0) if overall_available > 0 else 0.0
    critical_groups = [tg.tool_group_id for tg in loading_table if tg.status in ("critical", "overload")]
    warning_groups = [tg.tool_group_id for tg in loading_table if tg.status == "warning"]
    feasible = not any(tg.status == "overload" for tg in loading_table)

    return RCCPResult(
        loading_table=loading_table,
        feasible=feasible,
        overall_loading_pct=overall_loading,
        critical_groups=critical_groups,
        warning_groups=warning_groups,
        computed_at=datetime.utcnow(),
        metadata={
            "n_products": len(valid_products),
            "n_tool_groups": len(loading_table),
            "time_window": inp.time_window,
            "wip_total_hours": round(sum(wip_remaining_hours.values()), 2),
            "thresholds": THRESHOLDS,
        },
    )


# ============================================================
# Capacity Matrix builder (从原始 route 数据构建)
# ============================================================
def build_capacity_matrix(
    route_master: pd.DataFrame,           # cols: product_id, step_seq, tool_group_id, run_time_hr, batch_size
    path_mix: dict[str, dict[str, float]] | None = None,
) -> pd.DataFrame:
    """
    从 route master 构建 Capacity Consumption Matrix

    C[i,j] = Σ over steps s where toolgroup(s)==j:
              run_time(s) * visit_count(s) / batch_size(s)

    多 path 加权: C_eff[i,j] = Σ_k α[i,k] * C[i,j,k]
    """
    if "path_id" not in route_master.columns:
        # 单 path 简化
        route_master = route_master.assign(path_id="default")
        if path_mix is None:
            path_mix = {}

    # 计算每个 step 的有效占用 = run_time / batch_size (visit_count 默认 1, reentrant 在 route 中已展开)
    route_master = route_master.copy()
    route_master["unit_hours"] = route_master["run_time_hr"] / route_master["batch_size"].clip(lower=1)

    # 按 (product, path, tool_group) 聚合
    grouped = (
        route_master.groupby(["product_id", "path_id", "tool_group_id"])["unit_hours"]
        .sum()
        .reset_index()
    )

    # 应用 path mix 加权
    if path_mix:
        def get_alpha(row):
            return path_mix.get(row["product_id"], {}).get(row["path_id"], 1.0)
        grouped["alpha"] = grouped.apply(get_alpha, axis=1)
    else:
        grouped["alpha"] = 1.0

    grouped["weighted_hours"] = grouped["unit_hours"] * grouped["alpha"]

    # 透视成矩阵 [product × tool_group]
    matrix = grouped.pivot_table(
        index="product_id",
        columns="tool_group_id",
        values="weighted_hours",
        aggfunc="sum",
        fill_value=0.0,
    )

    return matrix


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 演示数据
    route_data = pd.DataFrame([
        # product_id, path_id, step_seq, tool_group_id, run_time_hr, batch_size
        ("28nm_DRAM",   "P1", 1, "LITHO_193i",   0.5, 1),
        ("28nm_DRAM",   "P1", 2, "ETCH_DRY_A",   0.3, 1),
        ("28nm_DRAM",   "P1", 3, "LITHO_193i",   0.5, 1),  # reentrant
        ("28nm_DRAM",   "P1", 4, "DEPO_CVD_3",   0.2, 1),
        ("64L_NAND",    "P1", 1, "LITHO_193i",   0.4, 1),
        ("64L_NAND",    "P1", 2, "ETCH_DRY_A",   0.6, 1),
        ("64L_NAND",    "P1", 3, "DEPO_CVD_3",   0.3, 1),
    ], columns=["product_id", "path_id", "step_seq", "tool_group_id", "run_time_hr", "batch_size"])

    C = build_capacity_matrix(route_data)
    print("Capacity Matrix (hours/wafer):")
    print(C)
    print()

    inp = RCCPInput(
        demand_plan={"28nm_DRAM": 1000, "64L_NAND": 800},
        capacity_matrix=C,
        available_hours={"LITHO_193i": 1200, "ETCH_DRY_A": 900, "DEPO_CVD_3": 600},
    )

    result = compute_rccp(inp)
    print(f"Feasible: {result.feasible}")
    print(f"Overall loading: {result.overall_loading_pct:.1f}%")
    print(f"Critical groups: {result.critical_groups}")
    print()
    for tg in result.loading_table:
        print(f"  {tg.tool_group_id:15s} {tg.loading_pct:6.1f}%  [{tg.status}]  gap={tg.gap_hours:+.1f}h")
