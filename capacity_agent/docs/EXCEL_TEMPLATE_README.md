# Excel 导入模板说明（中文版）

## 一、文件结构

导入的 Excel 工作簿必须包含以下 **4 个 Sheet**：

| Sheet 名称 | 中文名称 | 必需字段 |
|-----------|----------|----------|
| `route_master` | 产品路线 | 6个字段 |
| `tool_groups` | 机台组 | 5个字段 |
| `oee` | OEE数据 | 7个字段 |
| `demand_plan` | 需求计划 | 3个字段 |

---

## 二、各Sheet字段说明

### 1. route_master（产品路线）

**用途**：定义每个产品的工艺流程，包括各步骤使用的机台和运行时间。

| 字段名 | 中文名 | 数据类型 | 说明 | 示例 |
|-------|--------|----------|------|------|
| product_id | 产品编码 | 文本 | 产品唯一标识 | `28nm_DRAM_A` |
| path_id | 路径标识 | 文本 | 多路径时区分不同路线 | `default` |
| step_seq | 步骤序号 | 整数 | 工艺步骤顺序号 | 1, 2, 3 |
| tool_group_id | 机台组编码 | 文本 | 执行该步骤的机台组 | `LITHO_01` |
| run_time_hr | 运行时间 | 数值 | 单次运行小时数 | 0.45 |
| batch_size | 批次大小 | 整数 | 每批处理片数 | 1, 25 |

**示例**：
```
product_id: 28nm_DRAM_A
path_id: default
step_seq: 1
tool_group_id: LITHO_01
run_time_hr: 0.45
batch_size: 1
```

---

### 2. tool_groups（机台组）

**用途**：定义工厂内各机台组的基本信息。

| 字段名 | 中文名 | 数据类型 | 说明 | 示例 |
|-------|--------|----------|------|------|
| tool_group_id | 机台组编码 | 文本 | 机台组唯一标识 | `LITHO_01` |
| tool_group_name | 机台组名称 | 文本 | 机台组显示名称 | `光刻机台01` |
| area | 工艺区域 | 文本 | 所属工艺区 | `LITHO` |
| n_machines | 设备数量 | 整数 | 机台组内设备数 | 8 |
| nameplate_throughput_wph | 名牌产出率 | 数值 | 片/小时 | 2.5 |

**工艺区域代码**：
- `LITHO` - 光刻
- `ETCH` - 刻蚀
- `DEPO` - 薄膜
- `CMP` - 抛光
- `IMPL` - 注入
- `DIFF` - 扩散
- `METRO` - 量测
- `WET` - 清洗

---

### 3. oee（OEE历史数据）

**用途**：记录机台组的设备效率历史数据，用于计算可用产能。

| 字段名 | 中文名 | 数据类型 | 说明 | 示例 |
|-------|--------|----------|------|------|
| fact_date | 日期 | 日期 | 数据日期 | `2026-04-01` |
| tool_group_id | 机台组编码 | 文本 | 对应机台组 | `LITHO_01` |
| availability | 可用率 | 数值(0-1) | 设备可用率 | 0.88 |
| performance | 性能率 | 数值(0-1) | 设备性能率 | 0.92 |
| quality | 质量率 | 数值(0-1) | 产品质量率 | 0.98 |
| oee | 综合OEE | 数值(0-1) | A×P×Q | 0.80 |
| available_hours | 可用小时 | 数值 | 当日可用小时数 | 168 |

**计算公式**：
```
OEE = Availability × Performance × Quality
Available_Hours = N_Machines × 24 × Availability
```

**数据要求**：
- 建议提供至少 **4周（28天）** 的历史数据
- 每个日期应覆盖所有活跃机台组

---

### 4. demand_plan（需求计划）

**用途**：定义各时间窗口的产品需求计划。

| 字段名 | 中文名 | 数据类型 | 说明 | 示例 |
|-------|--------|----------|------|------|
| time_window | 时间窗口 | 文本 | 计划周期 | `2026-W16` |
| product_id | 产品编码 | 文本 | 产品唯一标识 | `28nm_DRAM_A` |
| wafer_count | 计划产量 | 数值 | 需求片数 | 1000 |

**可选字段**：
| 字段名 | 中文名 | 说明 |
|-------|--------|------|
| contract_min | 合约最低量 | 必须完成的最低产量 |
| market_max | 市场最大量 | 市场需求上限 |
| unit_profit | 单片利润 | 用于优化计算 |
| priority | 优先级 | 产品优先级权重 |

**时间窗口格式**：
推荐使用 ISO 周格式：`YYYY-WNN`（如 `2026-W16` 表示2026年第16周）

---

## 三、导入步骤

### 1. 准备数据文件

下载模板文件：`capacity_import_template.xlsx`

修改为实际生产数据：
- 更新产品编码和名称
- 更新机台组信息
- 填入OEE历史数据
- 填入需求计划

### 2. 前端导入

1. 打开 http://localhost:5174
2. 在左侧"数据源"面板点击 **"导入 Excel 工作簿"**
3. 选择准备好的 Excel 文件
4. 系统自动验证并导入

### 3. 验证结果

导入成功后显示：
- 数据集ID
- 产品数量
- 机台组数量
- 路线记录数
- OEE记录数
- 需求记录数

---

## 四、常见错误

### 错误1：缺少Sheet

**提示**：`Excel missing required sheets: route_master`

**解决**：检查Excel是否包含全部4个Sheet

### 错误2：缺少列

**提示**：`Sheet 'route_master' missing columns: batch_size`

**解决**：在对应Sheet中添加缺失的列

### 错误3：数据格式错误

**提示**：数值字段包含非数值内容

**解决**：确保数值列只包含数字，日期列使用标准格式

---

## 五、模板文件位置

| 文件 | 路径 |
|------|------|
| Excel模板 | `capacity_agent/docs/capacity_import_template.xlsx` |
| 说明文档 | `capacity_agent/docs/EXCEL_TEMPLATE_GUIDE.md` |
| 生成脚本 | `capacity_agent/scripts/generate_excel_template.py` |

---

## 六、快速测试

使用内置模板测试导入功能：

```bash
# 重新生成模板（可选）
python scripts/generate_excel_template.py

# 模板位置
docs/capacity_import_template.xlsx
```

导入后可立即进行产能分析测试。