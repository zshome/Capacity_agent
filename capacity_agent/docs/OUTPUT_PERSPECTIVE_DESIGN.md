# Output Perspective Capacity Planning - 产出视角产能规划系统设计

> **版本**: v2.0  
> **日期**: 2026-04-28  
> **适用场景**: 存储芯片制造（CT = 90-120天，高重入）  
> **核心变化**: 从"投入量规划"转为"产出目标规划"

---

## 一、Input vs Output 视角对比

### 1.1 核心概念对比

| 维度 | **Input 视角**（旧） | **Output 视角**（新） |
|------|---------------------|---------------------|
| **规划对象** | 投入量（要投多少） | **产出目标**（要出多少） |
| **时间关系** | 投入时间 = 规划周 | 产出时间 = 规划周 |
| **产能计算** | 投入 × 总工时 | **WIP 后续工序小时** |
| **WIP 角色** | 未考虑（盲区） | **核心输入**（产出来源） |
| **Cycle Time** | 未考虑 | **关键参数**（投入→产出延迟） |
| **物理意义** | 投入瞬间占用（错误） | **持续占用**（正确） |

### 1.2 存储芯片制造的真实过程

```
存储芯片制造时间线:

Week 10: 投入 Lot L001 (28nm_DRAM)
  ↓
Week 10-12: 光刻区（20 次重入）
  ↓ 每次占用 2h，累计 40h
Week 13-20: 刻蚀区（30 步工序）
  ↓ 每步 0.5h，累计 15h
Week 21-35: 沉积区（40 步工序）
  ↓ 每步 0.3h，累计 12h
Week 36-40: CMP/清洗/检测
  ↓
Week 40: 产出（Cycle Time = 100天）
```

**关键事实**：
- 投入 Week 10，产出 Week 40
- Week 17 时，Lot L001 处于刻蚀区第 15 步
- **Week 17 的产能需求 = L001 在刻蚀区后续工序的小时**
- **不是 L001 的全部工时（会严重高估）**

---

## 二、Output 视角数据模型

### 2.1 新增数据表

#### 2.1.1 产出目标表（核心）

```sql
-- 产出目标表（替代 demand_plan）
CREATE TABLE IF NOT EXISTS fact_output_target (
    plan_version    VARCHAR(32) NOT NULL,
    time_window     VARCHAR(32) NOT NULL,           -- 产出时间窗口（如 2026-W20）
    product_id      VARCHAR(64) NOT NULL,
    
    -- 产出目标
    target_wafers   NUMERIC(10,2) NOT NULL,         -- 目标产出晶圆数
    priority        NUMERIC(4,2) DEFAULT 1.0,       -- 产品优先级
    
    -- 约束边界
    contract_min    NUMERIC(10,2) DEFAULT 0,        -- 合约最低产出
    market_max      NUMERIC(10,2) DEFAULT 999999,   -- 市场最大需求
    
    -- 经济参数
    unit_profit     NUMERIC(10,2) DEFAULT 0,        -- 单片利润
    
    -- 投入时间反推
    input_window    VARCHAR(32),                    -- 需投入的时间窗口（反推）
    ct_days         NUMERIC(6,1),                   -- 预估 Cycle Time
    
    created_ts      TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (plan_version, time_window, product_id)
);

COMMENT ON TABLE fact_output_target IS '产出目标表 - Output 视角的核心输入';
COMMENT ON COLUMN fact_output_target.time_window IS '产出时间窗口（这是目标产出周，不是投入周）';
COMMENT ON COLUMN fact_output_target.input_window IS '反推的投入时间窗口 = time_window - CT';
```

#### 2.1.2 WIP 产出预测表

```sql
-- WIP 产出预测（每周刷新）
CREATE TABLE IF NOT EXISTS mart_wip_output_prediction (
    prediction_ts   TIMESTAMPTZ NOT NULL,
    product_id      VARCHAR(64) NOT NULL,
    output_week     VARCHAR(32) NOT NULL,           -- 预计产出周
    
    -- 预测产量
    predicted_wafers NUMERIC(10,2) NOT NULL,        -- 预测产出晶圆数
    wip_lots        INT NOT NULL,                   -- 来源 Lot 数
    wip_wafers      INT NOT NULL,                   -- 来源晶圆数
    
    -- 完成度
    avg_percent_complete NUMERIC(5,2),              -- 平均完成百分比
    min_percent_complete NUMERIC(5,2),              -- 最小完成度（保守估计）
    
    -- 风险因素
    hold_lot_count  INT DEFAULT 0,                  -- Hold Lot 数
    risk_flag       VARCHAR(32),                    -- LOW/MEDIUM/HIGH
    
    -- 计算依据
    snapshot_ts     TIMESTAMPTZ NOT NULL,           -- WIP 快照时间
    PRIMARY KEY (prediction_ts, product_id, output_week)
);

CREATE INDEX idx_wip_output_week ON mart_wip_output_prediction(output_week, product_id);
```

#### 2.1.3 周产能需求表（Output 视角）

```sql
-- 周产能需求（基于 WIP 后续工序）
CREATE TABLE IF NOT EXISTS mart_weekly_capacity_demand (
    week_id         VARCHAR(16) NOT NULL,           -- 规划周（如 2026-W17）
    tool_group_id   VARCHAR(64) NOT NULL,
    
    -- 产能需求分解
    wip_remaining_hours NUMERIC(10,2) NOT NULL,     -- WIP 后续工序小时（核心）
    new_input_hours     NUMERIC(10,2) DEFAULT 0,    -- 新投入小时（按工序时间分布）
    total_demand_hours  NUMERIC(10,2) NOT NULL,     -- 总需求
    
    -- 产品分布
    demand_by_product   JSONB,                      -- {product: hours}
    
    -- 与产能对比
    available_hours     NUMERIC(10,2),
    loading_pct         NUMERIC(6,2),
    status              VARCHAR(16),                -- healthy/warning/critical/overload
    
    -- 计算时间
    computed_at         TIMESTAMPTZ,
    PRIMARY KEY (week_id, tool_group_id)
);
```

#### 2.1.4 投入计划表（反推生成）

