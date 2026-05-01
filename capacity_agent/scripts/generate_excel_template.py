"""
Generate Excel template for capacity data import.
Run: python generate_excel_template.py
Output: capacity_import_template.xlsx
"""
import pandas as pd
from pathlib import Path

# ============================================================
# Sheet 1: route_master (产品工艺路线)
# ============================================================
route_master_data = [
    # 示例产品1: 28nm DRAM
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 1, "tool_group_id": "LITHO_01", "run_time_hr": 0.45, "batch_size": 1},
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 2, "tool_group_id": "ETCH_03", "run_time_hr": 0.32, "batch_size": 1},
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 3, "tool_group_id": "DEPO_02", "run_time_hr": 0.28, "batch_size": 25},
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 4, "tool_group_id": "LITHO_01", "run_time_hr": 0.45, "batch_size": 1},
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 5, "tool_group_id": "ETCH_05", "run_time_hr": 0.35, "batch_size": 1},
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 6, "tool_group_id": "CMP_01", "run_time_hr": 0.20, "batch_size": 1},
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 7, "tool_group_id": "WET_03", "run_time_hr": 0.15, "batch_size": 100},
    {"product_id": "28nm_DRAM_A", "path_id": "default", "step_seq": 8, "tool_group_id": "METRO_01", "run_time_hr": 0.10, "batch_size": 1},
    
    # 示例产品2: 64L NAND
    {"product_id": "64L_NAND_B", "path_id": "default", "step_seq": 1, "tool_group_id": "LITHO_02", "run_time_hr": 0.50, "batch_size": 1},
    {"product_id": "64L_NAND_B", "path_id": "default", "step_seq": 2, "tool_group_id": "ETCH_04", "run_time_hr": 0.40, "batch_size": 1},
    {"product_id": "64L_NAND_B", "path_id": "default", "step_seq": 3, "tool_group_id": "DEPO_03", "run_time_hr": 0.30, "batch_size": 25},
    {"product_id": "64L_NAND_B", "path_id": "default", "step_seq": 4, "tool_group_id": "CMP_02", "run_time_hr": 0.25, "batch_size": 1},
    
    # 示例产品3: 128L NAND
    {"product_id": "128L_NAND_A", "path_id": "default", "step_seq": 1, "tool_group_id": "LITHO_01", "run_time_hr": 0.55, "batch_size": 1},
    {"product_id": "128L_NAND_A", "path_id": "default", "step_seq": 2, "tool_group_id": "ETCH_03", "run_time_hr": 0.45, "batch_size": 1},
    {"product_id": "128L_NAND_A", "path_id": "default", "step_seq": 3, "tool_group_id": "DEPO_02", "run_time_hr": 0.35, "batch_size": 25},
]

route_master_df = pd.DataFrame(route_master_data)

# ============================================================
# Sheet 2: tool_groups (机台组信息)
# ============================================================
tool_groups_data = [
    {"tool_group_id": "LITHO_01", "tool_group_name": "光刻机台01", "area": "LITHO", "n_machines": 8, "nameplate_throughput_wph": 2.5},
    {"tool_group_id": "LITHO_02", "tool_group_name": "光刻机台02", "area": "LITHO", "n_machines": 6, "nameplate_throughput_wph": 2.8},
    {"tool_group_id": "ETCH_03", "tool_group_name": "刻蚀机台03", "area": "ETCH", "n_machines": 12, "nameplate_throughput_wph": 3.2},
    {"tool_group_id": "ETCH_04", "tool_group_name": "刻蚀机台04", "area": "ETCH", "n_machines": 10, "nameplate_throughput_wph": 3.5},
    {"tool_group_id": "ETCH_05", "tool_group_name": "刻蚀机台05", "area": "ETCH", "n_machines": 8, "nameplate_throughput_wph": 3.0},
    {"tool_group_id": "DEPO_02", "tool_group_name": "薄膜机台02", "area": "DEPO", "n_machines": 6, "nameplate_throughput_wph": 4.5},
    {"tool_group_id": "DEPO_03", "tool_group_name": "薄膜机台03", "area": "DEPO", "n_machines": 8, "nameplate_throughput_wph": 4.2},
    {"tool_group_id": "CMP_01", "tool_group_name": "抛光机台01", "area": "CMP", "n_machines": 4, "nameplate_throughput_wph": 2.8},
    {"tool_group_id": "CMP_02", "tool_group_name": "抛光机台02", "area": "CMP", "n_machines": 6, "nameplate_throughput_wph": 3.0},
    {"tool_group_id": "WET_03", "tool_group_name": "清洗机台03", "area": "WET", "n_machines": 8, "nameplate_throughput_wph": 5.0},
    {"tool_group_id": "METRO_01", "tool_group_name": "量测机台01", "area": "METRO", "n_machines": 4, "nameplate_throughput_wph": 1.8},
]

