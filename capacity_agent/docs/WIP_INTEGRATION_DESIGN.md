# WIP Integration Design - 存储芯片产能规划 WIP 数据模型与引擎增强

> **版本**: v1.0  
> **日期**: 2026-04-28  
> **适用场景**: 存储芯片制造（CT = 90-120天，300+工序，高重入）

---

## 一、问题定义

### 1.1 存储芯片制造特性

| 参数 | 存储芯片 | Logic 芯片 |
|------|---------|-----------|
| **Cycle Time (CT)** | 90-120 天 | 30-60 天 |
| **工序数** | 300-500 步 | 50-100 步 |
| **光刻重入次数** | 20-40 次 | 5-15 次 |
| **WIP 占产能力** | 极大（持续占用机台数周） | 较小 |

### 1.2 当前系统的计算盲区

**现有 RCCP 公式**：
```
demand_hours[j] = Σ_i new_input[i] × hours_per_wafer[i,j]
loading[j] = demand_hours[j] / available_hours[j]
```

**遗漏的 WIP 负载**：
```
wip_hours[j] = Σ_lot wip_remaining_hours[lot, j]  ← 未计算
```

**后果**：
- 系统显示 loading = 75%（健康）
- 实际 loading = 95%（过载，WIP 累积风险）

---

## 二、完整 WIP 数据模型

### 2.1 数据库 Schema 增强

#### 2.1.1 Lot 级 WIP 详情表

```sql
-- Lot 级 WIP 详情（核心表）
CREATE TABLE IF NOT EXISTS fact_wip_lot_detail (
    snapshot_ts       TIMESTAMPTZ NOT NULL,        -- 快照时间戳
    lot_id            VARCHAR(64) NOT NULL,        -- Lot 批号
    product_id        VARCHAR(64) NOT NULL,        -- 产品ID
    path_id           VARCHAR(64) DEFAULT 'default', -- 制程路径
    
    -- 当前位置
    current_step_seq  INT NOT NULL,                -- 当前工序序号 (1-500)
    current_step_name VARCHAR(128),                -- 当前工序名称
    current_tool_group VARCHAR(64),                -- 当前所在机台组
    lot_status        VARCHAR(32) NOT NULL,        -- WAIT/RUN/HOLD/MOVE
    
    -- Queue 信息
    queue_position    INT,                         -- 队列位置 (1=最先)
    queue_entry_ts    TIMESTAMPTZ,                 -- 进入队列时间
    wait_hours_so_far NUMERIC(8,2),                -- 已等待小时数
    
    -- 数量与进度
    wafer_count       INT NOT NULL,                -- Lot 内晶圆数
    good_wafer_count  INT,                         -- 良品数（用于考虑良率）
    percent_complete  NUMERIC(5,2),                -- 完成百分比
    
    -- 剩余工序预测
    remaining_steps   INT,                         -- 剩余工序数
    remaining_hours   NUMERIC(10,2),               -- 剩余总小时数（关键字段）
    est_completion_ts TIMESTAMPTZ,                 -- 预计完成时间
    
    -- 来源追溯
    start_ts          TIMESTAMPTZ,                 -- Lot 开始时间
    input_week        VARCHAR(16),                 -- 投入周 (2026-W17)
    
    PRIMARY KEY (snapshot_ts, lot_id)
);

-- 索引
CREATE INDEX idx_wip_lot_product ON fact_wip_lot_detail(product_id, snapshot_ts DESC);
CREATE INDEX idx_wip_lot_toolgroup ON fact_wip_lot_detail(current_tool_group, lot_status, snapshot_ts DESC);
CREATE INDEX idx_wip_lot_status ON fact_wip_lot_detail(lot_status, snapshot_ts DESC);
```

#### 2.1.2 机台组 WIP 队列表

```sql
-- 机台组级 WIP 队列聚合（每小时刷新）
CREATE TABLE IF NOT EXISTS fact_wip_queue_hourly (
    snapshot_ts       TIMESTAMPTZ NOT NULL,
    tool_group_id     VARCHAR(64) NOT NULL,
    
    -- 队列统计
    wip_waiting       INT NOT NULL,                -- 等待中的 Lot 数
    wip_wafers        INT NOT NULL,                -- 等待中的晶圆数
    avg_queue_position NUMERIC(6,2),
    avg_wait_hours    NUMERIC(6,2),                -- 平均已等待时间
    max_wait_hours    NUMERIC(8,2),                -- 最大等待时间
    oldest_lot_age    NUMERIC(8,2),                -- 最老 Lot 的等待时间
    
    -- 运行中
    wip_running       INT NOT NULL,                -- 正在运行的 Lot 数
    running_wafers    INT NOT NULL,
    
    -- HOLD 状态（异常）
    wip_hold          INT NOT NULL,                -- 持 Hold 的 Lot 数
    hold_wafers       INT NOT NULL,
    hold_reason_top   VARCHAR(128),                -- 主要 Hold 原因
    
    -- 负载预测
    pending_hours     NUMERIC(10,2) NOT NULL,      -- 队列中待加工小时数
    running_hours     NUMERIC(10,2),               -- 正在加工的小时数（估算）
    
    PRIMARY KEY (snapshot_ts, tool_group_id)
);

CREATE INDEX idx_wip_queue_latest ON fact_wip_queue_hourly(tool_group_id, snapshot_ts DESC);
```

#### 2.1.3 WIP 后续负载表（关键派生表）

```sql
-- WIP 后续负载（按机台组 × 产品聚合）
-- 用于 RCCP 计算：wip_remaining_hours[tool_group, product]
CREATE TABLE IF NOT EXISTS mart_wip_remaining_load (
    snapshot_ts       TIMESTAMPTZ NOT NULL,
    tool_group_id     VARCHAR(64) NOT NULL,
    product_id        VARCHAR(64) NOT NULL,
    
    -- 负载分解
    wip_lots          INT NOT NULL,                -- Lot 数
    wip_wafers        INT NOT NULL,                -- 晶圆数
    
    -- 后续工序中需要经过该机台组的负载
    remaining_visits  INT NOT NULL,                -- 剩余访问次数
    remaining_hours   NUMERIC(10,2) NOT NULL,      -- 剩余小时数（核心字段）
    
    -- 时间分布
    hours_next_24h    NUMERIC(8,2),                -- 未来24小时预计到达
    hours_next_7d     NUMERIC(10,2),               -- 未来7天预计到达
    hours_next_30d    NUMERIC(12,2),               -- 未来30天预计到达
    
    -- 风险标识
    is_bottleneck_risk BOOLEAN DEFAULT FALSE,      -- 是否瓶颈风险
    
    PRIMARY KEY (snapshot_ts, tool_group_id, product_id)
);

CREATE INDEX idx_wip_load_latest ON mart_wip_remaining_load(tool_group_id, snapshot_ts DESC);
```

#### 2.1.4 产品-机台组 WIP 状态汇总

