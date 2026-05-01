-- =================================================================
-- Output Perspective Schema - 产出视角数据模型
-- =================================================================
-- 设计原则:
--   1. 规划输入 = 产出目标（而非投入计划）
--   2. 产能需求 = WIP 后续工序小时（而非投入总工时）
--   3. 投入时间 = 产出时间 - Cycle Time
--
-- 适用场景: 存储芯片制造（CT = 90-120天）
-- =================================================================

CREATE SCHEMA IF NOT EXISTS capacity;
SET search_path TO capacity, public;

-- =================================================================
-- 维度表（新增）
-- =================================================================

-- 产品 Cycle Time 基准
CREATE TABLE IF NOT EXISTS dim_product_cycle_time (
    product_id      VARCHAR(64) PRIMARY KEY,
    avg_cycle_time_days NUMERIC(6,1) NOT NULL,      -- 平均 CT (天)
    min_cycle_time_days NUMERIC(6,1),               -- 最快 CT
    max_cycle_time_days NUMERIC(6,1),               -- 最慢 CT
    ct_variation_pct NUMERIC(5,2),                  -- CT 波动百分比
    source          VARCHAR(64),                    -- 数据来源
    updated_ts      TIMESTAMP DEFAULT NOW()
);

COMMENT ON TABLE dim_product_cycle_time IS '产品 Cycle Time 基准 - 用于反推投入时间';


-- =================================================================
-- 产出目标表（核心输入）
-- =================================================================

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
    
    -- 状态
    status          VARCHAR(32) DEFAULT 'PLANNED',  -- PLANNED/IN_PROGRESS/COMPLETED
    actual_wafers   NUMERIC(10,2),                  -- 实际产出
    
    created_ts      TIMESTAMP DEFAULT NOW(),
    updated_ts      TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (plan_version, time_window, product_id)
);

CREATE INDEX idx_output_target_window ON fact_output_target(time_window, plan_version);
CREATE INDEX idx_output_target_product ON fact_output_target(product_id, time_window);

COMMENT ON TABLE fact_output_target IS '产出目标表 - Output 视角的核心输入';
COMMENT ON COLUMN fact_output_target.time_window IS '产出时间窗口（目标产出周，不是投入周）';
COMMENT ON COLUMN fact_output_target.input_window IS '反推的投入时间窗口 = time_window - CT';


-- =================================================================
-- WIP 详情表（增强）
-- =================================================================

CREATE TABLE IF NOT EXISTS fact_wip_lot_detail (
    snapshot_ts     TIMESTAMPTZ NOT NULL,
    lot_id          VARCHAR(64) NOT NULL,
    product_id      VARCHAR(64) NOT NULL,
    
    -- 当前位置
    current_step_seq    INT NOT NULL,
    current_step_name   VARCHAR(128),
    current_tool_group  VARCHAR(64),
    lot_status          VARCHAR(32) NOT NULL,      -- WAIT/RUN/HOLD/MOVE
    
    -- 完成度（Output 视角核心）
    percent_complete    NUMERIC(5,2) NOT NULL,     -- 完成百分比 (0-100)
    remaining_steps     INT NOT NULL,
    remaining_hours     NUMERIC(10,2) NOT NULL,    -- 剩余总小时
    remaining_hours_by_tg JSONB,                   -- {tg_id: hours} 后续工序小时
    
    -- 产出预测
    est_completion_week VARCHAR(16),               -- 预计产出周
    est_completion_ts   TIMESTAMPTZ,
    
    -- Queue 信息
    queue_position      INT,
    queue_entry_ts      TIMESTAMPTZ,
    wait_hours_so_far   NUMERIC(8,2),
    
    -- 数量
    wafer_count         INT NOT NULL,
    good_wafer_count    INT,
    
    -- 来源
    start_ts            TIMESTAMPTZ,
    input_week          VARCHAR(16),
    
    PRIMARY KEY (snapshot_ts, lot_id)
);

CREATE INDEX idx_wip_product ON fact_wip_lot_detail(product_id, snapshot_ts DESC);
CREATE INDEX idx_wip_toolgroup ON fact_wip_lot_detail(current_tool_group, lot_status);
CREATE INDEX idx_wip_completion ON fact_wip_lot_detail(est_completion_week, product_id);
CREATE INDEX idx_wip_percent ON fact_wip_lot_detail(percent_complete DESC);


-- =================================================================
-- WIP 产出预测表（派生表）
-- =================================================================

