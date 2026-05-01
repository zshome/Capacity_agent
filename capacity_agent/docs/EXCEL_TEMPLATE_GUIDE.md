# Excel 数据导入模板说明

## 概述

系统支持通过 Excel 工作簿导入产能规划数据。Excel 文件需包含 **4 个必需 Sheet**：

| Sheet 名称 | 用途 | 必需字段数 |
|-----------|------|-----------|
| `route_master` | 产品工艺路线 | 6 |
| `tool_groups` | 机台组信息 | 5 |
| `oee` | OEE 历史数据 | 7 |
| `demand_plan` | 需求计划 | 3 |

---

## Sheet 1: route_master (产品工艺路线)

### 字段定义

| 字段名 | 类型 | 说明 | 示例 |
|-------|------|------|------|
| `product_id` | 文本 | 产品编码 | `28nm_DRAM_A` |
| `path_id` | 文本 | 路径标识（多路径场景） | `default`, `P1`, `P2` |
| `step_seq` | 整数 | 工艺步骤序号 | 1, 2, 3... |
| `tool_group_id` | 文本 | 执行该步骤的机台组 | `LITHO_01` |
| `run_time_hr` | 数值 | 单次运行时间（小时/批次） | 0.5, 1.2, 2.8 |
| `batch_size` | 整数 | 批次大小（片/批） | 1, 25, 100 |

### 可选字段

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `step_name` | 文本 | 步骤名称 |
| `visit_count` | 整数 | 重复次数（默认 1） |
| `route_version` | 文本 | 路线版本（默认 `current`） |

### 数据规则

1. **步骤序号**：每个产品的 `step_seq` 应连续递增（1, 2, 3...）
2. **路径标识**：单路径产品使用 `default`；多路径产品使用不同 `path_id`
3. **运行时间**：单位为小时，支持小数
4. **批次大小**：
   - 光刻/量测通常为 1（单片处理）
   - 薄膜/清洗可能为 25 或 100（批次处理）

### 示例数据

```
product_id    | path_id | step_seq | tool_group_id | run_time_hr | batch_size
--------------|---------|----------|---------------|-------------|------------
28nm_DRAM_A   | default | 1        | LITHO_01      | 0.45        | 1
28nm_DRAM_A   | default | 2        | ETCH_03       | 0.32        | 1
28nm_DRAM_A   | default | 3        | DEPO_02       | 0.28        | 25
28nm_DRAM_A   | default | 4        | LITHO_01      | 0.45        | 1
28nm_DRAM_A   | default | 5        | WET_05        | 0.15        | 100
```

---

## Sheet 2: tool_groups (机台组信息)

### 字段定义

| 字段名 | 类型 | 说明 | 示例 |
|-------|------|------|------|
| `tool_group_id` | 文本 | 机台组编码（唯一） | `LITHO_01` |
| `tool_group_name` | 文本 | 机台组名称 | `光刻机台01` |
| `area` | 文本 | 工艺区域 | `LITHO`, `ETCH`, `DEPO` |
| `n_machines` | 整数 | 机台组内设备数量 | 8, 12, 4 |
| `nameplate_throughput_wph` | 数值 | 名牌产出率（片/小时） | 2.5, 3.8 |

### 可选字段

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `process_type` | 文本 | 工艺类型 |
| `is_active` | 布尔 | 是否活跃（默认 True） |

### 工艺区域标准命名

| 区域代码 | 中文名称 | 典型机台数 |
|---------|----------|-----------|
| `LITHO` | 光刻区 | 8-12 |
| `ETCH` | 刻蚀区 | 10-15 |
| `DEPO` | 薄膜区 | 8-12 |
| `CMP` | 抛光区 | 4-8 |
| `IMPL` | 注入区 | 4-6 |
| `DIFF` | 扩散区 | 6-10 |
| `METRO` | 量测区 | 3-6 |
| `WET` | 清洗区 | 6-10 |

### 示例数据

```
tool_group_id | tool_group_name | area  | n_machines | nameplate_throughput_wph
--------------|-----------------|-------|------------|--------------------------
LITHO_01      | 光刻机台01      | LITHO | 8          | 2.5
ETCH_03       | 刻蚀机台03      | ETCH  | 12         | 3.2
DEPO_02       | 薄膜机台02      | DEPO  | 6          | 4.5
CMP_01        | 抛光机台01      | CMP   | 4          | 2.8
```

---

## Sheet 3: oee (OEE 历史数据)

### 字段定义

| 字段名 | 类型 | 说明 | 示例 |
|-------|------|------|------|
| `fact_date` | 日期 | 事实日期 | `2026-04-01` |
| `tool_group_id` | 文本 | 机台组编码 | `LITHO_01` |
| `availability` | 数值 | 可用率（0-1） | 0.88, 0.92 |
| `performance` | 数值 | 性能率（0-1） | 0.90, 0.95 |
| `quality` | 数值 | 质量率（0-1） | 0.98, 0.99 |
| `oee` | 数值 | 综合OEE（0-1） | 0.78, 0.85 |
| `available_hours` | 数值 | 当日可用小时 | 168, 180 |

