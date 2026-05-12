"""
In-process demo data service for sample data and Excel imports.
"""
from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from engines.rccp import build_capacity_matrix
from engines.sample_data_utils import SampleDataset, ensure_sample_files, generate_sample_dataset


REQUIRED_SHEETS = {
    "route_master": {
        "product_id",
        "path_id",
        "step_seq",
        "tool_group_id",
        "run_time_hr",
        "batch_size",
    },
    "tool_groups": {
        "tool_group_id",
        "tool_group_name",
        "area",
        "n_machines",
        "nameplate_throughput_wph",
    },
    "oee": {
        "fact_date",
        "tool_group_id",
        "availability",
        "performance",
        "quality",
        "oee",
        "available_hours",
    },
    "demand_plan": {
        "time_window",
        "product_id",
        "wafer_count",
        "plan_version",
        "release_status",
    },
    "wip_lot_detail": {
        "lot_id",
        "product_id",
        "current_step_seq",
        "wafer_count",
        "percent_complete",
        "lot_status",
    },
}

OPTIONAL_COMPLEX_PATH_SHEETS = {
    "tool_master": {
        "tool_id",
        "tool_group_id",
        "tool_name",
        "status",
        "uptime",
        "loss_time",
    },
    "process_master": {
        "process_id",
        "process_name",
        "process_seq",
    },
    "tool_process_capability": {
        "product_id",
        "process_id",
        "tool_id",
        "can_run",
        "run_time_hr",
        "batch_size",
    },
    "backup_path": {
        "primary_tool_id",
        "backup_tool_id",
        "process_id",
        "enable_rule",
    },
}

COMPLEX_PATH_SHEET_NAMES = set(OPTIONAL_COMPLEX_PATH_SHEETS.keys())


@dataclass
class DatasetBundle:
    dataset_id: str
    dataset_name: str
    source_type: str
    tool_groups: pd.DataFrame
    routes: pd.DataFrame
    oee: pd.DataFrame
    demand: pd.DataFrame
    wip_lot_detail: pd.DataFrame
    products: pd.DataFrame
    tool_master: pd.DataFrame | None = None
    process_master: pd.DataFrame | None = None
    tool_process_capability: pd.DataFrame | None = None
    backup_path: pd.DataFrame | None = None