CREATE TABLE IF NOT EXISTS mart_wip_output_prediction (
    prediction_ts   TIMESTAMPTZ NOT NULL,
    product_id      VARCHAR(64) NOT NULL,
    output_week     VARCHAR(16) NOT NULL,
    
    -- 预测产量
    predicted_wafers    NUMERIC(10,2) NOT NULL,
    wip_lots           INT NOT NULL,
    wip_wafers         INT NOT NULL,
    
    -- 完成度
    avg_percent_complete NUMERIC(5,2),
    min_percent_complete NUMERIC(5,2),
    
    -- 风险
    hold_lot_count      INT DEFAULT 0,
    risk_flag           VARCHAR(16),              -- LOW/MEDIUM/HIGH
    confidence          VARCHAR(16),              -- HIGH/MEDIUM/LOW
    
    -- 来源
    snapshot_ts         TIMESTAMPTZ NOT NULL,
    
    PRIMARY KEY (prediction_ts, product_id, output_week)
);

CREATE INDEX idx_wip_output_week ON mart_wip_output_prediction(output_week, product_id);


-- =================================================================
-- WIP 后续负载表（核心派生表）
-- =================================================================

CREATE TABLE IF NOT EXISTS mart_wip_remaining_load (
    snapshot_ts       TIMESTAMPTZ NOT NULL,
    tool_group_id     VARCHAR(64) NOT NULL,
    product_id        VARCHAR(64) NOT NULL,
    
    -- 负载
    wip_lots          INT NOT NULL,
    wip_wafers        INT NOT NULL,
    remaining_visits  INT NOT NULL,
    remaining_hours   NUMERIC(10,2) NOT NULL,      -- 核心：后续工序小时
    
    -- 时间分布
    hours_next_24h    NUMERIC(8,2),
    hours_next_7d     NUMERIC(10,2),
    hours_next_30d    NUMERIC(12,2),
    
    -- 风险
    is_bottleneck_risk BOOLEAN DEFAULT FALSE,
    
    PRIMARY KEY (snapshot_ts, tool_group_id, product_id)
);

CREATE INDEX idx_wip_load_tg ON mart_wip_remaining_load(tool_group_id, snapshot_ts DESC);


-- =================================================================
-- 周产能需求表（Output 视角）
-- =================================================================

CREATE TABLE IF NOT EXISTS mart_weekly_capacity_demand (
    week_id           VARCHAR(16) NOT NULL,
    tool_group_id     VARCHAR(64) NOT NULL,
    
    -- 需求分解（核心区别）
    wip_remaining_hours NUMERIC(10,2) NOT NULL,    -- WIP 后续工序小时
    new_input_hours     NUMERIC(10,2) DEFAULT 0,   -- 新投入本周工序小时
    total_demand_hours  NUMERIC(10,2) NOT NULL,
    
    -- 产品分布
    demand_by_product   JSONB,
    
    -- WIP 详情
    wip_lot_count       INT DEFAULT 0,
    wip_wafer_count     INT DEFAULT 0,
    avg_wip_wait_hours  NUMERIC(6,2),
    
    -- 产能对比
    available_hours     NUMERIC(10,2),
    loading_pct         NUMERIC(6,2),
    status              VARCHAR(16),               -- healthy/warning/critical/overload
    
    -- 计算
    computed_at         TIMESTAMPTZ,
    
    PRIMARY KEY (week_id, tool_group_id)
);

CREATE INDEX idx_weekly_demand_week ON mart_weekly_capacity_demand(week_id);
CREATE INDEX idx_weekly_demand_status ON mart_weekly_capacity_demand(status, loading_pct);


-- =================================================================
-- 投入计划表（反推生成）
-- =================================================================

CREATE TABLE IF NOT EXISTS fact_input_schedule (
    schedule_version   VARCHAR(32) NOT NULL,
    input_window       VARCHAR(16) NOT NULL,       -- 投入时间窗口
    product_id         VARCHAR(64) NOT NULL,
    
    -- 投入量
    planned_wafers     NUMERIC(10,2) NOT NULL,
    
    -- 产出目标关联
    output_window      VARCHAR(16) NOT NULL,       -- 目标产出周
    output_target_wafers NUMERIC(10,2),
    wip_contribution   NUMERIC(10,2),              -- 来自现有 WIP
    gap_to_fill        NUMERIC(10,2),              -- 需新投入填补
    
    -- 投入类型
    input_type         VARCHAR(32) NOT NULL,       -- NEW_INPUT/SUPPLEMENT
    
    -- 状态
    status             VARCHAR(32) DEFAULT 'PLANNED',
    actual_wafers      NUMERIC(10,2),
    
    created_ts         TIMESTAMP DEFAULT NOW(),
    
    PRIMARY KEY (schedule_version, input_window, product_id)
);

CREATE INDEX idx_input_schedule_window ON fact_input_schedule(input_window, status);


-- =================================================================
-- 产出缺口分析表
-- =================================================================

