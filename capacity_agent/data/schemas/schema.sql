-- =================================================================
-- Capacity Agent - PostgreSQL Schema
-- =================================================================
-- 设计原则:
--   1. 维度表 (dim_*) 较稳定,变化缓慢
--   2. 事实表 (fact_*) 高频写入,设分区
--   3. 派生表 (mart_*) 由 dbt 定时计算,Agent 直接读取
--
-- 适用 PostgreSQL 14+
-- =================================================================

CREATE SCHEMA IF NOT EXISTS capacity;
SET search_path TO capacity, public;


-- =================================================================
-- 维度表
-- =================================================================

-- 机台组维度
CREATE TABLE IF NOT EXISTS dim_tool_group (
    tool_group_id   VARCHAR(64) PRIMARY KEY,
    tool_group_name VARCHAR(128) NOT NULL,
    area            VARCHAR(64) NOT NULL,            -- LITHO/ETCH/DEPO/CMP/...
    process_type    VARCHAR(64),
    n_machines      INT NOT NULL,
    nameplate_throughput_wph NUMERIC(10,2),         -- wafer/hour 单机
    valid_from      TIMESTAMP DEFAULT NOW(),
    valid_to        TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_tool_group_area ON dim_tool_group(area);


-- 产品维度
CREATE TABLE IF NOT EXISTS dim_product (
    product_id      VARCHAR(64) PRIMARY KEY,
    product_name    VARCHAR(128) NOT NULL,
    product_family  VARCHAR(64),                    -- DRAM/NAND/Logic
    technology_node VARCHAR(32),                    -- 28nm, 64L, 128L
    is_active       BOOLEAN DEFAULT TRUE
);


-- 工艺路线维度 (Route Master 核心表)
CREATE TABLE IF NOT EXISTS dim_route (
    route_id        BIGSERIAL PRIMARY KEY,
    product_id      VARCHAR(64) NOT NULL REFERENCES dim_product(product_id),
    path_id         VARCHAR(64) NOT NULL DEFAULT 'default',
    step_seq        INT NOT NULL,
    step_name       VARCHAR(128),
    tool_group_id   VARCHAR(64) NOT NULL REFERENCES dim_tool_group(tool_group_id),
    run_time_hr     NUMERIC(8,4) NOT NULL,           -- 单 wafer 加工小时
    batch_size      INT NOT NULL DEFAULT 1,
    visit_count     INT NOT NULL DEFAULT 1,          -- 重入次数 (一般已展开为多 step)
    setup_time_hr   NUMERIC(6,4) DEFAULT 0,
    route_version   VARCHAR(32) NOT NULL DEFAULT 'current',
    valid_from      TIMESTAMP DEFAULT NOW(),
    valid_to        TIMESTAMP,
    UNIQUE (product_id, path_id, step_seq, route_version)
);
CREATE INDEX IF NOT EXISTS idx_route_product ON dim_route(product_id, route_version);
CREATE INDEX IF NOT EXISTS idx_route_toolgroup ON dim_route(tool_group_id);


-- =================================================================
-- 事实表
-- =================================================================

-- 设备实时状态 (从 MES 推送)
CREATE TABLE IF NOT EXISTS fact_equipment_state (
    record_ts       TIMESTAMP NOT NULL,
    tool_group_id   VARCHAR(64) NOT NULL,
    equipment_id    VARCHAR(64) NOT NULL,
    state           VARCHAR(32) NOT NULL,           -- UP/DOWN/MAINT/IDLE/RUN
    state_reason    VARCHAR(128),
    PRIMARY KEY (tool_group_id, equipment_id, record_ts)
);
-- 月度分区建议: pg_partman 或手动 CREATE TABLE ... PARTITION OF


-- OEE 历史 (日级)
CREATE TABLE IF NOT EXISTS fact_oee_daily (
    fact_date       DATE NOT NULL,
    tool_group_id   VARCHAR(64) NOT NULL,
    availability    NUMERIC(5,4),                   -- 0-1
    performance     NUMERIC(5,4),
    quality         NUMERIC(5,4),
    oee             NUMERIC(5,4),
    available_hours NUMERIC(8,2),                   -- 当日实际可用小时 (sum across all machines)
    PRIMARY KEY (fact_date, tool_group_id)
);
CREATE INDEX IF NOT EXISTS idx_oee_toolgroup ON fact_oee_daily(tool_group_id, fact_date DESC);


-- Cycle time 历史 (按 lot)
CREATE TABLE IF NOT EXISTS fact_cycle_time (
    lot_id          VARCHAR(64),
    product_id      VARCHAR(64),
    start_ts        TIMESTAMP,
    end_ts          TIMESTAMP,
    cycle_hours     NUMERIC(10,2),
    wait_hours      NUMERIC(10,2),
    move_hours      NUMERIC(10,2),
    PRIMARY KEY (lot_id, start_ts)
);
CREATE INDEX IF NOT EXISTS idx_cycle_product ON fact_cycle_time(product_id, start_ts DESC);


-- WIP 实时 (定期快照)
CREATE TABLE IF NOT EXISTS fact_wip_snapshot (
    snapshot_ts     TIMESTAMP NOT NULL,
    tool_group_id   VARCHAR(64) NOT NULL,
    product_id      VARCHAR(64),
    wafer_count     INT NOT NULL,
    PRIMARY KEY (snapshot_ts, tool_group_id, product_id)
);


-- 需求计划
CREATE TABLE IF NOT EXISTS fact_demand_plan (
    plan_version    VARCHAR(32) NOT NULL,
    time_window     VARCHAR(32) NOT NULL,           -- 2026-W17, 2026-04, ...
    product_id      VARCHAR(64) NOT NULL,
    wafer_count     NUMERIC(10,2) NOT NULL,
    priority        NUMERIC(4,2) DEFAULT 1.0,
    contract_min    NUMERIC(10,2) DEFAULT 0,
    market_max      NUMERIC(10,2) DEFAULT 999999,
    unit_profit     NUMERIC(10,2) DEFAULT 0,
    created_ts      TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (plan_version, time_window, product_id)
);


-- Path Mix 历史 (用于估计 α)
CREATE TABLE IF NOT EXISTS fact_path_mix (
    fact_date       DATE NOT NULL,
    product_id      VARCHAR(64) NOT NULL,
    path_id         VARCHAR(64) NOT NULL,
    wafer_count     INT NOT NULL,
    fraction        NUMERIC(5,4),
    PRIMARY KEY (fact_date, product_id, path_id)
);


-- =================================================================
-- 审计日志 (Agent 调用追溯)
-- =================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMP DEFAULT NOW(),
    session_id      VARCHAR(64),
    user_query      TEXT,
    tool_name       VARCHAR(64),
    arguments       JSONB,
    result_summary  JSONB,
    success         BOOLEAN,
    elapsed_seconds NUMERIC(8,3)
);
CREATE INDEX IF NOT EXISTS idx_audit_session ON audit_log(session_id, ts);


