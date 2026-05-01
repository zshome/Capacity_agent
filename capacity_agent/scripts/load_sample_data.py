"""
Load Sample Data
================

生成可运行的演示数据,模拟 50 产品 × 200 tool group × 1500 step 的小型 fab。
执行后可以直接调用 Agent 测试。

Usage:
  python scripts/load_sample_data.py
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from engines.sample_data_utils import ensure_sample_files


# ============================================================
# Main
# ============================================================
def main():
    print("Generating sample data...")
    out_dir = Path(__file__).resolve().parent.parent / "data" / "sample"
    dataset = ensure_sample_files(out_dir)

    print(f"  Tool groups:    {len(dataset.tool_groups)}")
    print(f"  Products:       {len(dataset.products)}")
    print(f"  Route steps:    {len(dataset.routes)}")
    print(f"  OEE records:    {len(dataset.oee)}")
    print(f"  Demand records: {len(dataset.demand)}")

    print(f"\nSample data written to {out_dir}/")
    print("\n下一步:")
    print("  - 在 docker compose 启动后,运行: python scripts/load_to_db.py")
    print("  - 或直接用 demo 模式测试: python scripts/demo_e2e.py")


if __name__ == "__main__":
    main()
