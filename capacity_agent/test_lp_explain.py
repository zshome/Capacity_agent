"""Explain LP result."""
import requests
import json

BASE = "http://localhost:8002"

# 获取数据
r1 = requests.get(f"{BASE}/data/tool_group_status")
tools = r1.json().get("tool_groups", [])
available_hours = {t["tool_group_id"]: t.get("available_hours", 0) for t in tools}

r2 = requests.post(f"{BASE}/data/capacity_matrix", json={"dataset_id": "sample"})
cm = r2.json().get("capacity_matrix", {})

r3 = requests.get(f"{BASE}/data/demand_plan?time_window=this_week")
demand = r3.json()
records = demand.get("records", [])

# LP 请求
payload = {
    "products": list(cm.keys()),
    "tool_groups": list(available_hours.keys()),
    "capacity_matrix": cm,
    "available_hours": available_hours,
    "demand_min": {},
    "demand_max": {row["product_id"]: row.get("market_max", row["wafer_count"]) for row in records},
    "demand_target": demand.get("demand_plan", {}),
    "unit_profit": {row["product_id"]: row.get("unit_profit", 1) for row in records},
    "objective": "max_profit",
}

r = requests.post(f"{BASE}/lp/optimize", json=payload)
result = r.json()

print("=" * 60)
print("LP OPTIMIZER 结果解释")
print("=" * 60)
print()
print(f"状态: {result['status']}")
print(f"目标值 (总利润): {result['objective_value']:.2f}")
print()

# 显示前10个产品的分配
plan = result.get("optimal_plan", {})
profit_map = {row["product_id"]: row.get("unit_profit", 1) for row in records}

print("最优产量分配 (Top 10):")
print("-" * 60)
total_profit_check = 0
for p, qty in sorted(plan.items(), key=lambda x: -x[1])[:10]:
    profit = profit_map.get(p, 1)
    contribution = qty * profit
    total_profit_check += contribution
    print(f"  {p}: {qty:.1f} wafers @ ${profit:.2f}/wafer = ${contribution:.2f}")

print()
print(f"验证总利润: ${total_profit_check:.2f}")

# 显示利用率最高的机台
util = result.get("capacity_utilization", {})
print()
print("产能利用率 (Top 5 瓶颈):")
print("-" * 60)
for t, u in sorted(util.items(), key=lambda x: -x[1])[:5]:
    print(f"  {t}: {u:.1f}%")

# 紧约束
binding = result.get("binding_constraints", [])
print()
print(f"瓶颈机台 (binding constraints): {binding}")
print()
print("=" * 60)