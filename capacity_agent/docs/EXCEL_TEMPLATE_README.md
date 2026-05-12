# Excel 通用导入模板说明

当前模板已统一为一个通用工作簿版本，覆盖基础 RCCP、R6 投片需求、WIP、复杂 Path 和 Backup。

## Sheet 结构

| Sheet | 类型 | 说明 |
| --- | --- | --- |
| `README` | 说明 | 模板结构、用途、复杂 Path 填写规则 |
| `field_dictionary` | 说明 | 复杂 Path / Backup 关键字段字典 |
| `route_master` | 基础必填 | 产品路线、Path、工序、机台组、TC |
| `tool_groups` | 基础必填 | 机台组主数据 |
| `oee` | 基础必填 | OEE 与可用小时 |
| `demand_plan` | 基础必填 | R6/MPS 投片需求 |
| `wip_lot_detail` | 基础必填 | Lot 级 WIP 明细 |
| `tool_master` | 复杂 Path 成套填写 | 单台设备主数据 |
| `process_master` | 复杂 Path 成套填写 | 制程主数据与顺序 |
| `tool_process_capability` | 复杂 Path 成套填写 | 产品-制程-单台设备可跑能力矩阵 |
| `backup_path` | 复杂 Path 成套填写 | 主设备与 Backup 替代关系 |

## 导入规则

- 基础分析必须包含 5 张基础必填表：`route_master`、`tool_groups`、`oee`、`demand_plan`、`wip_lot_detail`。
- 如需验证不同 Path / Backup 场景，必须同时填写 4 张复杂 Path 表。
- 如果只填写了部分复杂 Path 表，系统会拒绝导入并提示缺失的 Sheet，避免半套数据导致错误结论。
- `tool_process_capability` 是复杂 Path 分配模型的核心输入，系统会由它生成产品-制程-单台设备可行性矩阵。

## 时间窗口建议

正式 R6 月度计划建议使用：

```text
R6-YYYY-MM
```

例如：

```text
R6-2026-05
```

周计划或演示数据仍可使用：

```text
2026-W17
```

## 复杂 Path 最小字段

`tool_master`：

```text
tool_id, tool_group_id, tool_name, status, uptime, loss_time
```

`process_master`：

```text
process_id, process_name, process_seq
```

`tool_process_capability`：

```text
product_id, process_id, tool_id, can_run, run_time_hr, batch_size
```

`backup_path`：

```text
primary_tool_id, backup_tool_id, process_id, enable_rule
```

## 验证方式

下载前端“Excel 模板”后，可直接导入模板示例数据。成功导入后，数据集指标应显示复杂 Path 已就绪，并能触发 `scenario_5` 与分配模型。
