"""
生成样例 WIP 数据用于 Output 视角测试
"""
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

# 输出路径
output_dir = r'C:\Users\stark\AI_Project\Capacity_agent\Capacity_agent\capacity_agent\data\sample'

# 产品列表（与现有样例数据一致）
products = [
    '28nm_DRAM_A', '28nm_DRAM_B', '28nm_DRAM_C',
    '20nm_DRAM_A', '20nm_DRAM_B', '20nm_DRAM_C',
    '1z_DRAM_A', '1z_DRAM_B', '1z_DRAM_C',
    '64L_NAND_A', '64L_NAND_B', '64L_NAND_C',
    '176L_NAND_A', '176L_NAND_B', '176L_NAND_C',
    '232L_NAND_A', '232L_NAND_B', '232L_NAND_C',
    '7nm_LOGIC_A', '7nm_LOGIC_B', '7nm_LOGIC_C',
    '14nm_LOGIC_A', '14nm_LOGIC_B', '14nm_LOGIC_C',
]

# 机台组列表
tool_groups = [
    'LITHO_01', 'LITHO_02', 'LITHO_03', 'LITHO_04', 'LITHO_05', 'LITHO_06',
    'ETCH_01', 'ETCH_02', 'ETCH_03', 'ETCH_04', 'ETCH_05', 'ETCH_06',
    'DEPO_01', 'DEPO_02', 'DEPO_03', 'DEPO_04', 'DEPO_05', 'DEPO_06',
    'CMP_01', 'CMP_02', 'CMP_03', 'CMP_04', 'CMP_05', 'CMP_06',
    'IMPL_01', 'IMPL_02', 'IMPL_03', 'IMPL_04',
    'WET_01', 'WET_02', 'WET_03', 'WET_04',
    'DIFF_01', 'DIFF_02', 'DIFF_03',
    'METRO_01', 'METRO_02', 'METRO_03',
]

# Cycle Time（天）
ct_days = {
    'DRAM': 100,
    'NAND': 110,
    'LOGIC': 90,
}

def get_product_family(product_id):
    if 'DRAM' in product_id:
        return 'DRAM'
    elif 'NAND' in product_id:
        return 'NAND'
    else:
        return 'LOGIC'

def generate_lot_id():
    """生成 Lot ID"""
    return f"L{np.random.randint(100000, 999999)}"

# 生成 WIP Lot 数据
np.random.seed(42)

n_lots = 150  # 150 个 Lot

wip_lots = []

for i in range(n_lots):
    # 随机选择产品
    product_id = np.random.choice(products)
    family = get_product_family(product_id)
    ct = ct_days[family]
    
    # 随机生成当前工序位置（分布在各个阶段）
    # 存储芯片约 300 步工序
    total_steps = 300
    
    # 分布在不同完成度区间
    pct_range = np.random.choice([10, 20, 30, 40, 50, 60, 70, 80, 85, 90], 
                                  p=[0.05, 0.08, 0.10, 0.12, 0.15, 0.12, 0.10, 0.12, 0.08, 0.08])
    
    current_step = int(total_steps * pct_range / 100)
    remaining_steps = total_steps - current_step
    
    # Lot 晶圆数（25 片标准）
    wafer_count = 25
    good_wafers = int(wafer_count * np.random.uniform(0.92, 0.98))
    
    # 当前机台组
    current_tg = np.random.choice(tool_groups)
    
    # Lot 状态
    lot_status = np.random.choice(['WAIT', 'RUN', 'HOLD', 'MOVE'], p=[0.60, 0.30, 0.05, 0.05])
    
    # 等待时间
    wait_hours = 0
    if lot_status == 'WAIT':
        wait_hours = np.random.uniform(1, 48)
    
    # 预计完成周
    remaining_days = ct * remaining_steps / total_steps
    current_week = datetime.now().isocalendar()[1]
    completion_week = current_week + int(remaining_days / 7)
    completion_week_str = f"2026-W{completion_week}"
    
    # 剩余小时（估算）
    remaining_hours = remaining_steps * 0.3  # 平均每步 0.3 小时
    
    # 投入周
    input_week = current_week - int(ct / 7)
    input_week_str = f"2026-W{input_week}"
    
    wip_lots.append({
        'lot_id': generate_lot_id(),
        'product_id': product_id,
        'current_step_seq': current_step,
        'current_step_name': f"OP{current_step:03d}_{current_tg}",
        'current_tool_group': current_tg,
        'lot_status': lot_status,
        'wafer_count': wafer_count,
        'good_wafer_count': good_wafers,
        'percent_complete': pct_range,
        'remaining_steps': remaining_steps,
        'remaining_hours': remaining_hours,
        'est_completion_week': completion_week_str,
        'est_completion_ts': (datetime.now() + timedelta(days=remaining_days)).isoformat(),
        'wait_hours_so_far': wait_hours,
        'queue_position': np.random.randint(1, 20) if lot_status == 'WAIT' else 0,
        'queue_entry_ts': (datetime.now() - timedelta(hours=wait_hours)).isoformat() if wait_hours > 0 else None,
        'start_ts': (datetime.now() - timedelta(days=ct * pct_range / 100)).isoformat(),
        'input_week': input_week_str,
        'snapshot_ts': datetime.now().isoformat(),
    })