```sql
-- 全厂 WIP 概览视图
CREATE OR REPLACE VIEW v_wip_overview AS
SELECT
    tg.tool_group_id,
    tg.tool_group_name,
    tg.area,
    tg.n_machines,
    
    -- WIP 统计
    COALESCE(q.wip_waiting, 0) AS wip_waiting_lots,
    COALESCE(q.wip_wafers, 0) AS wip_waiting_wafers,
    COALESCE(q.avg_wait_hours, 0) AS avg_wait_hours,
    COALESCE(q.wip_hold, 0) AS wip_hold_lots,
    
    -- 负载
    COALESCE(w.remaining_hours, 0) AS wip_remaining_hours,
    
    -- 与产能对比
    tg.n_machines * 24 * 7 * COALESCE(o.availability, 0.85) AS weekly_available_hours,
    ROUND(COALESCE(w.remaining_hours, 0) / 
          (tg.n_machines * 24 * 7 * COALESCE(o.availability, 0.85)) * 100, 2) 
          AS wip_loading_pct,
    
    q.snapshot_ts AS last_update
    
FROM dim_tool_group tg
LEFT JOIN fact_wip_queue_hourly q 
    ON q.tool_group_id = tg.tool_group_id 
    AND q.snapshot_ts = (
        SELECT MAX(snapshot_ts) FROM fact_wip_queue_hourly
    )
LEFT JOIN (
    SELECT tool_group_id, SUM(remaining_hours) AS remaining_hours
    FROM mart_wip_remaining_load
    WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM mart_wip_remaining_load)
    GROUP BY tool_group_id
) w ON w.tool_group_id = tg.tool_group_id
LEFT JOIN fact_oee_daily o 
    ON o.tool_group_id = tg.tool_group_id 
    AND o.fact_date = (SELECT MAX(fact_date) FROM fact_oee_daily)
WHERE tg.is_active = TRUE;
```

---

### 2.2 MES 数据同步脚本

#### 2.2.1 Lot 状态同步

```python
# scripts/sync_wip_from_mes.py

"""
MES WIP 数据同步脚本

数据源: MES Lot Transaction API
频率: 每 5 分钟快照
"""

import os
import sys
import json
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

logger = logging.getLogger(__name__)

# MES API 配置
MES_API_URL = os.environ.get("MES_API_URL", "http://mes-api.internal/api/v1")
MES_API_KEY = os.environ.get("MES_API_KEY")

# PostgreSQL 配置
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", 5432))
PG_DATABASE = os.environ.get("PG_DATABASE", "capacity_db")
PG_USER = os.environ.get("PG_USER", "capacity")
PG_PASSWORD = os.environ.get("PG_PASSWORD")


def fetch_lot_status_from_mes() -> list[dict[str, Any]]:
    """
    从 MES 获取所有活跃 Lot 的状态
    
    MES API 返回格式:
    {
        "lot_id": "L12345",
        "product_id": "28nm_DRAM_A",
        "current_operation": "OP230_LITHO_2",
        "operation_seq": 230,
        "status": "WAIT",
        "location": "LITHO_01",
        "wafer_count": 25,
        "good_wafers": 24,
        "queue_position": 3,
        "start_time": "2026-04-01T08:00:00Z",
        "track_in_time": "2026-04-28T15:30:00Z"
    }
    """
    import requests
    
    headers = {"Authorization": f"Bearer {MES_API_KEY}"}
    
    # 查询所有活跃 Lot (非 COMPLETE 状态)
    params = {
        "status": "ACTIVE",  # WAIT, RUN, HOLD, MOVE
        "fields": [
            "lot_id", "product_id", "current_operation", "operation_seq",
            "status", "location", "wafer_count", "good_wafers",
            "queue_position", "start_time", "track_in_time"
        ]
    }
    
    response = requests.post(
        f"{MES_API_URL}/lots/query",
        headers=headers,
        json=params,
        timeout=30
    )
    
    if response.status_code != 200:
        raise RuntimeError(f"MES API error: {response.status_code}")
    
    return response.json()["lots"]


def enrich_lot_with_route_info(
    lot_data: list[dict], 
    route_df: pd.DataFrame,
    product_df: pd.DataFrame
) -> pd.DataFrame:
    """
    补充 Lot 的剩余工序信息
    
    计算:
    - remaining_steps: 剩余工序数
    - remaining_hours: 剩余总小时数（按机台组分解）
    - percent_complete: 完成百分比
    """
    enriched = []
    
    for lot in lot_data:
        lot_id = lot["lot_id"]
        product_id = lot["product_id"]
        current_seq = lot["operation_seq"]
        wafer_count = lot["wafer_count"]
        
        # 获取该产品的完整制程路线
        product_route = route_df[
            (route_df["product_id"] == product_id) &
            (route_df["step_seq"] >= current_seq)
        ]
        
        total_steps = route_df[route_df["product_id"] == product_id]["step_seq"].max()
        remaining_steps = total_steps - current_seq
        
        # 计算剩余小时数（按机台组聚合）
        remaining_hours = (
            product_route["run_time_hr"] / product_route["batch_size"] * wafer_count
        ).sum()
        
        # 计算完成百分比
        completed_steps = current_seq
        percent_complete = round(completed_steps / total_steps * 100, 2) if total_steps > 0 else 0
        
        # 预计完成时间（基于历史 CT）
        avg_ct_hours = 100 * 24  # 存储 CT 约 100 天
        est_completion = datetime.now(timezone.utc) + timedelta(hours=avg_ct_hours * (1 - percent_complete/100))
        
        enriched.append({
            "lot_id": lot_id,
            "product_id": product_id,
            "current_step_seq": current_seq,
            "current_tool_group": lot["location"],
            "lot_status": lot["status"],
            "queue_position": lot.get("queue_position"),
            "queue_entry_ts": lot.get("track_in_time"),
            "wait_hours_so_far": calculate_wait_hours(lot),
            "wafer_count": wafer_count,
            "good_wafer_count": lot.get("good_wafers"),
            "percent_complete": percent_complete,
            "remaining_steps": remaining_steps,
            "remaining_hours": remaining_hours,
            "est_completion_ts": est_completion.isoformat(),
            "start_ts": lot["start_time"],
            "input_week": parse_input_week(lot["start_time"]),
        })
    
    return pd.DataFrame(enriched)


def calculate_wait_hours(lot: dict) -> float:
    """计算 Lot 已等待小时数"""
    if lot.get("track_in_time") and lot["status"] == "WAIT":
        track_in = datetime.fromisoformat(lot["track_in_time"].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return round((now - track_in).total_seconds() / 3600, 2)
    return 0.0


def parse_input_week(start_time: str) -> str:
    """解析投入周"""
    from datetime import datetime
    dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    return dt.strftime("%Y-W%V")


def save_wip_snapshot(df: pd.DataFrame, snapshot_ts: datetime):
    """保存 WIP 快照到数据库"""
    
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, database=PG_DATABASE,
        user=PG_USER, password=PG_PASSWORD
    )
    
    try:
        # 插入 Lot 详情
        lot_records = [
            (
                snapshot_ts,
                row["lot_id"],
                row["product_id"],
                row["current_step_seq"],
                row["current_tool_group"],
                row["lot_status"],
                row["queue_position"],
                row["queue_entry_ts"],
                row["wait_hours_so_far"],
                row["wafer_count"],
                row["good_wafer_count"],
                row["percent_complete"],
                row["remaining_steps"],
                row["remaining_hours"],
                row["est_completion_ts"],
                row["start_ts"],
                row["input_week"],
            )
            for _, row in df.iterrows()
        ]
        
        execute_values(
            conn,
            """
            INSERT INTO fact_wip_lot_detail (
                snapshot_ts, lot_id, product_id, current_step_seq,
                current_tool_group, lot_status, queue_position,
                queue_entry_ts, wait_hours_so_far, wafer_count,
                good_wafer_count, percent_complete, remaining_steps,
                remaining_hours, est_completion_ts, start_ts, input_week
            ) VALUES %s
            """,
            lot_records
        )
        
        conn.commit()
        logger.info(f"Saved {len(lot_records)} lot records at {snapshot_ts}")
        
    finally:
        conn.close()


def main():
    """主同步流程"""
    snapshot_ts = datetime.now(timezone.utc)
    
    logger.info(f"Starting WIP sync at {snapshot_ts}")
    
    # 1. 从 MES 获取 Lot 状态
    lot_data = fetch_lot_status_from_mes()
    logger.info(f"Fetched {len(lot_data)} lots from MES")
    
    # 2. 加载制程路线数据
    route_df = pd.read_sql("SELECT * FROM dim_route WHERE route_version = 'current'", PG_CONNECTION)
    product_df = pd.read_sql("SELECT * FROM dim_product WHERE is_active = TRUE", PG_CONNECTION)
    
    # 3. 补充剩余工序信息
    enriched_df = enrich_lot_with_route_info(lot_data, route_df, product_df)
    
    # 4. 保存快照
    save_wip_snapshot(enriched_df, snapshot_ts)
    
    logger.info("WIP sync completed")


if __name__ == "__main__":
    main()
```

