-- =================================================================
-- dbt model: mart_capacity_matrix
-- =================================================================
-- 从 dim_route 派生 Capacity Matrix
--   C[i,j] = Σ over steps where toolgroup(s)=j: run_time_hr / batch_size
--
-- materialization: table (一天一次刷新即可)
-- depends on: dim_route, fact_path_mix
-- =================================================================

{{ config(
    materialized='table',
    indexes=[
      {'columns': ['product_id'], 'unique': false},
      {'columns': ['tool_group_id'], 'unique': false},
      {'columns': ['product_id', 'tool_group_id', 'route_version'], 'unique': true}
    ]
) }}

WITH route_steps AS (
    SELECT
        product_id,
        path_id,
        tool_group_id,
        route_version,
        SUM(run_time_hr * visit_count / GREATEST(batch_size, 1)) AS unit_hours_per_path
    FROM {{ source('capacity', 'dim_route') }}
    WHERE valid_to IS NULL OR valid_to > NOW()
    GROUP BY product_id, path_id, tool_group_id, route_version
),

-- 计算近 4 周 path mix α (用历史均值稳定估计)
path_mix AS (
    SELECT
        product_id,
        path_id,
        AVG(fraction) AS alpha
    FROM {{ source('capacity', 'fact_path_mix') }}
    WHERE fact_date >= CURRENT_DATE - INTERVAL '28 days'
    GROUP BY product_id, path_id
),

-- 加权汇总 (默认 alpha=1.0 if 没有历史)
weighted AS (
    SELECT
        rs.product_id,
        rs.tool_group_id,
        rs.route_version,
        SUM(rs.unit_hours_per_path * COALESCE(pm.alpha, 1.0)) AS hours_per_wafer
    FROM route_steps rs
    LEFT JOIN path_mix pm
      ON rs.product_id = pm.product_id
     AND rs.path_id = pm.path_id
    GROUP BY rs.product_id, rs.tool_group_id, rs.route_version
)

SELECT
    product_id,
    tool_group_id,
    hours_per_wafer,
    route_version,
    NOW() AS refreshed_at
FROM weighted
WHERE hours_per_wafer > 0
