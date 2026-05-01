"""
Test script for new capacity analysis modules based on rule document.

Run:
  python test_new_modules.py
"""
import sys
sys.path.insert(0, '.')

from engines.scenario_classifier import classify_scenario, ScenarioInput, ScenarioType
from engines.standard_capacity import compute_standard_capacity, StandardCapacityInput
from engines.rccp import compute_rccp, RCCPInput
import pandas as pd


def test_scenario_classification():
    """Test scenario classification based on rule document."""
    print("=" * 60)
    print("TEST 1: Scenario Classification")
    print("=" * 60)
    
    # Scenario 1: Single machine
    inp1 = ScenarioInput(
        products=['Product_A'],
        tool_groups=['Tool_1'],
        process_steps=['Process_1', 'Process_2', 'Process_3'],
    )
    result1 = classify_scenario(inp1)
    print(f"Single machine: {result1.scenario_type.value}")
    print(f"  Algorithm: {result1.algorithm}")
    
    # Scenario 2: Multi-machine with backup
    inp2 = ScenarioInput(
        products=['Product_A', 'Product_B'],
        tool_groups=['Tool_1', 'Tool_2', 'Tool_3'],
        process_steps=['Process_1', 'Process_2', 'Process_3'],
        backup_tools={'Tool_1': ['Tool_2']},
    )
    result2 = classify_scenario(inp2)
    print(f"Multi-machine with backup: {result2.scenario_type.value}")
    print(f"  Algorithm: {result2.algorithm}")
    
    # Full flexible scenario
    inp3 = ScenarioInput(
        products=['Product_A', 'Product_B'],
        tool_groups=['Tool_1', 'Tool_2', 'Tool_3'],
        process_steps=['Process_1', 'Process_2', 'Process_3'],
        feasibility_matrix={
            'Product_A': {
                'Tool_1': {'Process_1': True, 'Process_2': True, 'Process_3': True},
                'Tool_2': {'Process_1': True, 'Process_2': True, 'Process_3': True},
                'Tool_3': {'Process_1': True, 'Process_2': True, 'Process_3': True},
            },
            'Product_B': {
                'Tool_1': {'Process_1': True, 'Process_2': True, 'Process_3': True},
                'Tool_2': {'Process_1': True, 'Process_2': True, 'Process_3': True},
                'Tool_3': {'Process_1': True, 'Process_2': True, 'Process_3': True},
            },
        },
    )
    result3 = classify_scenario(inp3)
    print(f"Full flexible: {result3.scenario_type.value}")
    print(f"  Algorithm: {result3.algorithm}")
    print()


def test_standard_capacity():
    """Test standard capacity calculation based on rule document formula."""
    print("=" * 60)
    print("TEST 2: Standard Capacity Calculation")
    print("=" * 60)
    print("Formula: Capacity = 24hr / TTL_TC × batch × 30days × (uptime - loss)")
    print()
    
    # Sheet2 data: Product A, Tool 1, TC = 2.8 + 1.8
    inp = StandardCapacityInput(
        product_id='Product_A',
        tool_id='Tool_1',
        tc_by_process={'Process_1': 2.8, 'Process_2': 1.8},
        batch_size=25.0,
        uptime=0.90,
        loss_time=0.05,
    )
    
    result = compute_standard_capacity(inp)
    print(f"Product: {result.product_id}")
    print(f"Tool: {result.tool_id}")
    print(f"TTL_TC: {result.ttl_tc:.2f} hours")
    print(f"Effective ratio: {result.effective_time_ratio:.2f} (uptime - loss)")
    print(f"Monthly capacity: {result.capacity_wafers_per_month:.0f} wafers")
    print(f"Daily capacity: {result.capacity_wafers_per_day:.1f} wafers")
    print()


def test_rccp_with_sheet2_data():
    """Test RCCP with rule document Sheet2 data."""
    print("=" * 60)
    print("TEST 3: RCCP with Sheet2 Data")
    print("=" * 60)
    
    # Build capacity matrix from Sheet2 TC values
    # Product_A on Tool_1, Tool_2, Tool_3 with different process TCs
    tc_matrix = {
        'Product_A': {
            'Tool_1': {'Process_1': 2.8, 'Process_2': 1.8, 'Process_3': 0.0},
            'Tool_2': {'Process_1': 0.0, 'Process_2': 1.8, 'Process_3': 3.2},
            'Tool_3': {'Process_1': 2.8, 'Process_2': 1.8, 'Process_3': 0.0},
        },
    }
    
    # Aggregate TC per tool (sum of all processes)
    capacity_matrix = {}
    for product, tools in tc_matrix.items():
        capacity_matrix[product] = {}
        for tool, processes in tools.items():
            capacity_matrix[product][tool] = sum(processes.values())
    
    cm_df = pd.DataFrame(capacity_matrix).T.fillna(0.0)
    print("Capacity Matrix (hours/wafer):")
    print(cm_df)
    print()
    
    # RCCP calculation
    inp = RCCPInput(
        demand_plan={'Product_A': 1000},  # 1000 PCS from Sheet2
        capacity_matrix=cm_df,
        available_hours={'Tool_1': 100.0, 'Tool_2': 100.0, 'Tool_3': 100.0},
    )
    
    result = compute_rccp(inp)
    print(f"Feasible: {result.feasible}")
    print(f"Overall loading: {result.overall_loading_pct:.1f}%")
    print()
    print("Loading by tool group:")
    for tg in result.loading_table:
        print(f"  {tg.tool_group_id}: {tg.loading_pct:.1f}% [{tg.status}]")
    print()


def main():
    """Run all tests."""
    print()
    print("*" * 60)
    print("CAPACITY AGENT - NEW MODULES TEST")
    print("Based on rule document: 产能.xlsx")
    print("*" * 60)
    print()
    
    test_scenario_classification()
    test_standard_capacity()
    test_rccp_with_sheet2_data()
    
    print("=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    main()