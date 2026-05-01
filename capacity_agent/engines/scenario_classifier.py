"""
Scenario Classifier
===================

基于产能规则文档的情景分类判断模块

情景定义:
  情景1: 单台/相同Path → 标准产能计算
  情景2: 多台/相同Path/有Backup → 穷举+约束+分配
  情景3: 多台/不同Path/有Backup → 穷举+约束+分配  
  情景4: 多台/不同Path/无Backup → 穷举+约束+分配

判断逻辑:
  - multi_product: 是否多产品
  - multi_machine: 是否多机台
  - same_path: 是否相同Path
  - has_backup: 是否有Backup机台
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScenarioType(str, Enum):
    """产能计算情景类型"""
    SCENARIO_1_SIMPLE = "scenario_1"       # 标准产能计算
    SCENARIO_2_SAME_PATH_BACKUP = "scenario_2"  # 穷举+约束+分配
    SCENARIO_3_DIFF_PATH_BACKUP = "scenario_3"  # 穷举+约束+分配
    SCENARIO_4_DIFF_PATH_NO_BACKUP = "scenario_4"  # 穷举+约束+分配
    FULL_FLEXIBLE = "full_flexible"  # 全适（所有机台可运行所有制程）


@dataclass
class ScenarioInput:
    """情景判断输入"""
    products: list[str]                      # 产品列表
    tool_groups: list[str]                   # 机台组列表（或机台列表）
    process_steps: list[str]                 # 制程列表
    
    # 产品-机台-制程可行性矩阵
    # {product_id: {tool_id: {process_id: bool}}}
    feasibility_matrix: dict[str, dict[str, dict[str, bool]]] = field(default_factory=dict)
    
    # TC矩阵（单位时间）
    # {product_id: {tool_id: {process_id: float}}}  hours/wafer
    tc_matrix: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    
    # Path配置
    # {product_id: {path_id: float}}  各path的占比
    path_mix: dict[str, dict[str, float]] | None = None
    
    # Backup机台配置
    # {tool_id: [backup_tool_ids]}
    backup_tools: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    """情景判断结果"""
    scenario_type: ScenarioType
    description: str
    algorithm: str                          # "standard" | "allocation_model"
    multi_product: bool
    multi_machine: bool
    same_path: bool
    has_backup: bool
    constraints: dict[str, Any]             # 额外约束信息
    recommended_solver: str                 # 推荐求解器


def classify_scenario(inp: ScenarioInput) -> ScenarioResult:
    """
    核心情景分类逻辑
    
    判断流程:
    1. multi_product = len(products) > 1
    2. multi_machine = len(tool_groups) > 1
    3. same_path = 检查是否所有产品只有单一path 或 path配置相同
    4. has_backup = 检查backup_tools是否非空
    """
    
    # 1. 判断多产品
    multi_product = len(inp.products) > 1
    
    # 2. 判断多机台
    multi_machine = len(inp.tool_groups) > 1
    
    # 3. 判断是否相同Path
    same_path = True
    if inp.path_mix:
        # 检查每个产品的path配置是否相同
        path_configs = []
        for product in inp.products:
            if product in inp.path_mix:
                paths = inp.path_mix[product]
                path_configs.append(set(paths.keys()))
            else:
                path_configs.append({"default"})
        
        # 如果path配置不完全相同，则为不同Path
        if len(set(tuple(sorted(p)) for p in path_configs)) > 1:
            same_path = False
        # 如果有多个path但配置相同，仍视为相同Path
        elif any(len(p) > 1 for p in path_configs):
            # 多path但相同配置，视为相同Path
            pass
    
    # 4. 判断是否有Backup
    has_backup = bool(inp.backup_tools)
    
    # 5. 判断是否全适场景
    full_flexible = False
    if inp.feasibility_matrix:
        # 检查是否所有产品可在所有机台运行所有制程
        all_tools_can_run_all_processes = True
        for product in inp.products:
            if product not in inp.feasibility_matrix:
                continue
            for tool in inp.tool_groups:
                if tool not in inp.feasibility_matrix[product]:
                    all_tools_can_run_all_processes = False
                    break
                for process in inp.process_steps:
                    if not inp.feasibility_matrix[product][tool].get(process, False):
                        all_tools_can_run_all_processes = False
                        break
                if not all_tools_can_run_all_processes:
                    break
            if not all_tools_can_run_all_processes:
                break
        
        if all_tools_can_run_all_processes:
            full_flexible = True
    
    # 6. 情景分类
    if full_flexible:
        scenario_type = ScenarioType.FULL_FLEXIBLE
        description = "全适场景：所有产品可在所有机台运行所有制程，无约束"
        algorithm = "standard"
        recommended_solver = "rccp"
    elif not multi_machine:
        # 单机台：情景1
        scenario_type = ScenarioType.SCENARIO_1_SIMPLE
        description = "情景1：单机台/相同Path → 标准产能计算"
        algorithm = "standard"
        recommended_solver = "rccp"
    elif same_path and not has_backup:
        # 多机台/相同Path/无Backup：仍是情景1
        scenario_type = ScenarioType.SCENARIO_1_SIMPLE
        description = "情景1扩展：多机台/相同Path/无Backup → 标准产能计算"
        algorithm = "standard"
        recommended_solver = "rccp"
    elif same_path and has_backup:
        # 情景2：多机台/相同Path/有Backup
        scenario_type = ScenarioType.SCENARIO_2_SAME_PATH_BACKUP
        description = "情景2：多机台/相同Path/有Backup → 穷举+约束+分配"
        algorithm = "allocation_model"
        recommended_solver = "allocation_lp"
    elif not same_path and has_backup:
        # 情景3：多机台/不同Path/有Backup
        scenario_type = ScenarioType.SCENARIO_3_DIFF_PATH_BACKUP
        description = "情景3：多机台/不同Path/有Backup → 穷举+约束+分配"
        algorithm = "allocation_model"
        recommended_solver = "allocation_lp"
    else:
        # 情景4：多机台/不同Path/无Backup
        scenario_type = ScenarioType.SCENARIO_4_DIFF_PATH_NO_BACKUP
        description = "情景4：多机台/不同Path/无Backup → 窃举+约束+分配"
        algorithm = "allocation_model"
        recommended_solver = "allocation_lp"
    
    return ScenarioResult(
        scenario_type=scenario_type,
        description=description,
        algorithm=algorithm,
        multi_product=multi_product,
        multi_machine=multi_machine,
        same_path=same_path,
        has_backup=has_backup,
        constraints={
            "feasibility_matrix_summary": _summarize_feasibility(inp.feasibility_matrix),
            "backup_summary": inp.backup_tools,
        },
        recommended_solver=recommended_solver,
    )


def _summarize_feasibility(matrix: dict) -> dict:
    """汇总可行性矩阵的约束数量"""
    if not matrix:
        return {"status": "empty"}
    
    total = 0
    feasible = 0
    for product, tools in matrix.items():
        for tool, processes in tools.items():
            for process, can_run in processes.items():
                total += 1
                if can_run:
                    feasible += 1
    
    return {
        "total_combinations": total,
        "feasible_combinations": feasible,
        "feasibility_rate": feasible / total if total > 0 else 0,
    }


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 测试情景1
    inp1 = ScenarioInput(
        products=["Product_A"],
        tool_groups=["Tool_1"],
        process_steps=["Step_1", "Step_2"],
    )
    result1 = classify_scenario(inp1)
    print(f"Test 1: {result1.scenario_type.value} - {result1.description}")
    
    # 测试情景2
    inp2 = ScenarioInput(
        products=["Product_A", "Product_B"],
        tool_groups=["Tool_1", "Tool_2", "Tool_3"],
        process_steps=["Step_1", "Step_2", "Step_3"],
        backup_tools={"Tool_1": ["Tool_2"]},
    )
    result2 = classify_scenario(inp2)
    print(f"Test 2: {result2.scenario_type.value} - {result2.description}")
    
    # 测试情景3
    inp3 = ScenarioInput(
        products=["Product_A", "Product_B"],
        tool_groups=["Tool_1", "Tool_2", "Tool_3"],
        process_steps=["Step_1", "Step_2", "Step_3"],
        path_mix={
            "Product_A": {"Path_1": 0.6, "Path_2": 0.4},
            "Product_B": {"Path_1": 1.0},
        },
        backup_tools={"Tool_1": ["Tool_2"]},
    )
    result3 = classify_scenario(inp3)
    print(f"Test 3: {result3.scenario_type.value} - {result3.description}")
    
    # 测试全适场景
    inp4 = ScenarioInput(
        products=["Product_A", "Product_B"],
        tool_groups=["Tool_1", "Tool_2", "Tool_3"],
        process_steps=["Step_1", "Step_2", "Step_3"],
        feasibility_matrix={
            "Product_A": {
                "Tool_1": {"Step_1": True, "Step_2": True, "Step_3": True},
                "Tool_2": {"Step_1": True, "Step_2": True, "Step_3": True},
                "Tool_3": {"Step_1": True, "Step_2": True, "Step_3": True},
            },
            "Product_B": {
                "Tool_1": {"Step_1": True, "Step_2": True, "Step_3": True},
                "Tool_2": {"Step_1": True, "Step_2": True, "Step_3": True},
                "Tool_3": {"Step_1": True, "Step_2": True, "Step_3": True},
            },
        },
    )
    result4 = classify_scenario(inp4)
    print(f"Test 4: {result4.scenario_type.value} - {result4.description}")