# 转为 DataFrame
wip_df = pd.DataFrame(wip_lots)

# 保存为 Parquet
output_path = os.path.join(output_dir, 'fact_wip_lot_detail.parquet')
wip_df.to_parquet(output_path, index=False)

print(f"生成 WIP Lot 数据: {len(wip_lots)} 条")
print(f"保存路径: {output_path}")

# 统计
print("\n=== 按完成度分布 ===")
print(wip_df.groupby('percent_complete')['lot_id'].count())

print("\n=== 按产品分布 ===")
print(wip_df.groupby('product_id')['wafer_count'].sum())

print("\n=== 按机台组分布 ===")
print(wip_df.groupby('current_tool_group')['lot_id'].count())

print("\n=== 按状态分布 ===")
print(wip_df.groupby('lot_status')['lot_id'].count())

# 同时生成产出目标样例
output_targets = []
for week_offset in range(4):
    week_num = datetime.now().isocalendar()[1] + week_offset + 4  # 未来 4-7 周
    week_str = f"2026-W{week_num}"
    
    # 每周产出目标
    for product_id in np.random.choice(products, 10, replace=False):
        target_wafers = np.random.randint(50, 200)
        output_targets.append({
            'plan_version': 'v1',
            'time_window': week_str,
            'product_id': product_id,
            'target_wafers': target_wafers,
            'priority': np.random.uniform(0.5, 2.0),
            'contract_min': int(target_wafers * 0.6),
            'market_max': int(target_wafers * 1.5),
            'unit_profit': np.random.uniform(50, 300),
            'input_window': f"2026-W{week_num - 14}",  # CT 约 14 周
            'ct_days': ct_days[get_product_family(product_id)],
        })

output_target_df = pd.DataFrame(output_targets)
output_target_path = os.path.join(output_dir, 'fact_output_target.parquet')
output_target_df.to_parquet(output_target_path, index=False)

print(f"\n生成产出目标数据: {len(output_targets)} 条")
print(f"保存路径: {output_target_path}")

# Cycle Time 基准
ct_data = []
for product_id in products:
    family = get_product_family(product_id)
    ct_data.append({
        'product_id': product_id,
        'avg_cycle_time_days': ct_days[family],
        'min_cycle_time_days': ct_days[family] - 10,
        'max_cycle_time_days': ct_days[family] + 20,
        'ct_variation_pct': 15.0,
        'source': 'historical_avg',
    })

ct_df = pd.DataFrame(ct_data)
ct_path = os.path.join(output_dir, 'dim_product_cycle_time.parquet')
ct_df.to_parquet(ct_path, index=False)

print(f"\n生成 CT 基准数据: {len(ct_data)} 条")
print(f"保存路径: {ct_path}")