### OEE 计算公式

```
OEE = Availability × Performance × Quality
Available_Hours = N_Machines × 24 × Availability
```

### 数据规则

1. **日期范围**：建议至少包含 4 周历史数据（约 28-30 天）
2. **数值范围**：所有比率字段应在 0.0-1.0 之间
3. **机台覆盖**：每个日期应覆盖所有活跃机台组

### 示例数据

```
fact_date   | tool_group_id | availability | performance | quality | oee   | available_hours
------------|---------------|--------------|-------------|---------|-------|----------------
2026-04-01  | LITHO_01      | 0.88         | 0.92        | 0.98    | 0.80  | 168.0
2026-04-01  | ETCH_03       | 0.90         | 0.95        | 0.99    | 0.85  | 259.2
2026-04-02  | LITHO_01      | 0.85         | 0.90        | 0.97    | 0.74  | 163.2
```

---

## Sheet 4: demand_plan (需求计划)

### 字段定义

| 字段名 | 类型 | 说明 | 示例 |
|-------|------|------|------|
| `time_window` | 文本 | 时间窗口 | `2026-W16`, `2026-W17` |
| `product_id` | 文本 | 产品编码 | `28nm_DRAM_A` |
| `wafer_count` | 数值 | 计划产量（片） | 100, 500, 1000 |

### 可选字段

| 字段名 | 类型 | 说明 |
|-------|------|------|
| `priority` | 数值 | 优先级权重 |
| `contract_min` | 数值 | 合约最低量 |
| `market_max` | 数值 | 市场最大量 |
| `unit_profit` | 数值 | 单片利润 |
| `plan_version` | 文本 | 计划版本 |

### 时间窗口格式

推荐使用 ISO 周格式：`YYYY-WNN`

| 格式 | 示例 | 说明 |
|------|------|------|
| ISO周 | `2026-W16` | 2026年第16周 |
| 日期范围 | `2026-04-15~04-21` | 自定义范围 |
| 别名 | `this_week`, `next_week` | 系统自动解析 |

### 示例数据

```
time_window | product_id    | wafer_count | contract_min | market_max | unit_profit
------------|---------------|-------------|--------------|------------|------------
2026-W16    | 28nm_DRAM_A   | 1000        | 600          | 1500       | 150.0
2026-W16    | 64L_NAND_B    | 800         | 400          | 1200       | 80.0
2026-W17    | 28nm_DRAM_A   | 1200        | 600          | 1500       | 150.0
2026-W17    | 128L_NAND_A   | 500         | 200          | 800        | 120.0
```

---

## 导入流程

### 1. 准备 Excel 文件

```bash
# 文件命名建议
capacity_data_2026Q2.xlsx
product_A_route_master.xlsx
```

### 2. 检查数据完整性

- 所有4个Sheet都存在
- 每个Sheet包含必需字段
- 无空行或重复数据
- 数值字段无异常值

### 3. 通过前端导入

1. 打开 http://localhost:5174
2. 点击"导入 Excel 工作簿"
3. 选择准备好的 Excel 文件
4. 系统自动验证并导入

### 4. 验证导入结果

导入成功后，系统返回：

```json
{
  "dataset_id": "excel-abc12345",
  "dataset_name": "capacity_data_2026Q2.xlsx",
  "source_type": "excel",
  "n_products": 5,
  "n_tool_groups": 20,
  "n_routes": 400,
  "n_oee_records": 600,
  "n_demand_records": 20
}
```

---

## 常见问题

### Q1: Sheet 名称不匹配？

系统支持以下别名：
- `route_master` → `Route`, `Routes`, `路线`
- `tool_groups` → `Tools`, `机台`
- `oee` → `OEE`, `OeeData`
- `demand_plan` → `Demand`, `需求`

### Q2: 字段名称不匹配？

确保字段名完全匹配（不区分大小写）。系统不支持字段别名。

### Q3: 导入失败：缺少列？

检查错误信息中指定的缺失列，添加对应字段。

### Q4: 数据量限制？

建议：
- 产品数 < 50
- 机台组 < 100
- 路线步骤 < 5000 行
- OEE历史 < 60 天

---

## 数据验证规则

| 字段 | 验证规则 |
|------|----------|
| `step_seq` | 正整数，连续递增 |
| `run_time_hr` | > 0，无上限 |
| `batch_size` | ≥ 1，整数 |
| `availability` | 0.0 ~ 1.0 |
| `performance` | 0.0 ~ 1.0 |
| `quality` | 0.0 ~ 1.0 |
| `wafer_count` | ≥ 0 |

---

## 模板文件下载

可使用以下命令生成模板文件：

```python
python scripts/generate_excel_template.py
```

模板文件将输出至：`data/templates/capacity_import_template.xlsx`