-- =================================================================
-- 派生表 (由 dbt 计算)
-- =================================================================

-- Capacity Matrix: [product × tool_group] 单 wafer 占用小时
-- 由 dbt model `mart_capacity_matrix` 生成,这里只建空表
CREATE TABLE IF NOT EXISTS mart_capacity_matrix (
    product_id      VARCHAR(64) NOT NULL,
    tool_group_id   VARCHAR(64) NOT NULL,
    hours_per_wafer NUMERIC(10,4) NOT NULL,
    route_version   VARCHAR(32) NOT NULL,
    refreshed_at    TIMESTAMP,
    PRIMARY KEY (product_id, tool_group_id, route_version)
);


-- Tool Group 周/日 loading 历史 (用于趋势分析)
CREATE TABLE IF NOT EXISTS mart_tool_group_loading_history (
    week_id         VARCHAR(16) NOT NULL,           -- 2026-W17
    tool_group_id   VARCHAR(64) NOT NULL,
    available_hours NUMERIC(10,2),
    demand_hours    NUMERIC(10,2),
    actual_hours    NUMERIC(10,2),
    loading_pct     NUMERIC(6,2),
    refreshed_at    TIMESTAMP,
    PRIMARY KEY (week_id, tool_group_id)
);
CREATE INDEX IF NOT EXISTS idx_loading_history ON mart_tool_group_loading_history(tool_group_id, week_id DESC);


-- 数据质量监控
CREATE TABLE IF NOT EXISTS mart_data_quality (
    check_date          DATE NOT NULL,
    metric_name         VARCHAR(64) NOT NULL,
    metric_value        NUMERIC(10,4),
    threshold           NUMERIC(10,4),
    status              VARCHAR(16),                -- pass/warn/fail
    PRIMARY KEY (check_date, metric_name)
);


-- =================================================================
-- 视图: 最新机台组状态汇总
-- =================================================================
CREATE OR REPLACE VIEW v_tool_group_current_status AS
SELECT
    tg.tool_group_id,
    tg.tool_group_name,
    tg.area,
    tg.n_machines,
    COALESCE(latest_oee.availability, 0.85) AS availability,
    COALESCE(latest_oee.oee, 0.75) AS oee,
    -- 估算可用小时 (以本周为窗口, 7 天 * 24 小时 * n_machines * availability)
    (tg.n_machines * 7 * 24 * COALESCE(latest_oee.availability, 0.85))::NUMERIC(10,2) AS weekly_available_hours
FROM dim_tool_group tg
LEFT JOIN LATERAL (
    SELECT availability, oee, fact_date
    FROM fact_oee_daily
    WHERE tool_group_id = tg.tool_group_id
    ORDER BY fact_date DESC
    LIMIT 1
) latest_oee ON TRUE
WHERE tg.is_active = TRUE;
