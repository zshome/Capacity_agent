"""Check LP constraints."""
import requests
import json

BASE = "http://localhost:8002"

dataset_id = "sample"

# 获取数据
r1 = requests.get(f"{BASE}/data/tool_group_status?dataset_id={dataset_id}")
status = r1.json()
available_hours = {t["tool_group_id"]: t.get("available_hours", 0) for t in status.get("tool_groups", [])}

r2 = requests.post(f"{BASE}/data/capacity_matrix", json={"dataset_id": dataset_id})
matrix = r2.json()
cm = matrix.get("capacity_matrix", {})

r3 = requests.get(f"{BASE}/data/demand_plan?dataset_id={dataset_id}&time_window=this_week")
demand = r3.json()
records = demand.get("records", [])

# 计算 contract_min 总需求小时数
contract_min = {row["product_id"]: row.get("contract_min", 0) for row in records}

# 计算每个机台需要的最小小时数
min_hours_needed = {}
for tg in available_hours.keys():
    total_min_hours = 0
    for product, min_qty in contract_min.items():
        if product in cm and tg in cm[product]:
            tc = cm[product][tg]
            total_min_hours += min_qty * tc
    min_hours_needed[tg] = total_min_hours

# 找出产能不足的机台
overload = []
for tg, min_h in min_hours_needed.items():
    avail = available_hours[tg]
    if min_h > avail and min_h > 0:
        overload.append((tg, min_h, avail, min_h/avail*100))

print(f"Tool groups with contract_min > capacity: {len(overload)}")
if overload:
    print("Overloaded tool groups:")
    for tg, min_h, avail, pct in overload[:10]:
        print(f"  {tg}: needs {min_h:.1f}h, has {avail:.1f}h ({pct:.1f}%)")
else:
    print("All tool groups have sufficient capacity for contract_min")
    
# 测试 LP with contract_min
payload = {
    "products": list(cm.keys()),
    "tool_groups": list(available_hours.keys()),
    "capacity_matrix": cm,
    "available_hours": available_hours,
    "demand_min": contract_min,
    "demand_max": {row["product_id"]: row.get("market_max", row["wafer_count"]) for row in records},
    "demand_target": demand.get("demand_plan", {}),
    "objective": "max_profit",
}

r = requests.post(f"{BASE}/lp/optimize", json=payload)
result = r.json()
print(f"\nLP with contract_min: {result['status']}")
print(f"Objective: {result['objective_value']}")