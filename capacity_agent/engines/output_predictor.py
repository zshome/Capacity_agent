"""
Output Predictor - 产出预测模块

预测未来各周的产出量（基于当前 WIP 位置）

核心逻辑:
  对于每个 Lot:
    1. 当前工序 + 剩余工序数 → 估算剩余天数
    2. 当前周 + 剩余天数 → 预计完成周
    3. 按完成周分组 → 各周产出预测

适用场景: 存储芯片制造（CT = 90-120天）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class WeeklyOutputPrediction:
    """单周产出预测"""
    week_id: str
    product_id: str
    predicted_wafers: float
    source_wip_lots: int
    avg_percent_complete_now: float  # 当前平均完成度
    est_completion_week: str         # 预计完成周
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "week_id": str(self.week_id),
            "product_id": str(self.product_id),
            "predicted_wafers": float(round(self.predicted_wafers, 0)),
            "source_wip_lots": int(self.source_wip_lots),
            "avg_percent_complete_now": float(round(self.avg_percent_complete_now, 2)),
            "est_completion_week": str(self.est_completion_week),
        }


@dataclass
class OutputPredictionResult:
    """产出预测结果"""
    predictions: list[WeeklyOutputPrediction]
    predictions_by_week: dict[str, dict[str, float]]  # {week: {product: wafers}}
    predictions_by_product: dict[str, dict[str, float]]  # {product: {week: wafers}}
    total_wip_wafers: float
    wip_distribution: dict[str, Any]  # WIP 分布统计
    computed_at: datetime
    
    def to_dict(self) -> dict[str, Any]:
        # 转换 numpy 类型为 Python 原生类型
        def convert_numpy(obj):
            if isinstance(obj, dict):
                return {str(k): convert_numpy(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_numpy(item) for item in obj]
            elif isinstance(obj, (np.integer, np.int64, np.int32)):
                return int(obj)
            elif isinstance(obj, (np.floating, np.float64, np.float32)):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, (int, float, str, bool)):
                return obj
            return str(obj)
        
        return {
            "predictions": [p.to_dict() for p in self.predictions],
            "predictions_by_week": convert_numpy(self.predictions_by_week),
            "predictions_by_product": convert_numpy(self.predictions_by_product),
            "total_wip_wafers": float(self.total_wip_wafers),
            "wip_distribution": convert_numpy(self.wip_distribution),
            "computed_at": self.computed_at.isoformat(),
        }


def compute_lot_completion_week_v2(
    lot: pd.Series,
    product_route: pd.DataFrame,
    current_week: str
) -> tuple[str, float, float]:
    """
    计算单个 Lot 的预计完成周（使用工序级 TC）
    
    Args:
        lot: Lot 信息，包含 current_step_seq, wafer_count, percent_complete
        product_route: 产品工艺路线，包含 step_seq, run_time_hr, tool_group_id
        current_week: 当前周
    
    Returns: (完成周, 剩余天数, 剩余工序总工时)
    """
    
    current_step = lot.get('current_step_seq', 0)
    wafer_count = lot.get('wafer_count', 1)
    
    # 获取剩余工序的工时
    remaining_route = product_route[product_route['step_seq'] > current_step].sort_values('step_seq')
    
    if remaining_route.empty:
        # 已完成所有工序，本周可产出
        return current_week, 0.0, 0.0
    
    # 计算剩余工序总工时（小时/批次）
    total_remaining_hours = remaining_route['run_time_hr'].sum() * wafer_count
    
    # 转换为天数（假设每天工作24小时）
    remaining_days = total_remaining_hours / 24.0
    
    # 计算完成周
    try:
        year, week_num = current_week.split('-W')
        week_num = int(week_num)
        year = int(year)
    except (ValueError, AttributeError):
        year = datetime.now().year
        week_num = datetime.now().isocalendar()[1]
    
    # 增加周数
    additional_weeks = int(remaining_days / 7)
    completion_week_num = week_num + additional_weeks
    
    # 处理跨年
    if completion_week_num > 52:
        completion_week_num -= 52
        year += 1
    
    completion_week = f"{year}-W{completion_week_num}"
    
    return completion_week, remaining_days, total_remaining_hours


def compute_lot_completion_week(
    lot: pd.Series,
    product_route: pd.DataFrame,
    cycle_time_days: float,
    current_week: str
) -> tuple[str, float]:
    """
    计算单个 Lot 的预计完成周（旧版：使用总 CT）
    
    兼容旧逻辑，优先使用工序级 TC，若无则回退到总 CT
    """
    
    current_step = lot.get('current_step_seq', 0)
    
    # 优先使用工序级 TC
    if 'run_time_hr' in product_route.columns:
        remaining_route = product_route[product_route['step_seq'] > current_step].sort_values('step_seq')
        if not remaining_route.empty:
            wafer_count = lot.get('wafer_count', 1)
            total_remaining_hours = remaining_route['run_time_hr'].sum() * wafer_count
            remaining_days = total_remaining_hours / 24.0
        else:
            remaining_days = 0.0
    else:
        # 回退到总 CT 估算
        total_steps = len(product_route)
        if total_steps == 0:
            return current_week, 0
        remaining_steps = total_steps - current_step
        remaining_days = cycle_time_days * (remaining_steps / total_steps)
    
    # 计算完成周
    try:
        year, week_num = current_week.split('-W')
        week_num = int(week_num)
        year = int(year)
    except (ValueError, AttributeError):
        year = datetime.now().year
        week_num = datetime.now().isocalendar()[1]
    
    additional_weeks = int(remaining_days / 7)
    completion_week_num = week_num + additional_weeks
    
    if completion_week_num > 52:
        completion_week_num -= 52
        year += 1
    
    completion_week = f"{year}-W{completion_week_num}"
    
    return completion_week, remaining_days


def predict_output_for_next_n_weeks(
    wip_lot_detail: pd.DataFrame,
    route: pd.DataFrame,
    cycle_time_days: dict[str, float],
    current_week: str,
    n_weeks: int = 8
) -> OutputPredictionResult:
    """
    预测未来 N 周的产出
    
    逻辑:
      对于每个 Lot:
        1. 当前工序 + 剩余工序数 → 估算剩余天数
        2. 当前周 + 剩余天数 → 预计完成周
        3. 按完成周分组 → 各周产出预测
    """
    
    t0 = datetime.utcnow()
    
    predictions: list[WeeklyOutputPrediction] = []
    predictions_by_week: dict[str, dict[str, float]] = {}
    predictions_by_product: dict[str, dict[str, float]] = {}
    
    # WIP 分布统计
    wip_distribution = {
        "by_status": {},
        "by_tool_group": {},
        "by_percent_complete_range": {},
    }
    
    if wip_lot_detail.empty:
        return OutputPredictionResult(
            predictions=predictions,
            predictions_by_week=predictions_by_week,
            predictions_by_product=predictions_by_product,
            total_wip_wafers=0,
            wip_distribution=wip_distribution,
            computed_at=datetime.utcnow(),
        )
    
    total_wip_wafers = wip_lot_detail['wafer_count'].sum()
    
    # 计算 WIP 分布
    if 'lot_status' in wip_lot_detail.columns:
        wip_distribution["by_status"] = wip_lot_detail.groupby('lot_status')['wafer_count'].sum().to_dict()
    
    if 'current_tool_group' in wip_lot_detail.columns:
        wip_distribution["by_tool_group"] = wip_lot_detail.groupby('current_tool_group')['wafer_count'].sum().to_dict()
    
    if 'percent_complete' in wip_lot_detail.columns:
        # 按完成度区间分组
        bins = [0, 20, 40, 60, 80, 100]
        labels = ['0-20%', '20-40%', '40-60%', '60-80%', '80-100%']
        wip_lot_detail['pct_range'] = pd.cut(
            wip_lot_detail['percent_complete'], 
            bins=bins, 
            labels=labels,
            include_lowest=True
        )
        wip_distribution["by_percent_complete_range"] = wip_lot_detail.groupby('pct_range')['wafer_count'].sum().to_dict()
    
    # Lot 级预测
    lot_predictions: list[dict] = []
    
    for _, lot in wip_lot_detail.iterrows():
        lot_id = lot.get('lot_id', 'UNKNOWN')
        product_id = lot['product_id']
        current_step = lot.get('current_step_seq', 0)
        wafer_count = lot.get('wafer_count', 25)
        percent_complete = lot.get('percent_complete', 0)
        
        # 获取产品制程路线
        product_route = route[route['product_id'] == product_id].sort_values('step_seq')
        
        if product_route.empty:
            # 无路线数据，使用默认 CT
            ct = cycle_time_days.get(product_id, 100.0)
            completion_week, remaining_days = current_week, ct
            remaining_hours = ct * 24.0  # 粗略估算
        else:
            # 使用工序级 TC（优先）
            if 'run_time_hr' in product_route.columns:
                completion_week, remaining_days, remaining_hours = compute_lot_completion_week_v2(
                    lot, product_route, current_week
                )
            else:
                # 回退到总 CT
                ct = cycle_time_days.get(product_id, 100.0)
                completion_week, remaining_days = compute_lot_completion_week(
                    lot, product_route, ct, current_week
                )
                # 粗略估算剩余工时
                total_steps = len(product_route)
                remaining_steps = total_steps - current_step
                avg_tc_per_step = ct * 24.0 / total_steps if total_steps > 0 else 0
                remaining_hours = remaining_steps * avg_tc_per_step
        
        lot_predictions.append({
            "lot_id": lot_id,
            "product_id": product_id,
            "wafer_count": wafer_count,
            "percent_complete": percent_complete / 100,
            "completion_week": completion_week,
            "remaining_days": remaining_days,
            "remaining_hours": remaining_hours,
        })
    
    # 按 周 × 产品 聚合
    lot_df = pd.DataFrame(lot_predictions)
    
    if lot_df.empty:
        return OutputPredictionResult(
            predictions=predictions,
            predictions_by_week=predictions_by_week,
            predictions_by_product=predictions_by_product,
            total_wip_wafers=total_wip_wafers,
            wip_distribution=wip_distribution,
            computed_at=datetime.utcnow(),
        )
    
    for (week_id, product_id), group in lot_df.groupby(['completion_week', 'product_id']):
        predicted_wafers = group['wafer_count'].sum()
        avg_pct = group['percent_complete'].mean()
        lots_count = len(group)
        
        predictions.append(WeeklyOutputPrediction(
            week_id=week_id,
            product_id=product_id,
            predicted_wafers=predicted_wafers,
            source_wip_lots=lots_count,
            avg_percent_complete_now=avg_pct,
            est_completion_week=week_id,
        ))
        
        # 按 周 汇总
        if week_id not in predictions_by_week:
            predictions_by_week[week_id] = {}
        predictions_by_week[week_id][product_id] = predicted_wafers
        
        # 按 产品 汇总
        if product_id not in predictions_by_product:
            predictions_by_product[product_id] = {}
        predictions_by_product[product_id][week_id] = predicted_wafers
    
    # 排序（按周）
    predictions.sort(key=lambda x: (x.week_id, x.product_id))
    
    return OutputPredictionResult(
        predictions=predictions,
        predictions_by_week=predictions_by_week,
        predictions_by_product=predictions_by_product,
        total_wip_wafers=total_wip_wafers,
        wip_distribution=wip_distribution,
        computed_at=datetime.utcnow(),
    )


def get_near_completion_wip(
    wip_lot_detail: pd.DataFrame,
    threshold_pct: float = 80.0
) -> pd.DataFrame:
    """
    获取即将完成的 WIP（完成度 >= threshold）
    
    用于本周产出预测
    """
    
    if wip_lot_detail.empty:
        return pd.DataFrame()
    
    return wip_lot_detail[wip_lot_detail['percent_complete'] >= threshold_pct]


def compute_output_confidence(
    wip_lot_detail: pd.DataFrame,
    threshold_pct: float = 80.0
) -> dict[str, str]:
    """
    计算各产品产出预测的置信度
    
    基于:
      - Lot 数量
      - Hold Lot 数量
      - 平均等待时间
    """
    
    confidence: dict[str, str] = {}
    
    if wip_lot_detail.empty:
        return confidence
    
    near_complete = get_near_completion_wip(wip_lot_detail, threshold_pct)
    
    for product_id in wip_lot_detail['product_id'].unique():
        product_wip = near_complete[near_complete['product_id'] == product_id]
        
        if product_wip.empty:
            confidence[product_id] = "LOW"
            continue
        
        # 风险因素计数
        risk_count = 0
        
        # Hold Lot
        if 'lot_status' in product_wip.columns:
            hold_count = len(product_wip[product_wip['lot_status'] == 'HOLD'])
            if hold_count > 0:
                risk_count += 1
        
        # 平均等待
        if 'wait_hours_so_far' in product_wip.columns:
            avg_wait = product_wip['wait_hours_so_far'].mean()
            if avg_wait > 48:
                risk_count += 1
        
        # Lot 数量少
        if len(product_wip) < 3:
            risk_count += 1
        
        # 置信度判断
        if risk_count == 0:
            confidence[product_id] = "HIGH"
        elif risk_count == 1:
            confidence[product_id] = "MEDIUM"
        else:
            confidence[product_id] = "LOW"
    
    return confidence


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 模拟 WIP 数据
    wip_data = pd.DataFrame([
        {"lot_id": "L001", "product_id": "28nm_DRAM", "current_step_seq": 230, "wafer_count": 25, "percent_complete": 85, "lot_status": "WAIT", "current_tool_group": "ETCH_01"},
        {"lot_id": "L002", "product_id": "28nm_DRAM", "current_step_seq": 150, "wafer_count": 25, "percent_complete": 50, "lot_status": "RUN", "current_tool_group": "LITHO_01"},
        {"lot_id": "L003", "product_id": "28nm_DRAM", "current_step_seq": 100, "wafer_count": 25, "percent_complete": 33, "lot_status": "WAIT", "current_tool_group": "DEPO_01"},
        {"lot_id": "L004", "product_id": "64L_NAND", "current_step_seq": 280, "wafer_count": 30, "percent_complete": 93, "lot_status": "WAIT", "current_tool_group": "CMP_01"},
        {"lot_id": "L005", "product_id": "64L_NAND", "current_step_seq": 200, "wafer_count": 30, "percent_complete": 66, "lot_status": "RUN", "current_tool_group": "ETCH_02"},
    ])
    
    route_data = pd.DataFrame([
        {"product_id": "28nm_DRAM", "step_seq": i, "tool_group_id": f"TG_{i%10}", "run_time_hr": 0.3}
        for i in range(1, 301)
    ] + [
        {"product_id": "64L_NAND", "step_seq": i, "tool_group_id": f"TG_{i%10}", "run_time_hr": 0.25}
        for i in range(1, 301)
    ])
    
    cycle_time = {"28nm_DRAM": 100, "64L_NAND": 90}
    
    result = predict_output_for_next_n_weeks(
        wip_data, route_data, cycle_time, "2026-W17", 8
    )
    
    print("=== 产出预测结果 ===")
    print(f"总 WIP 晶圆数: {result.total_wip_wafers}")
    print(f"WIP 分布（按完成度）: {result.wip_distribution.get('by_percent_complete_range', {})}")
    print()
    print("=== 各周产出预测 ===")
    for week, products in sorted(result.predictions_by_week.items()):
        print(f"{week}:")
        for product, wafers in products.items():
            print(f"  {product}: {wafers:.0f} 片")