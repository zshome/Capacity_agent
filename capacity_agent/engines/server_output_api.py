# Output Perspective API endpoints for server.py
# Add this to the end of engines/server.py

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
        bundle = DATASET_REGISTRY.get_dataset(None)
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
    current_week: str = Field(..., description="当前周")
    wip_lot_detail: list[dict] | None = Field(default=None, description="WIP Lot 详情")
    n_weeks: int = Field(default=8, description="预测周数")


@app.post("/output/prediction/weekly")
def output_prediction_weekly(req: OutputPredictionRequest) -> dict[str, Any]:
    """产出预测（未来 N 周）- 基于 WIP 位置预测各周产出量"""
    try:
        bundle = DATASET_REGISTRY.get_dataset(None)
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
    output_targets: dict[str, dict[str, float]] = Field(..., description="{week: {product: target}}")
    wip_predictions: dict[str, dict[str, float]] | None = Field(default=None)
    current_week: str = Field(..., description="当前周")
    planning_weeks: int = Field(default=12)


@app.post("/input/plan")
def input_plan(req: InputPlanRequest) -> dict[str, Any]:
    """投入计划（从产出目标反推）- 投入时间 = 产出时间 - CT"""
    try:
        bundle = DATASET_REGISTRY.get_dataset(None)
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