---

### 2.3 dbt 模型：计算 WIP 后续负载

#### 2.3.1 机台组队列聚合

```sql
-- data/dbt/models/wip_queue_hourly.sql

{{ config(
    materialized='table',
    unique_key='snapshot_ts,tool_group_id',
    indexes=[
        {'columns': ['tool_group_id', 'snapshot_ts desc']}
    ]
) }}

WITH latest_snapshot AS (
    SELECT MAX(snapshot_ts) AS latest_ts
    FROM {{ ref('fact_wip_lot_detail') }}
),

wip_by_toolgroup AS (
    SELECT
        s.latest_ts AS snapshot_ts,
        w.current_tool_group AS tool_group_id,
        w.lot_status,
        
        COUNT(DISTINCT w.lot_id) AS lot_count,
        SUM(w.wafer_count) AS wafers,
        AVG(w.wait_hours_so_far) AS avg_wait,
        MAX(w.wait_hours_so_far) AS max_wait,
        
        -- 计算待加工小时数
        SUM(
            CASE WHEN w.lot_status = 'WAIT'
            THEN w.wafer_count * (
                SELECT AVG(run_time_hr / batch_size)
                FROM {{ ref('dim_route') }}
                WHERE tool_group_id = w.current_tool_group
                AND route_version = 'current'
            )
            ELSE 0 END
        ) AS pending_hours
        
    FROM {{ ref('fact_wip_lot_detail') }} w
    CROSS JOIN latest_snapshot s
    WHERE w.snapshot_ts = s.latest_ts
    GROUP BY s.latest_ts, w.current_tool_group, w.lot_status
)

SELECT
    snapshot_ts,
    tool_group_id,
    
    SUM(CASE WHEN lot_status = 'WAIT' THEN lot_count ELSE 0 END) AS wip_waiting,
    SUM(CASE WHEN lot_status = 'WAIT' THEN wafers ELSE 0 END) AS wip_wafers,
    AVG(CASE WHEN lot_status = 'WAIT' THEN avg_wait ELSE NULL END) AS avg_wait_hours,
    MAX(CASE WHEN lot_status = 'WAIT' THEN max_wait ELSE 0 END) AS max_wait_hours,
    
    SUM(CASE WHEN lot_status = 'RUN' THEN lot_count ELSE 0 END) AS wip_running,
    SUM(CASE WHEN lot_status = 'RUN' THEN wafers ELSE 0 END) AS running_wafers,
    
    SUM(CASE WHEN lot_status = 'HOLD' THEN lot_count ELSE 0 END) AS wip_hold,
    SUM(CASE WHEN lot_status = 'HOLD' THEN wafers ELSE 0 END) AS hold_wafers,
    
    SUM(pending_hours) AS pending_hours

FROM wip_by_toolgroup
GROUP BY snapshot_ts, tool_group_id
```

#### 2.3.2 WIP 后续负载计算（核心模型）

```sql
-- data/dbt/models/wip_remaining_load.sql

{{ config(
    materialized='table',
    unique_key='snapshot_ts,tool_group_id,product_id'
) }}

-- 计算 WIP 在后续工序中对各机台组的负载

WITH latest_snapshot AS (
    SELECT MAX(snapshot_ts) AS latest_ts
    FROM {{ ref('fact_wip_lot_detail') }}
),

-- 每个 Lot 的剩余工序路径
lot_remaining_route AS (
    SELECT
        w.lot_id,
        w.product_id,
        w.wafer_count,
        w.current_step_seq,
        r.tool_group_id,
        r.step_seq,
        r.run_time_hr / r.batch_size AS unit_hours,
        
        -- 该工序是否在 Lot 当前位置之后
        CASE WHEN r.step_seq >= w.current_step_seq THEN 1 ELSE 0 END AS is_remaining
        
    FROM {{ ref('fact_wip_lot_detail') }} w
    CROSS JOIN latest_snapshot s
    JOIN {{ ref('dim_route') }} r
        ON r.product_id = w.product_id
        AND r.route_version = 'current'
    WHERE w.snapshot_ts = s.latest_ts
),

-- 按 机台组 × 产品 聚合 WIP 后续负载
wip_remaining_load AS (
    SELECT
        s.latest_ts AS snapshot_ts,
        lrr.tool_group_id,
        lrr.product_id,
        
        COUNT(DISTINCT CASE WHEN lrr.is_remaining = 1 THEN lrr.lot_id ELSE NULL END) AS wip_lots,
        SUM(CASE WHEN lrr.is_remaining = 1 THEN lrr.wafer_count ELSE 0 END) AS wip_wafers,
        
        -- 剩余访问次数
        COUNT(CASE WHEN lrr.is_remaining = 1 THEN 1 ELSE NULL END) AS remaining_visits,
        
        -- 剩余小时数（核心指标）
        SUM(CASE WHEN lrr.is_remaining = 1 THEN lrr.wafer_count * lrr.unit_hours ELSE 0 END) 
            AS remaining_hours
        
    FROM lot_remaining_route lrr
    CROSS JOIN latest_snapshot s
    GROUP BY s.latest_ts, lrr.tool_group_id, lrr.product_id
),

-- 计算未来时间窗口的负载分布
time_distribution AS (
    SELECT
        wrl.*,
        
        -- 未来 24h/7d/30d 的负载估算（基于历史到达分布）
        -- 假设均匀分布: hours_per_day = remaining_hours / remaining_days
        LEAST(wrl.remaining_hours, wrl.remaining_hours * 24.0 / 100.0) AS hours_next_24h,
        LEAST(wrl.remaining_hours, wrl.remaining_hours * 7.0 / 100.0) AS hours_next_7d,
        wrl.remaining_hours * 30.0 / 100.0 AS hours_next_30d
        
    FROM wip_remaining_load wrl
)

SELECT
    td.*,
    
    -- 瓶颈风险标识
    CASE WHEN td.remaining_hours > (
        SELECT tg.n_machines * 168 * COALESCE(o.availability, 0.85)
        FROM {{ ref('dim_tool_group') }} tg
        LEFT JOIN {{ ref('fact_oee_daily') }} o 
            ON o.tool_group_id = tg.tool_group_id
            AND o.fact_date = (SELECT MAX(fact_date) FROM {{ ref('fact_oee_daily') }})
        WHERE tg.tool_group_id = td.tool_group_id
    ) * 0.85 THEN TRUE ELSE FALSE END AS is_bottleneck_risk

FROM time_distribution td
```

