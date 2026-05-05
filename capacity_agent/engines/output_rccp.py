"""
Output RCCP Engine - 产出视角产能规划

核心逻辑:
  1. 计算本周产能需求 = WIP 后续工序小时（而非投入总工时）
  2. 预测本周产出 = 基于 WIP 位置
  3. 分析产出缺口
  4. 反推投入需求（为未来产出）

与 Input RCCP 的区别:
  Input: demand = Σ 新投入 × 总工时
  Output: demand = Σ WIP后续工序小时 + Σ 新投入本周工序小时

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


def _period_index_from_week_id(week_id: str) -> tuple[str, int]:
    """Return (year, week) for simple ISO week strings like 2026-W17."""
    try:
        year, week = str(week_id).split("-W", 1)
        return year, int(week)
    except (ValueError, TypeError):
        now = datetime.utcnow()
        return str(now.year), int(now.isocalendar()[1])


def _add_weeks(week_id: str, week_offset: int) -> str:
    year, week = _period_index_from_week_id(week_id)
    week += int(week_offset)
    year_num = int(year)
    while week > 52:
        week -= 52
        year_num += 1
    while week < 1:
        week += 52
        year_num -= 1
    return f"{year_num}-W{week:02d}"


def _month_from_week(week_id: str) -> str:
    """Approximate an ISO week bucket to YYYY-MM for R6 monthly planning."""
    year, week = _period_index_from_week_id(week_id)
    # R6 is a monthly commitment view. Use the week-ending day so a week that
    # starts in the prior month but mostly belongs to the target month is not
    # lost from that month's capacity bucket.
    dt = datetime.fromisocalendar(int(year), int(week), 7)
    return dt.strftime("%Y-%m")


def _bucket_key(week_id: str, granularity: str) -> str:
    return _month_from_week(week_id) if granularity == "monthly" else week_id


def _week_start(week_id: str) -> datetime:
    text = str(week_id or "")
    if len(text) == 7 and text[4] == "-":
        year, month = text.split("-", 1)
        return datetime(int(year), int(month), 1)
    year, week = _period_index_from_week_id(week_id)
    return datetime.fromisocalendar(int(year), int(week), 1)


def _bucket_key_from_datetime(dt: datetime, granularity: str) -> str:
    if granularity == "monthly":
        return dt.strftime("%Y-%m")
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _next_bucket_start(dt: datetime, granularity: str) -> datetime:
    if granularity == "monthly":
        if dt.month == 12:
            return datetime(dt.year + 1, 1, 1)
        return datetime(dt.year, dt.month + 1, 1)
    week_start = dt - timedelta(days=dt.weekday())
    return datetime(week_start.year, week_start.month, week_start.day) + timedelta(days=7)


def _lot_start_offset_hours(lot: pd.Series, current_week: str, week_duration_days: float) -> float:
    """Optional WIP queue/hold offset before remaining-route load starts."""
    offset_hours = 0.0
    remaining_wait = pd.to_numeric(pd.Series([lot.get("remaining_wait_hours")]), errors="coerce").iloc[0]
    if pd.notna(remaining_wait) and float(remaining_wait) > 0:
        offset_hours = max(offset_hours, float(remaining_wait))

    release_date = pd.to_datetime(lot.get("hold_release_date"), errors="coerce")
    if pd.notna(release_date):
        release_offset = (release_date.to_pydatetime() - _week_start(current_week)).total_seconds() / 3600.0
        offset_hours = max(offset_hours, release_offset)

    lot_status = str(lot.get("lot_status", "") or "").upper()
    if lot_status in {"HOLD", "ON_HOLD"} and offset_hours <= 0:
        offset_hours = max(week_duration_days, 1.0) * 24.0
    return max(offset_hours, 0.0)


# ============================================================
# Data classes
# ============================================================
@dataclass
class OutputRCCPInput:
    """Output RCCP 输入"""
    
    # 产出目标（核心输入）
    output_target: dict[str, float]              # {product_id: target_wafers} 本周产出目标
    output_target_week: str                      # 规划周（如 "2026-W17")
    
    # WIP 数据
    wip_lot_detail: pd.DataFrame                 # Lot 级 WIP 详情
    # 必须字段: lot_id, product_id, current_step_seq, wafer_count, percent_complete
    
    # 制程路线
    route: pd.DataFrame                          # dim_route
    cycle_time_days: dict[str, float]            # {product_id: avg_ct_days}
    
    # 产能数据
    available_hours: dict[str, float]            # {tool_group_id: weekly_hours}
    oee: dict[str, float] | None = None          # {tool_group_id: oee_factor}
    
    # 新投入计划（可选）
    new_input_plan: dict[str, float] | None = None  # {product_id: wafers} 本周新投入
    
    # 参数
    safety_margin_pct: float = 0.15              # 安全余量
    output_completion_threshold: float = 0.80    # 完成度阈值（>=80%预计本周产出）
    wip_critical_threshold: float = 0.50         # WIP 占产能超过50%预警


@dataclass
class OutputPrediction:
    """产出预测"""
    product_id: str
    predicted_wafers: float                      # 预测产出数
    source_wip_lots: int                         # 来源 Lot 数
    avg_percent_complete: float                 # 平均完成度
    confidence: str                              # HIGH/MEDIUM/LOW
    risk_factors: list[str]                      # 风险因素
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "predicted_wafers": round(self.predicted_wafers, 0),
            "source_wip_lots": self.source_wip_lots,
            "avg_percent_complete": round(self.avg_percent_complete, 2),
            "confidence": self.confidence,
            "risk_factors": self.risk_factors,
        }


@dataclass
class WeeklyCapacityDemand:
    """周产能需求（Output 视角）"""
    tool_group_id: str
    
    # 需求分解（关键区别）
    wip_remaining_hours: float                   # WIP 后续工序小时（核心）
    new_input_hours: float                       # 新投入本周工序小时
    total_demand_hours: float                    # 总需求
    
    # 产品分布
    demand_by_product: dict[str, float]          # {product: hours}
    
    # 产能对比
    available_hours: float
    loading_pct: float
    status: str                                  # healthy/warning/critical/overload
    
    # WIP 详情
    wip_lot_count: int
    avg_wip_wait_hours: float
    
    # 占比
    wip_share_pct: float                         # WIP 占总需求百分比
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_group_id": self.tool_group_id,
            "wip_remaining_hours": round(self.wip_remaining_hours, 2),
            "new_input_hours": round(self.new_input_hours, 2),
            "total_demand_hours": round(self.total_demand_hours, 2),
            "demand_by_product": {k: round(v, 2) for k, v in self.demand_by_product.items()},
            "available_hours": round(self.available_hours, 2),
            "loading_pct": round(self.loading_pct, 2),
            "status": self.status,
            "wip_lot_count": self.wip_lot_count,
            "avg_wip_wait_hours": round(self.avg_wip_wait_hours, 2),
            "wip_share_pct": round(self.wip_share_pct, 2),
        }


@dataclass
class OutputRCCPResult:
    """Output RCCP 输出"""
    
    # 产出预测
    output_predictions: list[OutputPrediction]
    total_predicted_output: float                # 预测总产出
    output_gap: dict[str, float]                 # {product: gap}
    
    # 产能需求（核心输出）
    capacity_demand: list[WeeklyCapacityDemand]
    overall_loading_pct: float
    critical_groups: list[str]
    bottleneck_groups: list[str]
    
    # WIP 占比分析
    overall_wip_share_pct: float                 # 全厂 WIP 占产能百分比
    wip_critical_groups: list[str]               # WIP 高占比机台组
    
    # 投入建议（反推）
    input_recommendations: dict[str, float]      # {product: recommended_input}
    
    # 可行性
    feasible: bool
    risk_summary: list[str]
    
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "output_predictions": [p.to_dict() for p in self.output_predictions],
            "total_predicted_output": round(self.total_predicted_output, 0),
            "output_gap": {k: round(v, 0) for k, v in self.output_gap.items()},
            "capacity_demand": [d.to_dict() for d in self.capacity_demand],
            "overall_loading_pct": round(self.overall_loading_pct, 2),
            "critical_groups": self.critical_groups,
            "bottleneck_groups": self.bottleneck_groups,
            "overall_wip_share_pct": round(self.overall_wip_share_pct, 2),
            "wip_critical_groups": self.wip_critical_groups,
            "input_recommendations": {k: round(v, 0) for k, v in self.input_recommendations.items()},
            "feasible": self.feasible,
            "risk_summary": self.risk_summary,
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


# ============================================================
# Core functions
# ============================================================

def predict_output_from_wip(
    wip_lot_detail: pd.DataFrame,
    output_completion_threshold: float = 0.80
) -> list[OutputPrediction]:
    """
    预测本周产出（基于 WIP 位置）
    
    逻辑:
      1. 找出完成度 >= threshold 的 Lot
      2. 这些 Lot 预计本周完成 → 产出预测
      3. 按 product 分组汇总
    
    示例:
      Lot L001: 28nm_DRAM, 完成度 85%, 25 wafers
      → 预计本周产出 25 片 28nm_DRAM
    """
    
    predictions: list[OutputPrediction] = []
    
    # 检查空数据
    if wip_lot_detail.empty:
        return predictions
    
    # 按 product 分组
    for product_id, wip_group in wip_lot_detail.groupby('product_id'):
        
        # 筛选即将完成的 Lot（完成度 >= threshold）
        near_complete_threshold = output_completion_threshold * 100
        
        near_complete_lots = wip_group[
            wip_group['percent_complete'] >= near_complete_threshold
        ]
        
        if near_complete_lots.empty:
            # 无即将完成的 Lot
            avg_pct = wip_group['percent_complete'].mean() / 100 if 'percent_complete' in wip_group.columns else 0
            predictions.append(OutputPrediction(
                product_id=product_id,
                predicted_wafers=0.0,
                source_wip_lots=0,
                avg_percent_complete=avg_pct,
                confidence="LOW",
                risk_factors=["无即将完成的 WIP"],
            ))
            continue
        
        # 计算预测产出
        predicted_wafers = float(near_complete_lots['wafer_count'].sum())
        
        # 考虑良率（如果有）
        if 'good_wafer_count' in near_complete_lots.columns:
            good_wafers = float(near_complete_lots['good_wafer_count'].sum())
            if good_wafers > 0:
                predicted_wafers = good_wafers
        
        # 风险因素
        risk_factors = []
        
        # Hold Lot
        if 'lot_status' in near_complete_lots.columns:
            hold_lots = near_complete_lots[near_complete_lots['lot_status'] == 'HOLD']
            if len(hold_lots) > 0:
                risk_factors.append(f"{len(hold_lots)} Lot 处于 HOLD 状态")
        
        # 长时间等待
        if 'wait_hours_so_far' in near_complete_lots.columns:
            avg_wait = near_complete_lots['wait_hours_so_far'].mean()
            if avg_wait > 48:
                risk_factors.append(f"平均等待 {avg_wait:.0f}h，可能延误")
        
        # 置信度
        confidence = "HIGH" if len(risk_factors) == 0 else "MEDIUM" if len(risk_factors) == 1 else "LOW"
        
        # 平均完成度
        avg_pct = float(near_complete_lots['percent_complete'].mean()) / 100
        
        predictions.append(OutputPrediction(
            product_id=product_id,
            predicted_wafers=predicted_wafers,
            source_wip_lots=int(len(near_complete_lots)),
            avg_percent_complete=avg_pct,
            confidence=confidence,
            risk_factors=risk_factors,
        ))
    
    return predictions


def compute_wip_remaining_load(
    wip_lot_detail: pd.DataFrame,
    route: pd.DataFrame
) -> dict[str, dict[str, float]]:
    """
    计算 WIP 后续工序负载（Output 视角的核心）
    
    逻辑:
      对于每个 Lot:
        1. 当前工序 = current_step_seq
        2. 后续工序 = step_seq >= current_step_seq
        3. 后续工序小时 = Σ 后续工序 run_time / batch_size × wafer_count
    
    输出:
      {tool_group_id: {product_id: remaining_hours}}
    
    示例:
      Lot L001: 28nm_DRAM, 当前工序 230, 25 wafers
      后续工序:
        - OP235 (刻蚀第10次): 0.5h × 25 = 12.5h → ETCH_01
        - OP240 (沉积第8次): 0.3h × 25 = 7.5h → DEPO_01
      
      结果:
        ETCH_01: {"28nm_DRAM": 12.5h + ...}
        DEPO_01: {"28nm_DRAM": 7.5h + ...}
    """
    
    load_by_tg_product: dict[str, dict[str, float]] = {}
    
    # 检查空数据
    if wip_lot_detail.empty or route.empty:
        return load_by_tg_product
    
    for _, lot in wip_lot_detail.iterrows():
        lot_id = lot.get('lot_id', 'UNKNOWN')
        product_id = lot['product_id']
        current_step = lot['current_step_seq']
        wafer_count = lot['wafer_count']
        
        # 获取该产品的完整制程路线
        product_route = route[
            (route['product_id'] == product_id) &
            (route['step_seq'] >= current_step)  # 后续工序
        ]
        
        if product_route.empty:
            continue
        
        for _, step in product_route.iterrows():
            tg_id = step['tool_group_id']
            
            # 单位小时 = run_time / batch_size
            batch_size = max(step.get('batch_size', 1), 1)
            run_time = step.get('run_time_hr', 0)
            unit_hours = run_time / batch_size
            
            # 该 Lot 在该工序的负载
            lot_hours = unit_hours * wafer_count
            
            # 汇总到 tool_group × product
            if tg_id not in load_by_tg_product:
                load_by_tg_product[tg_id] = {}
            
            if product_id not in load_by_tg_product[tg_id]:
                load_by_tg_product[tg_id][product_id] = 0.0
            
            load_by_tg_product[tg_id][product_id] += lot_hours
    
    return load_by_tg_product


def compute_wip_remaining_load_by_period(
    wip_lot_detail: pd.DataFrame,
    route: pd.DataFrame,
    current_week: str = "2026-W17",
    granularity: str = "weekly",
    week_duration_days: float = 7.0,
) -> dict[str, dict[str, dict[str, float]]]:
    """
    Spread WIP remaining load into future week/month buckets.

    This is still an RCCP approximation, but it prevents a long-CT storage fab lot
    from placing all downstream route hours into the current planning bucket.
    """
    load_by_period: dict[str, dict[str, dict[str, float]]] = {}
    if wip_lot_detail.empty or route.empty:
        return load_by_period

    granularity = "monthly" if granularity == "monthly" else "weekly"

    for _, lot in wip_lot_detail.iterrows():
        product_id = lot["product_id"]
        current_step = lot["current_step_seq"]
        wafer_count = float(lot["wafer_count"])
        product_route = route[
            (route["product_id"] == product_id) &
            (route["step_seq"] >= current_step)
        ].sort_values("step_seq")

        elapsed_hours = 0.0
        anchor = _week_start(current_week) + timedelta(
            hours=_lot_start_offset_hours(lot, current_week, week_duration_days)
        )
        for _, step in product_route.iterrows():
            tg_id = step["tool_group_id"]
            batch_size = max(float(step.get("batch_size", 1) or 1), 1.0)
            visit_count = float(step.get("visit_count", 1) or 1)
            run_time = float(step.get("run_time_hr", 0) or 0)
            step_hours = run_time * visit_count / batch_size * wafer_count
            if step_hours <= 0:
                continue

            # Use the same RCCP elapsed-time approximation as before, but split
            # each step across calendar bucket boundaries instead of assigning
            # the whole operation to a single midpoint month/week.
            step_start = anchor + timedelta(hours=elapsed_hours)
            step_end = anchor + timedelta(hours=elapsed_hours + step_hours)
            cursor = step_start
            while cursor < step_end:
                boundary = min(_next_bucket_start(cursor, granularity), step_end)
                segment_ratio = (boundary - cursor).total_seconds() / max((step_end - step_start).total_seconds(), 1.0)
                segment_hours = step_hours * segment_ratio
                period = _bucket_key_from_datetime(cursor, granularity)

                load_by_period.setdefault(period, {}).setdefault(tg_id, {}).setdefault(product_id, 0.0)
                load_by_period[period][tg_id][product_id] += segment_hours
                cursor = boundary
            elapsed_hours += step_hours

    return load_by_period


def collapse_wip_period_load(
    period_load: dict[str, dict[str, dict[str, float]]],
    target_period: str,
) -> dict[str, dict[str, float]]:
    """Return {tool_group: {product: hours}} for one planning bucket."""
    return period_load.get(target_period, {})


def compute_new_input_load_distribution(
    new_input_plan: dict[str, float],
    route: pd.DataFrame,
    cycle_time_days: dict[str, float],
    week_duration_days: float = 7.0
) -> dict[str, dict[str, float]]:
    """
    计算新投入的本周工序负载（按工序时间分布）
    
    逻辑:
      新投入的 Lot 在本周只会经过前 N 步工序（按 CT 分布）
      
      本周工序 = 前 N 步，N = 7 / CT × 总步数
      
    示例:
      28nm_DRAM: CT = 100天, 总步数 300
      本周步数 = 7 / 100 × 300 = 21 步
      
      投入 100 片:
        - 前 21 步工序在本周执行
        - 计算这 21 步在各机台组的小时
    """
    
    load_by_tg_product: dict[str, dict[str, float]] = {}
    
    if not new_input_plan or route.empty:
        return load_by_tg_product
    
    for product_id, wafer_count in new_input_plan.items():
        
        # 获取该产品的制程路线
        product_route = route[route['product_id'] == product_id].sort_values('step_seq')
        
        if product_route.empty:
            continue
        
        total_steps = len(product_route)
        
        # 计算本周执行的步数
        ct = cycle_time_days.get(product_id, 100.0)
        steps_per_week = int(total_steps * week_duration_days / max(ct, 1))
        
        # 取前 N 步（本周工序）
        weekly_steps = product_route.head(steps_per_week)
        
        for _, step in weekly_steps.iterrows():
            tg_id = step['tool_group_id']
            batch_size = max(step.get('batch_size', 1), 1)
            run_time = step.get('run_time_hr', 0)
            unit_hours = run_time / batch_size
            lot_hours = unit_hours * wafer_count
            
            if tg_id not in load_by_tg_product:
                load_by_tg_product[tg_id] = {}
            
            if product_id not in load_by_tg_product[tg_id]:
                load_by_tg_product[tg_id][product_id] = 0.0
            
            load_by_tg_product[tg_id][product_id] += lot_hours
    
    return load_by_tg_product


def classify_status(loading_pct: float) -> str:
    """根据 loading 率分类状态"""
    if loading_pct >= 100:
        return "overload"
    elif loading_pct >= 85:
        return "critical"
    elif loading_pct >= 75:
        return "warning"
    else:
        return "healthy"


def merge_capacity_demand(
    wip_load: dict[str, dict[str, float]],
    new_input_load: dict[str, dict[str, float]],
    available_hours: dict[str, float],
    wip_lot_count: dict[str, int] | None = None,
    wip_avg_wait: dict[str, float] | None = None
) -> list[WeeklyCapacityDemand]:
    """
    合并 WIP 负载 + 新投入负载 → 周产能需求
    """
    
    all_tg_ids = set(wip_load.keys()) | set(new_input_load.keys()) | set(available_hours.keys())
    
    demand_list: list[WeeklyCapacityDemand] = []
    
    for tg_id in all_tg_ids:
        # WIP 负载
        wip_hours_by_product = wip_load.get(tg_id, {})
        wip_total = sum(wip_hours_by_product.values())
        
        # 新投入负载
        new_hours_by_product = new_input_load.get(tg_id, {})
        new_total = sum(new_hours_by_product.values())
        
        # 合并产品分布
        demand_by_product: dict[str, float] = {}
        for product, hours in wip_hours_by_product.items():
            demand_by_product[product] = demand_by_product.get(product, 0) + hours
        for product, hours in new_hours_by_product.items():
            demand_by_product[product] = demand_by_product.get(product, 0) + hours
        
        # 总需求
        total_demand = wip_total + new_total
        
        # 可用产能
        h = available_hours.get(tg_id, 0)
        
        # 利用率
        loading_pct = (total_demand / h * 100) if h > 0 else 0
        
        # WIP 占比
        wip_share = (wip_total / total_demand * 100) if total_demand > 0 else 0
        
        # 状态判断
        status = classify_status(loading_pct)
        
        # WIP 详情
        lot_count = wip_lot_count.get(tg_id, 0) if wip_lot_count else 0
        avg_wait = wip_avg_wait.get(tg_id, 0.0) if wip_avg_wait else 0.0
        
        demand_list.append(WeeklyCapacityDemand(
            tool_group_id=tg_id,
            wip_remaining_hours=float(wip_total),
            new_input_hours=float(new_total),
            total_demand_hours=float(total_demand),
            demand_by_product={k: float(v) for k, v in demand_by_product.items()},
            available_hours=float(h),
            loading_pct=float(loading_pct),
            status=status,
            wip_lot_count=int(lot_count),
            avg_wip_wait_hours=float(avg_wait),
            wip_share_pct=float(wip_share),
        ))
    
    # 排序（按负载降序）
    demand_list.sort(key=lambda x: x.loading_pct, reverse=True)
    
    return demand_list


def compute_input_recommendations(
    output_target: dict[str, float],
    predicted_output: list[OutputPrediction],
    cycle_time_days: dict[str, float],
    current_week_num: int,
    planning_weeks: int = 4
) -> dict[str, float]:
    """
    反推投入建议（为未来产出做准备）
    
    逻辑:
      为了在 Week T+CT 产出 target 量，Week T 需要投入多少？
      
      投入量 = 产出目标 - 预测产出（来自现有 WIP）
      
    示例:
      Week 17 规划:
        - 28nm_DRAM 目标产出 Week 20: 500 片
        - 当前 WIP 预测 Week 20 产出: 300 片
        - 需要新投入: 500 - 300 = 200 片
    """
    
    recommendations: dict[str, float] = {}
    
    # 将预测产出转为 dict
    predicted_dict = {p.product_id: p.predicted_wafers for p in predicted_output}
    
    for product_id, target in output_target.items():
        # 预测产出（来自现有 WIP）
        predicted = predicted_dict.get(product_id, 0.0)
        
        # 产出缺口
        gap = target - predicted
        
        if gap > 0:
            # 需要新投入来弥补缺口
            # 注意：本周投入的产出时间是 T+CT 周
            recommendations[product_id] = gap
        else:
            # 预测产出已足够，无需新投入
            recommendations[product_id] = 0.0
    
    return recommendations


def run_output_rccp(inp: OutputRCCPInput) -> OutputRCCPResult:
    """
    Output RCCP 主函数
    """
    
    t0 = datetime.utcnow()
    
    # 1. 预测本周产出（基于 WIP）
    output_predictions = predict_output_from_wip(
        inp.wip_lot_detail, inp.output_completion_threshold
    )
    
    total_predicted = float(sum(p.predicted_wafers for p in output_predictions))
    
    # 2. 计算产出缺口
    output_gap: dict[str, float] = {}
    for product_id, target in inp.output_target.items():
        predicted = float(sum(p.predicted_wafers for p in output_predictions if p.product_id == product_id))
        output_gap[product_id] = float(target) - predicted
    
    # 3. 计算 WIP 后续负载（核心）
    wip_load = compute_wip_remaining_load(inp.wip_lot_detail, inp.route)
    
    # 4. 计算新投入负载（按工序时间分布）
    new_input_load: dict[str, dict[str, float]] = {}
    if inp.new_input_plan:
        new_input_load = compute_new_input_load_distribution(
            inp.new_input_plan, inp.route, inp.cycle_time_days
        )
    
    # 5. 合并产能需求
    wip_lot_count: dict[str, int] = {}
    wip_avg_wait: dict[str, float] = {}
    
    if not inp.wip_lot_detail.empty:
        if 'current_tool_group' in inp.wip_lot_detail.columns:
            wip_lot_count = inp.wip_lot_detail.groupby('current_tool_group')['lot_id'].count().to_dict()
        if 'wait_hours_so_far' in inp.wip_lot_detail.columns:
            wip_avg_wait = inp.wip_lot_detail.groupby('current_tool_group')['wait_hours_so_far'].mean().to_dict()
    
    capacity_demand = merge_capacity_demand(
        wip_load, new_input_load, inp.available_hours, wip_lot_count, wip_avg_wait
    )
    
    # 6. 计算总体利用率
    total_demand = float(sum(d.total_demand_hours for d in capacity_demand))
    total_available = float(sum(d.available_hours for d in capacity_demand))
    overall_loading = float((total_demand / total_available * 100) if total_available > 0 else 0)
    
    # 7. 计算总体 WIP 占比
    total_wip = float(sum(d.wip_remaining_hours for d in capacity_demand))
    overall_wip_share = float((total_wip / total_demand * 100) if total_demand > 0 else 0)
    
    # 8. 识别瓶颈
    critical_groups = [d.tool_group_id for d in capacity_demand if d.status in ("critical", "overload")]
    bottleneck_groups = [d.tool_group_id for d in capacity_demand if d.status == "overload"]
    
    # WIP 高占比机台组
    wip_critical_groups = [d.tool_group_id for d in capacity_demand 
                          if d.wip_share_pct >= inp.wip_critical_threshold * 100]
    
    # 9. 反推投入建议
    try:
        week_num = int(inp.output_target_week.split('-W')[1])
    except (ValueError, IndexError):
        week_num = 17
    
    input_recommendations = compute_input_recommendations(
        inp.output_target, output_predictions, inp.cycle_time_days, week_num
    )
    
    # 10. 风险分析
    risk_summary: list[str] = []
    
    # 产出缺口风险
    for product_id, gap in output_gap.items():
        if gap > 0:
            risk_summary.append(f"{product_id}: 产出缺口 {gap:.0f} 片，需新投入")
    
    # 瓶颈风险
    for tg_id in bottleneck_groups[:3]:
        tg_demand = next((d for d in capacity_demand if d.tool_group_id == tg_id), None)
        if tg_demand:
            risk_summary.append(
                f"{tg_id}: 过载 {tg_demand.loading_pct:.0f}%，WIP 占比 {tg_demand.wip_share_pct:.0f}%"
            )
    
    # WIP 高占比风险
    if len(wip_critical_groups) > 0:
        risk_summary.append(
            f"WIP 占用产能超过 {inp.wip_critical_threshold*100:.0f}% 的机台组: {', '.join(wip_critical_groups[:5])}"
        )
    
    # 11. 可行性判断
    feasible = (
        len(bottleneck_groups) == 0 and
        all(gap <= 0 for gap in output_gap.values())
    )
    
    return OutputRCCPResult(
        output_predictions=output_predictions,
        total_predicted_output=total_predicted,
        output_gap=output_gap,
        capacity_demand=capacity_demand,
        overall_loading_pct=overall_loading,
        critical_groups=critical_groups,
        bottleneck_groups=bottleneck_groups,
        overall_wip_share_pct=overall_wip_share,
        wip_critical_groups=wip_critical_groups,
        input_recommendations=input_recommendations,
        feasible=feasible,
        risk_summary=risk_summary,
        computed_at=datetime.utcnow(),
        metadata={
            "output_target_week": inp.output_target_week,
            "wip_lot_count": int(len(inp.wip_lot_detail)),
            "wip_wafer_count": int(inp.wip_lot_detail['wafer_count'].sum()) if not inp.wip_lot_detail.empty else 0,
            "output_completion_threshold": inp.output_completion_threshold,
            "compute_time_seconds": float((datetime.utcnow() - t0).total_seconds()),
            "perspective": "OUTPUT",
        },
    )


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 模拟数据
    wip_data = pd.DataFrame([
        {"lot_id": "L001", "product_id": "28nm_DRAM", "current_step_seq": 230, "wafer_count": 25, "percent_complete": 85, "lot_status": "WAIT", "current_tool_group": "ETCH_01", "wait_hours_so_far": 12},
        {"lot_id": "L002", "product_id": "28nm_DRAM", "current_step_seq": 150, "wafer_count": 25, "percent_complete": 50, "lot_status": "RUN", "current_tool_group": "LITHO_01", "wait_hours_so_far": 0},
        {"lot_id": "L003", "product_id": "64L_NAND", "current_step_seq": 280, "wafer_count": 30, "percent_complete": 93, "lot_status": "WAIT", "current_tool_group": "CMP_01", "wait_hours_so_far": 5},
    ])
    
    route_data = pd.DataFrame([
        {"product_id": "28nm_DRAM", "step_seq": 230, "tool_group_id": "ETCH_01", "run_time_hr": 0.5, "batch_size": 1},
        {"product_id": "28nm_DRAM", "step_seq": 235, "tool_group_id": "DEPO_01", "run_time_hr": 0.3, "batch_size": 1},
        {"product_id": "28nm_DRAM", "step_seq": 240, "tool_group_id": "CMP_01", "run_time_hr": 0.2, "batch_size": 1},
        {"product_id": "64L_NAND", "step_seq": 280, "tool_group_id": "CMP_01", "run_time_hr": 0.15, "batch_size": 1},
        {"product_id": "64L_NAND", "step_seq": 285, "tool_group_id": "INS_01", "run_time_hr": 0.1, "batch_size": 1},
    ])
    
    inp = OutputRCCPInput(
        output_target={"28nm_DRAM": 50, "64L_NAND": 30},
        output_target_week="2026-W17",
        wip_lot_detail=wip_data,
        route=route_data,
        cycle_time_days={"28nm_DRAM": 100, "64L_NAND": 90},
        available_hours={"ETCH_01": 500, "DEPO_01": 400, "CMP_01": 300, "LITHO_01": 600, "INS_01": 200},
    )
    
    result = run_output_rccp(inp)
    
    print("=== Output RCCP 结果 ===")
    print(f"视角: {result.metadata['perspective']}")
    print(f"预测产出: {result.total_predicted_output} 片")
    print(f"产出缺口: {result.output_gap}")
    print(f"可行: {result.feasible}")
    print()
    print("=== 产能需求（Output 视角）===")
    for d in result.capacity_demand:
        print(f"  {d.tool_group_id}: WIP负载 {d.wip_remaining_hours:.0f}h ({d.wip_share_pct:.0f}%), 总负载 {d.loading_pct:.0f}% [{d.status}]")