```sql
-- 投入计划（从产出目标反推）
CREATE TABLE IF NOT EXISTS fact_input_schedule (
    schedule_version VARCHAR(32) NOT NULL,
    input_window     VARCHAR(32) NOT NULL,          -- 投入时间窗口
    product_id       VARCHAR(64) NOT NULL,
    
    -- 投入量
    planned_wafers   NUMERIC(10,2) NOT NULL,        -- 计划投入晶圆数
    
    -- 产出目标关联
    output_window    VARCHAR(32) NOT NULL,          -- 目标产出周
    output_target_id VARCHAR(64),                   -- 关联产出目标
    
    -- 投入类型
    input_type       VARCHAR(32) NOT NULL,          -- NEW_INPUT /补充投入
    
    -- 状态
    status           VARCHAR(32) DEFAULT 'PLANNED', -- PLANNED/RELEASED/COMPLETED
    
    created_ts       TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (schedule_version, input_window, product_id)
);
```

### 2.2 数据流转架构

```
┌─────────────────────────────────────────────────────────────┐
│  外部系统                                                    │
│    - MPS (主生产计划) → 产出目标                             │
│    - MES → WIP 实时数据                                     │
│    - ERP → 合约订单                                         │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  数据层 (PostgreSQL)                                         │
│                                                             │
│  输入端:                                                    │
│    fact_output_target        ← 产出目标（规划输入）          │
│    fact_wip_lot_detail       ← WIP 实时位置                  │
│    dim_route                 ← 制程路线                      │
│    dim_cycle_time            ← Cycle Time 基准               │
│                                                             │
│  计算中间层 (dbt):                                          │
│    mart_wip_output_prediction ← WIP 产出预测                 │
│    mart_wip_remaining_load    ← WIP 后续负载                 │
│    mart_weekly_capacity_demand← 周产能需求                   │
│                                                             │
│  输出端:                                                    │
│    fact_input_schedule       ← 投入计划（反推生成）          │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  计算引擎                                                    │
│                                                             │
│  Output RCCP Engine:                                        │
│    - 计算周产能需求（基于 WIP 后续工序）                      │
│    - 产能缺口分析                                            │
│    - 状态判断（健康/预警/瓶颈）                              │
│                                                             │
│  Output Predictor:                                          │
│    - 预测本周/下周产出                                       │
│    - 基于 WIP 位置和工序进度                                 │
│                                                             │
│  Input Planner (反推):                                      │
│    - 从产出目标反推投入量                                    │
│    - 考虑 CT 延迟                                            │
│    - 生成分周投入计划                                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│  前端展示                                                    │
│                                                             │
│  产出预测面板:                                              │
│    - 本周预测产出 vs 目标                                    │
│    - 下周预测产出                                            │
│    - 产出缺口分析                                            │
│                                                             │
│  产能负载面板:                                              │
│    - WIP 后续负载（核心）                                    │
│    - 新投入负载                                              │
│    - 总负载占比                                              │
│                                                             │
│  投入建议面板:                                              │
│    - 为达成未来产出目标，本周需投入多少                       │
│    - 分周投入计划                                            │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、核心计算引擎

### 3.1 Output RCCP Engine

#### 3.1.1 数据结构

```python
# engines/output_rccp.py

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
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import numpy as np


