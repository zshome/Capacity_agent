"""
Test new API endpoints for Capacity Agent.

Run after starting the server:
  uvicorn engines.server:app --host 0.0.0.0 --port 8001
  
Then:
  python test_api.py
"""
import requests
import json

BASE_URL = 'http://localhost:8001'

def test_health():
    """Test health endpoint."""
    print("=== Testing Health Endpoint ===")
    resp = requests.get(f"{BASE_URL}/health")
    print(f"Health: {resp.json()}")
    print()

def test_scenario_classification():
    """Test scenario classification endpoint."""
    print("=== Test 1: Scenario Classification ===")
    payload = {
        "products": ["Product_A", "Product_B"],
        "tool_groups": ["Tool_1", "Tool_2", "Tool_3"],
        "process_steps": ["Process_1", "Process_2", "Process_3"],
        "feasibility_matrix": {},
        "tc_matrix": {},
        "backup_tools": {"Tool_1": ["Tool_2"]}
    }
    resp = requests.post(f"{BASE_URL}/scenario/classify", json=payload)
    print(f"Status: {resp.status_code}")
    result = resp.json()
    print(f"Scenario: {result.get('scenario_type', 'N/A')}")
    print(f"Description: {result.get('description', 'N/A')}")
    print(f"Algorithm: {result.get('algorithm', 'N/A')}")
    print(f"Solver: {result.get('recommended_solver', 'N/A')}")
    print()

def test_standard_capacity():
    """Test standard capacity calculation endpoint."""
    print("=== Test 2: Standard Capacity Calculation ===")
    payload = {
        "product_id": "Product_A",
        "tool_id": "Tool_1",
        "tc_by_process": {"Process_1": 2.8, "Process_2": 1.8, "Process_3": 3.2},
        "batch_size": 25.0,
        "uptime": 0.90,
        "loss_time": 0.05
    }
    resp = requests.post(f"{BASE_URL}/capacity/standard_compute", json=payload)
    print(f"Status: {resp.status_code}")
    result = resp.json()
    print(f"Monthly capacity: {result.get('capacity_wafers_per_month', 'N/A')} wafers")
    print(f"Daily capacity: {result.get('capacity_wafers_per_day', 'N/A')} wafers")
    print(f"TTL_TC: {result.get('ttl_tc', 'N/A')} hours")
    print()

def test_rccp():
    """Test RCCP compute endpoint."""
    print("=== Test 3: RCCP Compute ===")
    payload = {
        "demand_plan": {"Product_A": 1000, "Product_B": 500},
        "capacity_matrix": {
            "Product_A": {"Tool_1": 4.6, "Tool_2": 5.0, "Tool_3": 4.6},
            "Product_B": {"Tool_1": 3.5, "Tool_2": 4.0, "Tool_3": 3.5}
        },
        "available_hours": {"Tool_1": 500, "Tool_2": 600, "Tool_3": 400},
        "time_window": "weekly"
    }
    resp = requests.post(f"{BASE_URL}/rccp/compute", json=payload)
    print(f"Status: {resp.status_code}")
    result = resp.json()
    print(f"Feasible: {result.get('feasible', 'N/A')}")
    print(f"Overall loading: {result.get('overall_loading_pct', 'N/A')}%")
    print(f"Critical groups: {result.get('critical_groups', [])}")
    print()

def test_batch_capacity():
    """Test batch capacity compute endpoint."""
    print("=== Test 4: Batch Capacity Compute ===")
    payload = {
        "tc_matrix": {
            "Product_A": {
                "Tool_1": {"Process_1": 2.8, "Process_2": 1.8},
                "Tool_2": {"Process_2": 1.8, "Process_3": 3.2}
            },
            "Product_B": {
                "Tool_1": {"Process_1": 3.0, "Process_2": 2.0},
                "Tool_3": {"Process_1": 3.0, "Process_3": 2.5}
            }
        },
        "uptime": 0.90,
        "loss_time": 0.05
    }
    resp = requests.post(f"{BASE_URL}/capacity/batch_compute", json=payload)
    print(f"Status: {resp.status_code}")
    result = resp.json()
    print(f"Total capacity: {result.get('total_capacity', 'N/A')} wafers/month")
    print(f"By product: {result.get('capacity_by_product', {})}")
    print()

def test_unified_analyze():
    """Test unified capacity analysis endpoint."""
    print("=== Test 5: Unified Capacity Analysis ===")
    payload = {
        "products": ["Product_A", "Product_B"],
        "tool_groups": ["Tool_1", "Tool_2", "Tool_3"],
        "process_steps": ["Process_1", "Process_2", "Process_3"],
        "feasibility_matrix": {},
        "tc_matrix": {
            "Product_A": {
                "Tool_1": {"Process_1": 2.8, "Process_2": 1.8},
                "Tool_2": {"Process_2": 1.8, "Process_3": 3.2}
            },
            "Product_B": {
                "Tool_1": {"Process_1": 3.0, "Process_2": 2.0},
                "Tool_3": {"Process_1": 3.0, "Process_3": 2.5}
            }
        },
        "available_hours": {"Tool_1": 500, "Tool_2": 600, "Tool_3": 400},
        "demand_plan": {"Product_A": 1000, "Product_B": 500}
    }
    resp = requests.post(f"{BASE_URL}/capacity/unified_analyze", json=payload)
    print(f"Status: {resp.status_code}")
    result = resp.json()
    print(f"Scenario: {result.get('scenario', 'N/A')}")
    print(f"Algorithm: {result.get('algorithm', 'N/A')}")
    if "rccp_result" in result:
        rccp = result["rccp_result"]
        print(f"RCCP Feasible: {rccp.get('feasible', 'N/A')}")
        print(f"RCCP Loading: {rccp.get('overall_loading_pct', 'N/A')}%")
    print()

def main():
    """Run all tests."""
    print()
    print("*" * 60)
    print("CAPACITY ENGINE API TEST")
    print("*" * 60)
    print()
    
    test_health()
    test_scenario_classification()
    test_standard_capacity()
    test_rccp()
    test_batch_capacity()
    test_unified_analyze()
    
    print("=" * 60)
    print("ALL API TESTS PASSED")
    print("=" * 60)

if __name__ == "__main__":
    main()