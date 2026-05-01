"""Simulate frontend calls."""
import requests
import json

BASE = "http://localhost:8002"

dataset_id = "sample"

# 1. tool_group_status
r1 = requests.get(f"{BASE}/data/tool_group_status?dataset_id={dataset_id}")
status = r1.json()
available_hours = {t["tool_group_id"]: t.get("available_hours", 0) for t in status.get("tool_groups", [])}
print(f"Available hours: {len(available_hours)} tool groups")

# 2. capacity_matrix - POST with body
r2 = requests.post(f"{BASE}/data/capacity_matrix", json={"dataset_id": dataset_id})
matrix = r2.json()
cm = matrix.get("capacity_matrix", {})
print(f"Capacity matrix: {len(cm)} products x {len(list(cm.values())[0]) if cm else 0} tool_groups")
print(f"Sample product: {list(cm.keys())[:3]}")

# 3. demand_plan
r3 = requests.get(f"{BASE}/data/demand_plan?dataset_id={dataset_id}&time_window=this_week")
demand = r3.json()
demand_plan = demand.get("demand_plan", {})
print(f"Demand plan: {len(demand_plan)} products")
print()

# 检查产品和机台是否匹配
products_in_cm = set(cm.keys())
products_in_demand = set(demand_plan.keys())

missing = products_in_demand - products_in_cm
extra = products_in_cm - products_in_demand

print(f"Products in demand but not in matrix: {len(missing)}")
print(f"Products in matrix but not in demand: {len(extra)}")

# 尝试 LP 请求
if cm and demand_plan:
    payload = {
        "products": list(demand_plan.keys()),
        "tool_groups": list(available_hours.keys()),
        "capacity_matrix": cm,
        "available_hours": available_hours,
        "demand_min": {},
        "demand_max": demand_plan,  # 使用 demand_plan 作为上限
        "demand_target": demand_plan,
        "objective": "max_profit",
    }
    
    r = requests.post(f"{BASE}/lp/optimize", json=payload)
    result = r.json()
    print(f"LP Status: {result['status']}")
    print(f"Objective: {result['objective_value']}")