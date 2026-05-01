"""
LP Optimizer
============

当 RCCP 显示 capacity gap 时,运行 LP 优化产品 mix 或决定削减计划。

支持两种模式:
  1. MAX_PROFIT: 在 capacity 约束下最大化产值/利润
  2. MIN_DEVIATION: 最小化与原计划的偏离 (优先保留高优先级产品)

求解器: CBC (开源, COIN-OR 项目),通过 Pyomo 调用
依赖: pip install pyomo coinor-cbc (Docker image 中安装)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd

# CBC 求解器路径
CBC_EXECUTABLE = os.environ.get("CBC_PATH", r"C:\Users\stark\bin\cbc.exe")

try:
    import pyomo.environ as pyo
    from pyomo.opt import SolverFactory, SolverStatus, TerminationCondition
    PYOMO_AVAILABLE = True
except ImportError:
    PYOMO_AVAILABLE = False

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


# ============================================================
# Data classes
# ============================================================
class Objective(str, Enum):
    MAX_PROFIT = "max_profit"
    MIN_DEVIATION = "min_deviation"
    MAX_OUTPUT = "max_output"


@dataclass
class LPInput:
    products: list[str]
    tool_groups: list[str]
    capacity_matrix: pd.DataFrame             # [product × tool_group] hours/wafer
    available_hours: dict[str, float]
    base_load_hours: dict[str, float] = field(default_factory=dict)  # {tool_group: reserved hours by WIP}
    demand_min: dict[str, float] = field(default_factory=dict)   # 最小产量约束 (合同/承诺)
    demand_max: dict[str, float] = field(default_factory=dict)   # 最大产量 (市场需求上限)
    demand_target: dict[str, float] = field(default_factory=dict)  # 目标产量 (用于 min_deviation)
    unit_profit: dict[str, float] = field(default_factory=dict)  # 单 wafer 利润 (max_profit)
    priority: dict[str, float] = field(default_factory=dict)     # 产品优先级权重
    objective: Objective = Objective.MAX_PROFIT
    solver: str = "cbc"                                          # cbc | glpk | highs
    time_limit_seconds: int = 60


@dataclass
class LPResult:
    status: str                                # "optimal" | "infeasible" | "timeout" | "error"
    objective_value: float
    optimal_plan: dict[str, float]             # {product: wafer_count}
    capacity_utilization: dict[str, float]     # {tool_group: utilization_pct}
    deviation_from_target: dict[str, float]    # {product: delta wafers}
    binding_constraints: list[str]             # 紧约束的 tool group
    solve_time_seconds: float
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "objective_value": round(self.objective_value, 2),
            "optimal_plan": {k: round(v, 1) for k, v in self.optimal_plan.items()},
            "capacity_utilization": {k: round(v, 2) for k, v in self.capacity_utilization.items()},
            "deviation_from_target": {k: round(v, 1) for k, v in self.deviation_from_target.items()},
            "binding_constraints": self.binding_constraints,
            "solve_time_seconds": round(self.solve_time_seconds, 2),
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


# ============================================================
# 主求解函数
# ============================================================
def solve_greedy_heuristic(inp: LPInput) -> LPResult:
    """
    贪心启发式算法（当 LP solver 失败时的回退）
    
    逻辑:
      1. 按单位利润排序产品（或优先级）
      2. 依次分配产量，直到产能瓶颈
      3. 计算利用率
    """
    t0 = datetime.utcnow()
    
    products = inp.products
    tool_groups = inp.tool_groups
    C = inp.capacity_matrix
    
    # 按利润/优先级排序
    if inp.objective == Objective.MAX_PROFIT:
        sorted_products = sorted(products, key=lambda p: inp.unit_profit.get(p, 1.0), reverse=True)
    elif inp.objective == Objective.MAX_OUTPUT:
        sorted_products = sorted(products, key=lambda p: inp.priority.get(p, 1.0), reverse=True)
    else:
        sorted_products = products
    
    # 初始化
    optimal_plan = {}
    remaining_hours = {
        tg: max(inp.available_hours.get(tg, 0.0) - inp.base_load_hours.get(tg, 0.0), 0.0)
        for tg in tool_groups
    }
    
    for product in sorted_products:
        # 计算该产品的瓶颈产能
        max_by_tg = {}
        for tg in tool_groups:
            if product in C.index and tg in C.columns:
                tc = C.loc[product, tg]
                if tc > 0:
                    max_wafers = remaining_hours.get(tg, 0) / tc
                    max_by_tg[tg] = max_wafers
        
        if not max_by_tg:
            optimal_plan[product] = 0
            continue
        
        # 瓶颈决定最大产量
        bottleneck_capacity = min(max_by_tg.values())
        
        # 考虑需求约束
        demand_min = inp.demand_min.get(product, 0)
        demand_max = inp.demand_max.get(product, 1e9)
        
        # 最终产量
        final_qty = max(demand_min, min(bottleneck_capacity, demand_max))
        optimal_plan[product] = final_qty
        
        # 更新剩余产能
        for tg in tool_groups:
            if product in C.index and tg in C.columns:
                tc = C.loc[product, tg]
                if tc > 0:
                    remaining_hours[tg] = remaining_hours.get(tg, 0) - final_qty * tc
    
    # 计算目标值
    if inp.objective == Objective.MAX_PROFIT:
        obj_value = sum(inp.unit_profit.get(p, 1.0) * optimal_plan[p] for p in products)
    elif inp.objective == Objective.MAX_OUTPUT:
        obj_value = sum(optimal_plan[p] for p in products)
    else:
        obj_value = sum(optimal_plan[p] for p in products)
    
    # 计算利用率
    util = {}
    binding = []
    for tg in tool_groups:
        used = sum(C.loc[p, tg] * optimal_plan[p] for p in products if p in C.index and tg in C.columns)
        used += inp.base_load_hours.get(tg, 0.0)
        cap = inp.available_hours.get(tg, 0)
        util_pct = (used / cap * 100.0) if cap > 0 else 0.0
        util[tg] = util_pct
        if util_pct >= 99.5:
            binding.append(tg)
    
    # 计算偏差
    deviation = {p: optimal_plan[p] - inp.demand_target.get(p, 0.0) for p in products}
    
    return LPResult(
        status="heuristic",
        objective_value=obj_value,
        optimal_plan=optimal_plan,
        capacity_utilization=util,
        deviation_from_target=deviation,
        binding_constraints=binding,
        solve_time_seconds=(datetime.utcnow() - t0).total_seconds(),
        computed_at=datetime.utcnow(),
        metadata={"method": "greedy_heuristic", "solver": inp.solver},
    )


def optimize(inp: LPInput) -> LPResult:
    """
    标准 LP 模型:

        decision: x[i] = product i 的产量 (wafers)

        max  Σ_i  profit[i] * x[i]              # 或 -Σ deviation[i]
        s.t. Σ_i  C[i,j] * x[i] <= H[j]   ∀j    # capacity
             demand_min[i] <= x[i] <= demand_max[i]  ∀i
             x[i] >= 0
    """
    if not PYOMO_AVAILABLE:
        raise RuntimeError("Pyomo not installed. Run: pip install pyomo")

    t0 = datetime.utcnow()

    products = inp.products
    tool_groups = inp.tool_groups
    C = inp.capacity_matrix

    # === Build Pyomo model ===
    m = pyo.ConcreteModel(name="capacity_lp")

    m.I = pyo.Set(initialize=products)         # 产品集合
    m.J = pyo.Set(initialize=tool_groups)      # tool group 集合

    # 决策变量: 产量
    def x_bounds(m, i):
        lo = inp.demand_min.get(i, 0.0)
        hi = inp.demand_max.get(i, 1e9)
        return (lo, hi)
    m.x = pyo.Var(m.I, domain=pyo.NonNegativeReals, bounds=x_bounds)

    # 约束: capacity
    def capacity_rule(m, j):
        return (
            sum(C.loc[i, j] * m.x[i] for i in m.I if i in C.index and j in C.columns)
            + inp.base_load_hours.get(j, 0.0)
            <= inp.available_hours.get(j, 0.0)
        )
    m.capacity_con = pyo.Constraint(m.J, rule=capacity_rule)

    # 目标
    if inp.objective == Objective.MAX_PROFIT:
        def obj_rule(m):
            return sum(inp.unit_profit.get(i, 1.0) * m.x[i] for i in m.I)
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.maximize)

    elif inp.objective == Objective.MAX_OUTPUT:
        def obj_rule(m):
            return sum(m.x[i] for i in m.I)
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.maximize)

    elif inp.objective == Objective.MIN_DEVIATION:
        # 最小化 |x[i] - target[i]| * priority[i]
        # 用绝对值技巧: 引入辅助变量 d_pos, d_neg
        m.d_pos = pyo.Var(m.I, domain=pyo.NonNegativeReals)
        m.d_neg = pyo.Var(m.I, domain=pyo.NonNegativeReals)

        def dev_rule(m, i):
            target = inp.demand_target.get(i, 0.0)
            return m.x[i] - target == m.d_pos[i] - m.d_neg[i]
        m.dev_con = pyo.Constraint(m.I, rule=dev_rule)

        def obj_rule(m):
            return sum(inp.priority.get(i, 1.0) * (m.d_pos[i] + m.d_neg[i]) for i in m.I)
        m.obj = pyo.Objective(rule=obj_rule, sense=pyo.minimize)

    else:
        raise ValueError(f"Unknown objective: {inp.objective}")

    # === Solve ===
    try:
        # 使用指定的 CBC 路径
        if inp.solver == "cbc" and os.path.exists(CBC_EXECUTABLE):
            solver = SolverFactory("cbc", executable=CBC_EXECUTABLE)
        else:
            solver = SolverFactory(inp.solver)
        
        # 尝试求解（不设置 timeout 参数，使用 solve 的 timeout 参数）
        result = solver.solve(m, tee=False)
    except Exception as e:
        logger.error(f"Solver error: {e}")
        # 尝试启发式回退
        try:
            heuristic_result = solve_greedy_heuristic(inp)
            heuristic_result.metadata["solver_error"] = str(e)
            return heuristic_result
        except Exception as he:
            logger.error(f"Heuristic also failed: {he}")
            return LPResult(
                status="error",
                objective_value=0.0,
                optimal_plan={},
                capacity_utilization={},
                deviation_from_target={},
                binding_constraints=[],
                solve_time_seconds=(datetime.utcnow() - t0).total_seconds(),
                computed_at=datetime.utcnow(),
                metadata={"error": str(e), "heuristic_error": str(he)},
            )

    # === Extract results ===
    term = result.solver.termination_condition

    if term == TerminationCondition.optimal:
        status = "optimal"
    elif term == TerminationCondition.infeasible:
        status = "infeasible"
    elif term == TerminationCondition.maxTimeLimit:
        status = "timeout"
    else:
        status = str(term)

    if status == "infeasible":
        # Infeasible: 返回空解,标记原因
        return LPResult(
            status="infeasible",
            objective_value=0.0,
            optimal_plan={i: 0.0 for i in products},
            capacity_utilization={},
            deviation_from_target={},
            binding_constraints=[],
            solve_time_seconds=(datetime.utcnow() - t0).total_seconds(),
            computed_at=datetime.utcnow(),
            metadata={
                "error": "Problem is infeasible. Check demand_min vs available capacity.",
                "objective_type": inp.objective.value,
            },
        )

    try:
        optimal_plan = {i: float(pyo.value(m.x[i])) if pyo.value(m.x[i]) is not None else 0.0 for i in m.I}
        obj_value = float(pyo.value(m.obj)) if status in ("optimal", "timeout") else 0.0

        # 利用率与紧约束
        util = {}
        binding = []
        for j in m.J:
            used = sum(C.loc[i, j] * optimal_plan[i] for i in m.I if i in C.index and j in C.columns)
            used += inp.base_load_hours.get(j, 0.0)
            cap = inp.available_hours.get(j, 0.0)
            util_pct = (used / cap * 100.0) if cap > 0 else 0.0
            util[j] = util_pct
            if util_pct >= 99.5:
                binding.append(j)

        deviation = {
            i: optimal_plan[i] - inp.demand_target.get(i, 0.0) for i in m.I
        }
    except Exception as e:
        logger.error(f"Solution extraction failed: {e}")
        heuristic_result = solve_greedy_heuristic(inp)
        heuristic_result.metadata["solution_extraction_error"] = str(e)
        heuristic_result.metadata["fallback_from_status"] = status
        return heuristic_result

    return LPResult(
        status=status,
        objective_value=obj_value,
        optimal_plan=optimal_plan,
        capacity_utilization=util,
        deviation_from_target=deviation,
        binding_constraints=binding,
        solve_time_seconds=(datetime.utcnow() - t0).total_seconds(),
        computed_at=datetime.utcnow(),
        metadata={
            "objective_type": inp.objective.value,
            "n_products": len(products),
            "n_tool_groups": len(tool_groups),
            "solver": inp.solver,
            "base_load_hours": {k: round(v, 2) for k, v in inp.base_load_hours.items() if v},
        },
    )


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    if not PYOMO_AVAILABLE:
        print("Pyomo not installed. Skipping LP test.")
        print("Install with: pip install pyomo")
    else:
        # 测试场景: 3 产品 3 tool group, 有 capacity 紧张
        C = pd.DataFrame(
            [[1.0, 0.3, 0.2], [0.4, 0.6, 0.3], [0.5, 0.4, 0.5]],
            index=["28nm_DRAM", "64L_NAND", "128L_NAND"],
            columns=["LITHO_193i", "ETCH_DRY_A", "DEPO_CVD_3"],
        )

        inp = LPInput(
            products=["28nm_DRAM", "64L_NAND", "128L_NAND"],
            tool_groups=["LITHO_193i", "ETCH_DRY_A", "DEPO_CVD_3"],
            capacity_matrix=C,
            available_hours={"LITHO_193i": 1200, "ETCH_DRY_A": 900, "DEPO_CVD_3": 600},
            demand_min={"28nm_DRAM": 500, "64L_NAND": 400, "128L_NAND": 0},     # 合同最低
            demand_max={"28nm_DRAM": 1500, "64L_NAND": 1200, "128L_NAND": 800},
            unit_profit={"28nm_DRAM": 100, "64L_NAND": 80, "128L_NAND": 150},
            objective=Objective.MAX_PROFIT,
        )

        try:
            result = optimize(inp)
            print(f"Status: {result.status}")
            print(f"Objective (total profit): {result.objective_value:.0f}")
            print(f"Solve time: {result.solve_time_seconds:.2f}s")
            print(f"Binding constraints: {result.binding_constraints}")
            print()
            print("Optimal plan:")
            for p, q in result.optimal_plan.items():
                print(f"  {p:15s} {q:8.0f} wafers")
            print()
            print("Capacity utilization:")
            for tg, u in result.capacity_utilization.items():
                print(f"  {tg:15s} {u:6.1f}%")
        except Exception as e:
            print(f"Test failed (likely CBC not installed): {e}")
            print("Install CBC: apt-get install coinor-cbc  (or use Docker image)")
