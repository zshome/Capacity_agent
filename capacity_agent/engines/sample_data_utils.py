"""
Sample data utilities for the Capacity Agent demo environment.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd


AREAS = ["LITHO", "ETCH", "DEPO", "CMP", "IMPL", "DIFF", "METRO", "WET"]
TOOL_GROUPS_PER_AREA = {
    "LITHO": 8,
    "ETCH": 12,
    "DEPO": 10,
    "CMP": 6,
    "IMPL": 5,
    "DIFF": 8,
    "METRO": 4,
    "WET": 7,
}


@dataclass
class SampleDataset:
    tool_groups: pd.DataFrame
    products: pd.DataFrame
    routes: pd.DataFrame
    oee: pd.DataFrame
    demand: pd.DataFrame


def _seed_rng() -> None:
    random.seed(42)
    np.random.seed(42)


def gen_tool_groups() -> pd.DataFrame:
    rows = []
    for area, count in TOOL_GROUPS_PER_AREA.items():
        for i in range(1, count + 1):
            tg_id = f"{area}_{i:02d}"
            rows.append(
                {
                    "tool_group_id": tg_id,
                    "tool_group_name": f"{area} Tool {i}",
                    "area": area,
                    "process_type": area,
                    "n_machines": random.randint(2, 12),
                    "nameplate_throughput_wph": round(random.uniform(1.5, 5.0), 2),
                    "is_active": True,
                }
            )
    return pd.DataFrame(rows)


def gen_products() -> pd.DataFrame:
    families = [
        ("DRAM", "28nm"),
        ("DRAM", "20nm"),
        ("DRAM", "1z"),
        ("NAND", "64L"),
        ("NAND", "128L"),
        ("NAND", "176L"),
        ("NAND", "232L"),
        ("LOGIC", "14nm"),
        ("LOGIC", "7nm"),
    ]
    rows = []
    for fam, node in families:
        for variant in ["A", "B", "C"]:
            pid = f"{node}_{fam}_{variant}"
            rows.append(
                {
                    "product_id": pid,
                    "product_name": f"{fam} {node} variant {variant}",
                    "product_family": fam,
                    "technology_node": node,
                    "is_active": True,
                }
            )
    return pd.DataFrame(rows)


def gen_routes(products: pd.DataFrame, tool_groups: pd.DataFrame, steps_per_product: int = 80) -> pd.DataFrame:
    rows = []
    tg_by_area = {
        area: tool_groups[tool_groups["area"] == area]["tool_group_id"].tolist()
        for area in tool_groups["area"].unique()
    }
    pattern = [
        "DEPO",
        "LITHO",
        "ETCH",
        "WET",
        "METRO",
        "DEPO",
        "LITHO",
        "ETCH",
        "WET",
        "METRO",
        "IMPL",
        "DIFF",
        "CMP",
        "METRO",
    ]
    area_ptrs = {area: 0 for area in tg_by_area}

    for pid in products["product_id"]:
        n_cycles = max(2, steps_per_product // len(pattern))
        seq = 0
        for _cycle in range(n_cycles):
            for area in pattern:
                tools = tg_by_area.get(area, [])
                if not tools:
                    continue
                seq += 1
                idx = (area_ptrs[area] + random.randint(0, 1)) % len(tools)
                tg = tools[idx]
                area_ptrs[area] = (area_ptrs[area] + 1) % len(tools)
                run_time = round(np.random.lognormal(mean=np.log(0.4), sigma=0.3), 4)
                batch = 1 if area in ("LITHO", "METRO", "ETCH", "CMP") else random.choice([1, 25, 100])
                rows.append(
                    {
                        "product_id": pid,
                        "path_id": "default",
                        "step_seq": seq,
                        "step_name": f"{area}_step_{seq:03d}",
                        "tool_group_id": tg,
                        "run_time_hr": run_time,
                        "batch_size": batch,
                        "visit_count": 1,
                        "route_version": "current",
                    }
                )
    return pd.DataFrame(rows)


def gen_oee_history(tool_groups: pd.DataFrame, n_days: int = 60) -> pd.DataFrame:
    rows = []
    today = date.today()
    for d in range(n_days):
        fact_date = today - timedelta(days=d)
        for tg_id in tool_groups["tool_group_id"]:
            avail = round(np.clip(np.random.normal(0.88, 0.05), 0.6, 0.99), 4)
            perf = round(np.clip(np.random.normal(0.92, 0.04), 0.7, 0.99), 4)
            qual = round(np.clip(np.random.normal(0.98, 0.01), 0.9, 1.0), 4)
            n_machines = int(tool_groups[tool_groups["tool_group_id"] == tg_id]["n_machines"].iloc[0])
            available_hours = round(n_machines * 24 * avail, 2)
            rows.append(
                {
                    "fact_date": fact_date,
                    "tool_group_id": tg_id,
                    "availability": avail,
                    "performance": perf,
                    "quality": qual,
                    "oee": round(avail * perf * qual, 4),
                    "available_hours": available_hours,
                }
            )
    return pd.DataFrame(rows)


def gen_demand_plan(products: pd.DataFrame, n_weeks: int = 4) -> pd.DataFrame:
    rows = []
    today = date.today()
    iso_year, iso_week, _ = today.isocalendar()
    for w in range(n_weeks):
        week_id = f"{iso_year}-W{(iso_week + w):02d}"
        for pid in products["product_id"]:
            base = random.randint(40, 120)
            qty = max(0, int(base * np.random.uniform(0.85, 1.15)))
            rows.append(
                {
                    "plan_version": "v1",
                    "time_window": week_id,
                    "product_id": pid,
                    "wafer_count": qty,
                    "priority": round(np.random.uniform(0.5, 2.0), 2),
                    "contract_min": int(qty * 0.6),
                    "market_max": int(qty * 1.5),
                    "unit_profit": round(np.random.uniform(50, 300), 2),
                }
            )
    return pd.DataFrame(rows)


def generate_sample_dataset() -> SampleDataset:
    _seed_rng()
    tool_groups = gen_tool_groups()
    products = gen_products()
    routes = gen_routes(products, tool_groups, steps_per_product=80)
    oee = gen_oee_history(tool_groups, n_days=60)
    demand = gen_demand_plan(products, n_weeks=4)
    return SampleDataset(tool_groups=tool_groups, products=products, routes=routes, oee=oee, demand=demand)


def ensure_sample_files(out_dir: Path) -> SampleDataset:
    out_dir.mkdir(parents=True, exist_ok=True)
    parquet_files = [
        out_dir / "dim_tool_group.parquet",
        out_dir / "dim_product.parquet",
        out_dir / "dim_route.parquet",
        out_dir / "fact_oee_daily.parquet",
        out_dir / "fact_demand_plan.parquet",
    ]
    if all(path.exists() for path in parquet_files):
        return load_sample_files(out_dir)

    dataset = generate_sample_dataset()
    dataset.tool_groups.to_parquet(out_dir / "dim_tool_group.parquet", index=False)
    dataset.products.to_parquet(out_dir / "dim_product.parquet", index=False)
    dataset.routes.to_parquet(out_dir / "dim_route.parquet", index=False)
    dataset.oee.to_parquet(out_dir / "fact_oee_daily.parquet", index=False)
    dataset.demand.to_parquet(out_dir / "fact_demand_plan.parquet", index=False)
    dataset.tool_groups.to_csv(out_dir / "dim_tool_group.csv", index=False)
    dataset.routes.head(50).to_csv(out_dir / "dim_route_sample.csv", index=False)
    return dataset


def load_sample_files(out_dir: Path) -> SampleDataset:
    return SampleDataset(
        tool_groups=pd.read_parquet(out_dir / "dim_tool_group.parquet"),
        products=pd.read_parquet(out_dir / "dim_product.parquet"),
        routes=pd.read_parquet(out_dir / "dim_route.parquet"),
        oee=pd.read_parquet(out_dir / "fact_oee_daily.parquet"),
        demand=pd.read_parquet(out_dir / "fact_demand_plan.parquet"),
    )