class DatasetRegistry:
    def __init__(self) -> None:
        self.sample_dir = Path(__file__).resolve().parent.parent / "data" / "sample"
        self._datasets: dict[str, DatasetBundle] = {}
        self._sample_bundle = self._load_or_generate_sample_bundle()
        self._complex_path_bundle = self._build_complex_path_demo_bundle()

    def _load_or_generate_sample_bundle(self) -> DatasetBundle:
        sample = ensure_sample_files(self.sample_dir)
        return DatasetBundle(
            dataset_id="sample",
            dataset_name="Built-in Sample Dataset",
            source_type="sample",
            tool_groups=sample.tool_groups,
            routes=sample.routes,
            oee=sample.oee,
            demand=sample.demand,
            wip_lot_detail=pd.DataFrame(columns=[
                "lot_id",
                "product_id",
                "current_step_seq",
                "wafer_count",
                "percent_complete",
                "lot_status",
                "good_wafer_count",
                "wait_hours_so_far",
            ]),
            products=sample.products,
            tool_master=pd.DataFrame(columns=[
                "tool_id",
                "tool_group_id",
                "tool_name",
                "status",
                "uptime",
                "loss_time",
            ]),
            process_master=pd.DataFrame(columns=["process_id", "process_name", "process_seq"]),
            tool_process_capability=pd.DataFrame(columns=[
                "product_id",
                "process_id",
                "tool_id",
                "can_run",
                "run_time_hr",
                "batch_size",
                "path_type",
            ]),
            backup_path=pd.DataFrame(columns=[
                "primary_tool_id",
                "backup_tool_id",
                "process_id",
                "enable_rule",
                "switch_cost",
            ]),
        )

    def _build_complex_path_demo_bundle(self) -> DatasetBundle:
        """Deterministic demo data for different Path + Backup validation."""
        tool_groups = pd.DataFrame([
            {"tool_group_id": "LITHO_MAIN", "tool_group_name": "ArF光刻主线", "area": "LITHO", "n_machines": 1, "nameplate_throughput_wph": 1.2},
            {"tool_group_id": "LITHO_BACKUP", "tool_group_name": "ArF光刻Backup", "area": "LITHO", "n_machines": 1, "nameplate_throughput_wph": 1.0},
            {"tool_group_id": "ETCH_A", "tool_group_name": "刻蚀A线", "area": "ETCH", "n_machines": 1, "nameplate_throughput_wph": 1.6},
            {"tool_group_id": "DEPO_A", "tool_group_name": "薄膜A线", "area": "DEPO", "n_machines": 1, "nameplate_throughput_wph": 2.0},
            {"tool_group_id": "CMP_A", "tool_group_name": "CMP共享线", "area": "CMP", "n_machines": 1, "nameplate_throughput_wph": 1.8},
        ])

        routes = pd.DataFrame([
            {"product_id": "DRAM_A", "path_id": "DRAM_MAIN", "step_seq": 10, "tool_group_id": "LITHO_MAIN", "run_time_hr": 0.80, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "DRAM_A", "path_id": "DRAM_MAIN", "step_seq": 20, "tool_group_id": "ETCH_A", "run_time_hr": 0.55, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "DRAM_A", "path_id": "DRAM_MAIN", "step_seq": 30, "tool_group_id": "DEPO_A", "run_time_hr": 0.35, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "DRAM_A", "path_id": "DRAM_MAIN", "step_seq": 40, "tool_group_id": "LITHO_MAIN", "run_time_hr": 0.90, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "DRAM_A", "path_id": "DRAM_MAIN", "step_seq": 50, "tool_group_id": "CMP_A", "run_time_hr": 0.30, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "NAND_B", "path_id": "NAND_ALT", "step_seq": 10, "tool_group_id": "LITHO_BACKUP", "run_time_hr": 0.70, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "NAND_B", "path_id": "NAND_ALT", "step_seq": 20, "tool_group_id": "DEPO_A", "run_time_hr": 0.50, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "NAND_B", "path_id": "NAND_ALT", "step_seq": 30, "tool_group_id": "ETCH_A", "run_time_hr": 0.70, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "NAND_B", "path_id": "NAND_ALT", "step_seq": 40, "tool_group_id": "LITHO_BACKUP", "run_time_hr": 0.75, "batch_size": 1, "visit_count": 1, "route_version": "current"},
            {"product_id": "NAND_B", "path_id": "NAND_ALT", "step_seq": 50, "tool_group_id": "CMP_A", "run_time_hr": 0.40, "batch_size": 1, "visit_count": 1, "route_version": "current"},
        ])

        products = pd.DataFrame([
            {"product_id": "DRAM_A", "product_name": "DRAM A", "product_family": "DRAM", "technology_node": "1xnm", "is_active": True},
            {"product_id": "NAND_B", "product_name": "NAND B", "product_family": "NAND", "technology_node": "128L", "is_active": True},
        ])

        oee_records = []
        for date in pd.date_range("2026-05-01", "2026-05-31"):
            for tg_id, available_hours, availability in [
                ("LITHO_MAIN", 18.0, 0.75),
                ("LITHO_BACKUP", 16.0, 0.70),
                ("ETCH_A", 20.0, 0.83),
                ("DEPO_A", 19.0, 0.79),
                ("CMP_A", 21.0, 0.88),
            ]:
                oee_records.append({
                    "fact_date": date.date(),
                    "tool_group_id": tg_id,
                    "availability": availability,
                    "performance": 0.95,
                    "quality": 0.99,
                    "oee": round(availability * 0.95 * 0.99, 4),
                    "available_hours": available_hours,
                })
        oee = pd.DataFrame(oee_records)

        demand = pd.DataFrame([
            {"time_window": "R6-2026-05", "product_id": "DRAM_A", "wafer_count": 620, "plan_version": "R6", "release_status": "frozen", "priority": 1, "contract_min": 420, "market_max": 760, "unit_profit": 150},
            {"time_window": "R6-2026-05", "product_id": "NAND_B", "wafer_count": 520, "plan_version": "R6", "release_status": "frozen", "priority": 2, "contract_min": 320, "market_max": 680, "unit_profit": 110},
            {"time_window": "R6-2026-06", "product_id": "DRAM_A", "wafer_count": 560, "plan_version": "R6", "release_status": "draft", "priority": 1, "contract_min": 380, "market_max": 720, "unit_profit": 150},
            {"time_window": "R6-2026-06", "product_id": "NAND_B", "wafer_count": 480, "plan_version": "R6", "release_status": "draft", "priority": 2, "contract_min": 300, "market_max": 640, "unit_profit": 110},
        ])

        wip_lot_detail = pd.DataFrame([
            {"lot_id": "CPWIP001", "product_id": "DRAM_A", "current_step_seq": 20, "wafer_count": 80, "percent_complete": 35, "lot_status": "RUN", "good_wafer_count": 78, "wait_hours_so_far": 5.0, "remaining_wait_hours": 0.0},
            {"lot_id": "CPWIP002", "product_id": "DRAM_A", "current_step_seq": 40, "wafer_count": 60, "percent_complete": 72, "lot_status": "WAIT", "good_wafer_count": 59, "wait_hours_so_far": 18.0, "remaining_wait_hours": 0.0},
            {"lot_id": "CPWIP003", "product_id": "NAND_B", "current_step_seq": 10, "wafer_count": 90, "percent_complete": 20, "lot_status": "WAIT", "good_wafer_count": 88, "wait_hours_so_far": 12.0, "remaining_wait_hours": 0.0},
            {"lot_id": "CPWIP004", "product_id": "NAND_B", "current_step_seq": 30, "wafer_count": 70, "percent_complete": 60, "lot_status": "HOLD", "good_wafer_count": 69, "wait_hours_so_far": 30.0, "remaining_wait_hours": 24.0},
        ])

        tool_master = pd.DataFrame([
            {"tool_id": "LM_T01", "tool_group_id": "LITHO_MAIN", "tool_name": "ArF主线-01", "status": "RUN", "uptime": 0.75, "loss_time": 0.05},
            {"tool_id": "LB_T01", "tool_group_id": "LITHO_BACKUP", "tool_name": "ArF备援-01", "status": "RUN", "uptime": 0.70, "loss_time": 0.05},
            {"tool_id": "EA_T01", "tool_group_id": "ETCH_A", "tool_name": "刻蚀A-01", "status": "RUN", "uptime": 0.83, "loss_time": 0.03},
            {"tool_id": "DA_T01", "tool_group_id": "DEPO_A", "tool_name": "薄膜A-01", "status": "RUN", "uptime": 0.79, "loss_time": 0.03},
            {"tool_id": "CA_T01", "tool_group_id": "CMP_A", "tool_name": "CMP-01", "status": "RUN", "uptime": 0.88, "loss_time": 0.04},
        ])

        process_master = pd.DataFrame([
            {"process_id": "PHOTO_1", "process_name": "首层光刻", "process_seq": 10, "area": "LITHO"},
            {"process_id": "ETCH_1", "process_name": "关键刻蚀", "process_seq": 20, "area": "ETCH"},
            {"process_id": "DEPO_1", "process_name": "薄膜沉积", "process_seq": 30, "area": "DEPO"},
            {"process_id": "PHOTO_2", "process_name": "二次光刻", "process_seq": 40, "area": "LITHO"},
            {"process_id": "CMP_1", "process_name": "CMP平坦化", "process_seq": 50, "area": "CMP"},
        ])

        tool_process_capability = pd.DataFrame([
            {"product_id": "DRAM_A", "process_id": "PHOTO_1", "tool_id": "LM_T01", "can_run": True, "run_time_hr": 0.80, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "DRAM_A", "process_id": "PHOTO_1", "tool_id": "LB_T01", "can_run": True, "run_time_hr": 1.05, "batch_size": 1, "path_type": "backup", "visit_count": 1},
            {"product_id": "DRAM_A", "process_id": "ETCH_1", "tool_id": "EA_T01", "can_run": True, "run_time_hr": 0.55, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "DRAM_A", "process_id": "DEPO_1", "tool_id": "DA_T01", "can_run": True, "run_time_hr": 0.35, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "DRAM_A", "process_id": "PHOTO_2", "tool_id": "LM_T01", "can_run": True, "run_time_hr": 0.90, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "DRAM_A", "process_id": "PHOTO_2", "tool_id": "LB_T01", "can_run": True, "run_time_hr": 1.10, "batch_size": 1, "path_type": "backup", "visit_count": 1},
            {"product_id": "DRAM_A", "process_id": "CMP_1", "tool_id": "CA_T01", "can_run": True, "run_time_hr": 0.30, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "NAND_B", "process_id": "PHOTO_1", "tool_id": "LB_T01", "can_run": True, "run_time_hr": 0.70, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "NAND_B", "process_id": "ETCH_1", "tool_id": "EA_T01", "can_run": True, "run_time_hr": 0.70, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "NAND_B", "process_id": "DEPO_1", "tool_id": "DA_T01", "can_run": True, "run_time_hr": 0.50, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "NAND_B", "process_id": "PHOTO_2", "tool_id": "LB_T01", "can_run": True, "run_time_hr": 0.75, "batch_size": 1, "path_type": "primary", "visit_count": 1},
            {"product_id": "NAND_B", "process_id": "CMP_1", "tool_id": "CA_T01", "can_run": True, "run_time_hr": 0.40, "batch_size": 1, "path_type": "primary", "visit_count": 1},
        ])

        backup_path = pd.DataFrame([
            {"primary_tool_id": "LM_T01", "backup_tool_id": "LB_T01", "process_id": "PHOTO_1", "enable_rule": "primary_overload", "switch_cost": 0.08},
            {"primary_tool_id": "LM_T01", "backup_tool_id": "LB_T01", "process_id": "PHOTO_2", "enable_rule": "primary_overload", "switch_cost": 0.10},
        ])

        return DatasetBundle(
            dataset_id="complex-path-demo",
            dataset_name="Complex Path + Backup Demo",
            source_type="sample_complex_path",
            tool_groups=tool_groups,
            routes=routes,
            oee=oee,
            demand=demand,
            wip_lot_detail=wip_lot_detail,
            products=products,
            tool_master=tool_master,
            process_master=process_master,
            tool_process_capability=tool_process_capability,
            backup_path=backup_path,
        )

    def list_datasets(self) -> list[dict[str, Any]]:
        datasets = [self._bundle_summary(self._sample_bundle), self._bundle_summary(self._complex_path_bundle)]
        datasets.extend(self._bundle_summary(bundle) for bundle in self._datasets.values())
        return datasets

    def get_dataset(self, dataset_id: str | None = None) -> DatasetBundle:
        if not dataset_id or dataset_id == "sample":
            return self._sample_bundle
        if dataset_id == "complex-path-demo":
            return self._complex_path_bundle
        if dataset_id not in self._datasets:
            raise KeyError(f"Unknown dataset_id: {dataset_id}")
        return self._datasets[dataset_id]

    def import_excel(self, content: bytes, filename: str) -> dict[str, Any]:
        workbook = pd.read_excel(io.BytesIO(content), sheet_name=None)
        normalized = {name.strip().lower(): frame for name, frame in workbook.items()}

        missing_sheets = [sheet for sheet in REQUIRED_SHEETS if sheet not in normalized]
        if missing_sheets:
            raise ValueError(f"Excel missing required sheets: {', '.join(missing_sheets)}")

        for sheet_name, required_columns in REQUIRED_SHEETS.items():
            frame = normalized[sheet_name]
            columns = set(frame.columns.astype(str))
            missing_columns = sorted(required_columns - columns)
            if missing_columns:
                raise ValueError(f"Sheet '{sheet_name}' missing columns: {', '.join(missing_columns)}")

        for sheet_name, required_columns in OPTIONAL_COMPLEX_PATH_SHEETS.items():
            if sheet_name not in normalized:
                continue
            frame = normalized[sheet_name]
            columns = set(frame.columns.astype(str))
            missing_columns = sorted(required_columns - columns)
            if missing_columns:
                raise ValueError(f"Sheet '{sheet_name}' missing columns: {', '.join(missing_columns)}")

        present_complex_sheets = {
            sheet_name
            for sheet_name in COMPLEX_PATH_SHEET_NAMES
            if sheet_name in normalized and not normalized[sheet_name].dropna(how="all").empty
        }
        if present_complex_sheets and present_complex_sheets != COMPLEX_PATH_SHEET_NAMES:
            missing_complex_sheets = sorted(COMPLEX_PATH_SHEET_NAMES - present_complex_sheets)
            raise ValueError(
                "Complex path import requires a complete sheet set. "
                f"Missing or empty sheets: {', '.join(missing_complex_sheets)}"
            )

        routes = normalized["route_master"].copy()
        tool_groups = normalized["tool_groups"].copy()
        oee = normalized["oee"].copy()
        demand = normalized["demand_plan"].copy()
        wip_lot_detail = normalized["wip_lot_detail"].copy()
        tool_master = normalized.get("tool_master", pd.DataFrame()).copy()
        process_master = normalized.get("process_master", pd.DataFrame()).copy()
        tool_process_capability = normalized.get("tool_process_capability", pd.DataFrame()).copy()
        backup_path = normalized.get("backup_path", pd.DataFrame()).copy()

        routes["step_seq"] = pd.to_numeric(routes["step_seq"], errors="coerce").fillna(0).astype(int)
        routes["run_time_hr"] = pd.to_numeric(routes["run_time_hr"], errors="coerce").fillna(0.0)
        routes["batch_size"] = pd.to_numeric(routes["batch_size"], errors="coerce").fillna(1).clip(lower=1)
        oee["fact_date"] = pd.to_datetime(oee["fact_date"]).dt.date
        numeric_oee_cols = ["availability", "performance", "quality", "oee", "available_hours"]
        for col in numeric_oee_cols:
            oee[col] = pd.to_numeric(oee[col], errors="coerce").fillna(0.0)
        demand["wafer_count"] = pd.to_numeric(demand["wafer_count"], errors="coerce").fillna(0.0)
        wip_lot_detail["current_step_seq"] = pd.to_numeric(wip_lot_detail["current_step_seq"], errors="coerce").fillna(0).astype(int)
        wip_lot_detail["wafer_count"] = pd.to_numeric(wip_lot_detail["wafer_count"], errors="coerce").fillna(0.0)
        wip_lot_detail["percent_complete"] = pd.to_numeric(wip_lot_detail["percent_complete"], errors="coerce").fillna(0.0)
        if "good_wafer_count" in wip_lot_detail.columns:
            wip_lot_detail["good_wafer_count"] = pd.to_numeric(wip_lot_detail["good_wafer_count"], errors="coerce").fillna(0.0)
        if "wait_hours_so_far" in wip_lot_detail.columns:
            wip_lot_detail["wait_hours_so_far"] = pd.to_numeric(wip_lot_detail["wait_hours_so_far"], errors="coerce").fillna(0.0)
        if "remaining_wait_hours" in wip_lot_detail.columns:
            wip_lot_detail["remaining_wait_hours"] = pd.to_numeric(wip_lot_detail["remaining_wait_hours"], errors="coerce").fillna(0.0)
        if "hold_release_date" in wip_lot_detail.columns:
            wip_lot_detail["hold_release_date"] = pd.to_datetime(wip_lot_detail["hold_release_date"], errors="coerce")
        if not tool_master.empty:
            for col in ["uptime", "loss_time"]:
                if col in tool_master.columns:
                    tool_master[col] = pd.to_numeric(tool_master[col], errors="coerce").fillna(0.0)
        if not process_master.empty and "process_seq" in process_master.columns:
            process_master["process_seq"] = pd.to_numeric(process_master["process_seq"], errors="coerce").fillna(0).astype(int)
        if not tool_process_capability.empty:
            tool_process_capability["can_run"] = tool_process_capability["can_run"].map(_coerce_bool)
            for col in ["run_time_hr", "batch_size", "visit_count", "switch_cost"]:
                if col in tool_process_capability.columns:
                    default = 1.0 if col in {"batch_size", "visit_count"} else 0.0
                    tool_process_capability[col] = pd.to_numeric(tool_process_capability[col], errors="coerce").fillna(default)
        if not backup_path.empty and "switch_cost" in backup_path.columns:
            backup_path["switch_cost"] = pd.to_numeric(backup_path["switch_cost"], errors="coerce").fillna(0.0)

        product_ids = sorted(set(routes["product_id"].astype(str)) | set(demand["product_id"].astype(str)))
        products = pd.DataFrame(
            {
                "product_id": product_ids,
                "product_name": product_ids,
                "product_family": [pid.split("_")[-1] if "_" in pid else "UNKNOWN" for pid in product_ids],
                "technology_node": [pid.split("_")[0] if "_" in pid else "UNKNOWN" for pid in product_ids],
                "is_active": True,
            }
        )

        dataset_id = f"excel-{uuid.uuid4().hex[:8]}"
        bundle = DatasetBundle(
            dataset_id=dataset_id,
            dataset_name=filename,
            source_type="excel",
            tool_groups=tool_groups,
            routes=routes,
            oee=oee,
            demand=demand,
            wip_lot_detail=wip_lot_detail,
            products=products,
            tool_master=tool_master,
            process_master=process_master,
            tool_process_capability=tool_process_capability,
            backup_path=backup_path,
        )
        self._datasets[dataset_id] = bundle
        return self._bundle_summary(bundle)

    def build_capacity_matrix_payload(
        self,
        dataset_id: str | None = None,
        products: list[str] | None = None,
        route_version: str = "current",
    ) -> dict[str, Any]:
        bundle = self.get_dataset(dataset_id)
        routes = bundle.routes.copy()
        if "route_version" in routes.columns:
            routes = routes[routes["route_version"].astype(str) == route_version]
        if products:
            routes = routes[routes["product_id"].isin(products)]
        matrix = build_capacity_matrix(routes)
        return {
            "dataset_id": bundle.dataset_id,
            "capacity_matrix": matrix.to_dict(orient="index"),
            "n_products": len(matrix.index),
            "n_tool_groups": len(matrix.columns),
        }

    def get_demand_plan_payload(
        self,
        dataset_id: str | None = None,
        time_window: str = "this_week",
        products: list[str] | None = None,
    ) -> dict[str, Any]:
        bundle = self.get_dataset(dataset_id)
        demand = bundle.demand.copy()
        available_windows = sorted(demand["time_window"].astype(str).unique().tolist())
        window = self._resolve_time_window(time_window, available_windows)
        demand = demand[demand["time_window"].astype(str) == window]
        if products:
            demand = demand[demand["product_id"].isin(products)]
        return {
            "dataset_id": bundle.dataset_id,
            "time_window": window,
            "available_windows": available_windows,
            "demand_plan": {
                str(row["product_id"]): float(row["wafer_count"])
                for _, row in demand.iterrows()
            },
            "records": demand.to_dict(orient="records"),
        }

    def get_historical_loading_payload(
        self,
        dataset_id: str | None = None,
        tool_groups: list[str] | None = None,
        n_weeks: int = 4,
    ) -> dict[str, Any]:
        bundle = self.get_dataset(dataset_id)
        oee = bundle.oee.copy()
        oee["fact_date"] = pd.to_datetime(oee["fact_date"])
        oee["week"] = oee["fact_date"].dt.strftime("%G-W%V")
        weekly = (
            oee.groupby(["tool_group_id", "week"], as_index=False)[["available_hours", "oee"]]
            .mean()
            .sort_values(["tool_group_id", "week"])
        )
        history: dict[str, list[float]] = {}
        target_tool_groups = tool_groups or sorted(weekly["tool_group_id"].astype(str).unique().tolist())
        for tg_id in target_tool_groups:
            group = weekly[weekly["tool_group_id"].astype(str) == tg_id].tail(n_weeks)
            history[tg_id] = [round(float(val * 100.0), 2) for val in group["oee"].tolist()]
        return {
            "dataset_id": bundle.dataset_id,
            "n_weeks": n_weeks,
            "history": history,
        }

    def get_tool_group_status_payload(self, dataset_id: str | None = None, area: str | None = None) -> dict[str, Any]:
        bundle = self.get_dataset(dataset_id)
        tool_groups = bundle.tool_groups.copy()
        if area:
            tool_groups = tool_groups[tool_groups["area"].astype(str).str.upper() == area.upper()]
        latest_date = pd.to_datetime(bundle.oee["fact_date"]).max()
        latest = bundle.oee.copy()
        latest["fact_date"] = pd.to_datetime(latest["fact_date"])
        latest = latest[latest["fact_date"] == latest_date]
        trailing_week = bundle.oee.copy()
        trailing_week["fact_date"] = pd.to_datetime(trailing_week["fact_date"])
        trailing_week = trailing_week[trailing_week["fact_date"] >= latest_date - pd.Timedelta(days=6)]
        weekly_hours = (
            trailing_week.groupby("tool_group_id", as_index=False)["available_hours"]
            .sum()
            .rename(columns={"available_hours": "weekly_available_hours"})
        )
        merged = tool_groups.merge(latest, on="tool_group_id", how="left")
        merged = merged.merge(weekly_hours, on="tool_group_id", how="left")
        records = []
        for _, row in merged.iterrows():
            n_machines = int(row.get("n_machines", 0) or 0)
            availability = float(row.get("availability", 0.0) or 0.0)
            n_up = int(round(n_machines * availability))
            records.append(
                {
                    "tool_group_id": str(row["tool_group_id"]),
                    "tool_group_name": str(row.get("tool_group_name", row["tool_group_id"])),
                    "area": str(row.get("area", "UNKNOWN")),
                    "n_machines": n_machines,
                    "nameplate_throughput_wph": round(float(row.get("nameplate_throughput_wph", 0.0) or 0.0), 2),
                    "n_up": n_up,
                    "availability": round(availability, 4),
                    "performance": round(float(row.get("performance", 0.0) or 0.0), 4),
                    "quality": round(float(row.get("quality", 0.0) or 0.0), 4),
                    "oee": round(float(row.get("oee", 0.0) or 0.0), 4),
                    "available_hours": round(float(row.get("weekly_available_hours", 0.0) or 0.0), 2),
                    "available_hours_daily": round(float(row.get("available_hours", 0.0) or 0.0), 2),
                    "fact_date": latest_date.date().isoformat() if not pd.isna(latest_date) else None,
                }
            )
        return {
            "dataset_id": bundle.dataset_id,
            "tool_groups": records,
        }

    def get_dataset_summary_payload(self, dataset_id: str | None = None) -> dict[str, Any]:
        bundle = self.get_dataset(dataset_id)
        capacity_matrix = build_capacity_matrix(bundle.routes.copy())
        time_windows = sorted(bundle.demand["time_window"].astype(str).unique().tolist())
        return {
            **self._bundle_summary(bundle),
            "time_windows": time_windows,
            "capacity_matrix_shape": [len(capacity_matrix.index), len(capacity_matrix.columns)],
        }

    def get_wip_lot_detail_payload(self, dataset_id: str | None = None) -> dict[str, Any]:
        bundle = self.get_dataset(dataset_id)
        wip_df = bundle.wip_lot_detail.copy()
        wip_df = wip_df.replace({pd.NA: None}).where(pd.notnull(wip_df), None)
        return {
            "dataset_id": bundle.dataset_id,
            "n_wip_lots": int(len(wip_df)),
            "total_wip_wafers": float(wip_df["wafer_count"].sum()) if "wafer_count" in wip_df.columns else 0.0,
            "records": wip_df.to_dict(orient="records"),
        }

    def get_complex_path_payload(
        self,
        dataset_id: str | None = None,
        days_in_month: float = 30.0,
    ) -> dict[str, Any]:
        """Build the scenario/allocation payload from optional tool-level path sheets."""
        bundle = self.get_dataset(dataset_id)
        capability = bundle.tool_process_capability.copy() if bundle.tool_process_capability is not None else pd.DataFrame()
        if capability.empty:
            raise ValueError("Dataset has no tool_process_capability sheet; complex path payload is unavailable")

        capability = capability[capability["can_run"].map(_coerce_bool)]
        if "visit_count" not in capability.columns:
            capability["visit_count"] = 1.0
        capability["visit_count"] = pd.to_numeric(capability["visit_count"], errors="coerce").fillna(1.0)
        capability["batch_size"] = pd.to_numeric(capability["batch_size"], errors="coerce").fillna(1.0).clip(lower=1.0)
        capability["run_time_hr"] = pd.to_numeric(capability["run_time_hr"], errors="coerce").fillna(0.0)
        capability["unit_hours"] = capability["run_time_hr"] * capability["visit_count"] / capability["batch_size"]

        products = sorted(capability["product_id"].astype(str).unique().tolist())
        tools = sorted(capability["tool_id"].astype(str).unique().tolist())
        if bundle.process_master is not None and not bundle.process_master.empty:
            process_master = bundle.process_master.copy().sort_values("process_seq")
            processes = process_master["process_id"].astype(str).tolist()
        else:
            processes = sorted(capability["process_id"].astype(str).unique().tolist())

        feasibility: dict[str, dict[str, dict[str, bool]]] = {
            p: {t: {s: False for s in processes} for t in tools}
            for p in products
        }
        tc_matrix: dict[str, dict[str, dict[str, float]]] = {
            p: {t: {s: 0.0 for s in processes} for t in tools}
            for p in products
        }

        grouped = (
            capability.groupby(["product_id", "tool_id", "process_id"], as_index=False)["unit_hours"]
            .sum()
        )
        for _, row in grouped.iterrows():
            product_id = str(row["product_id"])
            tool_id = str(row["tool_id"])
            process_id = str(row["process_id"])
            if product_id not in feasibility or tool_id not in feasibility[product_id]:
                continue
            feasibility[product_id][tool_id][process_id] = True
            tc_matrix[product_id][tool_id][process_id] = float(row["unit_hours"])

        available_hours = self._build_tool_available_hours(bundle, tools, days_in_month)
        backup_tools: dict[str, list[str]] = {}
        backup_path = bundle.backup_path.copy() if bundle.backup_path is not None else pd.DataFrame()
        if not backup_path.empty:
            for _, row in backup_path.iterrows():
                primary = str(row["primary_tool_id"])
                backup = str(row["backup_tool_id"])
                if primary and backup:
                    backup_tools.setdefault(primary, [])
                    if backup not in backup_tools[primary]:
                        backup_tools[primary].append(backup)

        return {
            "dataset_id": bundle.dataset_id,
            "products": products,
            "tools": tools,
            "processes": processes,
            "feasibility": feasibility,
            "tc_matrix": tc_matrix,
            "available_hours": available_hours,
            "backup_tools": backup_tools,
            "metadata": {
                "days_in_month": days_in_month,
                "n_capability_records": int(len(capability)),
                "n_backup_paths": int(len(backup_path)),
                "tc_unit": "hours_per_wafer",
            },
        }

    def _build_tool_available_hours(
        self,
        bundle: DatasetBundle,
        tools: list[str],
        days_in_month: float,
    ) -> dict[str, float]:
        tool_master = bundle.tool_master.copy() if bundle.tool_master is not None else pd.DataFrame()
        if not tool_master.empty:
            available_hours: dict[str, float] = {}
            for _, row in tool_master.iterrows():
                tool_id = str(row["tool_id"])
                if tool_id not in tools:
                    continue
                uptime = float(row.get("uptime", 0.90) or 0.90)
                loss_time = float(row.get("loss_time", 0.0) or 0.0)
                available_hours[tool_id] = max(0.0, 24.0 * days_in_month * (uptime - loss_time))
            return {tool_id: available_hours.get(tool_id, 0.0) for tool_id in tools}

        latest_oee = bundle.oee.copy()
        latest_oee["fact_date"] = pd.to_datetime(latest_oee["fact_date"], errors="coerce")
        latest_date = latest_oee["fact_date"].max()
        latest_oee = latest_oee[latest_oee["fact_date"] == latest_date]
        group_hours = {
            str(row["tool_group_id"]): float(row.get("available_hours", 0.0) or 0.0) * days_in_month
            for _, row in latest_oee.iterrows()
        }
        group_machine_count = {
            str(row["tool_group_id"]): max(1, int(row.get("n_machines", 1) or 1))
            for _, row in bundle.tool_groups.iterrows()
        }
        return {
            tool_id: group_hours.get(tool_id, 0.0) / group_machine_count.get(tool_id, 1)
            for tool_id in tools
        }

    def _resolve_time_window(self, time_window: str, available_windows: list[str]) -> str:
        if not available_windows:
            raise ValueError("Dataset has no demand plan windows")
        if time_window in available_windows:
            return time_window
        aliases = {
            "this_week": 0,
            "current": 0,
            "next_week": 1,
        }
        if time_window in aliases:
            idx = min(aliases[time_window], len(available_windows) - 1)
            return available_windows[idx]
        return available_windows[0]

    def _bundle_summary(self, bundle: DatasetBundle) -> dict[str, Any]:
        return {
            "dataset_id": bundle.dataset_id,
            "dataset_name": bundle.dataset_name,
            "source_type": bundle.source_type,
            "n_products": int(bundle.products["product_id"].nunique()),
            "n_tool_groups": int(bundle.tool_groups["tool_group_id"].nunique()),
            "n_routes": int(len(bundle.routes)),
            "n_oee_records": int(len(bundle.oee)),
            "n_demand_records": int(len(bundle.demand)),
            "n_wip_lots": int(len(bundle.wip_lot_detail)),
            "n_tools": int(len(bundle.tool_master)) if bundle.tool_master is not None else 0,
            "n_processes": int(len(bundle.process_master)) if bundle.process_master is not None else 0,
            "n_capability_records": int(len(bundle.tool_process_capability)) if bundle.tool_process_capability is not None else 0,
            "n_backup_paths": int(len(bundle.backup_path)) if bundle.backup_path is not None else 0,
            "complex_path_ready": bool(
                bundle.tool_master is not None and not bundle.tool_master.empty
                and bundle.process_master is not None and not bundle.process_master.empty
                and bundle.tool_process_capability is not None and not bundle.tool_process_capability.empty
            ),
        }