@dataclass
class OutputRCCPInput:
    """Output RCCP 输入"""
    
    # 产出目标（核心输入）
    output_target: dict[str, float]              # {product_id: target_wafers} 本周产出目标
    output_target_week: str                      # 规划周（如 "2026-W17")
    
    # WIP 数据
    wip_lot_detail: pd.DataFrame                 # Lot 级 WIP 详情
    # 必须字段: lot_id, product_id, current_step_seq, wafer_count, remaining_hours_by_tg
    
    # 制程路线
    route: pd.DataFrame                          # dim_route
    cycle_time_days: dict[str, float]            # {product_id: avg_ct_days}
    
    # 产能数据
    available_hours: dict[str, float]            # {tool_group_id: weekly_hours}
    oee: dict[str, float]                        # {tool_group_id: oee_factor}
    
    # 新投入计划（可选）
    new_input_plan: dict[str, float] | None = None  # {product_id: wafers} 本周新投入
    
    # 参数
    safety_margin_pct: float = 0.15              # 安全余量
    output_completion_threshold: float = 0.80    # 完成度阈值（>=80%预计本周产出）


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
            "input_recommendations": {k: round(v, 0) for k, v in self.input_recommendations.items()},
            "feasible": self.feasible,
            "risk_summary": self.risk_summary,
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }
```

#### 3.1.2 核心计算函数

```python
def predict_output_from_wip(
    wip_lot_detail: pd.DataFrame,
    route: pd.DataFrame,
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
    
    # 按产品分组
    for product_id, wip_group in wip_lot_detail.groupby('product_id'):
        
        # 筛选即将完成的 Lot
        near_complete_lots = wip_group[
            wip_group['percent_complete'] >= output_completion_threshold * 100
        ]
        
        if near_complete_lots.empty:
            # 无即将完成的 Lot
            predictions.append(OutputPrediction(
                product_id=product_id,
                predicted_wafers=0.0,
                source_wip_lots=0,
                avg_percent_complete=wip_group['percent_complete'].mean() / 100,
                confidence="LOW",
                risk_factors=["无即将完成的 WIP"],
            ))
            continue
        
        # 计算预测产出
        predicted_wafers = near_complete_lots['wafer_count'].sum()
        
        # 考虑良率（如果有）
        if 'good_wafer_count' in near_complete_lots.columns:
            predicted_wafers = near_complete_lots['good_wafer_count'].sum()
        
        # 风险因素
        risk_factors = []
        hold_lots = near_complete_lots[near_complete_lots['lot_status'] == 'HOLD']
        if len(hold_lots) > 0:
            risk_factors.append(f"{len(hold_lots)} Lot 处于 HOLD 状态")
        
        avg_wait = near_complete_lots['wait_hours_so_far'].mean()
        if avg_wait > 48:
            risk_factors.append(f"平均等待 {avg_wait:.0f}h，可能延误")
        
        # 置信度
        confidence = "HIGH" if len(risk_factors) == 0 else "MEDIUM" if len(risk_factors) == 1 else "LOW"
        
        predictions.append(OutputPrediction(
            product_id=product_id,
            predicted_wafers=predicted_wafers,
            source_wip_lots=len(near_complete_lots),
            avg_percent_complete=near_complete_lots['percent_complete'].mean() / 100,
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
        - OP230 (光刻第15次): 当前正在加工，不计入
        - OP235 (刻蚀第10次): 0.5h × 25 = 12.5h → ETCH_01
        - OP240 (沉积第8次): 0.3h × 25 = 7.5h → DEPO_01
        ...
      
      结果:
        ETCH_01: {"28nm_DRAM": 12.5h + ...}
        DEPO_01: {"28nm_DRAM": 7.5h + ...}
    """
    
    load_by_tg_product: dict[str, dict[str, float]] = {}
    
    for _, lot in wip_lot_detail.iterrows():
        lot_id = lot['lot_id']
        product_id = lot['product_id']
        current_step = lot['current_step_seq']
        wafer_count = lot['wafer_count']
        
        # 获取该产品的完整制程路线
        product_route = route[
            (route['product_id'] == product_id) &
            (route['step_seq'] >= current_step)  # 后续工序
        ]
        
        for _, step in product_route.iterrows():
            tg_id = step['tool_group_id']
            
            # 单位小时 = run_time / batch_size
            unit_hours = step['run_time_hr'] / max(step['batch_size'], 1)
            
            # 该 Lot 在该工序的负载
            lot_hours = unit_hours * wafer_count
            
            # 汇总到 tool_group × product
            if tg_id not in load_by_tg_product:
                load_by_tg_product[tg_id] = {}
            
            if product_id not in load_by_tg_product[tg_id]:
                load_by_tg_product[tg_id][product_id] = 0.0
            
            load_by_tg_product[tg_id][product_id] += lot_hours
    
    return load_by_tg_product


def compute_new_input_load_distribution(
    new_input_plan: dict[str, float],
    route: pd.DataFrame,
    cycle_time_days: dict[str, float],
    current_week: str,
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
    
    for product_id, wafer_count in new_input_plan.items():
        
        # 获取该产品的制程路线
        product_route = route[route['product_id'] == product_id].sort_values('step_seq')
        
        if product_route.empty:
            continue
        
        total_steps = len(product_route)
        
        # 计算本周执行的步数
        ct = cycle_time_days.get(product_id, 100.0)
        steps_per_week = int(total_steps * week_duration_days / ct)
        
        # 取前 N 步（本周工序）
        weekly_steps = product_route.head(steps_per_week)
        
        for _, step in weekly_steps.iterrows():
            tg_id = step['tool_group_id']
            unit_hours = step['run_time_hr'] / max(step['batch_size'], 1)
            lot_hours = unit_hours * wafer_count
            
            if tg_id not in load_by_tg_product:
                load_by_tg_product[tg_id] = {}
            
            if product_id not in load_by_tg_product[tg_id]:
                load_by_tg_product[tg_id][product_id] = 0.0
            
            load_by_tg_product[tg_id][product_id] += lot_hours
    
    return load_by_tg_product


def merge_capacity_demand(
    wip_load: dict[str, dict[str, float]],
    new_input_load: dict[str, dict[str, float]],
    available_hours: dict[str, float],
    wip_lot_count: dict[str, int],
    wip_avg_wait: dict[str, float]
) -> list[WeeklyCapacityDemand]:
    """
    合并 WIP 负载 + 新投入负载 → 周产能需求
    """
    
    all_tg_ids = set(wip_load.keys()) | set(new_input_load.keys())
    
    demand_list: list[WeeklyCapacityDemand] = []
    
    for tg_id in all_tg_ids:
        # WIP 负载
        wip_hours_by_product = wip_load.get(tg_id, {})
        wip_total = sum(wip_hours_by_product.values())
        
        # 新投入负载
        new_hours_by_product = new_input_load.get(tg_id, {})
        new_total = sum(new_hours_by_product.values())
        
        # 合并产品分布
        demand_by_product = {}
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
        
        # 状态判断
        if loading_pct >= 100:
            status = "overload"
        elif loading_pct >= 85:
            status = "critical"
        elif loading_pct >= 75:
            status = "warning"
        else:
            status = "healthy"
        
        demand_list.append(WeeklyCapacityDemand(
            tool_group_id=tg_id,
            wip_remaining_hours=wip_total,
            new_input_hours=new_total,
            total_demand_hours=total_demand,
            demand_by_product=demand_by_product,
            available_hours=h,
            loading_pct=loading_pct,
            status=status,
            wip_lot_count=wip_lot_count.get(tg_id, 0),
            avg_wip_wait_hours=wip_avg_wait.get(tg_id, 0.0),
        ))
    
    # 排序（按负载降序）
    demand_list.sort(key=lambda x: x.loading_pct, reverse=True)
    
    return demand_list


def compute_input_recommendations(
    output_target: dict[str, float],
    predicted_output: list[OutputPrediction],
    cycle_time_days: dict[str, float],
    future_weeks: int = 4
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
        - 投入时间: Week 20 - 100天 = Week 10 → 已过去
        - 实际: Week 17 投入为 Week 27+ 产出准备
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
            # 所以本周投入是为 T+CT 周产出做准备
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
        inp.wip_lot_detail, inp.route, inp.output_completion_threshold
    )
    
    total_predicted = sum(p.predicted_wafers for p in output_predictions)
    
    # 2. 计算产出缺口
    output_gap = {}
    for product_id, target in inp.output_target.items():
        predicted = sum(p.predicted_wafers for p in output_predictions if p.product_id == product_id)
        output_gap[product_id] = target - predicted
    
    # 3. 计算 WIP 后续负载（核心）
    wip_load = compute_wip_remaining_load(inp.wip_lot_detail, inp.route)
    
    # 4. 计算新投入负载（按工序时间分布）
    new_input_load = {}
    if inp.new_input_plan:
        new_input_load = compute_new_input_load_distribution(
            inp.new_input_plan, inp.route, inp.cycle_time_days, inp.output_target_week
        )
    
    # 5. 合并产能需求
    wip_lot_count = inp.wip_lot_detail.groupby('current_tool_group')['lot_id'].count().to_dict()
    wip_avg_wait = inp.wip_lot_detail.groupby('current_tool_group')['wait_hours_so_far'].mean().to_dict()
    
    capacity_demand = merge_capacity_demand(
        wip_load, new_input_load, inp.available_hours, wip_lot_count, wip_avg_wait
    )
    
    # 6. 计算总体利用率
    total_demand = sum(d.total_demand_hours for d in capacity_demand)
    total_available = sum(d.available_hours for d in capacity_demand)
    overall_loading = (total_demand / total_available * 100) if total_available > 0 else 0
    
    # 7. 识别瓶颈
    critical_groups = [d.tool_group_id for d in capacity_demand if d.status in ("critical", "overload")]
    bottleneck_groups = [d.tool_group_id for d in capacity_demand if d.status == "overload"]
    
    # 8. 反推投入建议
    input_recommendations = compute_input_recommendations(
        inp.output_target, output_predictions, inp.cycle_time_days
    )
    
    # 9. 风险分析
    risk_summary = []
    
    # 产出缺口风险
    for product_id, gap in output_gap.items():
        if gap > 0:
            risk_summary.append(f"{product_id}: 产出缺口 {gap:.0f} 片，需新投入")
    
    # 瓶颈风险
    for tg_id in bottleneck_groups[:3]:
        tg_demand = next(d for d in capacity_demand if d.tool_group_id == tg_id)
        wip_share = tg_demand.wip_remaining_hours / tg_demand.total_demand_hours * 100
        risk_summary.append(f"{tg_id}: 过载 {tg_demand.loading_pct:.0f}%，WIP 占比 {wip_share:.0f}%")
    
    # 10. 可行性判断
    feasible = (
        len(bottleneck_groups) == 0 and
        all(gap <= 0 for gap in output_gap.values())  # 产出目标可达成
    )
    
    return OutputRCCPResult(
        output_predictions=output_predictions,
        total_predicted_output=total_predicted,
        output_gap=output_gap,
        capacity_demand=capacity_demand,
        overall_loading_pct=overall_loading,
        critical_groups=critical_groups,
        bottleneck_groups=bottleneck_groups,
        input_recommendations=input_recommendations,
        feasible=feasible,
        risk_summary=risk_summary,
        computed_at=datetime.utcnow(),
        metadata={
            "output_target_week": inp.output_target_week,
            "wip_lot_count": len(inp.wip_lot_detail),
            "wip_wafer_count": inp.wip_lot_detail['wafer_count'].sum(),
            "output_completion_threshold": inp.output_completion_threshold,
            "compute_time_seconds": (datetime.utcnow() - t0).total_seconds(),
        },
    )
```

---

### 3.2 Output Predictor（产出预测器）

```python
# engines/output_predictor.py

"""
Output Predictor - 产出预测模块

预测未来各周的产出量（基于当前 WIP 位置）
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
import numpy as np


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
            "week_id": self.week_id,
            "product_id": self.product_id,
            "predicted_wafers": round(self.predicted_wafers, 0),
            "source_wip_lots": self.source_wip_lots,
            "avg_percent_complete_now": round(self.avg_percent_complete_now, 2),
            "est_completion_week": self.est_completion_week,
        }


@dataclass
class OutputPredictionResult:
    """产出预测结果"""
    predictions: list[WeeklyOutputPrediction]
    predictions_by_week: dict[str, dict[str, float]]  # {week: {product: wafers}}
    computed_at: datetime
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "predictions": [p.to_dict() for p in self.predictions],
            "predictions_by_week": self.predictions_by_week,
            "computed_at": self.computed_at.isoformat(),
        }


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
    
    predictions: list[WeeklyOutputPrediction] = []
    predictions_by_week: dict[str, dict[str, float]] = {}
    
    for _, lot in wip_lot_detail.iterrows():
        lot_id = lot['lot_id']
        product_id = lot['product_id']
        current_step = lot['current_step_seq']
        wafer_count = lot['wafer_count']
        percent_complete = lot['percent_complete']
        
        # 获取制程路线
        product_route = route[route['product_id'] == product_id].sort_values('step_seq')
        
        if product_route.empty:
            continue
        
        total_steps = len(product_route)
        remaining_steps = total_steps - current_step
        
        # 估算剩余天数
        ct = cycle_time_days.get(product_id, 100.0)
        remaining_days = ct * (remaining_steps / total_steps)
        
        # 预计完成周
        current_week_num = int(current_week.split('-W')[1])
        completion_week_num = current_week_num + int(remaining_days / 7)
        completion_week = f"{current_week.split('-W')[0]}-W{completion_week_num}"
        
        # 按周汇总
        if completion_week not in predictions_by_week:
            predictions_by_week[completion_week] = {}
        
        if product_id not in predictions_by_week[completion_week]:
            predictions_by_week[completion_week][product_id] = 0.0
        
        predictions_by_week[completion_week][product_id] += wafer_count
        
        predictions.append(WeeklyOutputPrediction(
            week_id=completion_week,
            product_id=product_id,
            predicted_wafers=wafer_count,
            source_wip_lots=1,
            avg_percent_complete_now=percent_complete / 100,
            est_completion_week=completion_week,
        ))
    
    # 汇总相同周+产品的预测
    final_predictions: list[WeeklyOutputPrediction] = []
    
    for week_id in sorted(predictions_by_week.keys()):
        for product_id in predictions_by_week[week_id]:
            wafers = predictions_by_week[week_id][product_id]
            lots_count = len([p for p in predictions 
                             if p.week_id == week_id and p.product_id == product_id])
            avg_pct = np.mean([p.avg_percent_complete_now for p in predictions 
                              if p.week_id == week_id and p.product_id == product_id])
            
            final_predictions.append(WeeklyOutputPrediction(
                week_id=week_id,
                product_id=product_id,
                predicted_wafers=wafers,
                source_wip_lots=lots_count,
                avg_percent_complete_now=avg_pct,
                est_completion_week=week_id,
            ))
    
    return OutputPredictionResult(
        predictions=final_predictions,
        predictions_by_week=predictions_by_week,
        computed_at=datetime.utcnow(),
    )
```

---

### 3.3 Input Planner（反推投入）

```python
# engines/input_planner_output.py

"""
Input Planner (Output 视角) - 从产出目标反推投入计划

核心逻辑:
  为了在 Week T 产出 X 片，需要在 Week T-CT 投入多少？

输入:
  - 未来各周的产出目标
  - Cycle Time 基准
  - 当前 WIP 预测产出

输出:
  - 分周投入计划
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd


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
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "input_week": self.input_week,
            "product_id": self.product_id,
            "planned_wafers": round(self.planned_wafers, 0),
            "target_output_week": self.target_output_week,
            "target_output_wafers": round(self.target_output_wafers, 0),
            "wip_contribution": round(self.wip_contribution, 0),
            "gap_to_fill": round(self.gap_to_fill, 0),
        }


@dataclass
class InputPlannerOutputResult:
    """投入规划结果"""
    weekly_plans: list[WeeklyInputPlan]
    total_input_needed: dict[str, float]         # {product: total_input}
    plans_by_week: dict[str, dict[str, float]]   # {input_week: {product: wafers}}
    computed_at: datetime
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "weekly_plans": [p.to_dict() for p in self.weekly_plans],
            "total_input_needed": {k: round(v, 0) for k, v in self.total_input_needed.items()},
            "plans_by_week": self.plans_by_week,
            "computed_at": self.computed_at.isoformat(),
        }


def compute_input_plan_from_output_targets(
    inp: InputPlannerOutputInput
) -> InputPlannerOutputResult:
    """
    从产出目标反推投入计划
    
    公式:
      投入时间 = 产出时间 - CT
      投入量 = 产出目标 - WIP 预计产出
    """
    
    weekly_plans: list[WeeklyInputPlan] = []
    total_input: dict[str, float] = {}
    plans_by_week: dict[str, dict[str, float]] = {}
    
    # 解析当前周
    year, week_num = inp.current_week.split('-W')
    current_week_int = int(week_num)
    
    # 遍历未来各周的产出目标
    for output_week, product_targets in inp.output_targets.items():
        
        # 解析产出周
        out_year, out_week_num = output_week.split('-W')
        output_week_int = int(out_week_num)
        
        for product_id, target_wafers in product_targets.items():
            
            # 获取 WIP 预计产出
            wip_predicted = inp.wip_output_predictions.get(output_week, {}).get(product_id, 0.0)
            
            # 产出缺口
            gap = target_wafers - wip_predicted
            
            if gap <= 0:
                # WIP 预计产出已足够，无需新投入
                continue
            
            # 计算投入时间（产出时间 - CT）
            ct_days = inp.cycle_time_days.get(product_id, 100.0)
            ct_weeks = int(ct_days / 7)
            
            input_week_int = output_week_int - ct_weeks
            
            # 如果投入时间已过去，跳过
            if input_week_int < current_week_int:
                # 无法为该产出目标投入（太晚）
                # 记录风险但不生成投入计划
                weekly_plans.append(WeeklyInputPlan(
                    input_week="ALREADY_PASSED",
                    product_id=product_id,
                    planned_wafers=0.0,
                    target_output_week=output_week,
                    target_output_wafers=target_wafers,
                    wip_contribution=wip_predicted,
                    gap_to_fill=gap,
                ))
                continue
            
            # 检查是否在规划范围内
            if input_week_int > current_week_int + inp.planning_weeks:
                continue
            
            input_week = f"{year}-W{input_week_int}"
            
            # 投入量 = 缺口
            planned_wafers = gap
            
            # 汇总
            if product_id not in total_input:
                total_input[product_id] = 0.0
            total_input[product_id] += planned_wafers
            
            if input_week not in plans_by_week:
                plans_by_week[input_week] = {}
            if product_id not in plans_by_week[input_week]:
                plans_by_week[input_week][product_id] = 0.0
            plans_by_week[input_week][product_id] += planned_wafers
            
            weekly_plans.append(WeeklyInputPlan(
                input_week=input_week,
                product_id=product_id,
                planned_wafers=planned_wafers,
                target_output_week=output_week,
                target_output_wafers=target_wafers,
                wip_contribution=wip_predicted,
                gap_to_fill=gap,
            ))
    
    return InputPlannerOutputResult(
        weekly_plans=weekly_plans,
        total_input_needed=total_input,
        plans_by_week=plans_by_week,
        computed_at=datetime.utcnow(),
    )
```

---

## 四、API 端点设计

### 4.1 新增 API 端点

```python
# engines/server.py 新增端点

from fastapi import APIRouter

output_router = APIRouter(prefix="/output", tags=["Output Planning"])


@output_router.get("/target/list")
async def list_output_targets(
    plan_version: str = "current",
    weeks: int = 8
):
    """
    获取产出目标列表
    
    返回未来 N 周的产出目标
    """
    query = """
        SELECT time_window, product_id, target_wafers, priority, input_window
        FROM fact_output_target
        WHERE plan_version = :version
        ORDER BY time_window, product_id
    """
    result = await db.fetch_all(query, {"version": plan_version})
    
    # 按周分组
    targets_by_week = {}
    for row in result:
        week = row["time_window"]
        if week not in targets_by_week:
            targets_by_week[week] = []
        targets_by_week[week].append(row)
    
    return {
        "targets_by_week": targets_by_week,
        "plan_version": plan_version,
    }


@output_router.get("/prediction/weekly")
async def get_weekly_output_prediction(
    current_week: str,
    n_weeks: int = 8
):
    """
    获取未来 N 周产出预测
    
    基于 WIP 位置预测各周产出量
    """
    # 获取 WIP 数据
    wip_query = """
        SELECT * FROM fact_wip_lot_detail
        WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM fact_wip_lot_detail)
    """
    wip_df = pd.DataFrame(await db.fetch_all(wip_query))
    
    # 获取制程路线
    route_query = "SELECT * FROM dim_route WHERE route_version = 'current'"
    route_df = pd.DataFrame(await db.fetch_all(route_query))
    
    # 获取 CT 基准
    ct_query = """
        SELECT product_id, avg_cycle_time_days AS ct_days
        FROM dim_product_cycle_time
    """
    ct_data = await db.fetch_all(ct_query)
    cycle_time_days = {row["product_id"]: row["ct_days"] for row in ct_data}
    
    # 运行预测
    result = predict_output_for_next_n_weeks(
        wip_df, route_df, cycle_time_days, current_week, n_weeks
    )
    
    return result.to_dict()


@output_router.post("/rccp/compute")
async def compute_output_rccp(request: OutputRCCPRequest):
    """
    Output RCCP 计算
    
    输入:
      - output_target_week: 规划周
      - output_target: {product: target_wafers}
    
    输出:
      - output_predictions: 本周产出预测
      - capacity_demand: WIP 后续负载
      - input_recommendations: 投入建议
    """
    
    # 1. 获取 WIP 数据
    wip_data = await get_latest_wip_snapshot()
    wip_df = pd.DataFrame(wip_data)
    
    # 2. 获取制程路线
    route_df = await get_route_matrix()
    
    # 3. 获取可用产能
    available_hours = await get_available_hours()
    
    # 4. 获取 CT 基准
    cycle_time_days = await get_cycle_time_baseline()
    
    # 5. 构建 Input
    inp = OutputRCCPInput(
        output_target=request.output_target,
        output_target_week=request.output_target_week,
        wip_lot_detail=wip_df,
        route=route_df,
        cycle_time_days=cycle_time_days,
        available_hours=available_hours,
    )
    
    # 6. 计算
    result = run_output_rccp(inp)
    
    return result.to_dict()


@output_router.post("/input/plan")
async def plan_input_from_output(request: InputPlanRequest):
    """
    从产出目标反推投入计划
    
    输入:
      - output_targets: {week: {product: target}}
      - current_week: 当前周
    
    输出:
      - weekly_plans: 分周投入计划
      - total_input_needed: 各产品总投入需求
    """
    
    # 1. 获取 WIP 产出预测
    predictions = await get_weekly_output_prediction(request.current_week, request.planning_weeks)
    wip_output_predictions = predictions["predictions_by_week"]
    
    # 2. 获取 CT 基准
    cycle_time_days = await get_cycle_time_baseline()
    
    # 3. 构建 Input
    inp = InputPlannerOutputInput(
        output_targets=request.output_targets,
        wip_output_predictions=wip_output_predictions,
        cycle_time_days=cycle_time_days,
        current_week=request.current_week,
        planning_weeks=request.planning_weeks,
    )
    
    # 4. 计算
    result = compute_input_plan_from_output_targets(inp)
    
    return result.to_dict()


@output_router.get("/gap/analysis")
async def analyze_output_gap(
    target_week: str,
    product_id: str | None = None
):
    """
    产出缺口分析
    
    对比产出目标 vs WIP 预测产出
    """
    
    # 1. 获取产出目标
    target_query = """
        SELECT product_id, target_wafers
        FROM fact_output_target
        WHERE time_window = :week
        AND plan_version = 'current'
    """
    targets = await db.fetch_all(target_query, {"week": target_week})
    output_target = {row["product_id"]: row["target_wafers"] for row in targets}
    
    # 2. 获取 WIP 产出预测
    predictions = await get_weekly_output_prediction(target_week.split('-W')[0] + '-W' + str(int(target_week.split('-W')[1]) - 10), 10)
    wip_predicted = predictions["predictions_by_week"].get(target_week, {})
    
    # 3. 计算缺口
    gap_analysis = []
    for product, target in output_target.items():
        if product_id and product != product_id:
            continue
        
        predicted = wip_predicted.get(product, 0.0)
        gap = target - predicted
        
        gap_analysis.append({
            "product_id": product,
            "target_wafers": target,
            "predicted_wafers": predicted,
            "gap": gap,
            "gap_pct": round(gap / target * 100, 2) if target > 0 else 0,
            "needs_input": gap > 0,
        })
    
    return {
        "target_week": target_week,
        "gap_analysis": gap_analysis,
        "total_gap": sum(g["gap"] for g in gap_analysis if g["gap"] > 0),
    }
```

---

## 五、前端展示设计

### 5.1 产出预测面板

```jsx
// frontend/src/components/OutputPredictionPanel.jsx

function OutputPredictionPanel({ currentWeek }) {
  const [predictions, setPredictions] = useState(null);
  
  useEffect(() => {
    api.getOutputPrediction(currentWeek, 8).then(data => setPredictions(data));
  }, [currentWeek]);
  
  if (!predictions) return <div>Loading...</div>;
  
  return (
    <div className="output-prediction-panel">
      <h2>产出预测（未来 8 周）</h2>
      
      <div className="prediction-chart">
        {/* 堆叠柱状图：各周各产品预测产出 */}
        <StackedBarChart data={predictions.predictions_by_week} />
      </div>
      
      <table className="prediction-table">
        <thead>
          <tr>
            <th>周</th>
            <th>产品</th>
            <th>预测产出</th>
            <th>来源 Lot 数</th>
            <th>当前完成度</th>
          </tr>
        </thead>
        <tbody>
          {predictions.predictions.map(p => (
            <tr key={`${p.week_id}-${p.product_id}`}>
              <td>{p.week_id}</td>
              <td>{p.product_id}</td>
              <td>{p.predicted_wafers} 片</td>
              <td>{p.source_wip_lots}</td>
              <td>{p.avg_percent_complete_now}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

### 5.2 Output RCCP 结果面板

```jsx
// frontend/src/components/OutputRCCPResult.jsx

function OutputRCCPResult({ result }) {
  return (
    <div className="output-rccp-result">
      {/* 产出预测 vs 目标 */}
      <div className="output-summary">
        <h3>产出达成情况</h3>
        <div className="cards">
          <div className="card">
            <label>预测产出</label>
            <value>{result.total_predicted_output} 片</value>
          </div>
          <div className="card">
            <label>产出缺口</label>
            <value>{Object.values(result.output_gap).reduce((a,b) => a+b, 0)} 片</value>
            <status>{result.feasible ? '✅' : '⚠️'}</status>
          </div>
        </div>
        
        {/* 产出缺口详情 */}
        <table className="gap-table">
          <thead>
            <tr>
              <th>产品</th>
              <th>目标</th>
              <th>预测</th>
              <th>缺口</th>
              <th>需投入</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(result.output_gap).map(([product, gap]) => (
              <tr key={product}>
                <td>{product}</td>
                <td>{result.output_predictions.find(p => p.product_id === product)?.predicted_wafers + gap || gap}</td>
                <td>{result.output_predictions.find(p => p.product_id === product)?.predicted_wafers || 0}</td>
                <td className={gap > 0 ? 'negative' : 'positive'}>{gap > 0 ? `-${gap}` : `+${-gap}`}</td>
                <td>{result.input_recommendations[product] || 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      
      {/* 产能负载（Output 视角） */}
      <div className="capacity-demand">
        <h3>产能负载（基于 WIP 后续工序）</h3>
        
        {/* 关键区别展示 */}
        <div className="perspective-note">
          <strong>Output 视角计算：</strong>
          <code>需求 = WIP后续工序小时 + 新投入本周工序小时</code>
          <span>（而非 Input 视角的"投入×总工时"）</span>
        </div>
        
        <table className="demand-table">
          <thead>
            <tr>
              <th>机台组</th>
              <th>WIP 后续负载</th>
              <th>新投入负载</th>
              <th>总需求</th>
              <th>可用产能</th>
              <th>负载率</th>
              <th>状态</th>
            </tr>
          </thead>
          <tbody>
            {result.capacity_demand.map(d => (
              <tr key={d.tool_group_id} className={`status-${d.status}`}>
                <td>{d.tool_group_id}</td>
                <td>{d.wip_remaining_hours.toFixed(0)}h ({(d.wip_remaining_hours/d.total_demand_hours*100).toFixed(0)}%)</td>
                <td>{d.new_input_hours.toFixed(0)}h</td>
                <td>{d.total_demand_hours.toFixed(0)}h</td>
                <td>{d.available_hours.toFixed(0)}h</td>
                <td>{d.loading_pct.toFixed(1)}%</td>
                <td>
                  {d.status === 'overload' ? '🔴' : d.status === 'critical' ? '⚠️' : d.status === 'warning' ? '🟡' : '✅'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      
      {/* 投入建议 */}
      <div className="input-recommendations">
        <h3>投入建议（为未来产出准备）</h3>
        <table>
          <thead>
            <tr>
              <th>产品</th>
              <th>建议投入</th>
              <th>目标产出周</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(result.input_recommendations).map(([product, input]) => (
              input > 0 && (
                <tr key={product}>
                  <td>{product}</td>
                  <td>{input} 片</td>
                  <td>Week {parseInt(result.metadata.output_target_week.split('-W')[1]) + 14}</td>
                </tr>
              )
            ))}
          </tbody>
        </table>
      </div>
      
      {/* 风险汇总 */}
      {result.risk_summary.length > 0 && (
        <div className="risk-summary">
          <h3>⚠️ 风险提示</h3>
          <ul>
            {result.risk_summary.map(risk => (
              <li key={risk}>{risk}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
```

### 5.3 投入计划面板

```jsx
// frontend/src/components/InputPlanPanel.jsx

function InputPlanPanel({ currentWeek, outputTargets }) {
  const [inputPlan, setInputPlan] = useState(null);
  
  const generatePlan = () => {
    api.planInputFromOutput({
      output_targets: outputTargets,
      current_week: currentWeek,
      planning_weeks: 12
    }).then(data => setInputPlan(data));
  };
  
  return (
    <div className="input-plan-panel">
      <h2>投入计划（从产出目标反推）</h2>
      
      <button onClick={generatePlan}>生成投入计划</button>
      
      {inputPlan && (
        <div className="plan-results">
          {/* 投入时间线 */}
          <div className="input-timeline">
            <h3>投入时间线</h3>
            <TimelineChart data={inputPlan.plans_by_week} />
          </div>
          
          {/* 分周计划表 */}
          <table className="weekly-plan-table">
            <thead>
              <tr>
                <th>投入周</th>
                <th>产品</th>
                <th>投入量</th>
                <th>目标产出周</th>
                <th>目标产出量</th>
                <th>WIP 贡献</th>
                <th>缺口</th>
              </tr>
            </thead>
            <tbody>
              {inputPlan.weekly_plans.map(plan => (
                <tr key={`${plan.input_week}-${plan.product_id}`}>
                  <td>{plan.input_week}</td>
                  <td>{plan.product_id}</td>
                  <td>{plan.planned_wafers} 片</td>
                  <td>{plan.target_output_week}</td>
                  <td>{plan.target_output_wafers} 片</td>
                  <td>{plan.wip_contribution} 片</td>
                  <td>{plan.gap_to_fill} 片</td>
                </tr>
              ))}
            </tbody>
          </table>
          
          {/* 总投入需求 */}
          <div className="total-input">
            <h3>各产品总投入需求</h3>
            <table>
              <thead>
                <tr><th>产品</th><th>总投入需求</th></tr>
              </thead>
              <tbody>
                {Object.entries(inputPlan.total_input_needed).map(([product, total]) => (
                  <tr key={product}>
                    <td>{product}</td>
                    <td>{total} 片</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
```

---

## 六、dbt 模型设计

### 6.1 WIP 产出预测模型

```sql
-- data/dbt/models/wip_output_prediction.sql

{{ config(
    materialized='table',
    unique_key='prediction_ts,product_id,output_week'
) }}

WITH wip_snapshot AS (
    SELECT * FROM {{ ref('fact_wip_lot_detail') }}
    WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM {{ ref('fact_wip_lot_detail') })
),

product_route AS (
    SELECT 
        product_id,
        COUNT(*) AS total_steps,
        MIN(step_seq) AS min_step,
        MAX(step_seq) AS max_step
    FROM {{ ref('dim_route') }}
    WHERE route_version = 'current'
    GROUP BY product_id
),

cycle_time AS (
    SELECT product_id, avg_cycle_time_days AS ct_days
    FROM {{ ref('dim_product_cycle_time') }}
),

-- 计算 Lot 预计完成周
lot_completion_prediction AS (
    SELECT
        w.lot_id,
        w.product_id,
        w.current_step_seq,
        w.wafer_count,
        w.percent_complete,
        w.lot_status,
        
        -- 剩余步数
        pr.total_steps - w.current_step_seq AS remaining_steps,
        
        -- 剩余天数 = CT × (剩余步数 / 总步数)
        ct.ct_days * (pr.total_steps - w.current_step_seq) / pr.total_steps AS remaining_days,
        
        -- 预计完成周
        DATE_TRUNC('week', CURRENT_TIMESTAMP + (ct.ct_days * (pr.total_steps - w.current_step_seq) / pr.total_steps) * INTERVAL '1 day')::VARCHAR AS output_week
        
    FROM wip_snapshot w
    JOIN product_route pr ON w.product_id = pr.product_id
    JOIN cycle_time ct ON w.product_id = ct.product_id
),

-- 按周×产品聚合
output_prediction AS (
    SELECT
        CURRENT_TIMESTAMP AS prediction_ts,
        product_id,
        output_week,
        
        SUM(wafer_count) AS predicted_wafers,
        COUNT(*) AS wip_lots,
        AVG(percent_complete) AS avg_percent_complete,
        MIN(percent_complete) AS min_percent_complete,
        
        SUM(CASE WHEN lot_status = 'HOLD' THEN 1 ELSE 0 END) AS hold_lot_count,
        
        CASE
            WHEN AVG(percent_complete) >= 80 THEN 'HIGH'
            WHEN AVG(percent_complete) >= 60 THEN 'MEDIUM'
            ELSE 'LOW'
        END AS confidence,
        
        CASE
            WHEN SUM(CASE WHEN lot_status = 'HOLD' THEN 1 ELSE 0 END) > 0 THEN 'HIGH'
            WHEN AVG(percent_complete) < 60 THEN 'MEDIUM'
            ELSE 'LOW'
        END AS risk_flag
        
    FROM lot_completion_prediction
    GROUP BY product_id, output_week
)

SELECT * FROM output_prediction
```

### 6.2 周产能需求模型

```sql
-- data/dbt/models/weekly_capacity_demand.sql

{{ config(
    materialized='table',
    unique_key='week_id,tool_group_id'
) }}

WITH current_week AS (
    SELECT DATE_TRUNC('week', CURRENT_TIMESTAMP)::VARCHAR AS week_id
),

-- WIP 后续负载
wip_remaining AS (
    SELECT * FROM {{ ref('mart_wip_remaining_load') }}
    WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM {{ ref('mart_wip_remaining_load') })
),

-- 可用产能
available_capacity AS (
    SELECT
        tg.tool_group_id,
        tg.n_machines * 24 * 7 * COALESCE(o.availability, 0.85) AS available_hours
    FROM {{ ref('dim_tool_group') }} tg
    LEFT JOIN {{ ref('fact_oee_daily') }} o 
        ON o.tool_group_id = tg.tool_group_id
        AND o.fact_date = (SELECT MAX(fact_date) FROM {{ ref('fact_oee_daily') })
),

-- 合并计算
capacity_demand AS (
    SELECT
        cw.week_id,
        wr.tool_group_id,
        
        wr.remaining_hours AS wip_remaining_hours,
        0 AS new_input_hours,  -- 可选：从投入计划计算
        wr.remaining_hours AS total_demand_hours,
        
        ac.available_hours,
        
        ROUND(wr.remaining_hours / ac.available_hours * 100, 2) AS loading_pct,
        
        CASE
            WHEN wr.remaining_hours / ac.available_hours >= 1.0 THEN 'overload'
            WHEN wr.remaining_hours / ac.available_hours >= 0.85 THEN 'critical'
            WHEN wr.remaining_hours / ac.available_hours >= 0.75 THEN 'warning'
            ELSE 'healthy'
        END AS status
        
    FROM current_week cw
    CROSS JOIN available_capacity ac
    LEFT JOIN wip_remaining wr ON wr.tool_group_id = ac.tool_group_id
)

SELECT * FROM capacity_demand
```

---

## 七、数据同步脚本

### 7.1 WIP 快照同步（增强）

```python
# scripts/sync_wip_enhanced.py

"""
WIP 快照同步（增强版）

