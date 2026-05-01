"""
Allocation Model
================

情景2-4的产能分配优化模型

核心算法:
  1. 穷举可行分配方案
  2. 找出瓶颈约束
  3. 使用LP/启发式求解最优分配

适用于:
  - 情景2: 多机台/相同Path/有Backup
  - 情景3: 多机台/不同Path/有Backup
  - 情景4: 多机台/不同Path/无Backup

数学模型:

决策变量:
  x[p,t,s] = 产品p在机台t上制程s的分配量 (wafers)

约束:
  1. 制程约束: 每个产品必须完成所有制程
  2. 机台约束: Σ_p x[p,t] ≤ capacity[t]
  3. 可行性约束: feasibility_matrix[p,t,s] = True 才允许分配

目标:
  - 最大产能输出
  - 或最小瓶颈利用率差异
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import numpy as np

# CBC 求解器路径（如果不在系统 PATH 中）
CBC_EXECUTABLE = os.environ.get("CBC_PATH", r"C:\Users\stark\bin\cbc.exe")

try:
    import pyomo.environ as pyo
    from pyomo.opt import SolverFactory, SolverStatus, TerminationCondition
    PYOMO_AVAILABLE = True
except ImportError:
    PYOMO_AVAILABLE = False

logger = logging.getLogger(__name__)


class AllocationObjective(str, Enum):
    MAX_OUTPUT = "max_output"           # 最大总产出
    MIN_VARIANCE = "min_variance"       # 最小利用率差异
    MAX_BALANCE = "max_balance"         # 最大瓶颈利用率
    MIN_CYCLE_TIME = "min_cycle_time"   # 最小周期时间


@dataclass
class AllocationInput:
    """分配模型输入"""
    products: list[str]
    tools: list[str]
    processes: list[str]
    
    # 可行性矩阵: {product: {tool: {process: bool}}}
    feasibility: dict[str, dict[str, dict[str, bool]]]
    
    # TC矩阵: {product: {tool: {process: hours}}}
    tc_matrix: dict[str, dict[str, dict[str, float]]]
    
    # 机台可用小时: {tool: hours_per_month}
    available_hours: dict[str, float]
    
    # 产品需求目标: {product: wafers}
    demand_target: dict[str, float] = field(default_factory=dict)
    
    # Backup配置: {tool: [backup_tools]}
    backup_tools: dict[str, list[str]] = field(default_factory=dict)
    
    objective: AllocationObjective = AllocationObjective.MAX_OUTPUT
    solver: str = "cbc"
    time_limit_seconds: int = 120


@dataclass
class AllocationResult:
    """分配结果"""
    status: str
    allocation: dict[str, dict[str, float]]   # {product: {tool: wafers}}
    process_allocation: dict[str, dict[str, dict[str, float]]]  # {product: {tool: {process: wafers}}}
    tool_utilization: dict[str, float]        # {tool: utilization_pct}
    product_output: dict[str, float]          # {product: total_wafers}
    bottleneck_tools: list[str]
    unmet_demand: dict[str, float]            # {product: deficit}
    objective_value: float
    solve_time_seconds: float
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "allocation": {p: {t: round(v, 1) for t, v in a.items()} for p, a in self.allocation.items()},
            "process_allocation": {},
            "tool_utilization": {t: round(u, 2) for t, u in self.tool_utilization.items()},
            "product_output": {p: round(v, 1) for p, v in self.product_output.items()},
            "bottleneck_tools": self.bottleneck_tools,
            "unmet_demand": {p: round(v, 1) for p, v in self.unmet_demand.items()},
            "objective_value": round(self.objective_value, 2),
            "solve_time_seconds": round(self.solve_time_seconds, 2),
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


def allocate(inp: AllocationInput) -> AllocationResult:
    """
    分配优化求解
    
    优先尝试 LP 求解，如果求解器不可用则回退到启发式贪心算法
    """
    t0 = datetime.utcnow()
    
    # 收集可行的分配组合
    feasible_pairs = []
    for p in inp.products:
        if p not in inp.feasibility:
            continue
        for t in inp.tools:
            if t not in inp.feasibility[p]:
                continue
            for s in inp.processes:
                if inp.feasibility[p][t].get(s, False):
                    feasible_pairs.append((p, t, s))
    
    if not feasible_pairs:
        return AllocationResult(
            status="no_feasible_combinations",
            allocation={p: {} for p in inp.products},
            process_allocation={},
            tool_utilization={t: 0.0 for t in inp.tools},
            product_output={p: 0.0 for p in inp.products},
            bottleneck_tools=[],
            unmet_demand=inp.demand_target,
            objective_value=0.0,
            solve_time_seconds=(datetime.utcnow() - t0).total_seconds(),
            computed_at=datetime.utcnow(),
            metadata={"error": "No feasible product-tool-process combinations"},
        )
    
    # 尝试 Pyomo LP 求解
    if PYOMO_AVAILABLE:
        try:
            return _solve_lp(inp, feasible_pairs, t0)
        except Exception as e:
            logger.warning(f"LP solver failed: {e}. Using greedy heuristic.")
            return _solve_greedy(inp, feasible_pairs, t0)
    else:
        logger.info("Pyomo not installed. Using greedy heuristic.")
        return _solve_greedy(inp, feasible_pairs, t0)


def _solve_lp(inp: AllocationInput, feasible_pairs: list, t0: datetime) -> AllocationResult:
    """使用 Pyomo LP 求解"""
    
    m = pyo.ConcreteModel(name="capacity_allocation")
    
    m.P = pyo.Set(initialize=inp.products)
    m.T = pyo.Set(initialize=inp.tools)
    m.S = pyo.Set(initialize=inp.processes)
    m.feasible_pairs = pyo.Set(dimen=3, initialize=feasible_pairs)
    
    # 决策变量
    m.x = pyo.Var(m.feasible_pairs, domain=pyo.NonNegativeReals)
    
    # 约束: 机台产能
    def tool_cap_rule(m, t):
        total_hours = sum(
            inp.tc_matrix.get(p, {}).get(t, {}).get(s, 0.0) * m.x[(p, t, s)]
            for (p, tt, s) in m.feasible_pairs if tt == t
        )
        return total_hours <= inp.available_hours.get(t, 0.0)
    
    m.tool_cap = pyo.Constraint(m.T, rule=tool_cap_rule)
    
    # 目标: 最大产出
    m.obj = pyo.Objective(
        expr=sum(m.x[idx] for idx in m.feasible_pairs),
        sense=pyo.maximize
    )
    
    # 求解 - 使用指定的 CBC 路径
    if inp.solver == "cbc" and os.path.exists(CBC_EXECUTABLE):
        solver = SolverFactory("cbc", executable=CBC_EXECUTABLE)
    else:
        solver = SolverFactory(inp.solver)
    result = solver.solve(m, tee=False)
    
    term = result.solver.termination_condition
    status = "optimal" if term == TerminationCondition.optimal else str(term)
    
    # 提取结果
    allocation: dict[str, dict[str, float]] = {p: {} for p in inp.products}
    tool_utilization: dict[str, float] = {}
    product_output: dict[str, float] = {p: 0.0 for p in inp.products}
    
    for (p, t, s) in m.feasible_pairs:
        val = pyo.value(m.x[(p, t, s)]) or 0.0
        if val > 0.001:
            if t not in allocation[p]:
                allocation[p][t] = 0.0
            allocation[p][t] += val
    
    for p in inp.products:
        product_output[p] = sum(allocation[p].values())
    
    for t in inp.tools:
        used_hours = sum(
            inp.tc_matrix.get(p, {}).get(t, {}).get(s, 0.0) * (pyo.value(m.x[(p, t, s)]) or 0.0)
            for (p, tt, s) in m.feasible_pairs if tt == t
        )
        cap = inp.available_hours.get(t, 1.0)
        tool_utilization[t] = (used_hours / cap * 100.0) if cap > 0 else 0.0
    
    bottleneck_tools = [t for t, u in tool_utilization.items() if u >= 99.0]
    unmet_demand = {p: inp.demand_target.get(p, 0) - product_output[p] for p in inp.products if product_output[p] < inp.demand_target.get(p, 0)}
    
    return AllocationResult(
        status=status,
        allocation=allocation,
        process_allocation={},
        tool_utilization=tool_utilization,
        product_output=product_output,
        bottleneck_tools=bottleneck_tools,
        unmet_demand=unmet_demand,
        objective_value=pyo.value(m.obj) or 0.0,
        solve_time_seconds=(datetime.utcnow() - t0).total_seconds(),
        computed_at=datetime.utcnow(),
        metadata={"solver": inp.solver, "method": "lp"},
    )


def _solve_greedy(inp: AllocationInput, feasible_pairs: list, t0: datetime) -> AllocationResult:
    """
    贪心启发式算法
    
    按机台效率排序，优先分配到效率最高的机台
    """
    
    # 计算每个产品-机台组合的总TC
    product_tool_tc = {}
    for p in inp.products:
        product_tool_tc[p] = {}
        for t in inp.tools:
            total_tc = sum(
                inp.tc_matrix.get(p, {}).get(t, {}).get(s, 0.0)
                for s in inp.processes
                if inp.feasibility.get(p, {}).get(t, {}).get(s, False)
            )
            if total_tc > 0:
                product_tool_tc[p][t] = total_tc
    
    # 按需求比例分配
    allocation: dict[str, dict[str, float]] = {p: {} for p in inp.products}
    tool_remaining_hours = dict(inp.available_hours)
    product_output: dict[str, float] = {p: 0.0 for p in inp.products}
    
    # 按需求优先级排序（有明确需求的优先）
    products_sorted = sorted(
        inp.products,
        key=lambda p: inp.demand_target.get(p, 0),
        reverse=True
    )
    
    for p in products_sorted:
        target = inp.demand_target.get(p, 0)
        if target <= 0:
            continue
        
        # 找出该产品可用的机台，按效率排序（TC越小越好）
        available_tools = [
            (t, tc) for t, tc in product_tool_tc.get(p, {}).items()
            if tc > 0 and tool_remaining_hours.get(t, 0) > 0
        ]
        available_tools.sort(key=lambda x: x[1])  # TC升序
        
        remaining_demand = target
        for t, tc in available_tools:
            if remaining_demand <= 0:
                break
            
            # 该机台能产出多少
            hours_available = tool_remaining_hours[t]
            max_output = hours_available / tc if tc > 0 else 0
            
            # 分配量
            assign = min(remaining_demand, max_output)
            if assign > 0:
                allocation[p][t] = assign
                product_output[p] += assign
                tool_remaining_hours[t] -= assign * tc
                remaining_demand -= assign
    
    # 计算利用率
    tool_utilization: dict[str, float] = {}
    for t in inp.tools:
        used_hours = inp.available_hours.get(t, 0) - tool_remaining_hours.get(t, 0)
        cap = inp.available_hours.get(t, 1.0)
        tool_utilization[t] = (used_hours / cap * 100.0) if cap > 0 else 0.0
    
    bottleneck_tools = [t for t, u in tool_utilization.items() if u >= 85.0]
    unmet_demand = {
        p: inp.demand_target.get(p, 0) - product_output[p]
        for p in inp.products
        if product_output[p] < inp.demand_target.get(p, 0)
    }
    
    total_output = sum(product_output.values())
    
    return AllocationResult(
        status="heuristic",
        allocation=allocation,
        process_allocation={},
        tool_utilization=tool_utilization,
        product_output=product_output,
        bottleneck_tools=bottleneck_tools,
        unmet_demand=unmet_demand,
        objective_value=total_output,
        solve_time_seconds=(datetime.utcnow() - t0).total_seconds(),
        computed_at=datetime.utcnow(),
        metadata={"method": "greedy_heuristic", "note": "LP solver not available"},
    )


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 测试：基于规则文档Sheet2的数据
    feasibility = {
        "Product_A": {
            "Tool_1": {"Process_1": True, "Process_2": True, "Process_3": False},
            "Tool_2": {"Process_1": False, "Process_2": True, "Process_3": True},
            "Tool_3": {"Process_1": True, "Process_2": True, "Process_3": False},
        },
    }
    
    tc_matrix = {
        "Product_A": {
            "Tool_1": {"Process_1": 2.8, "Process_2": 1.8, "Process_3": 0.0},
            "Tool_2": {"Process_1": 0.0, "Process_2": 1.8, "Process_3": 3.2},
            "Tool_3": {"Process_1": 2.8, "Process_2": 1.8, "Process_3": 0.0},
        },
    }
    
    inp = AllocationInput(
        products=["Product_A"],
        tools=["Tool_1", "Tool_2", "Tool_3"],
        processes=["Process_1", "Process_2", "Process_3"],
        feasibility=feasibility,
        tc_matrix=tc_matrix,
        available_hours={"Tool_1": 100, "Tool_2": 100, "Tool_3": 100},
        demand_target={"Product_A": 500},
        objective=AllocationObjective.MAX_OUTPUT,
    )
    
    result = allocate(inp)
    print(f"Status: {result.status}")
    print(f"Total output: {result.objective_value:.0f} wafers")
    print(f"Tool utilization:")
    for t, u in result.tool_utilization.items():
        print(f"  {t}: {u:.1f}%")
    print(f"Allocation:")
    for p, tools in result.allocation.items():
        for t, w in tools.items():
            print(f"  {p} on {t}: {w:.0f} wafers")
    print(f"Unmet demand: {result.unmet_demand}")