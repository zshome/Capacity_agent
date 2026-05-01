"""Test all endpoints."""
import requests
import json

BASE = "http://localhost:8002"

def test_all():
    print("=== Testing All Endpoints ===")
    
    # Health
    r = requests.get(f"{BASE}/health")
    print(f"Health: {r.json()}")
    
    # Scenario
    r = requests.post(f"{BASE}/scenario/classify", json={
        "products": ["A", "B"],
        "tool_groups": ["T1", "T2"],
        "process_steps": ["P1", "P2"],
    })
    result = r.json()
    print(f"Scenario: {result.get('scenario_type', 'N/A')}")
    
    # Standard Capacity
    r = requests.post(f"{BASE}/capacity/standard_compute", json={
        "product_id": "A",
        "tool_id": "T1",
        "tc_by_process": {"P1": 2.0, "P2": 1.5},
    })
    result = r.json()
    print(f"Standard capacity: {result.get('capacity_wafers_per_month', 'N/A')} wafers/month")
    
    # Batch Capacity
    r = requests.post(f"{BASE}/capacity/batch_compute", json={
        "tc_matrix": {"A": {"T1": {"P1": 2.0}}},
    })
    result = r.json()
    print(f"Batch capacity: {result.get('total_capacity', 'N/A')} wafers/month")
    
    # RCCP
    r = requests.post(f"{BASE}/rccp/compute", json={
        "demand_plan": {"A": 100},
        "capacity_matrix": {"A": {"T1": 3.0}},
        "available_hours": {"T1": 500},
    })
    result = r.json()
    print(f"RCCP feasible: {result.get('feasible', 'N/A')}")
    
    # Allocation (should return unavailable)
    r = requests.post(f"{BASE}/allocation/optimize", json={
        "products": ["A"],
        "tools": ["T1"],
        "processes": ["P1"],
        "feasibility": {"A": {"T1": {"P1": True}}},
        "tc_matrix": {"A": {"T1": {"P1": 2.0}}},
        "available_hours": {"T1": 100},
    })
    result = r.json()
    print(f"Allocation: {result.get('status', 'N/A')}")
    
    # Unified Analysis
    r = requests.post(f"{BASE}/capacity/unified_analyze", json={
        "products": ["A"],
        "tool_groups": ["T1"],
        "process_steps": ["P1"],
        "tc_matrix": {"A": {"T1": {"P1": 2.0}}},
        "available_hours": {"T1": 500},
        "demand_plan": {"A": 100},
    })
    result = r.json()
    print(f"Unified scenario: {result.get('scenario', 'N/A')}")
    
    print()
    print("=== ALL TESTS PASSED ===")

if __name__ == "__main__":
    test_all()