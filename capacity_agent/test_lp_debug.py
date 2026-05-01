"""Test LP optimizer."""
import requests
import json

BASE = "http://localhost:8002"

# Test 1: Simple feasible problem
print("=== Test 1: Simple LP ===")
r = requests.post(f"{BASE}/lp/optimize", json={
    "products": ["A", "B"],
    "tool_groups": ["T1", "T2"],
    "capacity_matrix": {"A": {"T1": 2.0, "T2": 1.5}, "B": {"T1": 3.0, "T2": 2.0}},
    "available_hours": {"T1": 100, "T2": 50},
    "demand_min": {"A": 0, "B": 0},
    "demand_max": {"A": 100, "B": 50},
    "demand_target": {"A": 50, "B": 30},
    "unit_profit": {"A": 100, "B": 80},
    "objective": "max_profit",
})
result = r.json()
print(f"Status: {result['status']}")
print(f"Objective: {result['objective_value']}")
print(f"Plan: {result['optimal_plan']}")
print()

# Test 2: Problem that might be infeasible
print("=== Test 2: Potential Infeasible ===")
r = requests.post(f"{BASE}/lp/optimize", json={
    "products": ["A"],
    "tool_groups": ["T1"],
    "capacity_matrix": {"A": {"T1": 5.0}},  # 5 hours per wafer
    "available_hours": {"T1": 100},  # 100 hours available = 20 wafers max
    "demand_min": {"A": 500},  # Requires 500 wafers = 2500 hours - INFEASIBLE
    "demand_max": {"A": 500},
    "objective": "max_profit",
})
result = r.json()
print(f"Status: {result['status']}")
print(f"Objective: {result['objective_value']}")
print(f"Plan: {result.get('optimal_plan', {})}")
print(f"Metadata: {result.get('metadata', {})}")
print()

# Test 3: Feasible with proper min values
print("=== Test 3: Feasible with demand_min=0 ===")
r = requests.post(f"{BASE}/lp/optimize", json={
    "products": ["A"],
    "tool_groups": ["T1"],
    "capacity_matrix": {"A": {"T1": 5.0}},
    "available_hours": {"T1": 100},
    "demand_min": {"A": 0},
    "demand_max": {"A": 500},
    "demand_target": {"A": 500},
    "unit_profit": {"A": 100},
    "objective": "max_profit",
})
result = r.json()
print(f"Status: {result['status']}")
print(f"Objective: {result['objective_value']}")
print(f"Plan: {result['optimal_plan']}")
print(f"Capacity util: {result['capacity_utilization']}")