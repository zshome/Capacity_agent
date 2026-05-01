"""
Engine Server
=============

Unified FastAPI service that exposes all L2 compute engines as HTTP tools.
The Agent (L3) calls these endpoints over the internal docker network.

Run:
  uvicorn engines.server:app --host 0.0.0.0 --port 8001
"""
from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from engines.bottleneck_analyzer import (
    BottleneckInput,
    analyze_bottleneck,
)
from engines.data_service import DATASET_REGISTRY, generate_excel_template
from engines.des_validator import (
    DESInput,
    DESJobArrival,
    DESToolGroup,
    run_des,
)
try:
    from engines.lp_optimizer import LPInput, Objective, optimize
    PYOMO_AVAILABLE = True
except ImportError:
    PYOMO_AVAILABLE = False
    LPInput = None
    Objective = None
    optimize = None
    logging.warning("lp_optimizer not available (Pyomo not installed). LP optimization will return unavailable status.")
from engines.rccp import RCCPInput, build_capacity_matrix, compute_rccp
from engines.output_rccp import compute_wip_remaining_load
from engines.scenario_classifier import (
    ScenarioInput,
    ScenarioType,
    classify_scenario,
)
from engines.standard_capacity import (
    StandardCapacityInput,
    compute_standard_capacity,
    compute_capacity_from_tc_matrix,
)
try:
    from engines.allocation_model import (
        AllocationInput,
        AllocationObjective,
        allocate,
        PYOMO_AVAILABLE as ALLOCATION_PYOMO_AVAILABLE,
    )
    ALLOCATION_AVAILABLE = ALLOCATION_PYOMO_AVAILABLE
except ImportError:
    ALLOCATION_AVAILABLE = False
    AllocationInput = None
    AllocationObjective = None
    allocate = None