DATASET_REGISTRY = DatasetRegistry()


def _coerce_bool(value: Any) -> bool:
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "v", "可跑", "可以"}


def generate_excel_template() -> bytes:
    """
    生成 Excel 导入模板文件
    
    通用版模板固定包含 5 个基础必填 Sheet + 4 个复杂 Path/Backup Sheet。
    """
    # Sheet 0: README / 字段字典
    readme_df = pd.DataFrame([
        {"sheet_name": "route_master", "required": "Y", "scope": "基础RCCP/路线", "description": "产品-路径-工序-机台组路线；用于RCCP、WIP后续负载、生产计划。"},
        {"sheet_name": "tool_groups", "required": "Y", "scope": "基础RCCP/产能", "description": "机台组主数据；用于机台组可用产能与瓶颈分析。"},
        {"sheet_name": "oee", "required": "Y", "scope": "基础RCCP/产能", "description": "按日期和机台组维护 OEE/可用小时；R6月度建议覆盖整月。"},
        {"sheet_name": "demand_plan", "required": "Y", "scope": "R6/MPS需求", "description": "按时间窗口维护产品投片需求；正式R6建议使用 R6-YYYY-MM。"},
        {"sheet_name": "wip_lot_detail", "required": "Y", "scope": "WIP口径", "description": "Lot级WIP状态；用于WIP后续工序负载与Output视角。"},
        {"sheet_name": "tool_master", "required": "N*", "scope": "复杂Path", "description": "单台设备主数据。若启用复杂Path，4张复杂Path表必须一起填写。"},
        {"sheet_name": "process_master", "required": "N*", "scope": "复杂Path", "description": "统一制程主数据与顺序。"},
        {"sheet_name": "tool_process_capability", "required": "N*", "scope": "复杂Path", "description": "产品-制程-单台设备可跑能力矩阵，核心输入。"},
        {"sheet_name": "backup_path", "required": "N*", "scope": "复杂Path/Backup", "description": "主设备到Backup设备的替代关系。"},
    ])

    field_dictionary_df = pd.DataFrame([
        {"sheet_name": "tool_process_capability", "field_name": "product_id", "required": "Y", "description": "产品编码，需与 demand_plan/route_master 一致。"},
        {"sheet_name": "tool_process_capability", "field_name": "process_id", "required": "Y", "description": "制程编码，需在 process_master 中维护。"},
        {"sheet_name": "tool_process_capability", "field_name": "tool_id", "required": "Y", "description": "单台设备编码，需在 tool_master 中维护。"},
        {"sheet_name": "tool_process_capability", "field_name": "can_run", "required": "Y", "description": "是否可跑，支持 TRUE/FALSE、1/0、可跑/不可跑。"},
        {"sheet_name": "tool_process_capability", "field_name": "run_time_hr", "required": "Y", "description": "该产品在该设备上执行该制程的小时/批。"},
        {"sheet_name": "tool_process_capability", "field_name": "batch_size", "required": "Y", "description": "批次片数；单片设备填 1。"},
        {"sheet_name": "tool_process_capability", "field_name": "visit_count", "required": "N", "description": "重复访问次数；默认 1。"},
        {"sheet_name": "tool_process_capability", "field_name": "path_type", "required": "N", "description": "primary/backup/alternate，用于展示和审查。"},
        {"sheet_name": "backup_path", "field_name": "primary_tool_id", "required": "Y", "description": "主设备编码。"},
        {"sheet_name": "backup_path", "field_name": "backup_tool_id", "required": "Y", "description": "Backup设备编码。"},
        {"sheet_name": "backup_path", "field_name": "process_id", "required": "Y", "description": "适用制程。"},
        {"sheet_name": "backup_path", "field_name": "enable_rule", "required": "Y", "description": "启用规则，例如 primary_overload / primary_down。"},
        {"sheet_name": "backup_path", "field_name": "switch_cost", "required": "N", "description": "切换成本/效率损失，可填 0-1。"},
    ])

    # Sheet 1: route_master (产品工艺路线)
    route_master_data = {
        "product_id": ["DRAM_A", "DRAM_A", "DRAM_A", "DRAM_A", "DRAM_A", "NAND_B", "NAND_B", "NAND_B", "NAND_B", "NAND_B"],
        "path_id": ["DRAM_MAIN", "DRAM_MAIN", "DRAM_MAIN", "DRAM_MAIN", "DRAM_MAIN", "NAND_ALT", "NAND_ALT", "NAND_ALT", "NAND_ALT", "NAND_ALT"],
        "step_seq": [10, 20, 30, 40, 50, 10, 20, 30, 40, 50],
        "tool_group_id": ["LITHO_MAIN", "ETCH_A", "DEPO_A", "LITHO_MAIN", "CMP_A", "LITHO_BACKUP", "DEPO_A", "ETCH_A", "LITHO_BACKUP", "CMP_A"],
        "run_time_hr": [0.80, 0.55, 0.35, 0.90, 0.30, 0.70, 0.50, 0.70, 0.75, 0.40],
        "batch_size": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        # 可选字段示例
        "step_name": ["首层光刻", "关键刻蚀", "薄膜沉积", "二次光刻", "CMP平坦化", "首层光刻", "薄膜沉积", "关键刻蚀", "二次光刻", "CMP平坦化"],
        "visit_count": [1, 1, 1, 1, 1, 1, 1, 1, 1, 1],
        "route_version": ["current"] * 10,
    }
    route_master_df = pd.DataFrame(route_master_data)
    
    # Sheet 2: tool_groups (机台组信息)
    tool_groups_data = {
        "tool_group_id": ["LITHO_MAIN", "LITHO_BACKUP", "ETCH_A", "DEPO_A", "CMP_A"],
        "tool_group_name": ["ArF光刻主线", "ArF光刻Backup", "刻蚀A线", "薄膜A线", "CMP共享线"],
        "area": ["LITHO", "LITHO", "ETCH", "DEPO", "CMP"],
        "n_machines": [1, 1, 1, 1, 1],
        "nameplate_throughput_wph": [1.2, 1.0, 1.6, 2.0, 1.8],
        # 可选字段示例
        "process_type": ["光刻", "光刻", "刻蚀", "薄膜沉积", "CMP抛光"],
        "is_active": [True, True, True, True, True],
    }
    tool_groups_df = pd.DataFrame(tool_groups_data)
    
    # Sheet 3: oee (OEE 历史数据) - 提供最近7天示例
    oee_records = []
    base_date = pd.Timestamp("2026-04-20")
    for day_offset in range(7):
        date = base_date + pd.Timedelta(days=day_offset)
        for tg_id, base_avail, base_perf, base_qual in [
            ("LITHO_MAIN", 0.75, 0.95, 0.99),
            ("LITHO_BACKUP", 0.70, 0.95, 0.99),
            ("ETCH_A", 0.83, 0.95, 0.99),
            ("DEPO_A", 0.79, 0.95, 0.99),
            ("CMP_A", 0.88, 0.95, 0.99),
        ]:
            # 添加一些随机波动
            avail = base_avail + (day_offset % 3 - 1) * 0.02
            perf = base_perf + (day_offset % 2) * 0.01
            qual = base_qual
            oee = round(avail * perf * qual, 4)
            n_machines_lookup = {"LITHO_MAIN": 1, "LITHO_BACKUP": 1, "ETCH_A": 1, "DEPO_A": 1, "CMP_A": 1}
            available_hours = round(n_machines_lookup[tg_id] * 24 * avail, 2)
            oee_records.append({
                "fact_date": date.date(),
                "tool_group_id": tg_id,
                "availability": round(avail, 4),
                "performance": round(perf, 4),
                "quality": round(qual, 4),
                "oee": oee,
                "available_hours": available_hours,
            })
    oee_df = pd.DataFrame(oee_records)
    
    # Sheet 4: demand_plan (需求计划)
    demand_plan_data = {
        "time_window": ["R6-2026-05", "R6-2026-05", "R6-2026-06", "R6-2026-06"],
        "product_id": ["DRAM_A", "NAND_B", "DRAM_A", "NAND_B"],
        "wafer_count": [620, 520, 560, 480],
        # 可选字段示例
        "priority": [1, 2, 1, 2],
        "contract_min": [420, 320, 380, 300],
        "market_max": [760, 680, 720, 640],
        "unit_profit": [150.0, 110.0, 150.0, 110.0],
        "plan_version": ["R6", "R6", "R6", "R6"],
        "release_status": ["frozen", "frozen", "draft", "draft"],
        "owner": ["MPS", "MPS", "MPS", "MPS"],
        "approved_at": ["2026-04-25", "2026-04-25", "", ""],
    }
    demand_plan_df = pd.DataFrame(demand_plan_data)

    # Sheet 5: wip_lot_detail (WIP Lot 明细)
    wip_lot_detail_data = {
        "lot_id": ["CPWIP001", "CPWIP002", "CPWIP003", "CPWIP004"],
        "product_id": ["DRAM_A", "DRAM_A", "NAND_B", "NAND_B"],
        "current_step_seq": [20, 40, 10, 30],
        "wafer_count": [80, 60, 90, 70],
        "percent_complete": [35, 72, 20, 60],
        # 可选字段示例
        "lot_status": ["RUN", "WAIT", "WAIT", "HOLD"],
        "good_wafer_count": [78, 59, 88, 69],
        "wait_hours_so_far": [5.0, 18.0, 12.0, 30.0],
        "remaining_wait_hours": [0.0, 0.0, 0.0, 24.0],
        "hold_release_date": ["", "", "", ""],
        "input_week": ["2026-W14", "2026-W15", "2026-W14", "2026-W15"],
    }
    wip_lot_detail_df = pd.DataFrame(wip_lot_detail_data)

    # Sheet 6: tool_master (单机台主数据，复杂 path 推荐)
    tool_master_df = pd.DataFrame({
        "tool_id": ["LM_T01", "LB_T01", "EA_T01", "DA_T01", "CA_T01"],
        "tool_group_id": ["LITHO_MAIN", "LITHO_BACKUP", "ETCH_A", "DEPO_A", "CMP_A"],
        "tool_name": ["ArF主线-01", "ArF备援-01", "刻蚀A-01", "薄膜A-01", "CMP-01"],
        "status": ["RUN", "RUN", "RUN", "RUN", "RUN"],
        "uptime": [0.75, 0.70, 0.83, 0.79, 0.88],
        "loss_time": [0.05, 0.05, 0.03, 0.03, 0.04],
    })

    # Sheet 7: process_master (制程主数据，复杂 path 推荐)
    process_master_df = pd.DataFrame({
        "process_id": ["PHOTO_1", "ETCH_1", "DEPO_1", "PHOTO_2", "CMP_1"],
        "process_name": ["首层光刻", "关键刻蚀", "薄膜沉积", "二次光刻", "CMP平坦化"],
        "process_seq": [10, 20, 30, 40, 50],
        "area": ["LITHO", "ETCH", "DEPO", "LITHO", "CMP"],
    })

    # Sheet 8: tool_process_capability (产品-制程-机台能力矩阵，复杂 path 推荐)
    tool_process_capability_df = pd.DataFrame({
        "product_id": ["DRAM_A", "DRAM_A", "DRAM_A", "DRAM_A", "DRAM_A", "DRAM_A", "DRAM_A", "NAND_B", "NAND_B", "NAND_B", "NAND_B", "NAND_B"],
        "process_id": ["PHOTO_1", "PHOTO_1", "ETCH_1", "DEPO_1", "PHOTO_2", "PHOTO_2", "CMP_1", "PHOTO_1", "ETCH_1", "DEPO_1", "PHOTO_2", "CMP_1"],
        "tool_id": ["LM_T01", "LB_T01", "EA_T01", "DA_T01", "LM_T01", "LB_T01", "CA_T01", "LB_T01", "EA_T01", "DA_T01", "LB_T01", "CA_T01"],
        "can_run": [True] * 12,
        "run_time_hr": [0.80, 1.05, 0.55, 0.35, 0.90, 1.10, 0.30, 0.70, 0.70, 0.50, 0.75, 0.40],
        "batch_size": [1] * 12,
        "path_type": ["primary", "backup", "primary", "primary", "primary", "backup", "primary", "primary", "primary", "primary", "primary", "primary"],
        "visit_count": [1] * 12,
    })

    # Sheet 9: backup_path (backup 替代路径，复杂 path 推荐)
    backup_path_df = pd.DataFrame({
        "primary_tool_id": ["LM_T01", "LM_T01"],
        "backup_tool_id": ["LB_T01", "LB_T01"],
        "process_id": ["PHOTO_1", "PHOTO_2"],
        "enable_rule": ["primary_overload", "primary_overload"],
        "switch_cost": [0.08, 0.10],
    })
    
    # 写入 Excel 文件
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        readme_df.to_excel(writer, sheet_name="README", index=False)
        field_dictionary_df.to_excel(writer, sheet_name="field_dictionary", index=False)
        route_master_df.to_excel(writer, sheet_name="route_master", index=False)
        tool_groups_df.to_excel(writer, sheet_name="tool_groups", index=False)
        oee_df.to_excel(writer, sheet_name="oee", index=False)
        demand_plan_df.to_excel(writer, sheet_name="demand_plan", index=False)
        wip_lot_detail_df.to_excel(writer, sheet_name="wip_lot_detail", index=False)
        tool_master_df.to_excel(writer, sheet_name="tool_master", index=False)
        process_master_df.to_excel(writer, sheet_name="process_master", index=False)
        tool_process_capability_df.to_excel(writer, sheet_name="tool_process_capability", index=False)
        backup_path_df.to_excel(writer, sheet_name="backup_path", index=False)
    
    return output.getvalue()