---

## 三、RCCP 引擎增强

### 3.1 输入数据结构增强

```python
# engines/rccp.py 增强

from dataclasses import dataclass, field
from typing import Any
import pandas as pd

@dataclass
class RCCPInput:
    """RCCP 输入（增强版）"""
    
    # 原有字段
    demand_plan: dict[str, float]              # {product_id: wafer_count} - 新投入计划
    capacity_matrix: pd.DataFrame              # [product × tool_group] hours/wafer
    available_hours: dict[str, float]          # {tool_group_id: hours}
    time_window: str = "weekly"
    path_mix: dict[str, dict[str, float]] | None = None
    
    # ===== 新增 WIP 字段 =====
    wip_remaining_hours: dict[str, float] | None = None  # {tool_group_id: hours} - WIP 后续负载汇总
    wip_breakdown: dict[str, dict[str, float]] | None = None  # {tool_group: {product: hours}} - WIP 分产品明细
    wip_lot_count: dict[str, int] | None = None  # {tool_group_id: lot_count}
    wip_avg_wait_hours: dict[str, float] | None = None  # {tool_group_id: avg_wait}
    
    # WIP 风险阈值
    wip_warning_threshold: float = 0.30  # WIP 负载超过 30% 产能时预警
    wip_critical_threshold: float = 0.50  # WIP 负载超过 50% 产能时严重预警


@dataclass
class WIPLoadingInfo:
    """WIP 负载详情"""
    tool_group_id: str
    wip_lots: int
    wip_wafers: int
    wip_hours: float
    avg_wait_hours: float
    wip_loading_pct: float  # wip_hours / available_hours
    wip_share_of_total: float  # wip_hours / (new_input + wip_hours)
    risk_level: str  # "low" | "warning" | "critical"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_group_id": self.tool_group_id,
            "wip_lots": self.wip_lots,
            "wip_wafers": self.wip_wafers,
            "wip_hours": round(self.wip_hours, 2),
            "avg_wait_hours": round(self.avg_wait_hours, 2),
            "wip_loading_pct": round(self.wip_loading_pct * 100, 2),
            "wip_share_of_total": round(self.wip_share_of_total * 100, 2),
            "risk_level": self.risk_level,
        }


@dataclass
class RCCPResult:
    """RCCP 输出（增强版）"""
    
    # 原有字段
    loading_table: list[ToolGroupLoading]
    feasible: bool
    overall_loading_pct: float
    critical_groups: list[str]
    warning_groups: list[str]
    
    # ===== 新增 WIP 字段 =====
    wip_loading_table: list[WIPLoadingInfo]  # WIP 负载详情
    new_input_loading_pct: float  # 新投入负载占比
    wip_loading_pct: float  # WIP 负载占比
    wip_critical_groups: list[str]  # WIP 高负载机台组
    bottleneck_due_to_wip: list[str]  # 因 WIP 导致的瓶颈
    
    # computed_at 等其他字段
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        base = {
            "loading_table": [tg.to_dict() for tg in self.loading_table],
            "feasible": self.feasible,
            "overall_loading_pct": round(self.overall_loading_pct, 2),
            "critical_groups": self.critical_groups,
            "warning_groups": self.warning_groups,
            
            # WIP 信息
            "wip_loading_table": [w.to_dict() for w in self.wip_loading_table],
            "new_input_loading_pct": round(self.new_input_loading_pct, 2),
            "wip_loading_pct": round(self.wip_loading_pct, 2),
            "wip_critical_groups": self.wip_critical_groups,
            "bottleneck_due_to_wip": self.bottleneck_due_to_wip,
            
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }
        return base
```

### 3.2 核心计算逻辑增强

```python
def compute_rccp(inp: RCCPInput) -> RCCPResult:
    """
    RCCP 计算（增强版）
    
    完整负载 = 新投入负载 + WIP 后续负载
    
    数学表达:
        D_new[j] = Σ_i  P_new[i] × C[i,j]       # 新投入需求小时
        D_wip[j] = Σ_lot  Σ_step>current  h[lot,step,j]  # WIP 后续需求小时
        D_total[j] = D_new[j] + D_wip[j]        # 总需求
        
        L[j] = D_total[j] / H[j] × 100          # 完整利用率
        
        L_new_share[j] = D_new[j] / D_total[j]  # 新投入占比
        L_wip_share[j] = D_wip[j] / D_total[j]  # WIP 占比
    """
    
    # 1. 新投入负载计算（原有逻辑）
    products = list(inp.demand_plan.keys())
    valid_products = [p for p in products if p in inp.capacity_matrix.index]
    C = inp.capacity_matrix.loc[valid_products].apply(pd.to_numeric, errors="coerce")
    C = C.replace([np.inf, -np.inf], 0.0).fillna(0.0)
    P = pd.Series([inp.demand_plan[p] for p in valid_products], index=valid_products)
    
    contribution_matrix = C.multiply(P, axis=0)
    new_demand_hours = contribution_matrix.sum(axis=0)
    
    # 2. WIP 负载（新增）
    wip_hours = pd.Series(inp.wip_remaining_hours or {})
    
    # 3. 合并总需求
    total_demand = new_demand_hours.add(wip_hours, fill_value=0.0)
    
    # 4. 构建 loading table（原有逻辑 + WIP 信息）
    loading_table: list[ToolGroupLoading] = []
    wip_loading_table: list[WIPLoadingInfo] = []
    
    overall_new_demand = 0.0
    overall_wip_demand = 0.0
    overall_available = 0.0
    
    for tg_id in C.columns:
        d_new = float(new_demand_hours.get(tg_id, 0.0))
        d_wip = float(wip_hours.get(tg_id, 0.0))
        d_total = d_new + d_wip
        h = float(inp.available_hours.get(tg_id, 0.0))
        
        if h <= 0:
            continue
        
        # 完整利用率
        loading_pct = (d_total / h) * 100.0
        gap = d_total - h
        
        # 新投入占比
        new_share = d_new / d_total if d_total > 0 else 0.0
        wip_share = d_wip / d_total if d_total > 0 else 0.0
        
        # 状态判断
        status = classify_status(loading_pct)
        
        # Top 贡献产品（原有）
        contrib = contribution_matrix[tg_id].sort_values(ascending=False)
        top_contributors = {p: float(v) for p, v in contrib.head(5).items() if v > 0}
        
        # WIP 详情（新增）
        wip_info = WIPLoadingInfo(
            tool_group_id=tg_id,
            wip_lots=inp.wip_lot_count.get(tg_id, 0) if inp.wip_lot_count else 0,
            wip_wafers=0,  # 需要从 wip_breakdown 计算
            wip_hours=d_wip,
            avg_wait_hours=inp.wip_avg_wait_hours.get(tg_id, 0.0) if inp.wip_avg_wait_hours else 0.0,
            wip_loading_pct=d_wip / h if h > 0 else 0.0,
            wip_share_of_total=wip_share,
            risk_level=classify_wip_risk(d_wip / h, inp.wip_warning_threshold, inp.wip_critical_threshold),
        )
        wip_loading_table.append(wip_info)
        
        # ToolGroup Loading（原有 + 增强）
        tg_loading = ToolGroupLoading(
            tool_group_id=tg_id,
            available_hours=h,
            demand_hours=d_total,  # 改用总需求
            loading_pct=loading_pct,
            gap_hours=gap,
            status=status,
            contributing_products=top_contributors,
        )
        loading_table.append(tg_loading)
        
        overall_new_demand += d_new
        overall_wip_demand += d_wip
        overall_available += h
    
    # 5. 排序
    loading_table.sort(key=lambda x: x.loading_pct, reverse=True)
    wip_loading_table.sort(key=lambda x: x.wip_loading_pct, reverse=True)
    
    # 6. 汇总指标
    overall_total = overall_new_demand + overall_wip_demand
    overall_loading = (overall_total / overall_available * 100.0) if overall_available > 0 else 0.0
    new_input_loading_pct = (overall_new_demand / overall_available * 100.0) if overall_available > 0 else 0.0
    wip_loading_pct = (overall_wip_demand / overall_available * 100.0) if overall_available > 0 else 0.0
    
    # 7. 瓶颈识别
    critical_groups = [tg.tool_group_id for tg in loading_table if tg.status in ("critical", "overload")]
    warning_groups = [tg.tool_group_id for tg in loading_table if tg.status == "warning"]
    
    # WIP 导致的瓶颈（新增）
    wip_critical_groups = [w.tool_group_id for w in wip_loading_table if w.risk_level == "critical"]
    bottleneck_due_to_wip = [
        tg.tool_group_id for tg in loading_table 
        if tg.status in ("critical", "overload") 
        and wip_share_dict.get(tg.tool_group_id, 0) > inp.wip_critical_threshold
    ]
    
    # 8. 可行性判断
    feasible = not any(tg.status == "overload" for tg in loading_table)
    
    return RCCPResult(
        loading_table=loading_table,
        feasible=feasible,
        overall_loading_pct=overall_loading,
        critical_groups=critical_groups,
        warning_groups=warning_groups,
        
        # WIP 信息（新增）
        wip_loading_table=wip_loading_table,
        new_input_loading_pct=new_input_loading_pct,
        wip_loading_pct=wip_loading_pct,
        wip_critical_groups=wip_critical_groups,
        bottleneck_due_to_wip=bottleneck_due_to_wip,
        
        computed_at=datetime.utcnow(),
        metadata={
            "n_products": len(valid_products),
            "n_tool_groups": len(loading_table),
            "time_window": inp.time_window,
            "thresholds": THRESHOLDS,
            "wip_integration": True,
            "wip_total_hours": round(overall_wip_demand, 2),
        },
    )


def classify_wip_risk(
    wip_loading: float, 
    warning_threshold: float = 0.30,
    critical_threshold: float = 0.50
) -> str:
    """分类 WIP 风险等级"""
    if wip_loading >= critical_threshold:
        return "critical"
    elif wip_loading >= warning_threshold:
        return "warning"
    else:
        return "low"
```

