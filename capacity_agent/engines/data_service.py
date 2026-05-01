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
    },
    "wip_lot_detail": {
        "lot_id",
        "product_id",
        "current_step_seq",
        "wafer_count",
        "percent_complete",
    },
}


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


class DatasetRegistry:
    def __init__(self) -> None:
        self.sample_dir = Path(__file__).resolve().parent.parent / "data" / "sample"
        self._datasets: dict[str, DatasetBundle] = {}
        self._sample_bundle = self._load_or_generate_sample_bundle()

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
        )

    def list_datasets(self) -> list[dict[str, Any]]:
        datasets = [self._bundle_summary(self._sample_bundle)]
        datasets.extend(self._bundle_summary(bundle) for bundle in self._datasets.values())
        return datasets

    def get_dataset(self, dataset_id: str | None = None) -> DatasetBundle:
        if not dataset_id or dataset_id == "sample":
            return self._sample_bundle
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

        routes = normalized["route_master"].copy()
        tool_groups = normalized["tool_groups"].copy()
        oee = normalized["oee"].copy()
        demand = normalized["demand_plan"].copy()
        wip_lot_detail = normalized["wip_lot_detail"].copy()

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
        return {
            "dataset_id": bundle.dataset_id,
            "n_wip_lots": int(len(wip_df)),
            "total_wip_wafers": float(wip_df["wafer_count"].sum()) if "wafer_count" in wip_df.columns else 0.0,
            "records": wip_df.to_dict(orient="records"),
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
        }


DATASET_REGISTRY = DatasetRegistry()


def generate_excel_template() -> bytes:
    """
    生成 Excel 导入模板文件
    
    包含5个Sheet: route_master, tool_groups, oee, demand_plan, wip_lot_detail
    每个Sheet包含必需字段标题和示例数据行
    """
    # Sheet 1: route_master (产品工艺路线)
    route_master_data = {
        "product_id": ["28nm_DRAM_A", "28nm_DRAM_A", "28nm_DRAM_A", "28nm_DRAM_A", "28nm_DRAM_A"],
        "path_id": ["default", "default", "default", "default", "default"],
        "step_seq": [1, 2, 3, 4, 5],
        "tool_group_id": ["LITHO_01", "ETCH_03", "DEPO_02", "LITHO_01", "WET_05"],
        "run_time_hr": [0.45, 0.32, 0.28, 0.45, 0.15],
        "batch_size": [1, 1, 25, 1, 100],
        # 可选字段示例
        "step_name": ["光刻步骤1", "刻蚀步骤1", "薄膜沉积", "光刻步骤2", "清洗"],
        "visit_count": [1, 1, 1, 1, 1],
        "route_version": ["current", "current", "current", "current", "current"],
    }
    route_master_df = pd.DataFrame(route_master_data)
    
    # Sheet 2: tool_groups (机台组信息)
    tool_groups_data = {
        "tool_group_id": ["LITHO_01", "ETCH_03", "DEPO_02", "CMP_01", "WET_05"],
        "tool_group_name": ["光刻机台01", "刻蚀机台03", "薄膜机台02", "抛光机台01", "清洗机台05"],
        "area": ["LITHO", "ETCH", "DEPO", "CMP", "WET"],
        "n_machines": [8, 12, 6, 4, 8],
        "nameplate_throughput_wph": [2.5, 3.2, 4.5, 2.8, 6.0],
        # 可选字段示例
        "process_type": ["光刻", "刻蚀", "薄膜沉积", "CMP抛光", "湿法清洗"],
        "is_active": [True, True, True, True, True],
    }
    tool_groups_df = pd.DataFrame(tool_groups_data)
    
    # Sheet 3: oee (OEE 历史数据) - 提供最近7天示例
    oee_records = []
    base_date = pd.Timestamp("2026-04-20")
    for day_offset in range(7):
        date = base_date + pd.Timedelta(days=day_offset)
        for tg_id, base_avail, base_perf, base_qual in [
            ("LITHO_01", 0.88, 0.92, 0.98),
            ("ETCH_03", 0.90, 0.95, 0.99),
            ("DEPO_02", 0.87, 0.93, 0.97),
            ("CMP_01", 0.85, 0.90, 0.96),
            ("WET_05", 0.92, 0.94, 0.98),
        ]:
            # 添加一些随机波动
            avail = base_avail + (day_offset % 3 - 1) * 0.02
            perf = base_perf + (day_offset % 2) * 0.01
            qual = base_qual
            oee = round(avail * perf * qual, 4)
            n_machines_lookup = {"LITHO_01": 8, "ETCH_03": 12, "DEPO_02": 6, "CMP_01": 4, "WET_05": 8}
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
        "time_window": ["2026-W17", "2026-W17", "2026-W17", "2026-W18", "2026-W18", "2026-W18"],
        "product_id": ["28nm_DRAM_A", "64L_NAND_B", "128L_NAND_A", "28nm_DRAM_A", "64L_NAND_B", "128L_NAND_A"],
        "wafer_count": [1000, 800, 500, 1200, 900, 600],
        # 可选字段示例
        "priority": [1, 2, 3, 1, 2, 3],
        "contract_min": [600, 400, 200, 600, 400, 200],
        "market_max": [1500, 1200, 800, 1500, 1200, 800],
        "unit_profit": [150.0, 80.0, 120.0, 150.0, 80.0, 120.0],
        "plan_version": ["v1.0", "v1.0", "v1.0", "v1.0", "v1.0", "v1.0"],
    }
    demand_plan_df = pd.DataFrame(demand_plan_data)

    # Sheet 5: wip_lot_detail (WIP Lot 明细)
    wip_lot_detail_data = {
        "lot_id": ["LOT0001", "LOT0002", "LOT0003", "LOT0004"],
        "product_id": ["28nm_DRAM_A", "64L_NAND_B", "128L_NAND_A", "28nm_DRAM_A"],
        "current_step_seq": [3, 2, 4, 5],
        "wafer_count": [25, 25, 25, 25],
        "percent_complete": [35, 55, 78, 88],
        # 可选字段示例
        "lot_status": ["WAIT", "RUN", "WAIT", "MOVE"],
        "good_wafer_count": [24, 25, 24, 25],
        "wait_hours_so_far": [12.0, 4.5, 26.0, 1.0],
        "input_week": ["2026-W05", "2026-W06", "2026-W04", "2026-W05"],
    }
    wip_lot_detail_df = pd.DataFrame(wip_lot_detail_data)
    
    # 写入 Excel 文件
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        route_master_df.to_excel(writer, sheet_name="route_master", index=False)
        tool_groups_df.to_excel(writer, sheet_name="tool_groups", index=False)
        oee_df.to_excel(writer, sheet_name="oee", index=False)
        demand_plan_df.to_excel(writer, sheet_name="demand_plan", index=False)
        wip_lot_detail_df.to_excel(writer, sheet_name="wip_lot_detail", index=False)
    
    return output.getvalue()
