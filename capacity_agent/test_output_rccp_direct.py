import sys
sys.path.insert(0, r'C:\Users\stark\AI_Project\Capacity_agent\Capacity_agent\capacity_agent')

import pandas as pd
from engines.output_rccp import run_output_rccp, OutputRCCPInput

# 测试数据
wip_data = pd.DataFrame([
    {"lot_id": "L001", "product_id": "28nm_DRAM_A", "current_step_seq": 230, "wafer_count": 25, "percent_complete": 85, "lot_status": "WAIT", "current_tool_group": "ETCH_01", "wait_hours_so_far": 12},
    {"lot_id": "L002", "product_id": "28nm_DRAM_A", "current_step_seq": 150, "wafer_count": 25, "percent_complete": 50, "lot_status": "RUN", "current_tool_group": "LITHO_01", "wait_hours_so_far": 0},
    {"lot_id": "L003", "product_id": "28nm_DRAM_B", "current_step_seq": 280, "wafer_count": 30, "percent_complete": 92, "lot_status": "WAIT", "current_tool_group": "CMP_01", "wait_hours_so_far": 5},
])

route_data = pd.DataFrame([
    {"product_id": "28nm_DRAM_A", "step_seq": 230, "tool_group_id": "ETCH_01", "run_time_hr": 0.5, "batch_size": 1},
    {"product_id": "28nm_DRAM_A", "step_seq": 235, "tool_group_id": "DEPO_01", "run_time_hr": 0.3, "batch_size": 1},
    {"product_id": "28nm_DRAM_A", "step_seq": 240, "tool_group_id": "CMP_01", "run_time_hr": 0.2, "batch_size": 1},
    {"product_id": "28nm_DRAM_B", "step_seq": 280, "tool_group_id": "CMP_01", "run_time_hr": 0.15, "batch_size": 1},
])

inp = OutputRCCPInput(
    output_target={"28nm_DRAM_A": 50, "28nm_DRAM_B": 30},
    output_target_week="2026-W17",
    wip_lot_detail=wip_data,
    route=route_data,
    cycle_time_days={"28nm_DRAM_A": 100, "28nm_DRAM_B": 100},
    available_hours={"ETCH_01": 500, "DEPO_01": 400, "CMP_01": 300, "LITHO_01": 600},
)

print("Running Output RCCP...")
result = run_output_rccp(inp)

print("\n=== Output RCCP Result ===")
print(f"Perspective: {result.metadata.get('perspective')}")
print(f"Predicted output: {result.total_predicted_output}")
print(f"Output gap: {result.output_gap}")
print(f"Feasible: {result.feasible}")
print(f"Overall loading: {result.overall_loading_pct:.1f}%")
print(f"WIP share: {result.overall_wip_share_pct:.1f}%")

print("\n=== Output Predictions ===")
for p in result.output_predictions:
    print(f"  {p.product_id}: {p.predicted_wafers} wafers (avg_pct={p.avg_percent_complete:.2f}, confidence={p.confidence})")

print("\n=== Capacity Demand ===")
for d in result.capacity_demand[:5]:
    print(f"  {d.tool_group_id}: WIP={d.wip_remaining_hours:.0f}h, Total={d.total_demand_hours:.0f}h, Loading={d.loading_pct:.1f}% [{d.status}]")

print("\n=== Input Recommendations ===")
for prod, rec in result.input_recommendations.items():
    if rec > 0:
        print(f"  {prod}: {rec:.0f} wafers")

print("\nTest completed successfully!")