---

## 四、Input Planner 模块（新增）

### 4.1 模块设计

```python
# engines/input_planner.py

"""
Input Planner - 投入节奏规划

核心功能:
  - 根据当前 WIP 水平，计算各机台组的安全投入上限
  - 协调新投入与 WIP 后续负载，避免产能过载
  - 输出建议投入计划（分周/分产品）

数学模型:
  safe_input[j] = (H[j] - D_wip[j]) / avg_hours_per_wafer[j]
  total_safe_input = min(safe_input[j]) over all bottleneck tool_groups
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class InputPlannerInput:
    """投入规划输入"""
    
    # 产能数据
    available_hours: dict[str, float]          # {tool_group_id: weekly_hours}
    capacity_matrix: pd.DataFrame              # [product × tool_group] hours/wafer
    
    # WIP 数据
    wip_remaining_hours: dict[str, float]      # {tool_group_id: hours}
    wip_avg_ct_hours: dict[str, float] = None  # {product_id: avg_cycle_time}
    
    # 需求计划
    demand_target: dict[str, float]            # {product_id: target_wafer_count}
    contract_min: dict[str, float] = None      # {product_id: minimum}
    market_max: dict[str, float] = None        # {product_id: maximum}
    
    # 安全阈值
    safety_margin_pct: float = 0.15            # 保留 15% 安全余量
    bottleneck_buffer_pct: float = 0.10        # 瓶颈机台额外 10% buffer
    
    # 规划窗口
    planning_weeks: int = 4                    # 规划 4 周


@dataclass
class SafeInputLimit:
    """单个机台组的安全投入上限"""
    tool_group_id: str
    available_hours: float
    wip_hours: float
    remaining_capacity: float                  # available - wip - safety_margin
    safe_input_wafers: float                   # 可安全投入的晶圆数（假设单位产品）
    bottleneck_flag: bool                      # 是否为瓶颈
    constraint_type: str                       # "capacity" | "wip" | "contract"
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_group_id": self.tool_group_id,
            "available_hours": round(self.available_hours, 2),
            "wip_hours": round(self.wip_hours, 2),
            "remaining_capacity": round(self.remaining_capacity, 2),
            "safe_input_wafers": round(self.safe_input_wafers, 0),
            "bottleneck_flag": self.bottleneck_flag,
            "constraint_type": self.constraint_type,
        }


@dataclass
class ProductInputRecommendation:
    """产品投入建议"""
    product_id: str
    target_wafers: float                       # 原计划
    recommended_wafers: float                  # 建议投入量
    reduction_pct: float                       # 削减百分比
    constraint_tool_group: str                 # 受限机台组
    constraint_reason: str                     # 受限原因
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "target_wafers": round(self.target_wafers, 0),
            "recommended_wafers": round(self.recommended_wafers, 0),
            "reduction_pct": round(self.reduction_pct * 100, 2),
            "constraint_tool_group": self.constraint_tool_group,
            "constraint_reason": self.constraint_reason,
        }


@dataclass
class InputPlannerResult:
    """投入规划输出"""
    
    # 安全投入上限（按机台组）
    safe_limits: list[SafeInputLimit]
    bottleneck_tool_groups: list[str]
    
    # 产品投入建议
    product_recommendations: list[ProductInputRecommendation]
    total_recommended_input: float
    total_reduction: float
    
    # 分周建议
    weekly_plan: dict[str, dict[str, float]]   # {week: {product: wafers}}
    
    # 元数据
    feasible: bool
    risk_summary: list[str]
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "safe_limits": [s.to_dict() for s in self.safe_limits],
            "bottleneck_tool_groups": self.bottleneck_tool_groups,
            "product_recommendations": [p.to_dict() for p in self.product_recommendations],
            "total_recommended_input": round(self.total_recommended_input, 0),
            "total_reduction": round(self.total_reduction, 0),
            "weekly_plan": self.weekly_plan,
            "feasible": self.feasible,
            "risk_summary": self.risk_summary,
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


def compute_safe_input_limits(inp: InputPlannerInput) -> list[SafeInputLimit]:
    """
    计算各机台组的安全投入上限
    
    公式:
      remaining[j] = H[j] - D_wip[j] - margin
      safe_input[j] = remaining[j] / avg_hours_per_wafer[j]
    """
    
    limits: list[SafeInputLimit] = []
    
    # 计算产品的平均单片小时（加权平均）
    avg_hours_per_wafer = inp.capacity_matrix.mean(axis=1).mean()  # 全厂平均
    
    for tg_id, h in inp.available_hours.items():
        d_wip = inp.wip_remaining_hours.get(tg_id, 0.0)
        
        # 计算剩余产能
        safety_margin = h * inp.safety_margin_pct
        remaining = h - d_wip - safety_margin
        
        # 瓶颈机台额外 buffer
        bottleneck_flag = remaining < h * 0.5  # 剩余产能 < 50%
        if bottleneck_flag:
            remaining -= h * inp.bottleneck_buffer_pct
        
        # 安全投入上限（晶圆数）
        # 假设平均每个产品经过该机台组 1 次
        avg_visit_hours = inp.capacity_matrix[tg_id].mean()
        safe_wafers = remaining / avg_visit_hours if avg_visit_hours > 0 else 0
        
        # 确定约束类型
        if d_wip > h * 0.5:
            constraint_type = "wip"
        elif remaining < 0:
            constraint_type = "capacity"
        else:
            constraint_type = "none"
        
        limits.append(SafeInputLimit(
            tool_group_id=tg_id,
            available_hours=h,
            wip_hours=d_wip,
            remaining_capacity=max(0, remaining),
            safe_input_wafers=max(0, safe_wafers),
            bottleneck_flag=bottleneck_flag,
            constraint_type=constraint_type,
        ))
    
    # 排序（按剩余产能降序）
    limits.sort(key=lambda x: x.remaining_capacity, reverse=False)  # 瓶颈在前
    
    return limits


def recommend_product_inputs(
    inp: InputPlannerInput,
    safe_limits: list[SafeInputLimit]
) -> list[ProductInputRecommendation]:
    """
    基于安全上限，生成各产品的投入建议
    
    策略:
      1. 找出最小安全上限的机台组（瓶颈）
      2. 计算该瓶颈对各产品的限制
      3. 按优先级分配产能
    """
    
    recommendations: list[ProductInputRecommendation] = []
    
    # 找出瓶颈机台组
    bottleneck_limits = [l for l in safe_limits if l.bottleneck_flag or l.constraint_type != "none"]
    
    if not bottleneck_limits:
        # 无瓶颈，按原计划推荐
        for product_id, target in inp.demand_target.items():
            recommendations.append(ProductInputRecommendation(
                product_id=product_id,
                target_wafers=target,
                recommended_wafers=target,
                reduction_pct=0.0,
                constraint_tool_group="",
                constraint_reason="No bottleneck",
            ))
        return recommendations
    
    # 计算每个产品在各瓶颈机台组的消耗
    total_capacity_available = sum(l.remaining_capacity for l in safe_limits)
    
    # 按瓶颈机台组限制分配
    for product_id, target in inp.demand_target.items():
        # 计算该产品在各机台组的需求小时
        product_hours = inp.capacity_matrix.loc[product_id] if product_id in inp.capacity_matrix.index else pd.Series()
        
        # 找出最受限的机台组
        min_ratio = float('inf')
        constraint_tg = ""
        constraint_reason = ""
        
        for limit in bottleneck_limits:
            hours_per_wafer = product_hours.get(limit.tool_group_id, 0)
            if hours_per_wafer > 0:
                # 该产品在该机台组的最大可投入量
                max_wafers = limit.remaining_capacity / hours_per_wafer
                ratio = max_wafers / target if target > 0 else float('inf')
                
                if ratio < min_ratio:
                    min_ratio = ratio
                    constraint_tg = limit.tool_group_id
                    constraint_reason = f"WIP 占用 {limit.wip_hours:.0f}h"
        
        # 计算建议投入量
        contract_min = inp.contract_min.get(product_id, 0) if inp.contract_min else 0
        
        if min_ratio < 1.0:
            # 受限，需要削减
            recommended = min(target * min_ratio, target)
            recommended = max(recommended, contract_min)  # 保证合约最低量
            reduction = (target - recommended) / target if target > 0 else 0
        else:
            recommended = target
            reduction = 0.0
        
        recommendations.append(ProductInputRecommendation(
            product_id=product_id,
            target_wafers=target,
            recommended_wafers=recommended,
            reduction_pct=reduction,
            constraint_tool_group=constraint_tg,
            constraint_reason=constraint_reason,
        ))
    
    return recommendations


def generate_weekly_plan(
    inp: InputPlannerInput,
    recommendations: list[ProductInputRecommendation]
) -> dict[str, dict[str, float]]:
    """
    生成分周投入计划
    
    策略:
      - Week 1: 优先保障合约最低量
      - Week 2-4: 按优先级逐步分配剩余产能
    """
    
    weekly_plan: dict[str, dict[str, float]] = {}
    
    for week_num in range(1, inp.planning_weeks + 1):
        week_key = f"W{week_num}"
        week_plan: dict[str, float] = {}
        
        if week_num == 1:
            # Week 1: 保守策略，优先合约
            for rec in recommendations:
                contract_min = inp.contract_min.get(rec.product_id, 0) if inp.contract_min else 0
                week_plan[rec.product_id] = contract_min
        
        elif week_num == 2:
            # Week 2: 分配推荐量的 50%
            for rec in recommendations:
                contract_min = inp.contract_min.get(rec.product_id, 0) if inp.contract_min else 0
                allocated = (rec.recommended_wafers - contract_min) * 0.5 + contract_min
                week_plan[rec.product_id] = round(allocated, 0)
        
        else:
            # Week 3-4: 分配剩余量
            week_fraction = 1.0 / (inp.planning_weeks - 2)  # 平均分配
            for rec in recommendations:
                contract_min = inp.contract_min.get(rec.product_id, 0) if inp.contract_min else 0
                remaining = rec.recommended_wafers - contract_min - weekly_plan.get("W2", {}).get(rec.product_id, 0)
                allocated = remaining * week_fraction
                week_plan[rec.product_id] = round(allocated, 0)
        
        weekly_plan[week_key] = week_plan
    
    return weekly_plan


def run_input_planner(inp: InputPlannerInput) -> InputPlannerResult:
    """主函数：运行投入规划"""
    
    t0 = datetime.utcnow()
    
    # 1. 计算安全投入上限
    safe_limits = compute_safe_input_limits(inp)
    
    # 2. 识别瓶颈机台组
    bottleneck_tgs = [l.tool_group_id for l in safe_limits if l.bottleneck_flag]
    
    # 3. 生成产品投入建议
    recommendations = recommend_product_inputs(inp, safe_limits)
    
    # 4. 生成分周计划
    weekly_plan = generate_weekly_plan(inp, recommendations)
    
    # 5. 汇总统计
    total_target = sum(inp.demand_target.values())
    total_recommended = sum(r.recommended_wafers for r in recommendations)
    total_reduction = total_target - total_recommended
    
    # 6. 风险分析
    risk_summary: list[str] = []
    for limit in safe_limits[:5]:  # Top 5 瓶颈
        if limit.constraint_type == "wip":
            risk_summary.append(f"{limit.tool_group_id}: WIP 占用 {limit.wip_hours:.0f}h，剩余产能仅 {limit.remaining_capacity:.0f}h")
        elif limit.constraint_type == "capacity":
            risk_summary.append(f"{limit.tool_group_id}: 产能不足，建议延后投入")
    
    # 7. 可行性判断
    feasible = all(r.recommended_wafers >= (inp.contract_min.get(r.product_id, 0) if inp.contract_min else 0) 
                   for r in recommendations)
    
    return InputPlannerResult(
        safe_limits=safe_limits,
        bottleneck_tool_groups=bottleneck_tgs,
        product_recommendations=recommendations,
        total_recommended_input=total_recommended,
        total_reduction=total_reduction,
        weekly_plan=weekly_plan,
        feasible=feasible,
        risk_summary=risk_summary,
        computed_at=datetime.utcnow(),
        metadata={
            "planning_weeks": inp.planning_weeks,
            "safety_margin_pct": inp.safety_margin_pct,
            "bottleneck_buffer_pct": inp.bottleneck_buffer_pct,
            "compute_time_seconds": (datetime.utcnow() - t0).total_seconds(),
        },
    )
```

