"""
Production scenario test based on rule document Sheet2 data.
"""
import requests

BASE_URL = "http://localhost:8001"

def main():
    print("=== Production Scenario Test ===")
    print()
    
    # Sheet2 realistic data from rule document
    print("Scenario: Sheet2 Product A with process constraints")
    print()
    
    # Step 1: Classify scenario with feasibility matrix
    feasibility = {
        "Product_A": {
            "Tool_1": {"Process_1": True, "Process_2": True, "Process_3": False},
            "Tool_2": {"Process_1": False, "Process_2": True, "Process_3": True},
            "Tool_3": {"Process_1": True, "Process_2": True, "Process_3": False},
        }
    }
    
    tc_matrix = {
        "Product_A": {
            "Tool_1": {"Process_1": 2.8, "Process_2": 1.8, "Process_3": 0.0},
            "Tool_2": {"Process_1": 0.0, "Process_2": 1.8, "Process_3": 3.2},
            "Tool_3": {"Process_1": 2.8, "Process_2": 1.8, "Process_3": 0.0},
        }
    }
    
    payload = {
        "products": ["Product_A"],
        "tool_groups": ["Tool_1", "Tool_2", "Tool_3"],
        "process_steps": ["Process_1", "Process_2", "Process_3"],
        "feasibility_matrix": feasibility,
        "tc_matrix": tc_matrix,
    }
    
    resp = requests.post(f"{BASE_URL}/scenario/classify", json=payload)
    result = resp.json()
    print(f"Scenario: {result['scenario_type']}")
    print(f"Algorithm: {result['algorithm']}")
    print()
    
    # Step 2: Calculate tool capacities
    print("=== Tool Capacity (Standard Formula) ===")
    for tool in ["Tool_1", "Tool_2", "Tool_3"]:
        total_tc = sum(tc_matrix["Product_A"][tool].values())
        if total_tc > 0:
            payload = {
                "product_id": "Product_A",
                "tool_id": tool,
                "tc_by_process": tc_matrix["Product_A"][tool],
                "batch_size": 25.0,
                "uptime": 0.90,
                "loss_time": 0.05
            }
            resp = requests.post(f"{BASE_URL}/capacity/standard_compute", json=payload)
            result = resp.json()
            print(f"{tool}: TTL_TC={result['ttl_tc']:.1f}h, Capacity={result['capacity_wafers_per_month']:.0f} wafers/month")
    print()
    
    # Step 3: Feasibility-constrained path
    print("=== Best Process Assignment ===")
    for process in ["Process_1", "Process_2", "Process_3"]:
        feasible_tools = [t for t in ["Tool_1", "Tool_2", "Tool_3"] if feasibility["Product_A"][t].get(process, False)]
        if feasible_tools:
            best_tool = min(feasible_tools, key=lambda t: tc_matrix["Product_A"][t].get(process, 999))
            best_tc = tc_matrix["Product_A"][best_tool].get(process, 0)
            print(f"{process}: Best tool={best_tool}, TC={best_tc:.1f}h")
        else:
            print(f"{process}: No feasible tool!")
    print()
    
    # Step 4: Unified analysis
    print("=== Unified Capacity Analysis ===")
    payload = {
        "products": ["Product_A"],
        "tool_groups": ["Tool_1", "Tool_2", "Tool_3"],
        "process_steps": ["Process_1", "Process_2", "Process_3"],
        "feasibility_matrix": feasibility,
        "tc_matrix": tc_matrix,
        "available_hours": {"Tool_1": 672, "Tool_2": 672, "Tool_3": 672},  # Weekly hours
        "demand_plan": {"Product_A": 1000}
    }
    resp = requests.post(f"{BASE_URL}/capacity/unified_analyze", json=payload)
    result = resp.json()
    print(f"Scenario: {result['scenario']}")
    print(f"Algorithm: {result['algorithm']}")
    if "rccp_result" in result:
        rccp = result["rccp_result"]
        print(f"Feasible: {rccp['feasible']}")
        print(f"Overall loading: {rccp['overall_loading_pct']:.1f}%")
        print(f"Recommendation: {result.get('recommendation', 'N/A')}")

if __name__ == "__main__":
    main()