# Capacity Agent

面向存储芯片制造场景的产能分析与计划优化工作台。项目把 `RCCP`、瓶颈识别、`LP` 优化、`DES` 校验、`Output` 视角规划和 `WIP-aware` 生产计划串成一套可落地的工程化原型，方便开发者在本地快速联调、扩展和验证。

## What It Does

- `WIP-aware RCCP`：将新计划负载与在制品后续负载合并，评估计划可执行性
- `Bottleneck Analysis`：识别关键机台组、负载热点和潜在约束
- `LP Optimizer`：在产能约束下给出需求收敛与投片优化建议
- `Production Plan`：输出原计划与 WIP 校正后计划的双口径结果
- `Output Perspective`：从目标产出反推投入，并联动 WIP 贡献与剩余负载
- `What-if`：支持调机、增产、需求变化等情景推演
- `Agent Chat`：通过自然语言查询计划、瓶颈和优化结论

## Why This Repo

这个仓库更偏“开发可验证原型”而不是纯算法样例，重点在于把几个常见的产能分析模块放进同一个工作台：

- 后端计算引擎可单独调 API
- 前端工作台可直接联调演示
- 支持 Excel 工作簿导入
- 已补齐存储厂场景下更关键的 `WIP-aware` 主判定链路

## Architecture

```text
Capacity_agent/
├── capacity_agent/
│   ├── app/                  # Agent API, LLM client, prompts, tools
│   ├── engines/              # RCCP, LP, bottleneck, DES, production plan
│   ├── data/                 # Sample parquet, schema, dbt artifacts
│   ├── docs/                 # Domain design docs and templates
│   └── scripts/              # Sample data, Excel template, demo scripts
├── frontend/
│   ├── src/App.jsx           # Main workbench UI
│   ├── src/api.js            # Frontend API client
│   └── src/styles.css        # Workbench layout and visual system
└── docs/
    └── plans/                # Review docs and planning notes
```

### Main Runtime Paths

- `capacity_agent/engines/server.py`
  Engine service entry for RCCP, LP, bottleneck, DES, production plan, dataset import
- `capacity_agent/app/main.py`
  Agent service entry for chat, tool orchestration, and LLM configuration
- `frontend/src/App.jsx`
  Unified workbench for dataset import, analysis, planning, what-if, and agent chat

## Core Planning Flow

```text
Excel / Sample Data
        ↓
Dataset Parsing
        ↓
RCCP + Bottleneck + DES
        ↓
WIP-aware Production Plan
        ↓
LP Demand Adjustment / Input Suggestion
        ↓
Output Perspective / What-if / Agent Explanation
```

## Quick Start

### 1. Backend

```bash
cd Capacity_agent
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi uvicorn pandas pyomo highspy openpyxl simpy python-multipart
```

启动引擎服务：

```bash
cd capacity_agent
uvicorn engines.server:app --host 0.0.0.0 --port 8002 --reload
```

启动 Agent 服务：

```bash
cd capacity_agent
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. Frontend

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

默认联调地址：

- `Engine`: `http://127.0.0.1:8002`
- `Agent`: `http://127.0.0.1:8000`
- `Frontend`: `http://127.0.0.1:5173`

## Excel Import

前端支持导入 Excel 工作簿。当前模板要求 5 个必填 sheet：

- `route_master`
- `tool_groups`
- `oee`
- `demand_plan`
- `wip_lot_detail`

其中 `wip_lot_detail` 为存储厂场景下的关键输入，用于：

- 计算 WIP 对目标产出的贡献
- 计算 WIP 对后续瓶颈机台的占机负载
- 驱动 WIP-aware RCCP 与 Production Plan

模板生成和导入解析相关代码：

- `capacity_agent/scripts/generate_excel_template.py`
- `capacity_agent/engines/data_service.py`

## Key Modules

### `capacity_agent/engines/rccp.py`

负责 `RCCP` 主计算，已经支持把 `WIP remaining load` 并入机台组负载评估。

### `capacity_agent/engines/lp_optimizer.py`

负责 `LP` 优化与需求收敛，支持在基础产能约束上叠加 WIP 占用。

### `capacity_agent/engines/production_plan.py`

负责双口径生产计划输出，包括：

- 原计划可行性
- WIP 校正后可行性
- 建议投片量
- 需求削减量

### `frontend/src/App.jsx`

当前前端已重构为工作台式布局，核心视图包括：

- `总览`
- `生产计划`
- `Output / What-if`
- `Agent`

## Validation

当前仓库至少做过以下本地验证：

- Python 代码可通过 `py_compile`
- 前端 `vite build` 可通过
- `LP Optimizer` 已接入 `HiGHS`
- 生产计划链路已验证“原计划可行但 WIP 校正后不可行”的业务场景

## Current Scope

适合用于：

- 存储厂产能分析原型验证
- 需求评审与功能演示
- WIP-aware 计划逻辑联调
- 前后端一体化 PoC

当前仍属于原型/样机阶段，以下部分还适合继续增强：

- 更完整的测试覆盖
- 更稳定的 Agent 推理与模型接入
- 更标准化的数据接入与鉴权
- 更贴近实际工厂规则的约束建模

## Docs

- [WIP Integration Design](./capacity_agent/docs/WIP_INTEGRATION_DESIGN.md)
- [Output Perspective Design](./capacity_agent/docs/OUTPUT_PERSPECTIVE_DESIGN.md)
- [需求评审一页纸](./docs/plans/2026-04-30-storage-fab-wip-review-design.md)

## License

仓库当前未附带正式开源许可证。如需公开分发，建议补充 `LICENSE` 文件并明确第三方依赖使用边界。