---

## 五、DES 仿真增强

### 5.1 用真实 WIP 初始化仿真

```python
# engines/des_validator.py 增强

@dataclass
class DESInput:
    # 原有字段
    tool_groups: list[DESToolGroup]
    arrivals: list[DESJobArrival]
    sim_duration_hours: float = 168.0
    warmup_hours: float = 24.0
    n_replications: int = 5
    
    # ===== 新增 WIP 初始化 =====
    initial_wip: dict[str, list[dict]] | None = None  # {tool_group_id: [{lot_id, wafers, wait_hours}]}
    use_real_wip_init: bool = False  # 是否使用真实 WIP 初始化


def initialize_wip_in_simulation(
    fab: WaferFab,
    initial_wip: dict[str, list[dict]],
    rng: np.random.Generator
):
    """
    用真实 WIP 数据初始化仿真队列
    
    效果:
      - 队列初始状态 = 当前真实 WIP 分布
      - 更准确预测未来瓶颈
    """
    
    env = fab.env
    
    for tg_id, wip_list in initial_wip.items():
        if tg_id not in fab.resources:
            continue
        
        res = fab.resources[tg_id]
        spec = fab.specs[tg_id]
        
        # 将 WIP 批次放入队列（模拟等待状态）
        for wip_lot in wip_list:
            lot_id = wip_lot["lot_id"]
            wafers = wip_lot["wafers"]
            wait_hours_so_far = wip_lot.get("wait_hours", 0.0)
            
            # 创建一个已经等待的 wafer job
            # 使用延迟启动模拟已等待时间
            def delayed_wafer_process():
                # 立即请求资源（模拟已在队列中）
                with res.request() as req:
                    yield req
                    
                    # 处理
                    st = wip_lot.get("service_hours", 1.0 / spec.service_rate)
                    yield env.timeout(st)
                    
                    fab.completed_jobs[tg_id] += wafers
            
            env.process(delayed_wafer_process())
            
            # 记录队列状态
            fab.queue_log[tg_id].append((env.now, len(res.queue) + len(wip_list)))


def run_des(inp: DESInput) -> DESResult:
    """DES 仿真（增强版）"""
    
    if not SIMPY_AVAILABLE:
        raise RuntimeError("SimPy not installed")
    
    t0 = datetime.utcnow()
    all_replications = []
    
    for rep in range(inp.n_replications):
        rng = np.random.default_rng(seed=42 + rep)
        env = simpy.Environment()
        fab = WaferFab(env, inp.tool_groups)
        
        # ===== 新增：WIP 初始化 =====
        if inp.use_real_wip_init and inp.initial_wip:
            initialize_wip_in_simulation(fab, inp.initial_wip, rng)
        
        # 新到达流（原有逻辑）
        for arr in inp.arrivals:
            env.process(_arrival_process(env, fab, arr, rng))
        
        env.run(until=inp.sim_duration_hours)
        
        # 统计提取（原有逻辑）
        rep_stats = extract_stats(fab, inp)
        all_replications.append(rep_stats)
    
    # 聚合结果（原有逻辑）
    final_stats, risk_flags = aggregate_results(all_replications, inp.tool_groups)
    
    return DESResult(
        feasible=not risk_flags,
        tool_group_stats=final_stats,
        risk_flags=risk_flags,
        sim_metadata={
            "sim_duration_hours": inp.sim_duration_hours,
            "warmup_hours": inp.warmup_hours,
            "n_replications": inp.n_replications,
            "n_tool_groups": len(inp.tool_groups),
            "wip_initialized": inp.use_real_wip_init,
            "initial_wip_lots": sum(len(w) for w in inp.initial_wip.values()) if inp.initial_wip else 0,
            "wall_time_seconds": (datetime.utcnow() - t0).total_seconds(),
        },
        computed_at=datetime.utcnow(),
    )
```

