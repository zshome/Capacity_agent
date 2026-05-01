"""
Production Plan Generator
=========================

整合 RCCP + Allocation + LP，生成结构化生产计划输出

输入:
  - demand_plan: 需求计划
  - capacity_matrix: 产能矩阵
  - available_hours: 机台可用小时
  - routes: 工艺路线数据
  - tool_groups: 机台组信息

输出:
  - feasibility: 可行性判断
  - weekly_plan: 周产量计划
  - tool_allocation: 机台分配方案
  - bottleneck_analysis: 瓶颈分析
  - recommendations: 建议措施
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from engines.rccp import RCCPInput, compute_rccp, THRESHOLDS
from engines.bottleneck_analyzer import BottleneckInput, analyze_bottleneck
from engines.output_rccp import compute_wip_remaining_load

try:
    from engines.allocation_model import AllocationInput, AllocationObjective, allocate, ALLOCATION_AVAILABLE
except ImportError:
    ALLOCATION_AVAILABLE = False
    AllocationInput = None
    allocate = None

try:
    from engines.lp_optimizer import LPInput, Objective, optimize, PYOMO_AVAILABLE
except ImportError:
    PYOMO_AVAILABLE = False
    LPInput = None
    Objective = None
    optimize = None

logger = logging.getLogger(__name__)

PRODUCTION_FEASIBILITY_EPSILON = 1e-6


# ============================================================
# Data classes
# ============================================================
@dataclass
class ToolAllocation:
    """单个机台的分配信息"""
    tool_group_id: str
    assigned_processes: list[str]      # 分配的制程列表
    assigned_products: list[str]       # 分配的产品列表
    allocated_hours: float             # 分配的总小时
    utilization_pct: float             # 利用率
    available_hours: float             # 可用小时
    status: str                        # "available" | "partial" | "full" | "overload"


@dataclass
class WeeklyProductionPlan:
    """周产量计划"""
    product_id: str
    target_wafers: float               # 目标产量
    allocated_wafers: float            # 已分配产能可支持的产量
    original_achievable_wafers: float  # 原始口径实际可达量
    achievable_wafers: float           # WIP校正后实际可达量
    gap_wafers: float                  # WIP校正后的缺口
    original_gap_wafers: float         # 原始口径缺口
    priority: int                      # 优先级 (1=最高)
    unit_profit: float                 # 单片利润
    total_profit: float                # 总利润


@dataclass
class BottleneckInfo:
    """瓶颈信息"""
    tool_group_id: str
    loading_pct: float
    gap_hours: float
    blocking_products: list[str]       # 受影响的产品
    recommended_actions: list[str]     # 建议措施


@dataclass
class ProductionPlanResult:
    """生产计划结果"""
    feasible: bool
    feasibility_score: float           # 0-100, 综合可行性评分
    weekly_plan: list[WeeklyProductionPlan]
    tool_allocation: list[ToolAllocation]
    bottlenecks: list[BottleneckInfo]
    overall_loading_pct: float
    total_profit: float
    recommendations: list[str]
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "feasibility_score": round(self.feasibility_score, 2),
            "weekly_plan": [
                {
                    "product_id": p.product_id,
                    "target_wafers": round(p.target_wafers, 0),
                    "allocated_wafers": round(p.allocated_wafers, 0),
                    "original_achievable_wafers": round(p.original_achievable_wafers, 0),
                    "achievable_wafers": round(p.achievable_wafers, 0),
                    "gap_wafers": round(p.gap_wafers, 0),
                    "original_gap_wafers": round(p.original_gap_wafers, 0),
                    "priority": p.priority,
                    "unit_profit": round(p.unit_profit, 2),
                    "total_profit": round(p.total_profit, 2),
                }
                for p in self.weekly_plan
            ],
            "tool_allocation": [
                {
                    "tool_group_id": t.tool_group_id,
                    "assigned_processes": t.assigned_processes,
                    "assigned_products": t.assigned_products,
                    "allocated_hours": round(t.allocated_hours, 2),
                    "utilization_pct": round(t.utilization_pct, 2),
                    "available_hours": round(t.available_hours, 2),
                    "status": t.status,
                }
                for t in self.tool_allocation
            ],
            "bottlenecks": [
                {
                    "tool_group_id": b.tool_group_id,
                    "loading_pct": round(b.loading_pct, 2),
                    "gap_hours": round(b.gap_hours, 2),
                    "blocking_products": b.blocking_products,
                    "recommended_actions": b.recommended_actions,
                }
                for b in self.bottlenecks
            ],
            "overall_loading_pct": round(self.overall_loading_pct, 2),
            "total_profit": round(self.total_profit, 2),
            "recommendations": self.recommendations,
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class ProductionPlanInput:
    """生产计划输入"""
    demand_plan: dict[str, float]              # {product_id: wafer_count}
    capacity_matrix: pd.DataFrame              # [product × tool_group] hours/wafer
    available_hours: dict[str, float]          # {tool_group_id: hours}
    routes: pd.DataFrame | None = None         # 工艺路线明细
    tool_groups: pd.DataFrame | None = None    # 机台组信息
    unit_profit: dict[str, float] | None = None  # {product_id: profit/wafer}
    priority: dict[str, int] | None = None     # {product_id: priority}
    demand_min: dict[str, float] | None = None  # {product_id: min_wafers} 最低需求约束
    demand_max: dict[str, float] | None = None  # {product_id: max_wafers} 产量上限
    wip_lot_detail: pd.DataFrame | None = None
    enable_wip_adjustment: bool = False
    lp_enabled: bool = True                    # 是否启用LP优化
    lp_objective: str = "max_profit"           # LP优化目标
    lp_solver: str = "cbc"                     # LP求解器
    lp_time_limit: int = 60                    # LP求解时限(秒)
    time_window: str = "weekly"                # weekly | monthly
    objective: str = "max_profit"              # max_profit | max_output | balance


# ============================================================
# Core algorithm
# ============================================================
def generate_production_plan(inp: ProductionPlanInput) -> ProductionPlanResult:
    """
    生成生产计划
    
    流程:
    1. 运行 RCCP 快速判断可行性
    2. 若可行: 分配产能，生成周计划
    3. 若不可行: 运行 LP 优化，给出最优产量建议
    4. 瓶颈分析，给出建议措施
    """
    products = list(inp.demand_plan.keys())
    if not products:
        raise ValueError("demand_plan is empty")

    # 1. 原始 RCCP 快速可行性判断
    original_rccp = compute_rccp(RCCPInput(
        demand_plan=inp.demand_plan,
        capacity_matrix=inp.capacity_matrix,
        available_hours=inp.available_hours,
        time_window=inp.time_window,
    ))

    # 2. 计算 WIP 后续负载，并生成 WIP 校正后的 RCCP 结果
    wip_hours_by_tg: dict[str, float] = {}
    if inp.enable_wip_adjustment and inp.wip_lot_detail is not None and inp.routes is not None:
        wip_load = compute_wip_remaining_load(inp.wip_lot_detail, inp.routes)
        wip_hours_by_tg = {
            tg_id: float(sum(product_hours.values()))
            for tg_id, product_hours in wip_load.items()
        }

    rccp_result = compute_rccp(RCCPInput(
        demand_plan=inp.demand_plan,
        capacity_matrix=inp.capacity_matrix,
        available_hours=inp.available_hours,
        wip_remaining_hours=wip_hours_by_tg,
        time_window=inp.time_window,
    ))

    # 3. 计算各产品可用产能
    capacity_per_product = _compute_capacity_per_product(
        inp.capacity_matrix,
        inp.available_hours,
        products,
    )

    # 4. 若不可行且启用LP优化，运行LP获取调整后的产量
    lp_result = None
    adjusted_demand = inp.demand_plan.copy()
    original_achievable = {
        product_id: min(inp.demand_plan.get(product_id, 0.0), capacity_per_product.get(product_id, 0.0))
        for product_id in products
    }
    
    if not rccp_result.feasible and inp.lp_enabled and PYOMO_AVAILABLE and LPInput and Objective:
        try:
            # 构建demand_max：优先使用用户传入值，否则用产能上限
            demand_max_for_lp = inp.demand_max if inp.demand_max else capacity_per_product
            
            # 构建demand_min：使用用户传入值或空
            demand_min_for_lp = inp.demand_min if inp.demand_min else {}
            
            # 使用用户配置的LP参数
            lp_inp = LPInput(
                products=products,
                tool_groups=list(inp.available_hours.keys()),
                capacity_matrix=inp.capacity_matrix,
                available_hours=inp.available_hours,
                base_load_hours=wip_hours_by_tg,
                demand_min=demand_min_for_lp,
                demand_max=demand_max_for_lp,
                demand_target=inp.demand_plan,
                unit_profit=inp.unit_profit or {},
                objective=Objective(inp.lp_objective),
                solver=inp.lp_solver,
                time_limit_seconds=inp.lp_time_limit,
            )
            lp_result = optimize(lp_inp)
            
            # 使用LP优化结果调整需求计划
            if lp_result and lp_result.status in ("optimal", "heuristic", "timeout") and lp_result.optimal_plan:
                adjusted_demand = lp_result.optimal_plan.copy()
                # 重新运行RCCP验证调整后的计划
                rccp_adjusted = compute_rccp(RCCPInput(
                    demand_plan=adjusted_demand,
                    capacity_matrix=inp.capacity_matrix,
                    available_hours=inp.available_hours,
                    wip_remaining_hours=wip_hours_by_tg,
                ))
                # 更新可行性
                rccp_result = rccp_adjusted
        except Exception as e:
            logger.warning(f"LP optimization failed: {e}")
            lp_result = None

    # 5. 生成周产量计划（使用调整后的需求）
    weekly_plan = _build_weekly_plan(
        inp.demand_plan,  # 原始需求（目标）
        capacity_per_product,
        inp.unit_profit or {},
        inp.priority or {},
        rccp_result,
        original_achievable,
        adjusted_demand,  # 调整后的需求（可达）
        lp_result,
    )

    # 6. 机台分配方案
    tool_allocation = _build_tool_allocation(
        rccp_result.loading_table,
        inp.capacity_matrix,
        adjusted_demand,
        inp.routes,
    )

    # 7. 瓶颈分析
    bottlenecks = _analyze_bottlenecks(
        rccp_result,
        inp.capacity_matrix,
        adjusted_demand,
    )

    adjusted_plan_feasible = _is_plan_feasible(rccp_result)

    # 8. 计算总利润
    total_profit = sum(p.total_profit for p in weekly_plan)

    # 9. 可行性评分 (综合多个指标)
    feasibility_score = _compute_feasibility_score(rccp_result, weekly_plan)

    # 10. 生成建议
    recommendations = _generate_recommendations(
        adjusted_plan_feasible,
        rccp_result,
        weekly_plan,
        bottlenecks,
        inp.objective,
        lp_result,
    )

    return ProductionPlanResult(
        feasible=adjusted_plan_feasible,
        feasibility_score=feasibility_score,
        weekly_plan=weekly_plan,
        tool_allocation=tool_allocation,
        bottlenecks=bottlenecks,
        overall_loading_pct=rccp_result.overall_loading_pct,
        total_profit=total_profit,
        recommendations=recommendations,
        computed_at=datetime.utcnow(),
        metadata={
            "n_products": len(products),
            "n_tool_groups": len(rccp_result.loading_table),
            "time_window": inp.time_window,
            "objective": inp.objective,
            "original_feasible": original_rccp.feasible,
            "wip_adjusted_feasible": adjusted_plan_feasible,
            "lp_adjusted": lp_result is not None and lp_result.status in ("optimal", "heuristic", "timeout"),
            "lp_status": lp_result.status if lp_result is not None else None,
            "adjusted_feasible": adjusted_plan_feasible,
            "original_demand_total": sum(inp.demand_plan.values()),
            "adjusted_demand_total": sum(adjusted_demand.values()),
            "demand_reduction": sum(inp.demand_plan.values()) - sum(adjusted_demand.values()),  # 需求削减量
            "wip_adjustment_enabled": inp.enable_wip_adjustment,
            "wip_total_hours": round(sum(wip_hours_by_tg.values()), 2),
            "wip_hours_by_tool_group": {k: round(v, 2) for k, v in wip_hours_by_tg.items()},
            "original_overall_loading_pct": round(original_rccp.overall_loading_pct, 2),
            "wip_adjusted_overall_loading_pct": round(rccp_result.overall_loading_pct, 2),
        },
    )


def _compute_capacity_per_product(
    capacity_matrix: pd.DataFrame,
    available_hours: dict[str, float],
    products: list[str],
) -> dict[str, float]:
    """
    计算每个产品在当前产能下可支持的最大产量
    
    方法: 找出每个产品在瓶颈机台上的产能上限
    """
    result = {}
    
    for product in products:
        if product not in capacity_matrix.index:
            result[product] = 0.0
            continue
        
        # 该产品在各机台的单位耗时
        unit_hours = capacity_matrix.loc[product]
        
        # 计算每个机台对该产品的产能上限
        max_wafers_per_tg = {}
        for tg_id in unit_hours.index:
            if tg_id not in available_hours:
                continue
            hours_per_wafer = float(unit_hours[tg_id])
            if hours_per_wafer > 0:
                # 该机台对该产品的最大产能 = 可用小时 / 单位耗时
                max_wafers = available_hours[tg_id] / hours_per_wafer
                max_wafers_per_tg[tg_id] = max_wafers
        
        # 取瓶颈机台的产能作为上限
        if max_wafers_per_tg:
            result[product] = min(max_wafers_per_tg.values())
        else:
            result[product] = float('inf')  # 无产能约束
    
    return result


def _build_weekly_plan(
    demand_plan: dict[str, float],
    capacity_per_product: dict[str, float],
    unit_profit: dict[str, float],
    priority: dict[str, int],
    rccp_result,
    original_achievable: dict[str, float],
    adjusted_demand: dict[str, float] | None = None,
    lp_result: Any | None = None,
) -> list[WeeklyProductionPlan]:
    """构建周产量计划"""
    weekly_plan = []
    
    # 使用调整后的需求（如果有LP优化结果）
    effective_demand = adjusted_demand or demand_plan
    
    for product_id, target in demand_plan.items():
        allocated = capacity_per_product.get(product_id, 0.0)
        original_value = original_achievable.get(product_id, min(target, allocated))
        
        # 使用LP优化后的产量（如果有）
        if lp_result and lp_result.status == "optimal" and lp_result.optimal_plan:
            achievable = lp_result.optimal_plan.get(product_id, 0.0)
        else:
            achievable = min(target, effective_demand.get(product_id, allocated))
        
        # 计算缺口（目标 - 实际可达）
        gap = target - achievable
        original_gap = target - original_value
        
        profit = unit_profit.get(product_id, 1.0)
        prio = priority.get(product_id, 1)
        
        weekly_plan.append(WeeklyProductionPlan(
            product_id=product_id,
            target_wafers=target,
            allocated_wafers=allocated,
            original_achievable_wafers=original_value,
            achievable_wafers=achievable,
            gap_wafers=gap,
            original_gap_wafers=original_gap,
            priority=prio,
            unit_profit=profit,
            total_profit=achievable * profit,
        ))
    
    # 按优先级排序
    weekly_plan.sort(key=lambda x: x.priority)
    
    return weekly_plan


def _build_tool_allocation(
    loading_table: list,
    capacity_matrix: pd.DataFrame,
    demand_plan: dict[str, float],
    routes: pd.DataFrame | None,
) -> list[ToolAllocation]:
    """构建机台分配方案"""
    allocation = []
    
    # 从工艺路线提取制程信息
    process_map = {}
    if routes is not None and "tool_group_id" in routes.columns:
        for tg_id in routes["tool_group_id"].unique():
            process_map[tg_id] = routes[routes["tool_group_id"] == tg_id]["step_seq"].unique().tolist()
    
    for tg_loading in loading_table:
        # 找出该机台服务的产品
        products = list(tg_loading.contributing_products.keys())
        
        # 提取制程
        processes = process_map.get(tg_loading.tool_group_id, [])
        
        # 状态判断
        util = tg_loading.loading_pct
        if util >= 100:
            status = "overload"
        elif util >= 85:
            status = "full"
        elif util >= 50:
            status = "partial"
        else:
            status = "available"
        
        allocation.append(ToolAllocation(
            tool_group_id=tg_loading.tool_group_id,
            assigned_processes=[str(p) for p in processes],
            assigned_products=products,
            allocated_hours=tg_loading.demand_hours,
            utilization_pct=util,
            available_hours=tg_loading.available_hours,
            status=status,
        ))
    
    return allocation


def _analyze_bottlenecks(
    rccp_result,
    capacity_matrix: pd.DataFrame,
    demand_plan: dict[str, float],
) -> list[BottleneckInfo]:
    """分析瓶颈并给出建议"""
    bottlenecks = []
    
    for tg in rccp_result.critical_groups + rccp_result.warning_groups:
        tg_loading = next((t for t in rccp_result.loading_table if t.tool_group_id == tg), None)
        if not tg_loading:
            continue
        
        # 受影响的产品
        blocking = list(tg_loading.contributing_products.keys())
        
        # 建议措施
        actions = []
        if tg_loading.status == "overload":
            actions.append(f"增加 {tg} 机台数量或延长运行时间")
            gap_hours = tg_loading.gap_hours
            if gap_hours > 0:
                actions.append(f"需额外 {gap_hours:.0f} 小时产能")
        elif tg_loading.status == "critical":
            actions.append(f"监控 {tg} 运行状态，预防停机")
        else:
            actions.append(f"优化 {tg} 调度，提高利用率")
        
        bottlenecks.append(BottleneckInfo(
            tool_group_id=tg,
            loading_pct=tg_loading.loading_pct,
            gap_hours=tg_loading.gap_hours,
            blocking_products=blocking,
            recommended_actions=actions,
        ))
    
    return bottlenecks


def _compute_feasibility_score(rccp_result, weekly_plan) -> float:
    """计算可行性评分 (0-100)"""
    # 基础评分 = 100 - 整体利用率 (利用率越高，余量越少，评分略低)
    base_score = 100 - min(rccp_result.overall_loading_pct, 100) * 0.3
    
    # 瓶颈扣分
    bottleneck_penalty = len(rccp_result.critical_groups) * 5 + len(rccp_result.warning_groups) * 2
    
    # 产能缺口扣分
    gap_penalty = sum(max(p.gap_wafers, 0) for p in weekly_plan) / sum(p.target_wafers for p in weekly_plan) * 20
    
    score = base_score - bottleneck_penalty - gap_penalty
    return max(0, min(100, score))


def _is_plan_feasible(rccp_result) -> bool:
    """生产计划口径下允许瓶颈机台打满到100%，超过100%才判定不可行。"""
    return all(tg.loading_pct <= 100.0 + PRODUCTION_FEASIBILITY_EPSILON for tg in rccp_result.loading_table)


def _generate_recommendations(
    adjusted_plan_feasible: bool,
    rccp_result,
    weekly_plan,
    bottlenecks,
    objective: str,
    lp_result: Any | None = None,
) -> list[str]:
    """生成建议措施"""
    recommendations = []
    
    if adjusted_plan_feasible:
        recommendations.append("✅ 当前产能可满足需求计划，建议按计划执行")
        
        # 有瓶颈但不过载
        if bottlenecks:
            recommendations.append(f"⚠️ 关注 {len(bottlenecks)} 个瓶颈机台的运行状态")
    else:
        # 检查是否有LP调整后的可行计划
        if lp_result and lp_result.status == "optimal":
            recommendations.append("✅ LP优化后产能可满足调整后的计划")
            
            # 显示调整后的产量对比
            total_original = sum(p.target_wafers for p in weekly_plan)
            total_adjusted = sum(p.achievable_wafers for p in weekly_plan)
            reduction = total_original - total_adjusted
            
            if reduction > 0:
                recommendations.append(f"📊 建议调整总产量: {total_original:.0f} → {total_adjusted:.0f} wafers (减少 {reduction:.0f})")
            
            # 显示具体产品调整
            adjusted_products = [p for p in weekly_plan if p.gap_wafers > 0]
            if adjusted_products:
                adjustments = ", ".join([f"{p.product_id}: {p.target_wafers:.0f}→{p.achievable_wafers:.0f}" for p in adjusted_products[:5]])
                recommendations.append(f"🔧 产品调整建议: {adjustments}")
            
            # 显示LP约束（瓶颈机台）
            if lp_result.binding_constraints:
                recommendations.append(f"⚠️ 瓶颈约束机台: {', '.join(lp_result.binding_constraints[:5])}")
        else:
            recommendations.append("❌ 当前产能不足，需调整计划或增加产能")
            
            # 总缺口
            total_gap = sum(max(p.gap_wafers, 0) for p in weekly_plan)
            if total_gap > 0:
                recommendations.append(f"📊 总产量缺口: {total_gap:.0f} wafers")
            
            # 瓶颈机台建议
            if bottlenecks:
                top_bottleneck = bottlenecks[0]
                recommendations.append(f"🔧 优先解决瓶颈: {top_bottleneck.tool_group_id} ({top_bottleneck.loading_pct:.1f}% loading)")
    
    # 利润优化建议
    if objective == "max_profit":
        # 找出高利润产品
        high_profit_products = sorted(weekly_plan, key=lambda x: x.unit_profit, reverse=True)[:3]
        if high_profit_products:
            recommendations.append(
                f"💰 高利润产品优先: {', '.join(p.product_id for p in high_profit_products)}"
            )
    
    # 产能余量建议
    healthy_capacity = [t for t in rccp_result.loading_table if t.status == "healthy"]
    if healthy_capacity:
        recommendations.append(
            f"📈 可利用余量产能: {len(healthy_capacity)} 个机台组有余量"
        )
    
    return recommendations


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 演示数据
    import pandas as pd
    
    C = pd.DataFrame({
        "LITHO_01": {"28nm_DRAM": 1.0, "64L_NAND": 0.8},
        "ETCH_03": {"28nm_DRAM": 0.6, "64L_NAND": 0.7},
        "DEPO_02": {"28nm_DRAM": 0.4, "64L_NAND": 0.5},
    }).T
    
    inp = ProductionPlanInput(
        demand_plan={"28nm_DRAM": 1000, "64L_NAND": 800},
        capacity_matrix=C,
        available_hours={"LITHO_01": 1200, "ETCH_03": 900, "DEPO_02": 600},
        unit_profit={"28nm_DRAM": 150, "64L_NAND": 80},
        priority={"28nm_DRAM": 1, "64L_NAND": 2},
    )
    
    result = generate_production_plan(inp)
    print(f"可行: {result.feasible}")
    print(f"可行性评分: {result.feasibility_score:.1f}")
    print(f"总利润: {result.total_profit:.0f}")
    print("\n周产量计划:")
    for p in result.weekly_plan:
        print(f"  {p.product_id}: 目标 {p.target_wafers:.0f}, 可达 {p.achievable_wafers:.0f}, 缺口 {p.gap_wafers:.0f}")
    print("\n建议:")
    for r in result.recommendations:
        print(f"  {r}")