新增字段:
  - percent_complete: 完成百分比
  - remaining_hours_by_tg: 后续工序小时（按机台组）
"""

def enrich_wip_with_output_perspective(
    lot_data: list[dict],
    route_df: pd.DataFrame,
    cycle_time_df: pd.DataFrame
) -> pd.DataFrame:
    """
    从 Output 视角补充 WIP 信息
    
    新增计算:
      1. percent_complete = current_step / total_steps × 100
      2. remaining_hours_by_tg = {tg_id: hours} 后续工序小时
      3. est_completion_week = 当前周 + 剩余天数/7
    """
    
    enriched = []
    
    for lot in lot_data:
        product_id = lot["product_id"]
        current_step = lot["operation_seq"]
        wafer_count = lot["wafer_count"]
        
        # 获取产品制程路线
        product_route = route_df[route_df["product_id"] == product_id]
        
        total_steps = len(product_route)
        remaining_steps = total_steps - current_step
        
        # 完成百分比
        percent_complete = round(current_step / total_steps * 100, 2) if total_steps > 0 else 0
        
        # 后续工序小时（按机台组）
        remaining_hours_by_tg = {}
        remaining_route = product_route[product_route["step_seq"] >= current_step]
        
        for _, step in remaining_route.iterrows():
            tg_id = step["tool_group_id"]
            unit_hours = step["run_time_hr"] / max(step["batch_size"], 1)
            
            if tg_id not in remaining_hours_by_tg:
                remaining_hours_by_tg[tg_id] = 0.0
            
            remaining_hours_by_tg[tg_id] += unit_hours * wafer_count
        
        # 总剩余小时
        remaining_hours = sum(remaining_hours_by_tg.values())
        
        # 预计完成周
        ct_days = cycle_time_df.get(product_id, 100.0)
        remaining_days = ct_days * remaining_steps / total_steps
        est_completion_date = datetime.now() + timedelta(days=remaining_days)
        est_completion_week = est_completion_date.strftime("%Y-W%V")
        
        enriched.append({
            "lot_id": lot["lot_id"],
            "product_id": product_id,
            "current_step_seq": current_step,
            "current_tool_group": lot["location"],
            "lot_status": lot["status"],
            "wafer_count": wafer_count,
            "percent_complete": percent_complete,
            "remaining_hours": remaining_hours,
            "remaining_hours_by_tg": json.dumps(remaining_hours_by_tg),
            "est_completion_week": est_completion_week,
        })
    
    return pd.DataFrame(enriched)
```

---

## 八、实施计划

### 8.1 分阶段实施

| 阶段 | 任务 | 周期 | 产出 |
|------|------|------|------|
| **Phase 1** | 数据模型转换 | Week 1-2 | fact_output_target, mart_wip_output_prediction |
| **Phase 2** | Output RCCP Engine | Week 3-4 | output_rccp.py, API 端点 |
| **Phase 3** | Output Predictor | Week 5-6 | 产出预测模块 |
| **Phase 4** | Input Planner (反推) | Week 7-8 | 投入计划反推模块 |
| **Phase 5** | 前端改造 | Week 9-10 | 产出预测面板、Output RCCP 结果面板 |
| **Phase 6** | dbt 模型 | Week 11-12 | wip_output_prediction, weekly_capacity_demand |
| **Phase 7** | 集成测试 | Week 13-14 | 全流程验证 |

### 8.2 数据依赖

| 数据源 | 需要字段 | 同步频率 |
|--------|---------|---------|
| **MPS** | 产出目标（按周） | 每 4 小时 |
| **MES Lot** | lot_id, current_operation, status | 每 5 分钟 |
| **MES Queue** | queue_position, wait_time | 每 5 分钟 |
| **Cycle Time 基准** | product_id, avg_ct_days | 静态数据 |
| **Route** | step_seq, tool_group_id, run_time_hr | 静态数据 |

---

## 九、对比总结

| 维度 | **Input 视角（旧）** | **Output 视角（新）** |
|------|---------------------|---------------------|
| **规划输入** | 投入计划 | **产出目标** |
| **产能计算** | 投入 × 总工时 | **WIP 后续工序小时** |
| **时间关系** | 投入时间 = 规划周 | 产出时间 = 规划周 |
| **WIP 角色** | 未考虑 | **核心输入** |
| **CT 处理** | 未考虑 | **反推投入时间** |
| **物理意义** | 错误（投入瞬间占用） | **正确（持续占用）** |
| **适用场景** | 短 CT（Logic） | **长 CT（存储芯片）** |

---

## 十、关键公式汇总

| 公式 | 说明 |
|------|------|
| `percent_complete = current_step / total_steps × 100` | Lot 完成度 |
| `remaining_hours[tg] = Σ_{step>=current} hours[step, tg]` | WIP 后续负载 |
| `demand[week] = Σ WIP后续工序小时 + Σ 新投入本周工序小时` | 周产能需求 |
| `predicted_output[week] = Σ_{lot完成度>=80%} wafer_count` | 产出预测 |
| `input_week = output_week - CT/7` | 反推投入时间 |
| `input_needed = output_target - wip_predicted` | 投入缺口 |

---

*文档版本: v2.0 | 最后更新: 2026-04-28 | Output 视角产能规划系统设计*