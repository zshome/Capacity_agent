"""
Agent Tools
===========

Each tool is:
  1. An HTTP wrapper around an L2 engine endpoint
  2. A JSON schema that the LLM can use for tool-calling

The Agent (L3) is the only thing that calls these. The LLM never touches
raw data — it only sees structured tool inputs/outputs.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ENGINE_BASE_URL = os.getenv("ENGINE_BASE_URL", "http://engine:8001")
DATA_BASE_URL = os.getenv("DATA_BASE_URL", "http://engine:8001")  # 简化: 数据层也走 engine 服务
HTTP_TIMEOUT = 120.0


# ============================================================
# Tool implementations (HTTP)
# ============================================================
def _post(path: str, json: dict) -> dict:
    """Synchronous POST helper (LangGraph tool nodes are sync)"""
    url = f"{ENGINE_BASE_URL}{path}"
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        resp = client.post(url, json=json)
        resp.raise_for_status()
        return resp.json()


def _get(path: str, params: dict | None = None) -> dict:
    url = f"{DATA_BASE_URL}{path}"
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# ----- Data layer tools (queried from data service) -----

def get_tool_group_status(area: str | None = None, time_range: str = "current") -> dict:
    """
    查询机台组实时状态 (从 PG 读取)
    返回: {tool_groups: [{id, area, n_machines, n_up, availability, ...}]}
    """
    return _get("/data/tool_group_status", {"area": area, "time_range": time_range})


def get_route_capacity_matrix(product_list: list[str], route_version: str = "current") -> dict:
    """
    获取产品-机台组 capacity 矩阵 (从 PG/dbt 读取派生表)
    返回: {capacity_matrix: {product_id: {tool_group_id: hours_per_wafer}}}
    """
    return _post("/data/capacity_matrix", {"products": product_list, "route_version": route_version})


def get_demand_plan(time_window: str = "this_week", products: list[str] | None = None) -> dict:
    """
    获取需求计划 (从 PG demand_forecast 表)
    返回: {demand_plan: {product_id: wafer_count}, time_window}
    """
    return _get("/data/demand_plan", {"time_window": time_window, "products": ",".join(products) if products else ""})


def get_historical_loading(tool_groups: list[str], n_weeks: int = 4) -> dict:
    """
    获取历史 loading (从 ClickHouse 聚合)
    返回: {history: {tool_group_id: [last n weeks loadings]}}
    """
    return _post("/data/historical_loading", {"tool_groups": tool_groups, "n_weeks": n_weeks})


# ----- Engine tools -----

def compute_rccp_simple(
    time_window: str = "this_week",
    area: str | None = None,
) -> dict:
    """
    简化版 RCCP 产能计算
    
    自动获取数据集的需求计划、产能矩阵、机台状态，然后运行 RCCP。
    参数简单，适合 LLM 调用。
    
    Args:
        time_window: 时间窗口 (this_week, next_week, this_month)
        area: 区域筛选 (可选，如 LITHO, ETCH)
    
    Returns:
        RCCP 计算结果，包括 loading_table, feasible, overall_loading_pct
    """
    # 1. 获取机台组状态
    status = _get("/data/tool_group_status", {"area": area or "", "time_range": "current"})
    available_hours = {tg["tool_group_id"]: tg["available_hours"] for tg in status.get("tool_groups", [])}
    
    # 2. 获取需求计划
    demand = _get("/data/demand_plan", {"time_window": time_window, "products": ""})
    demand_plan = demand.get("demand_plan", {})
    
    # 3. 获取产能矩阵
    matrix = _post("/data/capacity_matrix", {"dataset_id": None, "products": None, "route_version": "current"})
    capacity_matrix = matrix.get("capacity_matrix", {})
    
    # 4. 运行 RCCP
    return _post("/rccp/compute", {
        "demand_plan": demand_plan,
        "capacity_matrix": capacity_matrix,
        "available_hours": available_hours,
        "time_window": "weekly",
    })


def compute_rccp(
    demand_plan: dict[str, float],
    capacity_matrix: dict[str, dict[str, float]],
    available_hours: dict[str, float],
    time_window: str = "weekly",
) -> dict:
    """运行 RCCP 产能计算（完整参数版，不推荐 LLM 直接调用）"""
    return _post("/rccp/compute", {
        "demand_plan": demand_plan,
        "capacity_matrix": capacity_matrix,
        "available_hours": available_hours,
        "time_window": time_window,
    })


def analyze_bottleneck_simple(
    time_window: str = "this_week",
    area: str | None = None,
) -> dict:
    """
    简化版瓶颈分析
    
    自动运行 RCCP 获取 loading_table，然后进行三维瓶颈识别。
    参数简单，适合 LLM 调用。
    
    Args:
        time_window: 时间窗口
        area: 区域筛选
    
    Returns:
        瓶颈分析结果，包括 primary_bottleneck, bottlenecks 列表
    """
    # 1. 先运行 RCCP 获取 loading_table
    rccp_result = compute_rccp_simple(time_window, area)
    
    # 2. 获取机台组信息用于服务率和机台数
    status = _get("/data/tool_group_status", {"area": area or "", "time_range": "current"})
    tool_groups = status.get("tool_groups", [])
    
    service_rates = {tg["tool_group_id"]: tg.get("nameplate_throughput_wph", 1.0) for tg in tool_groups}
    n_servers = {tg["tool_group_id"]: tg.get("n_machines", 1) for tg in tool_groups}
    
    # 3. 获取历史 loading
    tg_ids = [tg["tool_group_id"] for tg in tool_groups]
    historical = _post("/data/historical_loading", {"tool_groups": tg_ids, "n_weeks": 4})
    
    # 4. 运行瓶颈分析
    return _post("/bottleneck/analyze", {
        "loading_table": rccp_result.get("loading_table", []),
        "historical_loading": historical.get("history", {}),
        "service_rates": service_rates,
        "n_servers": n_servers,
    })


def analyze_bottleneck(
    loading_table: list[dict],
    historical_loading: dict[str, list[float]] | None = None,
    service_rates: dict[str, float] | None = None,
    n_servers: dict[str, int] | None = None,
) -> dict:
    """三维瓶颈识别（完整参数版，不推荐 LLM 直接调用）"""
    return _post("/bottleneck/analyze", {
        "loading_table": loading_table,
        "historical_loading": historical_loading or {},
        "service_rates": service_rates or {},
        "n_servers": n_servers or {},
    })


def run_lp_optimizer(
    products: list[str],
    tool_groups: list[str],
    capacity_matrix: dict[str, dict[str, float]],
    available_hours: dict[str, float],
    objective: str = "max_profit",
    demand_min: dict[str, float] | None = None,
    demand_max: dict[str, float] | None = None,
    unit_profit: dict[str, float] | None = None,
) -> dict:
    """LP 优化产品 mix"""
    return _post("/lp/optimize", {
        "products": products,
        "tool_groups": tool_groups,
        "capacity_matrix": capacity_matrix,
        "available_hours": available_hours,
        "objective": objective,
        "demand_min": demand_min or {},
        "demand_max": demand_max or {},
        "unit_profit": unit_profit or {},
    })


def run_des_local(
    tool_groups: list[dict],
    arrivals: list[dict],
    sim_duration_hours: float = 168.0,
) -> dict:
    """局部 DES 仿真验证 (1-3 minutes)"""
    return _post("/des/run", {
        "tool_groups": tool_groups,
        "arrivals": arrivals,
        "sim_duration_hours": sim_duration_hours,
        "n_replications": 3,
    })


def whatif_simulate(
    baseline_demand: dict[str, float],
    baseline_capacity_matrix: dict[str, dict[str, float]],
    baseline_available_hours: dict[str, float],
    perturbation_type: str,
    perturbation_params: dict,
) -> dict:
    """What-if 场景"""
    return _post("/whatif/run", {
        "baseline_rccp_input": {
            "demand_plan": baseline_demand,
            "capacity_matrix": baseline_capacity_matrix,
            "available_hours": baseline_available_hours,
        },
        "perturbation_type": perturbation_type,
        "perturbation_params": perturbation_params,
    })


# ============================================================
# NEW: Scenario Classification
# ============================================================
def classify_scenario(
    products: list[str],
    tool_groups: list[str],
    process_steps: list[str],
    feasibility_matrix: dict[str, dict[str, dict[str, bool]]] | None = None,
    tc_matrix: dict[str, dict[str, dict[str, float]]] | None = None,
    path_mix: dict[str, dict[str, float]] | None = None,
    backup_tools: dict[str, list[str]] | None = None,
) -> dict:
    """
    情景分类判断
    
    根据产品-机台-制程配置判断属于情景1-4还是全适场景
    返回推荐的计算方法
    """
    return _post("/scenario/classify", {
        "products": products,
        "tool_groups": tool_groups,
        "process_steps": process_steps,
        "feasibility_matrix": feasibility_matrix or {},
        "tc_matrix": tc_matrix or {},
        "path_mix": path_mix,
        "backup_tools": backup_tools or {},
    })


# ============================================================
# NEW: Standard Capacity Calculation
# ============================================================
def compute_standard_capacity(
    product_id: str,
    tool_id: str,
    tc_by_process: dict[str, float],
    batch_size: float = 25.0,
    uptime: float = 0.90,
    loss_time: float = 0.05,
) -> dict:
    """
    标准产能计算
    
    公式: 产能 = 24hr / TTL_TC × batch_size × 30天 × (uptime - loss_time)
    
    适用: 情景1（单机台/相同Path）
    """
    return _post("/capacity/standard_compute", {
        "product_id": product_id,
        "tool_id": tool_id,
        "tc_by_process": tc_by_process,
        "batch_size": batch_size,
        "uptime": uptime,
        "loss_time": loss_time,
    })


def batch_compute_capacity(
    tc_matrix: dict[str, dict[str, dict[str, float]]],
    batch_sizes: dict[str, float] | None = None,
    uptime: float = 0.90,
    loss_time: float = 0.05,
) -> dict:
    """
    从TC矩阵批量计算产能
    
    适用: 多产品多机台场景
    """
    return _post("/capacity/batch_compute", {
        "tc_matrix": tc_matrix,
        "batch_sizes": batch_sizes,
        "uptime": uptime,
        "loss_time": loss_time,
    })


# ============================================================
# NEW: Allocation Model
# ============================================================
def optimize_allocation(
    products: list[str],
    tools: list[str],
    processes: list[str],
    feasibility: dict[str, dict[str, dict[str, bool]]],
    tc_matrix: dict[str, dict[str, dict[str, float]]],
    available_hours: dict[str, float],
    demand_target: dict[str, float] | None = None,
    backup_tools: dict[str, list[str]] | None = None,
    objective: str = "max_output",
) -> dict:
    """
    分配优化模型
    
    算法: 窃举+约束+分配
    适用: 情景2-4（多机台/不同配置）
    """
    return _post("/allocation/optimize", {
        "products": products,
        "tools": tools,
        "processes": processes,
        "feasibility": feasibility,
        "tc_matrix": tc_matrix,
        "available_hours": available_hours,
        "demand_target": demand_target or {},
        "backup_tools": backup_tools or {},
        "objective": objective,
    })


# ============================================================
# NEW: Unified Analysis (智能路由)
# ============================================================
def unified_capacity_analyze(
    products: list[str],
    tool_groups: list[str],
    process_steps: list[str],
    available_hours: dict[str, float],
    feasibility_matrix: dict[str, dict[str, dict[str, bool]]] | None = None,
    tc_matrix: dict[str, dict[str, dict[str, float]]] | None = None,
    demand_plan: dict[str, float] | None = None,
    path_mix: dict[str, dict[str, float]] | None = None,
    backup_tools: dict[str, list[str]] | None = None,
) -> dict:
    """
    统一产能分析入口
    
    自动判断情景类型，选择合适的计算方法:
    - 情景1/全适 → 标准产能计算或RCCP
    - 情景2-4 → 分配模型优化
    """
    return _post("/capacity/unified_analyze", {
        "products": products,
        "tool_groups": tool_groups,
        "process_steps": process_steps,
        "feasibility_matrix": feasibility_matrix or {},
        "tc_matrix": tc_matrix or {},
        "available_hours": available_hours,
        "demand_plan": demand_plan or {},
        "path_mix": path_mix,
        "backup_tools": backup_tools or {},
    })


# ============================================================
# Tool registry (name → callable)
# ============================================================
TOOL_FUNCTIONS = {
    "get_tool_group_status": get_tool_group_status,
    "get_route_capacity_matrix": get_route_capacity_matrix,
    "get_demand_plan": get_demand_plan,
    "get_historical_loading": get_historical_loading,
    "compute_rccp_simple": compute_rccp_simple,  # 简化版，推荐 LLM 使用
    "compute_rccp": compute_rccp,
    "analyze_bottleneck_simple": analyze_bottleneck_simple,  # 简化版，推荐 LLM 使用
    "analyze_bottleneck": analyze_bottleneck,
    "run_lp_optimizer": run_lp_optimizer,
    "run_des_local": run_des_local,
    "whatif_simulate": whatif_simulate,
    "classify_scenario": classify_scenario,
    "compute_standard_capacity": compute_standard_capacity,
    "batch_compute_capacity": batch_compute_capacity,
    "optimize_allocation": optimize_allocation,
    "unified_capacity_analyze": unified_capacity_analyze,
}


# ============================================================
# OpenAI-compatible tool schemas (for LLM tool-calling)
# ============================================================
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_tool_group_status",
            "description": "查询机台组实时状态,包含设备数、可用数、availability 等",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "description": "工艺区域,如 LITHO/ETCH/DEPO/CMP,留空表示全厂",
                    },
                    "time_range": {"type": "string", "default": "current"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route_capacity_matrix",
            "description": "获取指定产品列表的 Capacity Matrix (单 wafer 在每个 tool group 占用小时数)",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_list": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "产品 ID 列表",
                    },
                    "route_version": {"type": "string", "default": "current"},
                },
                "required": ["product_list"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_demand_plan",
            "description": "获取指定时间窗的需求计划",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_window": {
                        "type": "string",
                        "description": "this_week | next_week | this_month",
                        "default": "this_week",
                    },
                    "products": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选,只返回指定产品的计划",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_historical_loading",
            "description": "获取最近 N 周每个 tool group 的历史 loading 序列",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_groups": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "n_weeks": {"type": "integer", "default": 4},
                },
                "required": ["tool_groups"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_rccp",
            "description": "运行 RCCP 产能计算,得到每个 tool group 的 loading/gap/状态 (需要完整参数,不推荐LLM直接调用)",
            "parameters": {
                "type": "object",
                "properties": {
                    "demand_plan": {"type": "object"},
                    "capacity_matrix": {"type": "object"},
                    "available_hours": {"type": "object"},
                    "time_window": {"type": "string", "default": "weekly"},
                },
                "required": ["demand_plan", "capacity_matrix", "available_hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_rccp_simple",
            "description": "简化版 RCCP 产能计算 - 自动获取数据并计算。推荐LLM使用此工具,参数简单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_window": {
                        "type": "string",
                        "description": "时间窗口: this_week | next_week | this_month",
                        "default": "this_week",
                    },
                    "area": {
                        "type": "string",
                        "description": "可选区域筛选,如 LITHO, ETCH, DEPO",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_bottleneck",
            "description": "对 RCCP 输出做三维瓶颈分析 (loading + 队列 + 漂移),返回瓶颈排名 (需要 loading_table 参数)",
            "parameters": {
                "type": "object",
                "properties": {
                    "loading_table": {"type": "array"},
                    "historical_loading": {"type": "object"},
                    "service_rates": {"type": "object"},
                    "n_servers": {"type": "object"},
                },
                "required": ["loading_table"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_bottleneck_simple",
            "description": "简化版瓶颈分析 - 自动运行RCCP并分析瓶颈。推荐LLM使用此工具,参数简单。",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_window": {
                        "type": "string",
                        "description": "时间窗口: this_week | next_week | this_month",
                        "default": "this_week",
                    },
                    "area": {
                        "type": "string",
                        "description": "可选区域筛选,如 LITHO, ETCH, DEPO",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_lp_optimizer",
            "description": "当 RCCP 出现 capacity gap 时,运行 LP 优化产品 mix 或决定削减计划",
            "parameters": {
                "type": "object",
                "properties": {
                    "products": {"type": "array", "items": {"type": "string"}},
                    "tool_groups": {"type": "array", "items": {"type": "string"}},
                    "capacity_matrix": {"type": "object"},
                    "available_hours": {"type": "object"},
                    "objective": {
                        "type": "string",
                        "enum": ["max_profit", "max_output", "min_deviation"],
                        "default": "max_profit",
                    },
                    "demand_min": {"type": "object"},
                    "demand_max": {"type": "object"},
                    "unit_profit": {"type": "object"},
                },
                "required": ["products", "tool_groups", "capacity_matrix", "available_hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_des_local",
            "description": "局部 DES 仿真,验证 RCCP/LP 输出是否在排队动态下可行 (耗时 1-3 分钟,只对瓶颈机台跑)",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_groups": {"type": "array"},
                    "arrivals": {"type": "array"},
                    "sim_duration_hours": {"type": "number", "default": 168.0},
                },
                "required": ["tool_groups", "arrivals"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "whatif_simulate",
            "description": "What-if 场景: 改变需求或机台可用小时,比较产能差异",
            "parameters": {
                "type": "object",
                "properties": {
                    "baseline_demand": {"type": "object"},
                    "baseline_capacity_matrix": {"type": "object"},
                    "baseline_available_hours": {"type": "object"},
                    "perturbation_type": {
                        "type": "string",
                        "enum": ["tool_down", "demand_change"],
                    },
                    "perturbation_params": {"type": "object"},
                },
                "required": [
                    "baseline_demand",
                    "baseline_capacity_matrix",
                    "baseline_available_hours",
                    "perturbation_type",
                    "perturbation_params",
                ],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "classify_scenario",
            "description": "情景分类判断: 根据产品-机台-制程配置判断属于情景1-4还是全适场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "products": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "产品列表",
                    },
                    "tool_groups": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "机台组列表",
                    },
                    "process_steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "制程步骤列表",
                    },
                    "feasibility_matrix": {
                        "type": "object",
                        "description": "可行性矩阵 {product: {tool: {process: bool}}}",
                    },
                    "tc_matrix": {
                        "type": "object",
                        "description": "TC矩阵 {product: {tool: {process: hours}}}",
                    },
                    "path_mix": {
                        "type": "object",
                        "description": "Path配置 {product: {path: alpha}}",
                    },
                    "backup_tools": {
                        "type": "object",
                        "description": "Backup配置 {tool: [backup_tools]}",
                    },
                },
                "required": ["products", "tool_groups", "process_steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compute_standard_capacity",
            "description": "标准产能计算（基于规则文档公式）: 产能 = 24hr / TTL_TC × batch_size × 30天 × (uptime - loss_time)",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "tool_id": {"type": "string"},
                    "tc_by_process": {
                        "type": "object",
                        "description": "各制程的周期时间 {process_id: hours/wafer}",
                    },
                    "batch_size": {"type": "number", "default": 25.0},
                    "uptime": {"type": "number", "default": 0.90},
                    "loss_time": {"type": "number", "default": 0.05},
                },
                "required": ["product_id", "tool_id", "tc_by_process"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "batch_compute_capacity",
            "description": "从TC矩阵批量计算产能，适用于多产品多机台场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "tc_matrix": {
                        "type": "object",
                        "description": "TC矩阵 {product: {tool: {process: hours}}}",
                    },
                    "batch_sizes": {
                        "type": "object",
                        "description": "各产品的批次大小 {product: batch_size}",
                    },
                    "uptime": {"type": "number", "default": 0.90},
                    "loss_time": {"type": "number", "default": 0.05},
                },
                "required": ["tc_matrix"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_allocation",
            "description": "分配优化模型（情景2-4）: 穷举+约束+分配，适用于多机台/不同配置场景",
            "parameters": {
                "type": "object",
                "properties": {
                    "products": {"type": "array", "items": {"type": "string"}},
                    "tools": {"type": "array", "items": {"type": "string"}},
                    "processes": {"type": "array", "items": {"type": "string"}},
                    "feasibility": {
                        "type": "object",
                        "description": "可行性矩阵 {product: {tool: {process: bool}}}",
                    },
                    "tc_matrix": {
                        "type": "object",
                        "description": "TC矩阵 {product: {tool: {process: hours}}}",
                    },
                    "available_hours": {
                        "type": "object",
                        "description": "机台可用小时 {tool: hours}",
                    },
                    "demand_target": {
                        "type": "object",
                        "description": "产品需求目标 {product: wafers}",
                    },
                    "backup_tools": {
                        "type": "object",
                        "description": "Backup配置 {tool: [backups]}",
                    },
                    "objective": {
                        "type": "string",
                        "enum": ["max_output", "min_variance", "max_balance"],
                        "default": "max_output",
                    },
                },
                "required": ["products", "tools", "processes", "feasibility", "tc_matrix", "available_hours"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "unified_capacity_analyze",
            "description": "统一产能分析入口: 自动判断情景类型并选择合适的计算方法",
            "parameters": {
                "type": "object",
                "properties": {
                    "products": {"type": "array", "items": {"type": "string"}},
                    "tool_groups": {"type": "array", "items": {"type": "string"}},
                    "process_steps": {"type": "array", "items": {"type": "string"}},
                    "feasibility_matrix": {"type": "object"},
                    "tc_matrix": {"type": "object"},
                    "available_hours": {"type": "object"},
                    "demand_plan": {"type": "object"},
                    "path_mix": {"type": "object"},
                    "backup_tools": {"type": "object"},
                },
                "required": ["products", "tool_groups", "process_steps", "available_hours"],
            },
        },
    },
]

# ============================================================
# Simplified tool schemas for LLM (火山引擎等 API 可能对 schema 大小有限制)
# ============================================================
SIMPLIFIED_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_tool_group_status",
            "description": "查询机台组实时状态,包含设备数、可用数、OEE等",
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {"type": "string", "description": "区域筛选(LITHO/ETCH/DEPO等)"},
                    "time_range": {"type": "string", "default": "current"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_demand_plan",
            "description": "获取需求计划",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_window": {"type": "string", "default": "this_week"},
                    "products": {"type": "array", "items": {"type": "string"}}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "compute_rccp_simple",
            "description": "简化版RCCP产能计算 - 自动获取数据并计算。推荐使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_window": {"type": "string", "default": "this_week"},
                    "area": {"type": "string", "description": "可选区域筛选"}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_bottleneck_simple",
            "description": "简化版瓶颈分析 - 自动运行RCCP并分析瓶颈。推荐使用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "time_window": {"type": "string", "default": "this_week"},
                    "area": {"type": "string", "description": "可选区域筛选"}
                },
                "required": []
            }
        }
    },
]
