"""
Company capacity-method readiness validation.

Run from repository root:
  PYTHONPATH=capacity_agent python3 capacity_agent/validate_company_method_readiness.py

The checks use deterministic simulation data to verify that the current project
can support R6-style wafer-start commitment review without overstating capacity.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engines.allocation_model import AllocationInput, allocate
from engines.data_service import DATASET_REGISTRY, generate_excel_template
from engines.rccp import RCCPInput, compute_rccp
from engines.scenario_classifier import ScenarioInput, classify_scenario


@dataclass
class ValidationCase:
    name: str
    purpose: str
    runner: Callable[[], dict[str, object]]


def assert_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected!r}, got {actual!r}")


def assert_close(actual: float, expected: float, tolerance: float, message: str) -> None:
    if abs(actual - expected) > tolerance:
        raise AssertionError(f"{message}: expected {expected}, got {actual}")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def validate_scenario_routing() -> dict[str, object]:
    same_path_with_backup = classify_scenario(
        ScenarioInput(
            products=["DRAM_A", "DRAM_B"],
            tool_groups=["LITHO_A", "LITHO_B"],
            process_steps=["PHOTO", "ETCH"],
            path_mix={"DRAM_A": {"MAIN": 1.0}, "DRAM_B": {"MAIN": 1.0}},
            backup_tools={"LITHO_A": ["LITHO_B"]},
        )
    )
    diff_path_with_backup = classify_scenario(
        ScenarioInput(
            products=["DRAM_A", "NAND_B"],
            tool_groups=["LITHO_A", "LITHO_B"],
            process_steps=["PHOTO", "ETCH"],
            feasibility_matrix={
                "DRAM_A": {
                    "LITHO_A": {"PHOTO": True, "ETCH": True},
                    "LITHO_B": {"PHOTO": True, "ETCH": False},
                },
                "NAND_B": {
                    "LITHO_A": {"PHOTO": False, "ETCH": True},
                    "LITHO_B": {"PHOTO": True, "ETCH": True},
                },
            },
            backup_tools={"LITHO_A": ["LITHO_B"]},
        )
    )

    assert_equal(same_path_with_backup.scenario_type.value, "scenario_3", "same path + backup should be scenario_3")
    assert_equal(same_path_with_backup.algorithm, "standard", "same path + backup should use standard capacity")
    assert_equal(diff_path_with_backup.scenario_type.value, "scenario_5", "different path + backup should be scenario_5")
    assert_equal(diff_path_with_backup.algorithm, "allocation_model", "different path + backup should use allocation")
    return {
        "same_path_backup": same_path_with_backup.scenario_type.value,
        "diff_path_backup": diff_path_with_backup.scenario_type.value,
        "diff_path_algorithm": diff_path_with_backup.algorithm,
    }


def validate_wip_aware_r6_rccp() -> dict[str, object]:
    capacity_matrix = pd.DataFrame({"LITHO": {"DRAM_A": 1.0}})
    base = compute_rccp(
        RCCPInput(
            demand_plan={"DRAM_A": 80},
            capacity_matrix=capacity_matrix,
            available_hours={"LITHO": 100},
            time_window="R6-2026-05",
        )
    )
    with_wip = compute_rccp(
        RCCPInput(
            demand_plan={"DRAM_A": 80},
            capacity_matrix=capacity_matrix,
            available_hours={"LITHO": 100},
            wip_remaining_hours={"LITHO": 30},
            time_window="R6-2026-05",
        )
    )

    assert_equal(base.feasible, True, "base R6 demand should be feasible without WIP")
    assert_equal(with_wip.feasible, False, "R6 demand should become infeasible after WIP load")
    assert_close(with_wip.loading_table[0].loading_pct, 110.0, 0.01, "WIP-aware loading should be 110%")
    return {
        "without_wip_feasible": base.feasible,
        "with_wip_feasible": with_wip.feasible,
        "with_wip_loading_pct": round(with_wip.loading_table[0].loading_pct, 2),
        "wip_hours": with_wip.metadata["wip_total_hours"],
    }


def validate_completed_wafer_allocation() -> dict[str, object]:
    result = allocate(
        AllocationInput(
            products=["DRAM_A"],
            tools=["TOOL_1"],
            processes=["PHOTO", "ETCH"],
            feasibility={"DRAM_A": {"TOOL_1": {"PHOTO": True, "ETCH": True}}},
            tc_matrix={"DRAM_A": {"TOOL_1": {"PHOTO": 1.0, "ETCH": 1.0}}},
            available_hours={"TOOL_1": 100.0},
            demand_target={"DRAM_A": 100.0},
        )
    )

    output = result.product_output["DRAM_A"]
    assert_close(output, 50.0, 0.1, "two-process same-tool output must not be over-counted")
    assert_close(result.tool_utilization["TOOL_1"], 100.0, 0.1, "tool should be fully loaded")
    assert_true(result.unmet_demand["DRAM_A"] >= 49.9, "unmet demand should be exposed for commitment review")
    return {
        "status": result.status,
        "completed_wafers": round(output, 1),
        "unmet_wafers": round(result.unmet_demand["DRAM_A"], 1),
        "tool_loading_pct": round(result.tool_utilization["TOOL_1"], 2),
        "decision_quality": result.metadata["decision_quality"],
    }


def validate_diff_path_backup_allocation() -> dict[str, object]:
    result = allocate(
        AllocationInput(
            products=["DRAM_A", "NAND_B"],
            tools=["LITHO_A", "LITHO_B", "ETCH_A"],
            processes=["PHOTO", "ETCH"],
            feasibility={
                "DRAM_A": {
                    "LITHO_A": {"PHOTO": True, "ETCH": False},
                    "LITHO_B": {"PHOTO": True, "ETCH": False},
                    "ETCH_A": {"PHOTO": False, "ETCH": True},
                },
                "NAND_B": {
                    "LITHO_A": {"PHOTO": False, "ETCH": False},
                    "LITHO_B": {"PHOTO": True, "ETCH": False},
                    "ETCH_A": {"PHOTO": False, "ETCH": True},
                },
            },
            tc_matrix={
                "DRAM_A": {
                    "LITHO_A": {"PHOTO": 1.0, "ETCH": 0.0},
                    "LITHO_B": {"PHOTO": 1.2, "ETCH": 0.0},
                    "ETCH_A": {"PHOTO": 0.0, "ETCH": 0.8},
                },
                "NAND_B": {
                    "LITHO_A": {"PHOTO": 0.0, "ETCH": 0.0},
                    "LITHO_B": {"PHOTO": 1.0, "ETCH": 0.0},
                    "ETCH_A": {"PHOTO": 0.0, "ETCH": 1.0},
                },
            },
            available_hours={"LITHO_A": 70.0, "LITHO_B": 60.0, "ETCH_A": 120.0},
            demand_target={"DRAM_A": 80.0, "NAND_B": 80.0},
            backup_tools={"LITHO_A": ["LITHO_B"]},
        )
    )

    total_output = sum(result.product_output.values())
    total_unmet = sum(result.unmet_demand.values())
    used_hours = {
        tool: round(result.available, 2) if hasattr(result, "available") else round(util, 2)
        for tool, util in result.tool_utilization.items()
    }

    assert_true(total_output <= 160.0, "allocation output should not exceed demand")
    assert_true(total_unmet > 0.0, "capacity shortfall should be visible as unmet demand")
    assert_true(max(result.tool_utilization.values()) <= 100.1, "allocation must respect tool capacity")
    return {
        "status": result.status,
        "product_output": {k: round(v, 1) for k, v in result.product_output.items()},
        "unmet_demand": {k: round(v, 1) for k, v in result.unmet_demand.items()},
        "tool_utilization_pct": used_hours,
        "bottleneck_tools": result.bottleneck_tools,
    }


def validate_excel_complex_path_template() -> dict[str, object]:
    content = generate_excel_template()
    summary = DATASET_REGISTRY.import_excel(content, "company-method-validation-template.xlsx")
    payload = DATASET_REGISTRY.get_complex_path_payload(summary["dataset_id"])

    assert_equal(summary["complex_path_ready"], True, "template import should enable complex path readiness")
    assert_true(len(payload["products"]) > 0, "complex payload should include products")
    assert_true(len(payload["tools"]) > 0, "complex payload should include tools")
    assert_true(len(payload["processes"]) > 0, "complex payload should include processes")
    assert_true(bool(payload["backup_tools"]), "complex payload should include backup mapping")
    return {
        "complex_path_ready": summary["complex_path_ready"],
        "n_tools": summary["n_tools"],
        "n_processes": summary["n_processes"],
        "n_capability_records": summary["n_capability_records"],
        "n_backup_paths": summary["n_backup_paths"],
    }


def run_validation() -> None:
    cases = [
        ValidationCase("Scenario routing", "5-class company scenario rules", validate_scenario_routing),
        ValidationCase("WIP-aware R6 RCCP", "R6 commitment must include WIP remaining load", validate_wip_aware_r6_rccp),
        ValidationCase("Completed wafer allocation", "multi-process output must not be over-counted", validate_completed_wafer_allocation),
        ValidationCase("Different path + backup allocation", "allocation must respect tool capacity and expose unmet demand", validate_diff_path_backup_allocation),
        ValidationCase("Excel complex path template", "front-end import template can drive allocation payload", validate_excel_complex_path_template),
    ]

    print("Capacity Agent company-method readiness validation")
    print("=" * 72)
    for idx, case in enumerate(cases, start=1):
        result = case.runner()
        print(f"[PASS] {idx}. {case.name}")
        print(f"       Purpose: {case.purpose}")
        print(f"       Result : {result}")
    print("=" * 72)
    print("READINESS RESULT: PASS")
    print("Note: heuristic allocation is acceptable for pre-check; formal frozen commitment should run with LP/CBC solver installed.")


if __name__ == "__main__":
    run_validation()