tool_groups_df = pd.DataFrame(tool_groups_data)

# ============================================================
# Sheet 3: oee (OEE 历史数据)
# ============================================================
import datetime

oee_data = []
today = datetime.date.today()

for days_back in range(28):  # 4周历史数据
    fact_date = today - datetime.timedelta(days=days_back)
    for tg in tool_groups_data:
        availability = 0.85 + (tg["tool_group_id"].split("_")[1] == "01") * 0.05  # 01号机台略高
        performance = 0.90 + 0.03 * (days_back % 7 == 0)  # 周末略高
        quality = 0.97 + 0.02  # 质量稳定
        
        oee_val = round(availability * performance * quality, 4)
        available_hours = round(tg["n_machines"] * 24 * availability, 2)
        
        oee_data.append({
            "fact_date": fact_date,
            "tool_group_id": tg["tool_group_id"],
            "availability": round(availability, 4),
            "performance": round(performance, 4),
            "quality": round(quality, 4),
            "oee": oee_val,
            "available_hours": available_hours,
        })

oee_df = pd.DataFrame(oee_data)

# ============================================================
# Sheet 4: demand_plan (需求计划)
# ============================================================
iso_year, iso_week, _ = today.isocalendar()

demand_data = [
    # 当前周
    {"time_window": f"{iso_year}-W{iso_week:02d}", "product_id": "28nm_DRAM_A", "wafer_count": 1000, "contract_min": 600, "market_max": 1500, "unit_profit": 150.0},
    {"time_window": f"{iso_year}-W{iso_week:02d}", "product_id": "64L_NAND_B", "wafer_count": 800, "contract_min": 400, "market_max": 1200, "unit_profit": 80.0},
    {"time_window": f"{iso_year}-W{iso_week:02d}", "product_id": "128L_NAND_A", "wafer_count": 500, "contract_min": 200, "market_max": 800, "unit_profit": 120.0},
    
    # 下周
    {"time_window": f"{iso_year}-W{(iso_week+1):02d}", "product_id": "28nm_DRAM_A", "wafer_count": 1200, "contract_min": 600, "market_max": 1500, "unit_profit": 150.0},
    {"time_window": f"{iso_year}-W{(iso_week+1):02d}", "product_id": "64L_NAND_B", "wafer_count": 900, "contract_min": 400, "market_max": 1200, "unit_profit": 80.0},
    {"time_window": f"{iso_year}-W{(iso_week+1):02d}", "product_id": "128L_NAND_A", "wafer_count": 600, "contract_min": 200, "market_max": 800, "unit_profit": 120.0},
]

demand_df = pd.DataFrame(demand_data)

# ============================================================
# 写入 Excel 文件
# ============================================================
output_dir = Path(__file__).parent / "docs"
output_dir.mkdir(exist_ok=True)
output_file = output_dir / "capacity_import_template.xlsx"

with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
    route_master_df.to_excel(writer, sheet_name="route_master", index=False)
    tool_groups_df.to_excel(writer, sheet_name="tool_groups", index=False)
    oee_df.to_excel(writer, sheet_name="oee", index=False)
    demand_df.to_excel(writer, sheet_name="demand_plan", index=False)

print(f"Excel template generated: {output_file}")
print(f"Sheets: {['route_master', 'tool_groups', 'oee', 'demand_plan']}")
print(f"Records:")
print(f"  - route_master: {len(route_master_df)} rows")
print(f"  - tool_groups: {len(tool_groups_df)} rows")
print(f"  - oee: {len(oee_df)} rows")
print(f"  - demand_plan: {len(demand_df)} rows")