# Capacity Agent - 存储芯片厂产能现况 Agent (开源版)

> 完全本地化、数据不出厂的产能分析智能 Agent  
> 技术栈: Python 3.11 + FastAPI + Pyomo + SimPy + LangGraph + Qwen/DeepSeek (vLLM)

## 一、架构

```
┌─────────────────────────────────────────────────────────────┐
│  L3  Agent Orchestration  (LangGraph + Local LLM via vLLM)  │
│      app/agent.py  app/tools.py  app/llm_client.py          │
└──────────────────────┬──────────────────────────────────────┘
                       │  Tool calls (HTTP)
┌──────────────────────▼──────────────────────────────────────┐
│  L2  Compute Engines  (FastAPI services)                    │
│      engines/rccp.py        engines/bottleneck.py           │
│      engines/lp_optimizer.py engines/des_validator.py       │
│      engines/whatif.py                                      │
└──────────────────────┬──────────────────────────────────────┘
                       │  SQL / Parquet
┌──────────────────────▼──────────────────────────────────────┐
│  L1  Data Layer  (PostgreSQL + ClickHouse + DBT)            │
│      data/schemas/    data/dbt/    data/loaders/            │
└─────────────────────────────────────────────────────────────┘
```

## 二、快速开始 (单机 Docker Compose)

```bash
# 1. 准备硬件: 至少 1×A100/H100 80G 或 2×A6000 48G (跑 Qwen2.5-32B)
#    最低: 1×RTX 4090 24G (跑 Qwen2.5-7B-Instruct)

# 2. 启动所有服务
cd capacity_agent
docker compose -f docker/docker-compose.yml up -d

# 3. 初始化数据
docker compose exec postgres psql -U capacity -d capacity_db -f /init/schema.sql
python scripts/load_sample_data.py

# 4. 测试 Agent
curl -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"query": "本周光刻区产能现况"}'
```

## 二点五、前端工作台

仓库根目录新增了 `frontend/`，提供一个 React + Vite 的演示工作台：

- `Direct Engine`：加载内置样例或导入 Excel，运行 RCCP、瓶颈识别、LP、DES、What-if
- `Agent Chat`：通过 `app/main.py` 的 Agent API 直接做自然语言问答

本地开发建议顺序：

```bash
# 1. 生成样例数据
cd capacity_agent
python scripts/load_sample_data.py

# 2. 启动 Engine 服务
uvicorn engines.server:app --host 0.0.0.0 --port 8001

# 3. 启动 Agent 服务
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 4. 启动前端
cd ../frontend
npm install
npm run dev
```

默认前端读取：

- Engine: `http://localhost:8001`
- Agent: `http://localhost:8000`

如需覆盖，可在前端环境变量中设置：

```bash
VITE_ENGINE_URL=http://localhost:8001
VITE_AGENT_URL=http://localhost:8000
```

### Excel 导入约定

前端支持上传 `.xlsx/.xls` 工作簿，后端会解析并注册成临时数据集。工作簿需包含四个 sheet：

1. `route_master`
2. `tool_groups`
3. `oee`
4. `demand_plan`

必须字段如下：

| Sheet | 必须字段 |
|---|---|
| `route_master` | `product_id`, `path_id`, `step_seq`, `tool_group_id`, `run_time_hr`, `batch_size` |
| `tool_groups` | `tool_group_id`, `tool_group_name`, `area`, `n_machines`, `nameplate_throughput_wph` |
| `oee` | `fact_date`, `tool_group_id`, `availability`, `performance`, `quality`, `oee`, `available_hours` |
| `demand_plan` | `time_window`, `product_id`, `wafer_count` |

## 三、目录结构