CREATE TABLE IF NOT EXISTS mart_output_gap_analysis (
    analysis_ts        TIMESTAMPTZ NOT NULL,
    target_week        VARCHAR(16) NOT NULL,
    product_id         VARCHAR(64) NOT NULL,
    
    -- 目标 vs 预测
    target_wafers      NUMERIC(10,2) NOT NULL,
    predicted_wafers   NUMERIC(10,2) NOT NULL,
    gap                NUMERIC(10,2) NOT NULL,     -- target - predicted
    gap_pct            NUMERIC(6,2),
    
    -- 投入建议
    recommended_input  NUMERIC(10,2),
    input_window       VARCHAR(16),
    
    -- 可行性
    feasible           BOOLEAN,
    risk_factors       JSONB,
    
    PRIMARY KEY (analysis_ts, target_week, product_id)
);


-- =================================================================
-- 视图：产出预测汇总
-- =================================================================

CREATE OR REPLACE VIEW v_output_prediction_summary AS
SELECT
    output_week,
    product_id,
    predicted_wafers,
    wip_lots,
    avg_percent_complete,
    confidence,
    risk_flag
FROM mart_wip_output_prediction
WHERE prediction_ts = (SELECT MAX(prediction_ts) FROM mart_wip_output_prediction)
ORDER BY output_week, product_id;


-- =================================================================
-- 视图：产能负载概览（Output 视角）
-- =================================================================

CREATE OR REPLACE VIEW v_capacity_demand_overview AS
SELECT
    wcd.week_id,
    tg.tool_group_id,
    tg.tool_group_name,
    tg.area,
    tg.n_machines,
    
    -- 需求分解
    wcd.wip_remaining_hours,
    wcd.new_input_hours,
    wcd.total_demand_hours,
    
    -- WIP 占比（关键指标）
    ROUND(wcd.wip_remaining_hours / wcd.total_demand_hours * 100, 2) AS wip_share_pct,
    
    -- 产能
    wcd.available_hours,
    wcd.loading_pct,
    wcd.status,
    
    -- WIP 详情
    wcd.wip_lot_count,
    wcd.avg_wip_wait_hours
    
FROM mart_weekly_capacity_demand wcd
JOIN dim_tool_group tg ON tg.tool_group_id = wcd.tool_group_id
WHERE wcd.week_id = (SELECT MAX(week_id) FROM mart_weekly_capacity_demand)
ORDER BY wcd.loading_pct DESC;


-- =================================================================
-- 视图：投入计划汇总
-- =================================================================

CREATE OR REPLACE VIEW v_input_schedule_summary AS
SELECT
    input_window,
    product_id,
    SUM(planned_wafers) AS total_planned,
    COUNT(*) AS schedule_count
FROM fact_input_schedule
WHERE status = 'PLANNED'
GROUP BY input_window, product_id
ORDER BY input_window, product_id;


-- =================================================================
-- 视图：产出目标达成情况
-- =================================================================

CREATE OR REPLACE VIEW v_output_target_achievement AS
SELECT
    ot.time_window,
    ot.product_id,
    ot.target_wafers,
    COALESCE(wop.predicted_wafers, 0) AS predicted_wafers,
    ot.target_wafers - COALESCE(wop.predicted_wafers, 0) AS gap,
    ROUND((ot.target_wafers - COALESCE(wop.predicted_wafers, 0)) / ot.target_wafers * 100, 2) AS gap_pct,
    CASE
        WHEN ot.target_wafers - COALESCE(wop.predicted_wafers, 0) <= 0 THEN 'ACHIEVABLE'
        WHEN ot.target_wafers - COALESCE(wop.predicted_wafers, 0) <= ot.target_wafers * 0.2 THEN 'MARGINAL'
        ELSE 'GAP_EXISTS'
    END AS feasibility
    
FROM fact_output_target ot
LEFT JOIN mart_wip_output_prediction wop 
    ON wop.output_week = ot.time_window AND wop.product_id = ot.product_id
    AND wop.prediction_ts = (SELECT MAX(prediction_ts) FROM mart_wip_output_prediction)
WHERE ot.plan_version = 'current'
ORDER BY ot.time_window, ot.product_id;


-- =================================================================
-- 初始化数据
-- =================================================================

-- 插入 Cycle Time 基准示例数据
INSERT INTO dim_product_cycle_time (product_id, avg_cycle_time_days, min_cycle_time_days, max_cycle_time_days, ct_variation_pct, source)
SELECT
    product_id,
    100 + RANDOM() * 20,     -- 100-120 天
    90,
    130,
    15.0,
    'historical_avg'
FROM dim_product
WHERE is_active = TRUE
ON CONFLICT (product_id) DO NOTHING;