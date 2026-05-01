"""Debug LP infeasible issue."""
import requests
import json

BASE = "http://localhost:8002"

# 获取完整数据
r1 = requests.get(f"{BASE}/data/demand_plan?time_window=this_week")
demand = r1.json()

r2 = requests.post(f"{BASE}/data/capacity_matrix")
matrix = r2.json().get("capacity_matrix", {})

r3 = requests.get(f"{BASE}/data/tool_group_status")
tools = r3.json().get("tool_groups", [])

available_hours = {t["tool_group_id"]: t.get("available_hours", 0) for t in tools}

print(f"Products: {len(demand['demand_plan'])}")
print(f"Tool groups: {len(available_hours)}")
print(f"Capacity matrix shape: {len(matrix)} products x {len(list(matrix.values())[0]) if matrix else 0} tool_groups")
print()

# 检查 capacity_matrix 和 available_hours 是否匹配
products = list(demand["demand_plan"].keys())
tool_groups = list(available_hours.keys())

# 确保 capacity_matrix 有所有产品和机台组
missing_products = [p for p in products if p not in matrix]
missing_tools = [t for t in tool_groups if t not in list(matrix.values())[0].keys() if matrix]

print(f"Products missing in matrix: {missing_products[:5]}")
print(f"Tools missing in matrix: {missing_tools[:5]}")
print()

# 构建完整 LP 请求（使用实际数据）
payload = {
    "products": products,
    "tool_groups": tool_groups,
    "capacity_matrix": matrix,
    "available_hours": available_hours,
    "demand_min": {row["product_id"]: row.get("contract_min", 0) for row in demand.get("records", [])},
    "demand_max": {row["product_id"]: row.get("market_max", row["wafer_count"]) for row in demand.get("records", [])},
    "demand_target": demand["demand_plan"],
    "unit_profit": {row["product_id"]: row.get("unit_profit", 1) for row in demand.get("records", [])},
    "objective": "max_profit",
}

print("Sending LP request with actual data...")
r = requests.post(f"{BASE}/lp/optimize", json=payload)
result = r.json()

print(f"LP Status: {result['status']}")
print(f"Objective: {result['objective_value']}")

if result["status"] == "infeasible":
    print(f"Error: {result.get('metadata', {}).get('error', 'unknown')}")
else:
    print(f"Binding constraints: {result.get('binding_constraints', [])}")
    # 显示前5个产品的计划
    plan = result.get("optimal_plan", {})
    print(f"Plan sample: {dict(list(plan.items())[:5])}")
    util = result.get("capacity_utilization", {})
    print(f"Utilization sample: {dict(list(util.items())[:5])}")
    
    # 检查是否有过载的机台
    overload = [t for t, u in util.items() if u > 100]
    if overload:
        print(f"Overloaded tools: {overload}")