```
capacity_agent/
├── app/                    # L3 Agent 编排层
│   ├── agent.py            # LangGraph 状态机
│   ├── tools.py            # Tool 定义 (函数 + schema)
│   ├── llm_client.py       # 本地 LLM 客户端 (vLLM OpenAI-compatible API)
│   ├── prompts.py          # System prompt 模板
│   └── main.py             # FastAPI 入口
├── engines/                # L2 计算引擎
│   ├── rccp.py             # RCCP 产能计算
│   ├── bottleneck.py       # 瓶颈识别 (含 Kingman 公式)
│   ├── lp_optimizer.py     # LP 产品 mix 优化 (Pyomo + CBC)
│   ├── des_validator.py    # 局部 DES 仿真 (SimPy)
│   ├── whatif.py           # What-if 场景模拟
│   └── server.py           # 引擎统一 FastAPI 服务
├── data/                   # L1 数据层
│   ├── schemas/            # PostgreSQL DDL
│   ├── dbt/                # dbt 模型 (派生表 capacity_matrix 等)
│   ├── loaders/            # MES → PG 数据加载脚本
│   └── sample/             # 测试样本数据
├── configs/
│   ├── settings.yaml       # 全局配置
│   └── tools_schema.json   # Tool function schema
├── docker/
│   ├── docker-compose.yml  # 一键启动所有服务
│   ├── Dockerfile.app      # Agent 服务镜像
│   ├── Dockerfile.engine   # 引擎服务镜像
│   └── Dockerfile.vllm     # 本地 LLM 服务镜像
├── scripts/
│   ├── load_sample_data.py
│   ├── benchmark.py
│   └── run_tests.py
└── docs/
    └── implementation_guide.docx   # 完整实施手册
```

## 四、技术选型说明 (全开源)

| 层 | 组件 | 选型 | 为什么 |
|---|---|---|---|
| LLM | 推理引擎 | **vLLM** | OpenAI-compatible API, 单 GPU 性能最佳 |
| LLM | 模型 | **Qwen2.5-32B-Instruct** / **DeepSeek-V2.5** | 中文好,tool-use 能力强,Apache 2.0 / MIT |
| Agent | 编排框架 | **LangGraph** | 状态机清晰,可调试,MIT |
| 引擎 | LP 求解器 | **CBC** (默认) / **HiGHS** | 完全开源,LP/MIP 都够用 |
| 引擎 | DES | **SimPy** | Python 原生,5 分钟上手 |
| 引擎 | 排队论 | **自实现 Kingman G/G/c** | 不依赖商业库 |
| API | Web 框架 | **FastAPI** | 异步、自动 schema |
| 数据 | OLTP | **PostgreSQL 16** | 成熟稳定 |
| 数据 | OLAP | **ClickHouse** | 大表聚合极快 (capacity matrix 适用) |
| 数据 | ETL | **dbt-core** | 派生表版本化 |
| 部署 | 编排 | **Docker Compose** | 单机即可,无需 K8s |
| 监控 | 指标 | **Prometheus + Grafana** | 标准组合 |

## 五、数据不出厂保证

1. **LLM 完全本地化**: vLLM 容器从 HuggingFace 离线下载模型权重一次,之后切断外网。
2. **无外部 API 调用**: 所有 tool 都是本地 HTTP,所有数据库都在 docker 网络内。
3. **审计日志**: 每次 LLM 调用、每次 tool 调用都落 PG `audit_log` 表。
4. **Air-Gap 模式**: 提供离线安装包脚本 `scripts/build_offline_bundle.sh`。

## 六、实施周期

| 阶段 | 周期 | 关键产物 |
|---|---|---|
| Week 1-4   | 数据底座 | PG schema, MES loader, Capacity Matrix 派生表 |
| Week 5-7   | RCCP+Bottleneck | 引擎 API,准确率 ≥ 95% |
| Week 8-12  | Agent MVP | LLM 部署,8 tools,对话式 UI |
| Week 13-18 | 仿真增强 | DES, LP, What-if, 滚动校准闭环 |
| Week 19-24 | 上线推广 | 灰度并行,培训,SOP |

详见 `docs/implementation_guide.docx`。