from engines.production_plan import (
    ProductionPlanInput,
    ProductionPlanResult,
    generate_production_plan,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Capacity Engine API",
    version="1.0.0",
    description="L2 计算引擎服务: RCCP / Bottleneck / LP / DES / What-if",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Health
# ============================================================
@app.get("/health")
def health():
    return {"status": "ok", "service": "capacity-engine", "version": app.version}


# ============================================================
# LLM Configuration
# ============================================================
class LLMConfigRequest(BaseModel):
    """LLM配置请求"""
    provider: str = Field(default="vllm", description="API提供商: vllm | openai | volces | dashscope | custom")
    model: str | None = Field(default=None, description="模型ID")
    api_key: str | None = Field(default=None, description="API密钥")
    base_url: str | None = Field(default=None, description="自定义API地址")


@app.post("/llm/configure")
def llm_configure(req: LLMConfigRequest):
    """
    配置LLM后端
    
    支持: vllm, openai, volces, dashscope, custom
    """
    import os
    from app.llm_client import reset_llm_client
    
    # 默认base_url（不包含model）
    defaults = {
        "vllm": "http://vllm:8000/v1",
        "openai": "https://api.openai.com/v1",
        "volces": "https://ark.cn-beijing.volces.com/api/v3",
        "dashscope": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    
    # 设置base_url
    if req.provider in defaults:
        os.environ["LLM_BASE_URL"] = defaults[req.provider]
    elif req.base_url:
        # 用户自定义URL，去掉末尾的/chat/completions等路径
        base = req.base_url.rstrip("/").replace("/chat/completions", "").replace("/completions", "")
        os.environ["LLM_BASE_URL"] = base
    
    # 设置model（用户指定的优先）
    if req.model:
        os.environ["LLM_MODEL"] = req.model
    
    # 设置api_key
    if req.api_key:
        os.environ["LLM_API_KEY"] = req.api_key
    
    # 重置客户端
    reset_llm_client()
    
    from app.llm_client import get_llm_client
    client = get_llm_client()
    
    return {
        "status": "configured",
        "provider": req.provider,
        "model": client.model,
        "base_url": client.base_url,
        "message": "LLM配置已更新，Agent问答将使用新模型"
    }


@app.get("/llm/status")
def llm_status():
    """获取当前LLM配置状态"""
    import os
    return {
        "base_url": os.getenv("LLM_BASE_URL", os.getenv("VLLM_BASE_URL", "http://vllm:8000/v1")),
        "model": os.getenv("LLM_MODEL", "Qwen/Qwen2.5-32B-Instruct"),
        "api_key_set": bool(os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")),
        "timeout": int(os.getenv("LLM_TIMEOUT", "120"))
    }


# ============================================================
# Data endpoints
# ============================================================
@app.get("/data/datasets")
def list_datasets():
    return {"datasets": DATASET_REGISTRY.list_datasets()}


@app.get("/data/dataset_summary")
def dataset_summary(dataset_id: str | None = None):
    try:
        return DATASET_REGISTRY.get_dataset_summary_payload(dataset_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("dataset_summary failed")
        raise HTTPException(500, str(e))


@app.get("/data/wip_lot_detail")
def data_wip_lot_detail(dataset_id: str | None = None):
    try:
        return DATASET_REGISTRY.get_wip_lot_detail_payload(dataset_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("data_wip_lot_detail failed")
        raise HTTPException(500, str(e))


@app.post("/data/excel/import")
async def import_excel_dataset(
    file: UploadFile = File(...),
    dataset_name: str | None = Form(default=None),
):
    try:
        content = await file.read()
        summary = DATASET_REGISTRY.import_excel(content, dataset_name or file.filename or "uploaded.xlsx")
        return {"dataset": summary}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("import_excel_dataset failed")
        raise HTTPException(500, str(e))


@app.get("/data/excel/template")
def download_excel_template():
    """
    下载 Excel 导入模板
    
    包含4个Sheet的示例数据:
    - route_master: 产品工艺路线
    - tool_groups: 机台组信息
    - oee: OEE历史数据
    - demand_plan: 需求计划
    """
    try:
        content = generate_excel_template()
        return StreamingResponse(
            iter([content]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=capacity_import_template.xlsx"
            }
        )
    except Exception as e:
        logger.exception("download_excel_template failed")
        raise HTTPException(500, str(e))


@app.get("/data/tool_group_status")
def data_tool_group_status(area: str | None = None, time_range: str = "current", dataset_id: str | None = None):
    try:
        payload = DATASET_REGISTRY.get_tool_group_status_payload(dataset_id=dataset_id, area=area)
        payload["time_range"] = time_range
        return payload
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("data_tool_group_status failed")
        raise HTTPException(500, str(e))


class CapacityMatrixDataRequest(BaseModel):
    products: list[str] | None = None
    route_version: str = "current"
    dataset_id: str | None = None


@app.post("/data/capacity_matrix")
def data_capacity_matrix(req: CapacityMatrixDataRequest):
    try:
        return DATASET_REGISTRY.build_capacity_matrix_payload(
            dataset_id=req.dataset_id,
            products=req.products,
            route_version=req.route_version,
        )
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("data_capacity_matrix failed")
        raise HTTPException(500, str(e))


@app.get("/data/demand_plan")
def data_demand_plan(
    time_window: str = "this_week",
    products: str = "",
    dataset_id: str | None = None,
):
    try:
        parsed_products = [item for item in products.split(",") if item]
        return DATASET_REGISTRY.get_demand_plan_payload(dataset_id, time_window, parsed_products or None)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("data_demand_plan failed")
        raise HTTPException(500, str(e))


class HistoricalLoadingDataRequest(BaseModel):
    tool_groups: list[str] | None = None
    n_weeks: int = 4
    dataset_id: str | None = None


@app.post("/data/historical_loading")
def data_historical_loading(req: HistoricalLoadingDataRequest):
    try:
        return DATASET_REGISTRY.get_historical_loading_payload(req.dataset_id, req.tool_groups, req.n_weeks)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        logger.exception("data_historical_loading failed")
        raise HTTPException(500, str(e))


# ============================================================
# RCCP endpoint
# ============================================================
class RCCPRequest(BaseModel):
    demand_plan: dict[str, float] = Field(..., description="{product_id: wafer_count}")
    capacity_matrix: dict[str, dict[str, float]] = Field(
        ..., description="{product_id: {tool_group_id: hours_per_wafer}}"
    )
    available_hours: dict[str, float] = Field(..., description="{tool_group_id: available_hours}")
    dataset_id: str | None = Field(default=None, description="数据集ID")
    wip_lot_detail: list[dict[str, Any]] | None = Field(default=None, description="WIP Lot 明细")
    enable_wip_adjustment: bool = Field(default=False, description="是否启用WIP校正")
    time_window: str = "weekly"


@app.post("/rccp/compute")
def rccp_compute(req: RCCPRequest):
    """运行 RCCP 产能计算"""
    try:
        cm_df = pd.DataFrame(req.capacity_matrix).T.fillna(0.0)
        wip_remaining_hours: dict[str, float] = {}

        if req.enable_wip_adjustment and req.wip_lot_detail:
            bundle = DATASET_REGISTRY.get_dataset(req.dataset_id)
            wip_df = pd.DataFrame(req.wip_lot_detail)
            wip_load = compute_wip_remaining_load(wip_df, bundle.routes.copy())
            wip_remaining_hours = {
                tg_id: float(sum(product_hours.values()))
                for tg_id, product_hours in wip_load.items()
            }

        inp = RCCPInput(
            demand_plan=req.demand_plan,
            capacity_matrix=cm_df,
            available_hours=req.available_hours,
            wip_remaining_hours=wip_remaining_hours,
            time_window=req.time_window,
        )
        result = compute_rccp(inp)
        return result.to_dict()
    except Exception as e:
        logger.exception("RCCP failed")
        raise HTTPException(500, str(e))


# ============================================================
# Build Capacity Matrix endpoint (utility)
# ============================================================
class BuildMatrixRequest(BaseModel):
    route_master: list[dict[str, Any]] = Field(
        ...,
        description="List of route records with keys: product_id, path_id, step_seq, tool_group_id, run_time_hr, batch_size",
    )
    path_mix: dict[str, dict[str, float]] | None = None


@app.post("/rccp/build_matrix")
def rccp_build_matrix(req: BuildMatrixRequest):
    try:
        df = pd.DataFrame(req.route_master)
        matrix = build_capacity_matrix(df, req.path_mix)
        return {
            "capacity_matrix": matrix.to_dict(orient="index"),
            "n_products": len(matrix.index),
            "n_tool_groups": len(matrix.columns),
        }
    except Exception as e:
        logger.exception("build_matrix failed")
        raise HTTPException(500, str(e))


# ============================================================
# Bottleneck endpoint
# ============================================================
class BottleneckRequest(BaseModel):
    loading_table: list[dict[str, Any]]
    historical_loading: dict[str, list[float]] = Field(default_factory=dict)
    service_rates: dict[str, float] = Field(default_factory=dict)
    n_servers: dict[str, int] = Field(default_factory=dict)
    weights: dict[str, float] | None = None


@app.post("/bottleneck/analyze")
def bottleneck_analyze(req: BottleneckRequest):
    try:
        inp = BottleneckInput(
            loading_table=req.loading_table,
            historical_loading=req.historical_loading,
            service_rates=req.service_rates,
            n_servers=req.n_servers,
            weights=req.weights or {"loading": 0.5, "queue": 0.3, "drift": 0.2},
        )
        result = analyze_bottleneck(inp)
        return result.to_dict()
    except Exception as e:
        logger.exception("Bottleneck analyze failed")
        raise HTTPException(500, str(e))


# ============================================================
# LP optimize endpoint
# ============================================================
class LPRequest(BaseModel):
    products: list[str]
    tool_groups: list[str]
    capacity_matrix: dict[str, dict[str, float]]
    available_hours: dict[str, float]
    demand_min: dict[str, float] = Field(default_factory=dict)
    demand_max: dict[str, float] = Field(default_factory=dict)
    demand_target: dict[str, float] = Field(default_factory=dict)
    unit_profit: dict[str, float] = Field(default_factory=dict)
    priority: dict[str, float] = Field(default_factory=dict)
    objective: str = "max_profit"
    solver: str = "cbc"
    time_limit_seconds: int = 60


@app.post("/lp/optimize")
def lp_optimize(req: LPRequest):
    if not PYOMO_AVAILABLE:
        return {
            "status": "unavailable",
            "error": "Pyomo not installed. LP optimization requires Pyomo + CBC solver.",
            "recommendation": "Install with: pip install pyomo",
            "alternative": "Use RCCP for basic capacity analysis without optimization."
        }
    try:
        cm = pd.DataFrame(req.capacity_matrix).T.fillna(0.0)
        inp = LPInput(
            products=req.products,
            tool_groups=req.tool_groups,
            capacity_matrix=cm,
            available_hours=req.available_hours,
            demand_min=req.demand_min,
            demand_max=req.demand_max,
            demand_target=req.demand_target,
            unit_profit=req.unit_profit,
            priority=req.priority,
            objective=Objective(req.objective),
            solver=req.solver,
            time_limit_seconds=req.time_limit_seconds,
        )
        result = optimize(inp)
        return result.to_dict()
    except Exception as e:
        logger.exception("LP optimize failed")
        raise HTTPException(500, str(e))


# ============================================================
# DES endpoint
# ============================================================
class DESToolGroupSpec(BaseModel):
    tool_group_id: str
    n_machines: int
    service_rate: float
    service_cv: float = 0.5
    availability: float = 0.85


class DESArrivalSpec(BaseModel):
    product_id: str
    arrival_rate: float
    arrival_cv: float = 1.0
    target_tool_groups: list[str]
    service_time_per_tg: dict[str, float]


class DESRequest(BaseModel):
    tool_groups: list[DESToolGroupSpec]
    arrivals: list[DESArrivalSpec]
    sim_duration_hours: float = 168.0
    warmup_hours: float = 24.0
    n_replications: int = 3


@app.post("/des/run")
def des_run(req: DESRequest):
    try:
        inp = DESInput(
            tool_groups=[
                DESToolGroup(**tg.model_dump()) for tg in req.tool_groups
            ],
            arrivals=[
                DESJobArrival(**a.model_dump()) for a in req.arrivals
            ],
            sim_duration_hours=req.sim_duration_hours,
            warmup_hours=req.warmup_hours,
            n_replications=req.n_replications,
        )
        result = run_des(inp)
        return result.to_dict()
    except Exception as e:
        logger.exception("DES run failed")
        raise HTTPException(500, str(e))


# ============================================================
# What-if endpoint (composable)
# ============================================================
class WhatIfRequest(BaseModel):
    """
    What-if 场景: 指定一个扰动,Agent 比较扰动前后的产能差异
    扰动类型:
      - "tool_down":    {tool_group_id, hours_lost}
      - "demand_change": {product_id, delta_wafers}
      - "path_switch":  {product_id, new_path_id}
    """
    baseline_rccp_input: RCCPRequest
    perturbation_type: str
    perturbation_params: dict[str, Any]


@app.post("/whatif/run")
def whatif_run(req: WhatIfRequest):
    try:
        # 1. 跑 baseline
        baseline_cm = pd.DataFrame(req.baseline_rccp_input.capacity_matrix).T.fillna(0.0)
        baseline = compute_rccp(RCCPInput(
            demand_plan=req.baseline_rccp_input.demand_plan,
            capacity_matrix=baseline_cm,
            available_hours=req.baseline_rccp_input.available_hours,
        ))

        # 2. 应用扰动
        scenario_demand = dict(req.baseline_rccp_input.demand_plan)
        scenario_avail = dict(req.baseline_rccp_input.available_hours)

        if req.perturbation_type == "tool_down":
            tg = req.perturbation_params["tool_group_id"]
            hours_lost = req.perturbation_params["hours_lost"]
            scenario_avail[tg] = max(0, scenario_avail.get(tg, 0) - hours_lost)
        elif req.perturbation_type == "demand_change":
            pid = req.perturbation_params["product_id"]
            delta = req.perturbation_params["delta_wafers"]
            scenario_demand[pid] = scenario_demand.get(pid, 0) + delta
        else:
            raise ValueError(f"Unknown perturbation type: {req.perturbation_type}")

        # 3. 跑 scenario
        scenario = compute_rccp(RCCPInput(
            demand_plan=scenario_demand,
            capacity_matrix=baseline_cm,
            available_hours=scenario_avail,
        ))

        # 4. Diff
        diff_table = []
        baseline_lookup = {tg.tool_group_id: tg for tg in baseline.loading_table}
        for tg in scenario.loading_table:
            base = baseline_lookup.get(tg.tool_group_id)
            if base:
                diff_table.append({
                    "tool_group_id": tg.tool_group_id,
                    "loading_baseline": round(base.loading_pct, 2),
                    "loading_scenario": round(tg.loading_pct, 2),
                    "delta_pp": round(tg.loading_pct - base.loading_pct, 2),
                    "status_baseline": base.status,
                    "status_scenario": tg.status,
                    "status_changed": base.status != tg.status,
                })

        diff_table.sort(key=lambda x: abs(x["delta_pp"]), reverse=True)

        return {
            "baseline": baseline.to_dict(),
            "scenario": scenario.to_dict(),
            "diff_table": diff_table,
            "perturbation": {
                "type": req.perturbation_type,
                "params": req.perturbation_params,
            },
        }
    except Exception as e:
        logger.exception("What-if run failed")
        raise HTTPException(500, str(e))


# ============================================================
# Scenario Classification endpoint (NEW)
# ============================================================
class ScenarioRequest(BaseModel):
    products: list[str]
    tool_groups: list[str]
    process_steps: list[str]
    feasibility_matrix: dict[str, dict[str, dict[str, bool]]] = Field(default_factory=dict)
    tc_matrix: dict[str, dict[str, dict[str, float]]] = Field(default_factory=dict)
    path_mix: dict[str, dict[str, float]] | None = None
    backup_tools: dict[str, list[str]] = Field(default_factory=dict)


@app.post("/scenario/classify")
def scenario_classify(req: ScenarioRequest):
    """
    基于产能规则文档的情景分类判断
    
    返回情景类型和推荐的求解算法
    """
    try:
        inp = ScenarioInput(
            products=req.products,
            tool_groups=req.tool_groups,
            process_steps=req.process_steps,
            feasibility_matrix=req.feasibility_matrix,
            tc_matrix=req.tc_matrix,
            path_mix=req.path_mix,
            backup_tools=req.backup_tools,
        )
        result = classify_scenario(inp)
        return {
            "scenario_type": result.scenario_type.value,
            "description": result.description,
            "algorithm": result.algorithm,
            "multi_product": result.multi_product,
            "multi_machine": result.multi_machine,
            "same_path": result.same_path,
            "has_backup": result.has_backup,
            "recommended_solver": result.recommended_solver,
            "constraints": result.constraints,
        }
    except Exception as e:
        logger.exception("Scenario classify failed")
        raise HTTPException(500, str(e))


# ============================================================
# Standard Capacity Calculation endpoint (NEW)
# ============================================================
class StandardCapacityRequest(BaseModel):
    product_id: str
    tool_id: str
    tc_by_process: dict[str, float]  # {process_id: hours/wafer}
    batch_size: float = 25.0
    uptime: float = 0.90
    loss_time: float = 0.05
    days_in_month: float = 30.0
    ttl_tc_override: float | None = None


@app.post("/capacity/standard_compute")
def capacity_standard_compute(req: StandardCapacityRequest):
    """
    标准产能计算（基于规则文档公式）
    
    公式: 产能 = 24hr / TTL_TC × batch_size × 30天 × (uptime - loss_time)
    
    适用: 情景1、全适场景
    """
    try:
        inp = StandardCapacityInput(
            product_id=req.product_id,
            tool_id=req.tool_id,
            tc_by_process=req.tc_by_process,
            batch_size=req.batch_size,
            uptime=req.uptime,
            loss_time=req.loss_time,
            days_in_month=req.days_in_month,
            ttl_tc_override=req.ttl_tc_override,
        )
        result = compute_standard_capacity(inp)
        return result.to_dict()
    except Exception as e:
        logger.exception("Standard capacity compute failed")
        raise HTTPException(500, str(e))


class TCMatrixCapacityRequest(BaseModel):
    tc_matrix: dict[str, dict[str, dict[str, float]]]  # {product: {tool: {process: hours}}}
    batch_sizes: dict[str, float] | None = None
    uptime: float = 0.90
    loss_time: float = 0.05
    days_in_month: float = 30.0


@app.post("/capacity/batch_compute")
def capacity_batch_compute(req: TCMatrixCapacityRequest):
    """
    从TC矩阵批量计算产能
    
    适用: 多产品多机台场景的标准产能计算
    """
    try:
        result = compute_capacity_from_tc_matrix(
            tc_matrix=req.tc_matrix,
            batch_sizes=req.batch_sizes,
            uptime=req.uptime,
            loss_time=req.loss_time,
            days_in_month=req.days_in_month,
        )
        return result.to_dict()
    except Exception as e:
        logger.exception("Batch capacity compute failed")
        raise HTTPException(500, str(e))


# ============================================================
# Allocation Model endpoint (NEW)
# ============================================================
class AllocationRequest(BaseModel):
    products: list[str]
    tools: list[str]
    processes: list[str]
    feasibility: dict[str, dict[str, dict[str, bool]]]  # {product: {tool: {process: bool}}}
    tc_matrix: dict[str, dict[str, dict[str, float]]]  # {product: {tool: {process: hours}}}
    available_hours: dict[str, float]  # {tool: hours_per_month}
    demand_target: dict[str, float] = Field(default_factory=dict)  # {product: wafers}
    backup_tools: dict[str, list[str]] = Field(default_factory=dict)  # {tool: [backups]}
    path_mix: dict[str, dict[str, float]] | None = None
    objective: str = "max_output"  # max_output | min_variance | max_balance | min_cycle_time
    solver: str = "cbc"
    time_limit_seconds: int = 120


@app.post("/allocation/optimize")
def allocation_optimize(req: AllocationRequest):
    """
    分配优化模型（情景2-4）
    
    算法: 窃举+约束+分配
    适用: 多机台/不同配置场景
    """
    if not ALLOCATION_AVAILABLE:
        return {
            "status": "unavailable",
            "error": "Pyomo not installed. Allocation optimization requires Pyomo + CBC solver.",
            "recommendation": "Install with: pip install pyomo",
            "alternative": "Use RCCP for basic capacity analysis or contact admin to install Pyomo."
        }
    try:
        inp = AllocationInput(
            products=req.products,
            tools=req.tools,
            processes=req.processes,
            feasibility=req.feasibility,
            tc_matrix=req.tc_matrix,
            available_hours=req.available_hours,
            demand_target=req.demand_target,
            objective=AllocationObjective(req.objective),
        )
        result = allocate(inp)
        return result.to_dict()
    except Exception as e:
        logger.exception("Allocation optimize failed")
        raise HTTPException(500, str(e))


# ============================================================
# Unified Capacity Analysis endpoint (智能路由)
# ============================================================
class UnifiedCapacityRequest(BaseModel):
    """
    统一产能分析入口
    
    自动判断情景类型，选择合适的计算方法
    """
    products: list[str]
    tool_groups: list[str]
    process_steps: list[str]
    feasibility_matrix: dict[str, dict[str, dict[str, bool]]] = Field(default_factory=dict)
    tc_matrix: dict[str, dict[str, dict[str, float]]] = Field(default_factory=dict)
    available_hours: dict[str, float]
    demand_plan: dict[str, float] = Field(default_factory=dict)
    path_mix: dict[str, dict[str, float]] | None = None
    backup_tools: dict[str, list[str]] = Field(default_factory=dict)
    batch_sizes: dict[str, float] | None = None
    uptime: float = 0.90
    loss_time: float = 0.05
    solver: str = "cbc"


@app.post("/capacity/unified_analyze")
def capacity_unified_analyze(req: UnifiedCapacityRequest):
    """
    智能产能分析：自动判断情景并选择算法
    
    流程:
    1. 情景分类判断
    2. 根据情景选择计算方法:
       - 情景1/全适: 标准产能计算
       - 情景2-4: 分配模型优化
    3. 返回结果和建议
    """
    try:
        # 1. 情景分类
        scenario_inp = ScenarioInput(
            products=req.products,
            tool_groups=req.tool_groups,
            process_steps=req.process_steps,
            feasibility_matrix=req.feasibility_matrix,
            tc_matrix=req.tc_matrix,
            path_mix=req.path_mix,
            backup_tools=req.backup_tools,
        )
        scenario_result = classify_scenario(scenario_inp)
        
        # 2. 根据情景选择算法
        if scenario_result.algorithm == "standard":
            # 情景1/全适: 使用标准产能计算或RCCP
            if req.tc_matrix and not req.demand_plan:
                # 只有TC矩阵，计算产能上限
                capacity_result = compute_capacity_from_tc_matrix(
                    tc_matrix=req.tc_matrix,
                    batch_sizes=req.batch_sizes,
                    uptime=req.uptime,
                    loss_time=req.loss_time,
                )
                return {
                    "scenario": scenario_result.scenario_type.value,
                    "algorithm": "standard_capacity",
                    "description": scenario_result.description,
                    "capacity_result": capacity_result.to_dict(),
                    "recommendation": "产能已计算，可满足需求",
                }
            else:
                # 有需求计划，运行RCCP
                # 构建capacity_matrix (从TC矩阵聚合)
                cm_dict = {}
                for p in req.products:
                    cm_dict[p] = {}
                    for t in req.tool_groups:
                        if p in req.tc_matrix and t in req.tc_matrix[p]:
                            # 聚合所有制程的TC
                            total_tc = sum(req.tc_matrix[p][t].values())
                            cm_dict[p][t] = total_tc
                
                cm_df = pd.DataFrame(cm_dict).T.fillna(0.0)
                rccp_inp = RCCPInput(
                    demand_plan=req.demand_plan,
                    capacity_matrix=cm_df,
                    available_hours=req.available_hours,
                )
                rccp_result = compute_rccp(rccp_inp)
                
                return {
                    "scenario": scenario_result.scenario_type.value,
                    "algorithm": "rccp",
                    "description": scenario_result.description,
                    "rccp_result": rccp_result.to_dict(),
                    "recommendation": "RCCP分析完成，检查瓶颈机台",
                }
        
        else:
            # 情景2-4: 使用分配模型
            if ALLOCATION_AVAILABLE:
                allocation_inp = AllocationInput(
                    products=req.products,
                    tools=req.tool_groups,
                    processes=req.process_steps,
                    feasibility=req.feasibility_matrix,
                    tc_matrix=req.tc_matrix,
                    available_hours=req.available_hours,
                    demand_target=req.demand_plan,
                    backup_tools=req.backup_tools,
                    path_mix=req.path_mix,
                    objective=AllocationObjective.MAX_OUTPUT,
                    solver=req.solver,
                )
                allocation_result = allocate(allocation_inp)
                
                return {
                    "scenario": scenario_result.scenario_type.value,
                    "algorithm": "allocation_model",
                    "description": scenario_result.description,
                    "allocation_result": allocation_result.to_dict(),
                    "recommendation": "分配优化完成，检查瓶颈机台和未满足需求",
                }
            else:
                # Pyomo不可用，降级为RCCP
                cm_dict = {}
                for p in req.products:
                    cm_dict[p] = {}
                    for t in req.tool_groups:
                        if p in req.tc_matrix and t in req.tc_matrix[p]:
                            total_tc = sum(req.tc_matrix[p][t].values())
                            cm_dict[p][t] = total_tc
                
                cm_df = pd.DataFrame(cm_dict).T.fillna(0.0)
                rccp_inp = RCCPInput(
                    demand_plan=req.demand_plan,
                    capacity_matrix=cm_df,
                    available_hours=req.available_hours,
                )
                rccp_result = compute_rccp(rccp_inp)
                
                return {
                    "scenario": scenario_result.scenario_type.value,
                    "algorithm": "rccp_fallback",
                    "description": scenario_result.description,
                    "rccp_result": rccp_result.to_dict(),
                    "note": "Allocation model requires Pyomo. Using RCCP fallback.",
                    "recommendation": "RCCP分析完成（分配优化模块未安装Pyomo）",
                }
    
    except Exception as e:
        logger.exception("Unified capacity analyze failed")
        raise HTTPException(500, str(e))


# ============================================================
# Production Plan Generator endpoint (生产计划输出)
# ============================================================
class ProductionPlanRequest(BaseModel):
    """
    生产计划生成请求
    
    输入需求计划和产能数据，输出结构化生产计划
    """
    demand_plan: dict[str, float] = Field(..., description="{product_id: wafer_count}")
    capacity_matrix: dict[str, dict[str, float]] = Field(
        ..., description="{product_id: {tool_group_id: hours_per_wafer}}"
    )
    available_hours: dict[str, float] = Field(..., description="{tool_group_id: available_hours}")
    routes: list[dict[str, Any]] | None = Field(default=None, description="工艺路线明细")
    wip_lot_detail: list[dict[str, Any]] | None = Field(default=None, description="WIP Lot 明细")
    enable_wip_adjustment: bool = Field(default=False, description="是否启用WIP校正")
    unit_profit: dict[str, float] | None = Field(default=None, description="{product_id: profit/wafer}")
    priority: dict[str, int] | None = Field(default=None, description="{product_id: priority}")
    demand_min: dict[str, float] | None = Field(default=None, description="{product_id: min_wafers} 最低需求约束")
    demand_max: dict[str, float] | None = Field(default=None, description="{product_id: max_wafers} 产量上限")
    lp_enabled: bool = Field(default=True, description="是否启用LP优化")
    lp_objective: str = Field(default="max_profit", description="LP优化目标")
    lp_solver: str = Field(default="cbc", description="LP求解器")
    lp_time_limit: int = Field(default=60, description="LP求解时限(秒)")
    time_window: str = Field(default="weekly", description="weekly | monthly")
    objective: str = Field(default="max_profit", description="max_profit | max_output | balance")


@app.post("/plan/generate")
def plan_generate(req: ProductionPlanRequest):
    """
    生成生产计划
    
    整合 RCCP + 瓶颈分析，输出:
    1. 可行性判断和评分
    2. 周产量计划（目标/可达/缺口）
    3. 机台分配方案
    4. 瓶颈分析和建议措施
    5. 总利润预估
    """
    try:
        cm_df = pd.DataFrame(req.capacity_matrix).T.fillna(0.0)
        routes_df = pd.DataFrame(req.routes) if req.routes else None
        wip_df = pd.DataFrame(req.wip_lot_detail) if req.wip_lot_detail else None
        
        inp = ProductionPlanInput(
            demand_plan=req.demand_plan,
            capacity_matrix=cm_df,
            available_hours=req.available_hours,
            routes=routes_df,
            wip_lot_detail=wip_df,
            enable_wip_adjustment=req.enable_wip_adjustment,
            unit_profit=req.unit_profit or {},
            priority=req.priority or {},
            demand_min=req.demand_min or {},
            demand_max=req.demand_max or {},
            lp_enabled=req.lp_enabled,
            lp_objective=req.lp_objective,
            lp_solver=req.lp_solver,
            lp_time_limit=req.lp_time_limit,
            time_window=req.time_window,
            objective=req.objective,
        )
        
        result = generate_production_plan(inp)
        return result.to_dict()
    except Exception as e:
        logger.exception("Production plan generate failed")
        raise HTTPException(500, str(e))


class ProductionPlanFromDatasetRequest(BaseModel):
    """
    从数据集生成生产计划
    
    使用已导入的数据集自动生成计划
    """
    dataset_id: str | None = Field(default=None, description="数据集ID，默认使用内置样例")
    time_window: str | None = Field(default=None, description="时间窗口，默认使用第一个可用窗口")
    objective: str = Field(default="max_profit", description="max_profit | max_output | balance")
    wip_lot_detail: list[dict[str, Any]] | None = Field(default=None, description="WIP Lot 明细")
    enable_wip_adjustment: bool = Field(default=False, description="是否启用WIP校正")
    lp_enabled: bool = Field(default=True, description="是否启用LP优化")
    lp_objective: str = Field(default="max_profit", description="LP优化目标")
    lp_solver: str = Field(default="cbc", description="LP求解器")
    lp_time_limit: int = Field(default=60, description="LP求解时限(秒)")
    demand_min: dict[str, float] | None = Field(default=None, description="产品最小需求约束")
    demand_max: dict[str, float] | None = Field(default=None, description="产品最大需求约束")


@app.post("/plan/generate_from_dataset")
def plan_generate_from_dataset(req: ProductionPlanFromDatasetRequest):
    """
    从数据集生成生产计划
    
    自动获取数据集中的需求计划和产能数据，生成计划
    """
    try:
        # 获取数据集
        bundle = DATASET_REGISTRY.get_dataset(req.dataset_id)
        
        # 构建产能矩阵
        capacity_matrix = build_capacity_matrix(bundle.routes.copy())
        
        # 获取可用小时（从最新OEE聚合）
        latest_date = pd.to_datetime(bundle.oee["fact_date"]).max()
        latest_oee = bundle.oee.copy()
        latest_oee["fact_date"] = pd.to_datetime(latest_oee["fact_date"])
        latest_oee = latest_oee[latest_oee["fact_date"] == latest_date]
        
        # 计算每周可用小时 (假设一周7天)
        weekly_hours = {}
        for _, row in latest_oee.iterrows():
            tg_id = row["tool_group_id"]
            daily_hours = row.get("available_hours", 0) or 0
            # 从tool_groups获取机台数，计算周产能
            tg_info = bundle.tool_groups[bundle.tool_groups["tool_group_id"] == tg_id]
            if not tg_info.empty:
                n_machines = int(tg_info.iloc[0].get("n_machines", 1) or 1)
                # 周可用小时 = 机台数 × 24小时 × 7天 × 可用率
                weekly_hours[tg_id] = n_machines * 24 * 7 * float(row.get("availability", 0.85) or 0.85)
            else:
                weekly_hours[tg_id] = daily_hours * 7
        
        # 获取需求计划
        time_windows = sorted(bundle.demand["time_window"].astype(str).unique().tolist())
        selected_window = req.time_window or time_windows[0] if time_windows else "this_week"
        
        demand = bundle.demand.copy()
        demand = demand[demand["time_window"].astype(str) == selected_window]
        demand_plan = {
            str(row["product_id"]): float(row["wafer_count"])
            for _, row in demand.iterrows()
        }
        unit_profit = {
            str(row["product_id"]): float(row.get("unit_profit", 1) or 1)
            for _, row in demand.iterrows()
        }
        priority = {
            str(row["product_id"]): int(row.get("priority", 1) or 1)
            for _, row in demand.iterrows()
        }
        default_demand_max = {
            str(row["product_id"]): float(row.get("market_max", row["wafer_count"]) or row["wafer_count"])
            for _, row in demand.iterrows()
        } if "market_max" in demand.columns else {}
        demand_max = req.demand_max if req.demand_max is not None else default_demand_max
        demand_min = req.demand_min if req.demand_min is not None else {}
        
        # 生成计划
        inp = ProductionPlanInput(
            demand_plan=demand_plan,
            capacity_matrix=capacity_matrix,
            available_hours=weekly_hours,
            routes=bundle.routes.copy(),
            wip_lot_detail=pd.DataFrame(req.wip_lot_detail) if req.wip_lot_detail else None,
            enable_wip_adjustment=req.enable_wip_adjustment,
            unit_profit=unit_profit,
            priority=priority,
            demand_min=demand_min,
            demand_max=demand_max,
            lp_enabled=req.lp_enabled,
            lp_objective=req.lp_objective,
            lp_solver=req.lp_solver,
            lp_time_limit=req.lp_time_limit,
            time_window=selected_window,
            objective=req.objective,
        )
        
        result = generate_production_plan(inp)
        
        return {
            **result.to_dict(),
            "dataset_id": bundle.dataset_id,
            "time_window": selected_window,
            "n_demand_records": len(demand),
        }
    except Exception as e:
        logger.exception("Production plan from dataset failed")
        raise HTTPException(500, str(e))


# ============================================================
# Output Perspective APIs - 产出视角产能规划
# ============================================================

from engines.output_rccp import (
    OutputRCCPInput,
    OutputRCCPResult,
    run_output_rccp,
)
from engines.output_predictor import (
    predict_output_for_next_n_weeks,
)
from engines.input_planner_output import (
    InputPlannerOutputInput,
    compute_input_plan_from_output_targets,
    generate_input_schedule_summary,
)


class OutputRCCPRequest(BaseModel):
    """Output RCCP 请求 - 产出视角产能规划"""
    dataset_id: str | None = Field(default=None, description="数据集ID")
    output_target: dict[str, float] = Field(..., description="{product: target_wafers} 本周产出目标")
    output_target_week: str = Field(..., description="规划周（如 2026-W17）")
    wip_lot_detail: list[dict] | None = Field(default=None, description="WIP Lot 详情")
    new_input_plan: dict[str, float] | None = Field(default=None, description="本周新投入计划")
    available_hours: dict[str, float] = Field(default_factory=dict, description="可用产能")
    output_completion_threshold: float = Field(default=0.80, description="产出完成度阈值")


@app.post("/output/rccp/compute")
def output_rccp_compute(req: OutputRCCPRequest) -> dict[str, Any]:
    """
    Output RCCP 计算
    
    核心区别:
      Input 视角: demand = 新投入 × 总工时
      Output 视角: demand = WIP后续工序小时 + 新投入本周工序小时
    
    输出:
      - output_predictions: 产出预测
      - capacity_demand: 产能需求（含 WIP 占比）
      - input_recommendations: 投入建议
    """
    try:
        bundle = DATASET_REGISTRY.get_dataset(req.dataset_id)
        route_df = bundle.routes.copy()
        cycle_time_days = {p: 100.0 for p in bundle.products["product_id"].unique()}
        
        wip_df = pd.DataFrame(req.wip_lot_detail) if req.wip_lot_detail else pd.DataFrame()
        
        inp = OutputRCCPInput(
            output_target=req.output_target,
            output_target_week=req.output_target_week,
            wip_lot_detail=wip_df,
            route=route_df,
            cycle_time_days=cycle_time_days,
            available_hours=req.available_hours,
            new_input_plan=req.new_input_plan,
            output_completion_threshold=req.output_completion_threshold,
        )
        
        result = run_output_rccp(inp)
        return result.to_dict()
        
    except Exception as e:
        logger.exception("Output RCCP compute failed")
        raise HTTPException(500, str(e))


class OutputPredictionRequest(BaseModel):
    """产出预测请求"""
    dataset_id: str | None = Field(default=None, description="数据集ID")
    current_week: str = Field(..., description="当前周")
    wip_lot_detail: list[dict] | None = Field(default=None, description="WIP Lot 详情")
    n_weeks: int = Field(default=8, description="预测周数")


@app.post("/output/prediction/weekly")
def output_prediction_weekly(req: OutputPredictionRequest) -> dict[str, Any]:
    """产出预测（未来 N 周）- 基于 WIP 位置预测各周产出量"""
    try:
        bundle = DATASET_REGISTRY.get_dataset(req.dataset_id)
        route_df = bundle.routes.copy()
        cycle_time_days = {p: 100.0 for p in bundle.products["product_id"].unique()}
        
        wip_df = pd.DataFrame(req.wip_lot_detail) if req.wip_lot_detail else pd.DataFrame()
        
        result = predict_output_for_next_n_weeks(
            wip_df, route_df, cycle_time_days, req.current_week, req.n_weeks
        )
        return result.to_dict()
        
    except Exception as e:
        logger.exception("Output prediction failed")
        raise HTTPException(500, str(e))


class InputPlanRequest(BaseModel):
    """投入计划请求"""
    dataset_id: str | None = Field(default=None, description="数据集ID")
    output_targets: dict[str, dict[str, float]] = Field(..., description="{week: {product: target}}")
    wip_predictions: dict[str, dict[str, float]] | None = Field(default=None)
    current_week: str = Field(..., description="当前周")
    planning_weeks: int = Field(default=12)


@app.post("/input/plan")
def input_plan(req: InputPlanRequest) -> dict[str, Any]:
    """投入计划（从产出目标反推）- 投入时间 = 产出时间 - CT"""
    try:
        bundle = DATASET_REGISTRY.get_dataset(req.dataset_id)
        cycle_time_days = {p: 100.0 for p in bundle.products["product_id"].unique()}
        
        inp = InputPlannerOutputInput(
            output_targets=req.output_targets,
            wip_output_predictions=req.wip_predictions or {},
            cycle_time_days=cycle_time_days,
            current_week=req.current_week,
            planning_weeks=req.planning_weeks,
        )
        
        result = compute_input_plan_from_output_targets(inp)
        summary = generate_input_schedule_summary(result)
        return {**result.to_dict(), "summary": summary}
        
    except Exception as e:
        logger.exception("Input plan failed")
        raise HTTPException(500, str(e))
