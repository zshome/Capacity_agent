"""
Standard Capacity Calculator
=============================

基于规则文档的标准产能计算公式:

  产能 = 24hr / TTL_TC × batch_size × 30天 × (uptime - loss_time)

其中:
  - TTL_TC: 总周期时间 (Total Cycle Time)，即 Σ TC[process]
  - batch_size: 每批次产品数量
  - uptime: 设备正常运行时间比率 (0-1)
  - loss_time: 设备损失时间比率 (0-1)

适用于:
  - 情景1: 单机台/相同Path
  - 全适场景: 无约束，直接计算

输入:
  - TC矩阵: {product_id: {tool_id: {process_id: hours_per_wafer}}}
  - batch_size: 批次大小
  - uptime: 正常运行率
  - loss_time: 损失时间率
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class StandardCapacityInput:
    """标准产能计算输入"""
    product_id: str
    tool_id: str
    
    # TC矩阵：各制程的周期时间 (hours/wafer)
    tc_by_process: dict[str, float]  # {process_id: hours/wafer}
    
    # 参数
    batch_size: float = 25.0           # 每批次wafer数（半导体典型值）
    uptime: float = 0.90               # 正常运行率 (90%)
    loss_time: float = 0.05            # 损失时间率 (5%)
    days_in_month: float = 30.0        # 月度天数
    
    # 可选：直接提供 TTL_TC
    ttl_tc_override: float | None = None


@dataclass
class CapacityResult:
    """产能计算结果"""
    product_id: str
    tool_id: str
    capacity_wafers_per_month: float   # 月产能 (wafers)
    capacity_wafers_per_day: float     # 日产能 (wafers)
    capacity_wafers_per_hour: float    # 小时产能 (wafers)
    ttl_tc: float                      # 总周期时间 (hours)
    effective_time_ratio: float        # 有效时间比率 (uptime - loss_time)
    formula: str                       # 计算公式
    computed_at: datetime
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "tool_id": self.tool_id,
            "capacity_wafers_per_month": round(self.capacity_wafers_per_month, 1),
            "capacity_wafers_per_day": round(self.capacity_wafers_per_day, 2),
            "capacity_wafers_per_hour": round(self.capacity_wafers_per_hour, 4),
            "ttl_tc": round(self.ttl_tc, 4),
            "effective_time_ratio": round(self.effective_time_ratio, 4),
            "formula": self.formula,
            "computed_at": self.computed_at.isoformat(),
        }


@dataclass
class MultiProductCapacityResult:
    """多产品产能汇总"""
    results: list[CapacityResult]
    total_capacity: float              # 总产能 (wafers/month)
    capacity_by_product: dict[str, float]  # 各产品产能
    bottlenecks: list[str]             # 瓶颈产品/机台
    computed_at: datetime
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "total_capacity": round(self.total_capacity, 1),
            "capacity_by_product": {k: round(v, 1) for k, v in self.capacity_by_product.items()},
            "bottlenecks": self.bottlenecks,
            "computed_at": self.computed_at.isoformat(),
        }


def compute_standard_capacity(inp: StandardCapacityInput) -> CapacityResult:
    """
    标准产能计算公式
    
    公式: 产能 = 24hr / TTL_TC × batch_size × 30天 × (uptime - loss_time)
    
    注意：原公式中的 batch_size 可能表示每批次的处理量，
    这里假设为每 wafer 的计算单位，即 batch_size = 1 时为单 wafer 产能。
    """
    
    # 1. 计算 TTL_TC (Total Cycle Time)
    if inp.ttl_tc_override is not None:
        ttl_tc = inp.ttl_tc_override
    else:
        ttl_tc = sum(inp.tc_by_process.values())
    
    if ttl_tc <= 0:
        raise ValueError(f"TTL_TC must be positive: {ttl_tc}")
    
    # 2. 有效时间比率
    effective_ratio = inp.uptime - inp.loss_time
    if effective_ratio <= 0:
        raise ValueError(f"Effective time ratio must be positive: uptime={inp.uptime}, loss_time={inp.loss_time}")
    
    # 3. 计算产能
    # 产能 = 24 / TTL_TC × batch_size × days × effective_ratio
    # 单位: wafers/month
    
    # 每小时产能 = 1 / TTL_TC (假设 batch_size = 1 wafer)
    capacity_per_hour = inp.batch_size / ttl_tc
    
    # 每天产能 = 24 × capacity_per_hour × effective_ratio
    capacity_per_day = 24.0 * capacity_per_hour * effective_ratio
    
    # 每月产能 = days × capacity_per_day
    capacity_per_month = inp.days_in_month * capacity_per_day
    
    formula = f"产能 = 24hr / {ttl_tc:.3f}h × {inp.batch_size} × {inp.days_in_month}天 × ({inp.uptime:.2f} - {inp.loss_time:.2f})"
    
    return CapacityResult(
        product_id=inp.product_id,
        tool_id=inp.tool_id,
        capacity_wafers_per_month=capacity_per_month,
        capacity_wafers_per_day=capacity_per_day,
        capacity_wafers_per_hour=capacity_per_hour,
        ttl_tc=ttl_tc,
        effective_time_ratio=effective_ratio,
        formula=formula,
        computed_at=datetime.utcnow(),
    )


def compute_capacity_from_tc_matrix(
    tc_matrix: dict[str, dict[str, dict[str, float]]],
    batch_sizes: dict[str, float] | None = None,
    uptime: float = 0.90,
    loss_time: float = 0.05,
    days_in_month: float = 30.0,
) -> MultiProductCapacityResult:
    """
    从TC矩阵批量计算产能
    
    输入:
      tc_matrix: {product_id: {tool_id: {process_id: hours}}}
    
    输出:
      MultiProductCapacityResult: 各产品在各机台的产能
    """
    results: list[CapacityResult] = []
    capacity_by_product: dict[str, float] = {}
    
    for product_id, tools in tc_matrix.items():
        product_capacity = 0.0
        batch_size = batch_sizes.get(product_id, 25.0) if batch_sizes else 25.0
        
        for tool_id, processes in tools.items():
            # 合并所有制程的 TC
            tc_by_process = {p: tc for p, tc in processes.items() if tc > 0}
            
            if not tc_by_process:
                continue
            
            inp = StandardCapacityInput(
                product_id=product_id,
                tool_id=tool_id,
                tc_by_process=tc_by_process,
                batch_size=batch_size,
                uptime=uptime,
                loss_time=loss_time,
                days_in_month=days_in_month,
            )
            
            result = compute_standard_capacity(inp)
            results.append(result)
            product_capacity += result.capacity_wafers_per_month
        
        capacity_by_product[product_id] = product_capacity
    
    # 找出瓶颈（产能最低的产品）
    bottlenecks = []
    if capacity_by_product:
        min_capacity = min(capacity_by_product.values())
        bottlenecks = [p for p, c in capacity_by_product.items() if c <= min_capacity * 1.1]
    
    total_capacity = sum(capacity_by_product.values())
    
    return MultiProductCapacityResult(
        results=results,
        total_capacity=total_capacity,
        capacity_by_product=capacity_by_product,
        bottlenecks=bottlenecks,
        computed_at=datetime.utcnow(),
    )


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 测试单个产能计算
    inp = StandardCapacityInput(
        product_id="Product_A",
        tool_id="Tool_1",
        tc_by_process={"Step_1": 2.8, "Step_2": 1.8, "Step_3": 3.2},
        batch_size=25.0,
        uptime=0.90,
        loss_time=0.05,
    )
    
    result = compute_standard_capacity(inp)
    print(f"产品: {result.product_id}, 机台: {result.tool_id}")
    print(f"总周期时间(TTL_TC): {result.ttl_tc:.2f} hours")
    print(f"月产能: {result.capacity_wafers_per_month:.0f} wafers")
    print(f"日产能: {result.capacity_wafers_per_day:.1f} wafers")
    print(f"公式: {result.formula}")
    print()
    
    # 测试从TC矩阵批量计算
    tc_matrix = {
        "Product_A": {
            "Tool_1": {"Step_1": 2.8, "Step_2": 1.8},
            "Tool_2": {"Step_2": 1.8, "Step_3": 3.2},
            "Tool_3": {"Step_1": 2.8, "Step_2": 1.8},
        },
        "Product_B": {
            "Tool_1": {"Step_2": 2.0, "Step_3": 4.0},
            "Tool_2": {"Step_1": 3.0, "Step_2": 2.0},
        },
    }
    
    multi_result = compute_capacity_from_tc_matrix(tc_matrix)
    print(f"总产能: {multi_result.total_capacity:.0f} wafers/month")
    print(f"各产品产能:")
    for p, c in multi_result.capacity_by_product.items():
        print(f"  {p}: {c:.0f} wafers/month")
    print(f"瓶颈产品: {multi_result.bottlenecks}")