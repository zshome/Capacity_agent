"""
Input Planner (Output 视角) - 从产出目标反推投入计划

核心逻辑:
  为了在 Week T 产出 X 片，需要在 Week T-CT 投入多少？

公式:
  投入时间 = 产出时间 - CT
  投入量 = 产出目标 - WIP 预计产出

适用场景: 存储芯片制造（CT = 90-120天）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Data classes
# ============================================================
@dataclass
class InputPlannerOutputInput:
    """投入规划输入（Output 视角）"""
    
    # 产出目标（未来 N 周）
    output_targets: dict[str, dict[str, float]]  # {week: {product: target_wafers}}
    
    # WIP 产出预测
    wip_output_predictions: dict[str, dict[str, float]]  # {week: {product: predicted_wafers}}
    
    # Cycle Time
    cycle_time_days: dict[str, float]            # {product: avg_ct}
    
    # 当前周
    current_week: str
    
    # 规划周数
    planning_weeks: int = 12
    
    # 约束
    min_input_per_week: dict[str, float] | None = None  # {product: min}
    max_input_per_week: dict[str, float] | None = None  # {product: max}
    
    # 参数
    yield_factor: dict[str, float] | None = None  # {product: yield} 考虑良率


@dataclass
class WeeklyInputPlan:
    """周投入计划"""
    input_week: str
    product_id: str
    planned_wafers: float
    target_output_week: str
    target_output_wafers: float
    wip_contribution: float                      # 来自现有 WIP 的产出
    gap_to_fill: float                           # 需要新投入填补的缺口
    ct_weeks: int                                # Cycle Time（周数）
    status: str                                  # PLANNED/ALREADY_PASSED/TOO_LATE
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "input_week": self.input_week,
            "product_id": self.product_id,
            "planned_wafers": round(self.planned_wafers, 0),
            "target_output_week": self.target_output_week,
            "target_output_wafers": round(self.target_output_wafers, 0),
            "wip_contribution": round(self.wip_contribution, 0),
            "gap_to_fill": round(self.gap_to_fill, 0),
            "ct_weeks": self.ct_weeks,
            "status": self.status,
        }


@dataclass
class InputPlannerOutputResult:
    """投入规划结果"""
    weekly_plans: list[WeeklyInputPlan]
    total_input_needed: dict[str, float]         # {product: total_input}
    plans_by_week: dict[str, dict[str, float]]   # {input_week: {product: wafers}}
    plans_by_product: dict[str, dict[str, float]]  # {product: {input_week: wafers}}
    
    # 不可达成分析
    unachievable_targets: list[dict[str, Any]]   # 无法达成的原因
    late_targets: list[dict[str, Any]]           # 投入时间已过
    
    # 汇总
    total_planned_wafers: float
    feasible: bool
    
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "weekly_plans": [p.to_dict() for p in self.weekly_plans],
            "total_input_needed": {k: round(v, 0) for k, v in self.total_input_needed.items()},
            "plans_by_week": self.plans_by_week,
            "plans_by_product": self.plans_by_product,
            "unachievable_targets": self.unachievable_targets,
            "late_targets": self.late_targets,
            "total_planned_wafers": round(self.total_planned_wafers, 0),
            "feasible": self.feasible,
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


def parse_week(week_str: str) -> tuple[int, int]:
    """解析周字符串，返回 (年份, 周数)"""
    try:
        year, week_num = week_str.split('-W')
        return int(year), int(week_num)
    except (ValueError, AttributeError):
        # 使用默认值
        now = datetime.now()
        return now.year, now.isocalendar()[1]


def format_week(year: int, week_num: int) -> str:
    """格式化周字符串"""
    # 处理跨年
    if week_num > 52:
        week_num -= 52
        year += 1
    elif week_num < 1:
        week_num += 52
        year -= 1
    
    return f"{year}-W{week_num}"


def compute_input_week(output_week: str, ct_days: float) -> str:
    """
    计算投入周（产出周 - CT）
    
    公式: input_week = output_week - CT/7
    """
    
    year, week_num = parse_week(output_week)
    
    ct_weeks = int(ct_days / 7)
    
    input_week_num = week_num - ct_weeks
    
    return format_week(year, input_week_num)


def compute_input_plan_from_output_targets(
    inp: InputPlannerOutputInput
) -> InputPlannerOutputResult:
    """
    从产出目标反推投入计划
    
    公式:
      投入时间 = 产出时间 - CT
      投入量 = 产出目标 - WIP 预计产出
    
    示例:
      产出目标 Week 20: 28nm_DRAM 500 片
      WIP 预计 Week 20 产出: 300 片
      CT = 100 天 ≈ 14 周
      
      投入时间: Week 20 - 14 = Week 6
      投入量: 500 - 300 = 200 片
    """
    
    t0 = datetime.utcnow()
    
    weekly_plans: list[WeeklyInputPlan] = []
    total_input: dict[str, float] = {}
    plans_by_week: dict[str, dict[str, float]] = {}
    plans_by_product: dict[str, dict[str, float]] = {}
    
    unachievable_targets: list[dict[str, Any]] = []
    late_targets: list[dict[str, Any]] = []
    
    current_year, current_week_num = parse_week(inp.current_week)
    
    # 遍历未来各周的产出目标
    for output_week, product_targets in inp.output_targets.items():
        
        out_year, out_week_num = parse_week(output_week)
        
        for product_id, target_wafers in product_targets.items():
            
            # 获取 CT
            ct_days = inp.cycle_time_days.get(product_id, 100.0)
            ct_weeks = int(ct_days / 7)
            
            # 获取 WIP 预计产出
            wip_predicted = inp.wip_output_predictions.get(output_week, {}).get(product_id, 0.0)
            
            # 产出缺口
            gap = target_wafers - wip_predicted
            
            if gap <= 0:
                # WIP 预计产出已足够，无需新投入
                weekly_plans.append(WeeklyInputPlan(
                    input_week="NO_NEED",
                    product_id=product_id,
                    planned_wafers=0.0,
                    target_output_week=output_week,
                    target_output_wafers=target_wafers,
                    wip_contribution=wip_predicted,
                    gap_to_fill=0.0,
                    ct_weeks=ct_weeks,
                    status="WIP_SUFFICIENT",
                ))
                continue
            
            # 计算投入时间
            input_week = compute_input_week(output_week, ct_days)
            input_year, input_week_num = parse_week(input_week)
            
            # 考虑良率（投入量需更多）
            planned_wafers = gap
            if inp.yield_factor:
                yield_rate = inp.yield_factor.get(product_id, 1.0)
                if yield_rate < 1.0:
                    planned_wafers = gap / yield_rate
            
            # 检查投入时间是否已过
            if (input_year < current_year) or (input_year == current_year and input_week_num < current_week_num):
                # 投入时间已过，无法达成
                late_targets.append({
                    "product_id": product_id,
                    "target_output_week": output_week,
                    "target_wafers": target_wafers,
                    "input_week_should_be": input_week,
                    "gap": gap,
                    "reason": "投入时间已过，无法为该产出目标投入",
                })
                
                weekly_plans.append(WeeklyInputPlan(
                    input_week=input_week,
                    product_id=product_id,
                    planned_wafers=0.0,
                    target_output_week=output_week,
                    target_output_wafers=target_wafers,
                    wip_contribution=wip_predicted,
                    gap_to_fill=gap,
                    ct_weeks=ct_weeks,
                    status="ALREADY_PASSED",
                ))
                continue
            
            # 检查是否在规划范围内
            if (input_year > current_year) or (input_year == current_year and input_week_num > current_week_num + inp.planning_weeks):
                # 超出规划范围
                unachievable_targets.append({
                    "product_id": product_id,
                    "target_output_week": output_week,
                    "target_wafers": target_wafers,
                    "input_week": input_week,
                    "reason": "投入时间超出规划范围",
                })
                continue
            
            # 约束检查
            if inp.max_input_per_week:
                max_input = inp.max_input_per_week.get(product_id, float('inf'))
                if planned_wafers > max_input:
                    planned_wafers = max_input
            
            # 汇总
            if product_id not in total_input:
                total_input[product_id] = 0.0
            total_input[product_id] += planned_wafers
            
            if input_week not in plans_by_week:
                plans_by_week[input_week] = {}
            if product_id not in plans_by_week[input_week]:
                plans_by_week[input_week][product_id] = 0.0
            plans_by_week[input_week][product_id] += planned_wafers
            
            if product_id not in plans_by_product:
                plans_by_product[product_id] = {}
            if input_week not in plans_by_product[product_id]:
                plans_by_product[product_id][input_week] = 0.0
            plans_by_product[product_id][input_week] += planned_wafers
            
            weekly_plans.append(WeeklyInputPlan(
                input_week=input_week,
                product_id=product_id,
                planned_wafers=planned_wafers,
                target_output_week=output_week,
                target_output_wafers=target_wafers,
                wip_contribution=wip_predicted,
                gap_to_fill=gap,
                ct_weeks=ct_weeks,
                status="PLANNED",
            ))
    
    # 计算总投入
    total_planned_wafers = sum(total_input.values())
    
    # 可行性判断
    feasible = len(late_targets) == 0 and len(unachievable_targets) == 0
    
    return InputPlannerOutputResult(
        weekly_plans=weekly_plans,
        total_input_needed=total_input,
        plans_by_week=plans_by_week,
        plans_by_product=plans_by_product,
        unachievable_targets=unachievable_targets,
        late_targets=late_targets,
        total_planned_wafers=total_planned_wafers,
        feasible=feasible,
        computed_at=datetime.utcnow(),
        metadata={
            "current_week": inp.current_week,
            "planning_weeks": inp.planning_weeks,
            "n_output_targets": len(inp.output_targets),
            "n_late_targets": len(late_targets),
            "compute_time_seconds": (datetime.utcnow() - t0).total_seconds(),
        },
    )


def generate_input_schedule_summary(
    result: InputPlannerOutputResult
) -> dict[str, Any]:
    """
    生成交替投入计划摘要
    
    输出:
      - 按投入周排序的计划
      - 各产品投入节奏
      - 风险提示
    """
    
    summary = {
        "schedule": [],
        "product_rhythm": {},
        "risk_notes": [],
    }
    
    # 按投入周排序
    planned_items = [p for p in result.weekly_plans if p.status == "PLANNED"]
    planned_items.sort(key=lambda x: (parse_week(x.input_week), x.product_id))
    
    for plan in planned_items:
        summary["schedule"].append({
            "投入周": plan.input_week,
            "产品": plan.product_id,
            "投入量": plan.planned_wafers,
            "目标产出周": plan.target_output_week,
            "目标产出量": plan.target_output_wafers,
            "CT周数": plan.ct_weeks,
        })
    
    # 各产品投入节奏
    for product, weeks in result.plans_by_product.items():
        rhythm = []
        for week, wafers in sorted(weeks.items(), key=lambda x: parse_week(x[0])):
            rhythm.append({"week": week, "wafers": wafers})
        summary["product_rhythm"][product] = rhythm
    
    # 风险提示
    for late in result.late_targets:
        summary["risk_notes"].append(
            f"⚠️ {late['product_id']}: {late['target_output_week']} 产出目标 {late['target_wafers']} 片，"
            f"应于 {late['input_week_should_be']} 投入（已过），缺口 {late['gap']} 片无法弥补"
        )
    
    for unachievable in result.unachievable_targets:
        summary["risk_notes"].append(
            f"⚠️ {unachievable['product_id']}: {unachievable['target_output_week']} 产出目标 "
            f"超出规划范围（投入周 {unachievable['input_week']}）"
        )
    
    return summary


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 模拟产出目标
    output_targets = {
        "2026-W20": {"28nm_DRAM": 500, "64L_NAND": 300},
        "2026-W21": {"28nm_DRAM": 520, "64L_NAND": 320},
        "2026-W22": {"28nm_DRAM": 550, "64L_NAND": 350},
        "2026-W23": {"28nm_DRAM": 600, "64L_NAND": 400},
    }
    
    # WIP 产出预测
    wip_predictions = {
        "2026-W20": {"28nm_DRAM": 300, "64L_NAND": 200},
        "2026-W21": {"28nm_DRAM": 350, "64L_NAND": 250},
        "2026-W22": {"28nm_DRAM": 400, "64L_NAND": 300},
        "2026-W23": {"28nm_DRAM": 450, "64L_NAND": 350},
    }
    
    # Cycle Time
    cycle_time = {"28nm_DRAM": 100, "64L_NAND": 90}
    
    inp = InputPlannerOutputInput(
        output_targets=output_targets,
        wip_output_predictions=wip_predictions,
        cycle_time_days=cycle_time,
        current_week="2026-W17",
        planning_weeks=12,
        yield_factor={"28nm_DRAM": 0.95, "64L_NAND": 0.90},
    )
    
    result = compute_input_plan_from_output_targets(inp)
    
    print("=== 投入计划（从产出目标反推）===")
    print(f"当前周: {inp.current_week}")
    print(f"总计划投入: {result.total_planned_wafers} 片")
    print(f"可行: {result.feasible}")
    print()
    
    # 生成摘要
    summary = generate_input_schedule_summary(result)
    
    print("=== 投入时间线 ===")
    for item in summary["schedule"]:
        print(f"  {item['投入周']}: {item['产品']} {item['投入量']:.0f} 片 → {item['目标产出周']} 产出 {item['目标产出量']:.0f} 片 (CT {item['CT周数']}周)")
    
    print()
    print("=== 各产品投入节奏 ===")
    for product, rhythm in summary["product_rhythm"].items():
        print(f"{product}:")
        for r in rhythm:
            print(f"  {r['week']}: {r['wafers']:.0f} 片")
    
    if summary["risk_notes"]:
        print()
        print("=== 风险提示 ===")
        for note in summary["risk_notes"]:
            print(f"  {note}")