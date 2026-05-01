"""
Load Sample Data into PostgreSQL
=================================

把 data/sample/*.parquet 导入到 PG 中。
前提: docker compose up postgres 已经启动。

Usage:
  python scripts/load_to_db.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = os.getenv("PG_PORT", "5432")
PG_USER = os.getenv("PG_USER", "capacity")
PG_PASSWORD = os.getenv("PG_PASSWORD", "capacity_dev")
PG_DB = os.getenv("PG_DB", "capacity_db")

CONN_URL = f"postgresql://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"

SAMPLE_DIR = Path(__file__).parent.parent / "data" / "sample"
SCHEMA_FILE = Path(__file__).parent.parent / "data" / "schemas" / "schema.sql"


def main():
    print(f"Connecting to {PG_HOST}:{PG_PORT}/{PG_DB}...")
    engine = create_engine(CONN_URL)

    # 1. 执行 schema
    print("Applying schema...")
    with engine.begin() as conn:
        with open(SCHEMA_FILE) as f:
            for stmt in f.read().split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        conn.execute(text(stmt))
                    except Exception as e:
                        print(f"  Warning: {e}")

    # 2. 加载 parquet 文件
    files_to_tables = {
        "dim_tool_group.parquet": ("capacity.dim_tool_group", "replace"),
        "dim_product.parquet": ("capacity.dim_product", "replace"),
        "dim_route.parquet": ("capacity.dim_route", "replace"),
        "fact_oee_daily.parquet": ("capacity.fact_oee_daily", "replace"),
        "fact_demand_plan.parquet": ("capacity.fact_demand_plan", "replace"),
    }

    for filename, (table, mode) in files_to_tables.items():
        fpath = SAMPLE_DIR / filename
        if not fpath.exists():
            print(f"  Skipping {filename} (not found)")
            continue
        df = pd.read_parquet(fpath)
        # 处理: dim_route 没有 route_id (会由 SERIAL 自增)
        if filename == "dim_route.parquet" and "route_id" in df.columns:
            df = df.drop(columns=["route_id"])

        schema, tbl = table.split(".")
        df.to_sql(tbl, engine, schema=schema, if_exists=mode, index=False, chunksize=1000)
        print(f"  Loaded {len(df)} rows into {table}")

    print("\nDone.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nFailed: {e}")
        print("Make sure PostgreSQL is running:")
        print("  cd docker && docker compose up -d postgres")
        sys.exit(1)