---

## 六、API 端点设计

### 6.1 新增 API 端点

```python
# engines/server.py 新增端点

@app.get("/wip/overview")
async def get_wip_overview():
    """获取 WIP 全厂概览"""
    query = """
        SELECT * FROM v_wip_overview ORDER BY wip_loading_pct DESC
    """
    result = await db.fetch_all(query)
    return {"wip_overview": result}


@app.get("/wip/queue/{tool_group_id}")
async def get_wip_queue(tool_group_id: str):
    """获取指定机台组的 WIP 队列详情"""
    query = """
        SELECT 
            lot_id, product_id, lot_status, wafer_count,
            wait_hours_so_far, queue_position, percent_complete
        FROM fact_wip_lot_detail
        WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM fact_wip_lot_detail)
          AND current_tool_group = :tg_id
          AND lot_status IN ('WAIT', 'HOLD')
        ORDER BY wait_hours_so_far DESC
        LIMIT 50
    """
    result = await db.fetch_all(query, {"tg_id": tool_group_id})
    return {"tool_group_id": tool_group_id, "wip_queue": result}


@app.get("/wip/remaining_load")
async def get_wip_remaining_load():
    """获取 WIP 后续负载汇总（用于 RCCP）"""
    query = """
        SELECT 
            tool_group_id,
            SUM(remaining_hours) AS total_remaining_hours,
            SUM(wip_wafers) AS total_wip_wafers,
            SUM(wip_lots) AS total_wip_lots
        FROM mart_wip_remaining_load
        WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM mart_wip_remaining_load)
        GROUP BY tool_group_id
    """
    result = await db.fetch_all(query)
    
    # 转换为 dict 格式供 RCCP 使用
    wip_remaining_hours = {r["tool_group_id"]: r["total_remaining_hours"] for r in result}
    
    return {
        "wip_remaining_hours": wip_remaining_hours,
        "details": result,
        "snapshot_ts": await db.fetch_val("SELECT MAX(snapshot_ts) FROM mart_wip_remaining_load"),
    }


@app.post("/input/plan")
async def plan_input(request: InputPlanRequest):
    """
    投入规划
    
    输入:
      - demand_target: {product: wafer_count}
      - safety_margin_pct: 安全余量
    
    输出:
      - safe_limits: 各机台组安全上限
      - recommendations: 产品投入建议
      - weekly_plan: 分周计划
    """
    
    # 1. 获取产能数据
    available_hours = await get_available_hours()
    capacity_matrix = await get_capacity_matrix()
    
    # 2. 获取 WIP 数据
    wip_data = await get_wip_remaining_load()
    wip_remaining_hours = wip_data["wip_remaining_hours"]
    
    # 3. 构建输入
    inp = InputPlannerInput(
        available_hours=available_hours,
        capacity_matrix=capacity_matrix,
        wip_remaining_hours=wip_remaining_hours,
        demand_target=request.demand_target,
        contract_min=request.contract_min,
        market_max=request.market_max,
        safety_margin_pct=request.safety_margin_pct,
        planning_weeks=request.planning_weeks,
    )
    
    # 4. 运行规划
    result = run_input_planner(inp)
    
    return result.to_dict()


@app.post("/rccp/compute_with_wip")
async def compute_rccp_with_wip(request: RCCPRequest):
    """
    RCCP 计算（含 WIP）
    
    自动获取最新 WIP 数据并合并计算
    """
    
    # 1. 获取 WIP 数据
    wip_data = await get_wip_remaining_load()
    
    # 2. 构建输入（加入 WIP）
    inp = RCCPInput(
        demand_plan=request.demand_plan,
        capacity_matrix=await get_capacity_matrix(),
        available_hours=await get_available_hours(),
        wip_remaining_hours=wip_data["wip_remaining_hours"],
        wip_lot_count=await get_wip_lot_count(),
        wip_avg_wait_hours=await get_wip_avg_wait(),
    )
    
    # 3. 计算
    result = compute_rccp(inp)
    
    return result.to_dict()
```

---

## 七、前端集成

### 7.1 WIP 概览页面

```jsx
// frontend/src/components/WIPOverview.jsx

function WIPOverview() {
  const [wipData, setWipData] = useState(null);
  
  useEffect(() => {
    api.getWipOverview().then(data => setWipData(data.wip_overview));
  }, []);
  
  if (!wipData) return <div>Loading...</div>;
  
  return (
    <div className="wip-overview">
      <h2>WIP 概览</h2>
      
      <table>
        <thead>
          <tr>
            <th>机台组</th>
            <th>区域</th>
            <th>等待 Lot</th>
            <th>等待晶圆</th>
            <th>平均等待(h)</th>
            <th>WIP 负载%</th>
            <th>风险</th>
          </tr>
        </thead>
        <tbody>
          {wipData.map(tg => (
            <tr key={tg.tool_group_id} className={tg.wip_loading_pct > 50 ? 'risk-high' : ''}>
              <td>{tg.tool_group_name}</td>
              <td>{tg.area}</td>
              <td>{tg.wip_waiting_lots}</td>
              <td>{tg.wip_waiting_wafers}</td>
              <td>{tg.avg_wait_hours.toFixed(1)}</td>
              <td>{tg.wip_loading_pct.toFixed(1)}%</td>
              <td>
                {tg.wip_loading_pct > 50 ? '🔴' : tg.wip_loading_pct > 30 ? '⚠️' : '✅'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

### 7.2 RCCP 结果页面增强

```jsx
// frontend/src/components/RCCPResult.jsx 增强

function RCCPResult({ result }) {
  return (
    <div className="rccp-result">
      {/* 负载汇总 */}
      <div className="loading-summary">
        <h3>产能负载汇总</h3>
        <div className="summary-cards">
          <div className="card">
            <label>总负载</label>
            <value>{result.overall_loading_pct.toFixed(1)}%</value>
          </div>
          <div className="card">
            <label>新投入占比</label>
            <value>{result.new_input_loading_pct.toFixed(1)}%</value>
          </div>
          <div className="card">
            <label>WIP 占比</label>
            <value>{result.wip_loading_pct.toFixed(1)}%</value>
            <status>{result.wip_loading_pct > 30 ? '⚠️' : '✅'}</status>
          </div>
        </div>
      </div>
      
      {/* WIP 负载详情（新增） */}
      <div className="wip-loading">
        <h3>WIP 负载详情</h3>
        <table>
          <thead>
            <tr>
              <th>机台组</th>
              <th>WIP Lot</th>
              <th>WIP 小时</th>
              <th>WIP 负载%</th>
              <th>占总需求%</th>
              <th>风险等级</th>
            </tr>
          </thead>
          <tbody>
            {result.wip_loading_table.map(w => (
              <tr key={w.tool_group_id}>
                <td>{w.tool_group_id}</td>
                <td>{w.wip_lots}</td>
                <td>{w.wip_hours.toFixed(0)}h</td>
                <td>{w.wip_loading_pct.toFixed(1)}%</td>
                <td>{w.wip_share_of_total.toFixed(1)}%</td>
                <td className={`risk-${w.risk_level}`}>
                  {w.risk_level === 'critical' ? '🔴' : w.risk_level === 'warning' ? '⚠️' : '✅'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      
      {/* 瓶颈分析 */}
      {result.bottleneck_due_to_wip.length > 0 && (
        <div className="wip-bottleneck-warning">
          <h3>⚠️ 因 WIP 导致的瓶颈</h3>
          <ul>
            {result.bottleneck_due_to_wip.map(tg => (
              <li key={tg}>{tg} - WIP 占用超过 50% 产能</li>
            ))}
          </ul>
        </div>
      )}
      
      {/* 原有负载表格 */}
      <LoadingTable data={result.loading_table} />
    </div>
  );
}
```

---

## 八、实施计划

### 8.1 分阶段实施

| 阶段 | 任务 | 周期 | 产出 |
|------|------|------|------|
| **Phase 1** | 数据模型增强 | Week 1-2 | Schema SQL、视图 |
| **Phase 2** | MES 同步脚本 | Week 3-4 | sync_wip_from_mes.py |
| **Phase 3** | dbt 模型开发 | Week 5-6 | wip_queue_hourly、wip_remaining_load |
| **Phase 4** | RCCP 引擎增强 | Week 7-8 | compute_rccp_with_wip API |
| **Phase 5** | Input Planner | Week 9-12 | input_plan API、前端页面 |
| **Phase 6** | DES 增强 | Week 13-14 | WIP 初始化仿真 |
| **Phase 7** | 集成测试 | Week 15-16 | 全流程验证 |

### 8.2 数据依赖

| 数据源 | 需要字段 | 同步频率 |
|--------|---------|---------|
| **MES Lot Transaction** | lot_id, current_operation, status, location | 每 5 分钟 |
| **MES Queue Status** | queue_position, track_in_time | 每 5 分钟 |
| **Route Master** | step_seq, tool_group_id, run_time_hr | 静态数据 |
| **OEE Daily** | availability, performance | 每天 |

---

## 九、关键公式汇总

| 公式 | 说明 |
|------|------|
| `remaining_hours[lot, j] = Σ_{step>current} hours[step, j]` | Lot 级后续负载 |
| `wip_hours[j] = Σ_{lot} remaining_hours[lot, j]` | 机台组级 WIP 负载 |
| `demand_hours[j] = new_input[j] + wip_hours[j]` | 完整需求 |
| `loading[j] = demand[j] / available[j]` | 利用率 |
| `safe_input[j] = (available[j] - wip[j] - margin) / avg_hours` | 安全投入上限 |
| `wip_share[j] = wip[j] / demand[j]` | WIP 占比 |

---

## 十、附录

### 10.1 数据字典

| 表名 | 字段 | 类型 | 说明 |
|------|------|------|------|
| fact_wip_lot_detail | lot_id | VARCHAR(64) | Lot 批号 |
| fact_wip_lot_detail | current_step_seq | INT | 当前工序序号 |
| fact_wip_lot_detail | remaining_hours | NUMERIC(10,2) | 剩余总小时 |
| fact_wip_queue_hourly | wip_waiting | INT | 等待 Lot 数 |
| mart_wip_remaining_load | remaining_hours | NUMERIC(10,2) | WIP 后续负载 |

### 10.2 性能估算

| 操作 | 数据量 | 预估耗时 |
|------|--------|---------|
| MES 同步 | 5000 Lot | 30 秒 |
| dbt 计算 | 5000 Lot × 300 步 | 2 分钟 |
| RCCP 计算 | 60 机台 × 27 产品 | < 100ms |
| Input Planner | 60 机台 × 27 产品 | < 500ms |

---

*文档版本: v1.0 | 最后更新: 2026-04-28*