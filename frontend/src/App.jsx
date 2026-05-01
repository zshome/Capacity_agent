import { useEffect, useMemo, useState } from "react";
import { api } from "./api";

function formatPercent(value) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function formatNumber(value) {
  return Number(value || 0).toLocaleString("zh-CN", { maximumFractionDigits: 1 });
}

function severityClass(status) {
  if (status === "overload" || status === "critical") return "danger";
  if (status === "warning") return "warn";
  return "ok";
}

function StatusPill({ label, tone }) {
  return <span className={`pill ${tone}`}>{label}</span>;
}

function MetricCard({ label, value, hint }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-hint">{hint}</div>
    </div>
  );
}

function App() {
  const [activeTab, setActiveTab] = useState("overview");
  const [datasets, setDatasets] = useState([]);
  const [datasetId, setDatasetId] = useState("sample");
  const [datasetSummary, setDatasetSummary] = useState(null);
  const [timeWindow, setTimeWindow] = useState("");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [analysis, setAnalysis] = useState(null);
  const [agentQuestion, setAgentQuestion] = useState("本周光刻区产能现况如何？");
  const [agentResult, setAgentResult] = useState(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [whatIfConfig, setWhatIfConfig] = useState({
    perturbationType: "tool_down",
    toolGroupId: "",
    hoursLost: 24,
    productId: "",
    deltaWafers: 50
  });
  const [scenarioInfo, setScenarioInfo] = useState(null);
  const [productionPlan, setProductionPlan] = useState(null);
  const [lpConfig, setLpConfig] = useState({
    enabled: true,                  // 是否启用LP优化
    demandMinType: "none",           // none | contract_min | custom
    demandMaxType: "market_max",     // market_max | capacity_limit | custom
    objective: "max_profit",         // max_profit | max_output | max_balance
    solver: "highs",                 // highs | cbc | glpk
    timeLimit: 60                    // 秒
  });
  
  // LLM配置状态
  const [llmConfig, setLlmConfig] = useState({
    provider: "vllm",     // vllm | openai | volces | dashscope | custom
    model: "",            // 模型ID
    apiKey: "",           // API密钥
    baseUrl: ""           // 自定义URL
  });
  
  // === Output Perspective 状态 - 产出视角产能规划 ===
  const [outputActive, setOutputActive] = useState(false);  // 是否启用Output视角
  const [outputConfig, setOutputConfig] = useState({
    outputTargetWeek: "2026-W18",  // 规划周
    outputTargets: {},             // {product: target_wafers} - 从数据集产品列表动态生成
    completionThreshold: 0.80,     // 产出完成度阈值
    predictionWeeks: 8             // 预测周数
  });
  const [wipData, setWipData] = useState([]);  // WIP Lot数据 - 初始为空，用户可导入或使用模拟
  const [outputResult, setOutputResult] = useState(null);  // Output RCCP结果
  const [outputPrediction, setOutputPrediction] = useState(null);  // 产出预测结果
  const [inputPlanResult, setInputPlanResult] = useState(null);  // 投入计划结果
  const [outputLoading, setOutputLoading] = useState(false);

  useEffect(() => {
    loadDatasets();
  }, []);

  useEffect(() => {
    if (datasetId) {
      loadDatasetSummary(datasetId);
      // 当数据集加载时，自动生成 Output 视角的产出目标
      generateOutputTargetsFromDataset(datasetId);
    }
  }, [datasetId]);
  
  // 从数据集生成产出目标
  async function generateOutputTargetsFromDataset(selectedId) {
    try {
      const summary = await api.datasetSummary(selectedId);
      const demandPlan = await api.demandPlan(selectedId, summary.time_windows?.[0] || "");
      const wipPayload = await api.wipLotDetail(selectedId);
      
      // 从需求计划生成产出目标
      const targets = {};
      for (const [product, wafers] of Object.entries(demandPlan.demand_plan || {})) {
        targets[product] = Math.round(wafers * 0.8); // 默认80%的目标
      }
      
      setOutputConfig(prev => ({
        ...prev,
        outputTargets: targets,
        outputTargetWeek: summary.time_windows?.[0] || prev.outputTargetWeek
      }));

      if ((wipPayload.records || []).length > 0) {
        setWipData(wipPayload.records);
        return;
      }

      // 样例数据没有真实WIP时，回退到模拟生成
      const stepsPerProduct = Math.round((summary.n_routes || 1890) / (summary.n_products || 27));
      const products = Object.keys(targets);
      const wipLots = [];
      let lotIndex = 1;

      for (const product of products) {
        const lotConfigs = [
          { pct: 85, stepPct: 0.85 },
          { pct: 60, stepPct: 0.60 },
          { pct: 35, stepPct: 0.35 },
        ];

        for (const config of lotConfigs) {
          const currentStep = Math.floor(stepsPerProduct * config.stepPct);
          wipLots.push({
            lot_id: `LOT${lotIndex.toString().padStart(3, '0')}`,
            product_id: product,
            current_step_seq: Math.max(1, currentStep),
            wafer_count: 25,
            percent_complete: config.pct
          });
          lotIndex++;
        }
      }

      setWipData(wipLots);
      
    } catch (err) {
      console.warn("Failed to generate output targets from dataset:", err);
    }
  }
  
  // 重新生成 WIP（用于按钮点击）
  function regenerateWipData() {
    if (!datasetSummary || Object.keys(outputConfig.outputTargets).length === 0) return;
    
    // 每个产品的工序数 = 总记录数 / 产品数
    // n_routes=1890 是所有产品的工序记录总数
    // 每个产品约 1890 / 27 ≈ 70 工序
    const stepsPerProduct = Math.round((datasetSummary.n_routes || 1890) / (datasetSummary.n_products || 27));
    const products = Object.keys(outputConfig.outputTargets);
    const wipLots = [];
    let lotIndex = 1;
    
    for (const product of products) {
      const lotConfigs = [
        { pct: 85, stepPct: 0.85 },   // 工序序号 = 70 × 0.85 = 59
        { pct: 60, stepPct: 0.60 },   // 工序序号 = 70 × 0.60 = 42
        { pct: 35, stepPct: 0.35 },   // 工序序号 = 70 × 0.35 = 24
      ];
      
      for (const config of lotConfigs) {
        const currentStep = Math.floor(stepsPerProduct * config.stepPct);
        wipLots.push({
          lot_id: `LOT${lotIndex.toString().padStart(3, '0')}`,
          product_id: product,
          current_step_seq: Math.max(1, currentStep),
          wafer_count: 25,
          percent_complete: config.pct
        });
        lotIndex++;
      }
    }
    
    setWipData(wipLots);
  }

  async function loadDatasets() {
    try {
      const response = await api.listDatasets();
      setDatasets(response.datasets || []);
    } catch (err) {
      setError(err.message);
    }
  }

  async function loadDatasetSummary(selectedId) {
    try {
      setError("");
      const summary = await api.datasetSummary(selectedId);
      setDatasetSummary(summary);
      if (!timeWindow || !(summary.time_windows || []).includes(timeWindow)) {
        setTimeWindow(summary.time_windows?.[0] || "");
      }
    } catch (err) {
      setError(err.message);
    }
  }

  async function handleUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setUploading(true);
      setError("");
      const response = await api.importExcel(file);
      await loadDatasets();
      setDatasetId(response.dataset.dataset_id);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function runAnalysis() {
    if (!datasetId || !timeWindow) return;
    try {
      setLoading(true);
      setError("");
      
      const [statusResponse, demandResponse, matrixResponse] = await Promise.all([
        api.toolGroupStatus(datasetId),
        api.demandPlan(datasetId, timeWindow),
        api.capacityMatrix(datasetId)
      ]);

      const availableHours = Object.fromEntries(
        (statusResponse.tool_groups || []).map((item) => [item.tool_group_id, item.available_hours])
      );
      const toolGroups = statusResponse.tool_groups || [];
      const capacityMatrix = matrixResponse.capacity_matrix || {};
      const demandPlan = demandResponse.demand_plan || {};

      // NEW: Classify scenario
      const products = Object.keys(demandPlan);
      const toolGroupIds = Object.keys(availableHours);
      const processSteps = datasetSummary?.process_steps || ["Step_1", "Step_2", "Step_3"];
      
      // 只有在有产品和机台数据时才进行情景分类
      if (products.length > 0 && toolGroupIds.length > 0) {
        try {
          const scenarioResult = await api.classifyScenario({
            products,
            tool_groups: toolGroupIds,
            process_steps: processSteps,
            feasibility_matrix: {},
            tc_matrix: {},  // 暂不传递tc_matrix，避免格式不匹配
          });
          setScenarioInfo(scenarioResult);
        } catch (e) {
          console.warn("Scenario classification failed:", e.message || e);
        }
      }

      const rccp = await api.computeRccp({
        demand_plan: demandPlan,
        capacity_matrix: capacityMatrix,
        available_hours: availableHours,
        dataset_id: datasetId,
        wip_lot_detail: wipData,
        enable_wip_adjustment: wipData.length > 0,
        time_window: "weekly"
      });

      const historical = await api.historicalLoading(
        datasetId,
        toolGroups.map((item) => item.tool_group_id)
      );

      const bottleneck = await api.analyzeBottleneck({
        loading_table: rccp.loading_table,
        historical_loading: historical.history,
        service_rates: Object.fromEntries(toolGroups.map((item) => [item.tool_group_id, item.nameplate_throughput_wph])),
        n_servers: Object.fromEntries(toolGroups.map((item) => [item.tool_group_id, item.n_machines]))
      });

      let lp = null;
      if (!rccp.feasible) {
        lp = await api.optimizeLp({
          products: Object.keys(demandPlan),
          tool_groups: Object.keys(availableHours),
          capacity_matrix: capacityMatrix,
          available_hours: availableHours,
          // 使用空 demand_min，让 LP 自动决定最优分配
          // contract_min 可能导致产能约束无法满足
          demand_min: {},
          demand_max: Object.fromEntries((demandResponse.records || []).map((row) => [row.product_id, row.market_max || row.wafer_count])),
          demand_target: demandPlan,
          unit_profit: Object.fromEntries((demandResponse.records || []).map((row) => [row.product_id, row.unit_profit || 1])),
          objective: lpConfig.objective,
          solver: lpConfig.solver,
          time_limit_seconds: lpConfig.timeLimit
        });
      }

      let des = null;
      if (bottleneck.primary_bottleneck) {
        const targetTool = bottleneck.primary_bottleneck;
        const targetRow = toolGroups.find((item) => item.tool_group_id === targetTool);
        const topContributor = rccp.loading_table.find((item) => item.tool_group_id === targetTool)?.contributing_products || {};
        const mainProduct = Object.keys(topContributor)[0] || Object.keys(demandPlan)[0];
        const serviceTime =
          capacityMatrix[mainProduct]?.[targetTool] ||
          capacityMatrix[Object.keys(capacityMatrix)[0]]?.[targetTool] ||
          0.5;
        
        // 计算合理的到达率：基于需求小时而非wafer count
        const bottleneckLoading = rccp.loading_table.find((item) => item.tool_group_id === targetTool);
        const demandHours = bottleneckLoading?.demand_hours || 100;  // 该机台的总需求小时
        const availableHours = bottleneckLoading?.available_hours || 1000;
        
        // arrival_rate = demand_hours / sim_duration (每小时到达多少"小时需求")
        // 转换为 wafer arrival: demand_hours / service_time / sim_duration
        const arrivalRate = Math.max(demandHours / (168 * serviceTime), 0.1);
        
        // 计算服务率：每小时能处理多少wafer
        const serviceRate = serviceTime > 0 ? (1 / serviceTime) : 2;
        
        // 预期利用率 = demand_hours / available_hours (应接近RCCP loading)
        const expectedUtilization = demandHours / availableHours;
        
        des = await api.runDes({
          tool_groups: [
            {
              tool_group_id: targetTool,
              n_machines: targetRow?.n_machines || 2,
              service_rate: serviceRate,
              service_cv: 0.5,
              availability: targetRow?.availability || 0.85
            }
          ],
          arrivals: [
            {
              product_id: mainProduct,
              arrival_rate: arrivalRate,
              arrival_cv: 1,
              target_tool_groups: [targetTool],
              service_time_per_tg: {
                [targetTool]: serviceTime
              }
            }
          ],
          sim_duration_hours: 168,
          n_replications: 2
        });
      }

      setWhatIfConfig((prev) => ({
        ...prev,
        toolGroupId: prev.toolGroupId || toolGroups[0]?.tool_group_id || "",
        productId: prev.productId || Object.keys(demandPlan)[0] || ""
      }));

      setAnalysis({
        datasetId,
        statusResponse,
        demandResponse,
        matrixResponse,
        availableHours,
        rccp,
        bottleneck,
        lp,
        des
      });

      // 生成生产计划（使用用户配置的LP参数）
      try {
        let demandMax = null;
        if (lpConfig.demandMaxType === "market_max") {
          demandMax = Object.fromEntries((demandResponse.records || []).map((row) => [row.product_id, row.market_max || row.wafer_count]));
        } else if (lpConfig.demandMaxType === "capacity_limit") {
          demandMax = {};
        }

        let demandMin = null;
        if (lpConfig.demandMinType === "contract_min") {
          demandMin = Object.fromEntries((demandResponse.records || []).map((row) => [row.product_id, row.contract_min || 0]));
        } else if (lpConfig.demandMinType === "none") {
          demandMin = {};
        }

        const planResult = await api.generateProductionPlanFromDataset({
          dataset_id: datasetId,
          time_window: timeWindow,
          wip_lot_detail: outputActive ? wipData : [],
          enable_wip_adjustment: outputActive && wipData.length > 0,
          demand_min: demandMin,
          demand_max: demandMax,
          lp_enabled: lpConfig.enabled,
          lp_objective: lpConfig.objective,
          lp_solver: lpConfig.solver,
          lp_time_limit: lpConfig.timeLimit,
          objective: lpConfig.objective
        });
        setProductionPlan(planResult);
      } catch (e) {
        console.warn("Production plan generation failed:", e);
        setError("生产计划生成失败: " + e.message);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function runWhatIf() {
    if (!analysis) return;
    try {
      setLoading(true);
      setError("");
      const payload =
        whatIfConfig.perturbationType === "tool_down"
          ? {
              baseline_rccp_input: {
                demand_plan: analysis.demandResponse.demand_plan,
                capacity_matrix: analysis.matrixResponse.capacity_matrix,
                available_hours: analysis.availableHours
              },
              perturbation_type: "tool_down",
              perturbation_params: {
                tool_group_id: whatIfConfig.toolGroupId,
                hours_lost: Number(whatIfConfig.hoursLost)
              }
            }
          : {
              baseline_rccp_input: {
                demand_plan: analysis.demandResponse.demand_plan,
                capacity_matrix: analysis.matrixResponse.capacity_matrix,
                available_hours: analysis.availableHours
              },
              perturbation_type: "demand_change",
              perturbation_params: {
                product_id: whatIfConfig.productId,
                delta_wafers: Number(whatIfConfig.deltaWafers)
              }
            };
      const result = await api.whatIf(payload);
      setAnalysis((prev) => ({ ...prev, whatIf: result }));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function askAgent(event) {
    event.preventDefault();
    if (!agentQuestion.trim()) return;
    try {
      setAgentLoading(true);
      setError("");
      const result = await api.agentQuery(agentQuestion);
      setAgentResult(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setAgentLoading(false);
    }
  }

  const topHotspots = useMemo(() => analysis?.rccp?.loading_table?.slice(0, 12) || [], [analysis]);
  const topBottlenecks = useMemo(() => analysis?.bottleneck?.bottlenecks?.slice(0, 8) || [], [analysis]);
  const productOptions = analysis?.demandResponse?.records || [];
  const toolOptions = analysis?.statusResponse?.tool_groups || [];
  const totalOutputTarget = Object.values(outputConfig.outputTargets || {}).reduce((sum, value) => sum + Number(value || 0), 0);
  const tabItems = [
    { id: "overview", label: "总览", hint: "RCCP、瓶颈、LP、DES" },
    { id: "planning", label: "生产计划", hint: "LP配置与WIP校正" },
    { id: "output", label: "Output / What-if", hint: "产出视角与扰动推演" },
    { id: "agent", label: "Agent", hint: "问答与模型配置" }
  ];

  return (
    <div className="app-shell">
      <header className="hero-band">
        <div>
          <div className="eyebrow">Capacity Agent</div>
          <h1>Fab Capacity Workbench</h1>
          <p>样例数据、Excel 导入、RCCP、瓶颈识别、LP、DES 和 Agent 问答都在同一个工作台里。</p>
        </div>
        <div className="hero-actions">
          <StatusPill label={loading ? "计算中" : "就绪"} tone={loading ? "warn" : "ok"} />
          <StatusPill label={datasetSummary?.source_type === "excel" ? "Excel 数据集" : "内置样例"} tone="neutral" />
        </div>
      </header>

      {error ? <div className="error-banner">{error}</div> : null}

      <main className="workbench-shell">
        <section className="panel control-deck">
          <div className="panel-header">
            <h2>全局控制台</h2>
            <span>先选数据、再运行主分析，其他页签共享同一套上下文</span>
          </div>
          <div className="control-grid">
            <label className="field">
              <span>当前数据集</span>
              <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
                {datasets.map((item) => (
                  <option key={item.dataset_id} value={item.dataset_id}>
                    {item.dataset_name}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>需求时间窗</span>
              <select value={timeWindow} onChange={(event) => setTimeWindow(event.target.value)}>
                {(datasetSummary?.time_windows || []).map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </label>
            <div className="field control-actions">
              <span>主分析执行</span>
              <button className="primary-button" onClick={runAnalysis} disabled={loading || !datasetId || !timeWindow}>
                {loading ? "运行中..." : "运行完整分析"}
              </button>
            </div>
          </div>
          <div className="control-grid upload-grid">
            <label className="upload-drop">
              <input type="file" accept=".xlsx,.xls" onChange={handleUpload} />
              <strong>{uploading ? "导入中..." : "导入 Excel 工作簿（5 个必填 Sheet）"}</strong>
              <span>需包含 `route_master`、`tool_groups`、`oee`、`demand_plan`、`wip_lot_detail`，其中 `wip_lot_detail` 为 WIP 必填 sheet</span>
            </label>
            <div className="control-actions-group">
              <button className="secondary-button" onClick={() => api.downloadExcelTemplate()}>
                下载 Excel 模板
              </button>
              <p className="muted">建议先下载最新模板，按 5 个必填 sheet 填充后再导入。</p>
            </div>
          </div>
          {datasetSummary ? (
            <div className="mini-grid four">
              <MetricCard label="产品数" value={datasetSummary.n_products} hint="用于计划与矩阵构建" />
              <MetricCard label="机台组" value={datasetSummary.n_tool_groups} hint="用于产能与瓶颈计算" />
              <MetricCard label="路线行数" value={datasetSummary.n_routes} hint="route master 明细规模" />
              <MetricCard label="WIP Lot" value={datasetSummary.n_wip_lots || 0} hint="来自必填 sheet：wip_lot_detail" />
            </div>
          ) : null}
        </section>

        <section className="tab-bar" aria-label="主工作台视图">
          {tabItems.map((tab) => (
            <button
              key={tab.id}
              type="button"
              className={`tab-button ${activeTab === tab.id ? "active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <strong>{tab.label}</strong>
              <span>{tab.hint}</span>
            </button>
          ))}
        </section>

        {activeTab === "overview" ? (
          <section className="tab-stage">
            <div className="content-grid two-col">
              <div className="panel">
                <div className="panel-header">
                  <h2>运行概览</h2>
                  <span>先看全局健康度，再下钻瓶颈与策略</span>
                </div>
                <div className="mini-grid four">
                  <MetricCard
                    label="整体 Loading"
                    value={analysis ? formatPercent(analysis.rccp?.overall_loading_pct) : "--"}
                    hint={analysis?.rccp?.metadata?.wip_total_hours > 0 ? "已含WIP后续负载" : "基于最近一周 available hours"}
                  />
                  <MetricCard
                    label="可行性"
                    value={analysis ? (analysis.rccp?.feasible ? "可行" : "不可行") : "--"}
                    hint={analysis?.rccp?.metadata?.wip_total_hours > 0 ? "WIP-aware RCCP 主判定" : "RCCP 主判定"}
                  />
                  <MetricCard
                    label="主瓶颈"
                    value={analysis?.bottleneck?.primary_bottleneck || "--"}
                    hint="综合 score 排名第一"
                  />
                  <MetricCard
                    label="DES 结论"
                    value={analysis?.des ? (analysis.des.feasible ? "通过" : "风险") : "--"}
                    hint="局部仿真验证"
                  />
                </div>
              </div>

              <div className="panel">
                <div className="panel-header">
                  <h2>分析结论</h2>
                  <span>这次运行最值得看的判断信息</span>
                </div>
                {scenarioInfo ? (
                  <div className="result-card">
                    <h3>情景分类</h3>
                    <p>
                      <strong>{scenarioInfo.scenario_type}</strong>
                      <span className="muted" style={{ marginLeft: "8px" }}>{scenarioInfo.description}</span>
                    </p>
                    <p className="muted">推荐算法: {scenarioInfo.algorithm} | 求解器: {scenarioInfo.recommended_solver}</p>
                  </div>
                ) : (
                  <div className="empty-state">运行完整分析后，这里会给出情景识别建议。</div>
                )}
                <div className="result-card" style={{ marginTop: "12px" }}>
                  <h3>建议下一步</h3>
                  <p className="muted">
                    {analysis?.rccp?.feasible
                      ? "当前主计划可执行，建议重点查看生产计划页中的 WIP 校正与利润分配。"
                      : "当前主计划不可行，建议优先查看 RCCP 热点与生产计划页中的 LP 收缩结果。"}
                  </p>
                </div>
              </div>

              <div className="panel span-full">
                <div className="panel-header">
                  <h2>RCCP 热点</h2>
                  <span>{analysis?.rccp?.metadata?.wip_total_hours > 0 ? "按 WIP 校正后 loading 从高到低排序" : "按 loading 从高到低排序"}</span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>Tool Group</th>
                        <th>新计划</th>
                        <th>WIP</th>
                        <th>Demand</th>
                        <th>Avail</th>
                        <th>Load</th>
                        <th>Gap</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topHotspots.map((item) => (
                        <tr key={item.tool_group_id}>
                          <td>{item.tool_group_id}</td>
                          <td>{formatNumber(item.new_plan_hours)}</td>
                          <td>{formatNumber(item.wip_hours)}</td>
                          <td>{formatNumber(item.demand_hours)}</td>
                          <td>{formatNumber(item.available_hours)}</td>
                          <td>{formatPercent(item.loading_pct)}</td>
                          <td>{formatNumber(item.gap_hours)}</td>
                          <td>
                            <StatusPill label={item.status} tone={severityClass(item.status)} />
                          </td>
                        </tr>
                      ))}
                      {!topHotspots.length ? (
                        <tr>
                          <td colSpan="8" className="empty-cell">
                            运行分析后这里会出现热点机台组。
                          </td>
                        </tr>
                      ) : null}
                    </tbody>
                  </table>
                </div>
                {analysis?.rccp?.metadata?.wip_total_hours > 0 ? (
                  <p className="muted" style={{ marginTop: "8px" }}>
                    当前热点口径已统一为 `新计划负载 + WIP后续工序负载`，WIP累计占机 {formatNumber(analysis.rccp.metadata.wip_total_hours)} 小时。
                  </p>
                ) : null}
              </div>

              <div className="panel">
                <div className="panel-header">
                  <h2>瓶颈评分</h2>
                  <span>Loading + Queue + Drift</span>
                </div>
                <div className="list-stack">
                  {topBottlenecks.map((item) => (
                    <div className="list-item" key={item.tool_group_id}>
                      <div>
                        <strong>{item.tool_group_id}</strong>
                        <div className="muted">
                          load {formatPercent(item.loading_pct)} | wait {formatNumber(item.expected_wait_hours)}h
                        </div>
                      </div>
                      <div className="score-chip">{formatNumber(item.composite_score)}</div>
                    </div>
                  ))}
                  {!topBottlenecks.length ? <div className="empty-state">还没有瓶颈评分结果。</div> : null}
                </div>
              </div>

              <div className="panel">
                <div className="panel-header">
                  <h2>LP 与 DES</h2>
                  <span>不可行时给优化建议，关键点做仿真确认</span>
                </div>
                <div className="result-card">
                  <h3>LP Optimizer</h3>
                  {analysis?.lp ? (
                    <>
                      <p>
                        <StatusPill label={analysis.lp.status} tone={analysis.lp.status === "optimal" ? "ok" : "warn"} />
                        <span style={{ marginLeft: "8px" }}>目标值（总利润）: <strong>{formatNumber(analysis.lp.objective_value)}</strong></span>
                      </p>
                      {analysis.lp.status === "optimal" && analysis.lp.optimal_plan ? (
                        <div style={{ marginTop: "12px" }}>
                          <p className="muted" style={{ marginBottom: "8px" }}>最优产量分配 (Top 5 高利润产品)</p>
                          <div className="mini-grid three">
                            {Object.entries(analysis.lp.optimal_plan)
                              .filter(([_, qty]) => qty > 1)
                              .sort((a, b) => b[1] - a[1])
                              .slice(0, 5)
                              .map(([product, qty]) => (
                                <MetricCard key={product} label={product} value={`${formatNumber(qty)} wafers`} hint="LP最优分配" />
                              ))}
                          </div>
                          {analysis.lp.binding_constraints?.length > 0 ? (
                            <p style={{ marginTop: "12px" }}>
                              <span className="warn">⚠️ 瓶颈机台:</span>
                              <strong>{analysis.lp.binding_constraints.join(", ")}</strong>
                              <span className="muted"> (产能100%利用，限制增产)</span>
                            </p>
                          ) : null}
                          <p style={{ marginTop: "8px", color: "#2a9d8f" }}>
                            💡 建议: 增加 {analysis.lp.binding_constraints?.join("、") || "瓶颈机台"} 的可用产能可提升利润
                          </p>
                        </div>
                      ) : null}
                      {analysis.lp.status === "infeasible" ? (
                        <p className="warn" style={{ marginTop: "8px" }}>
                          ❌ 无法满足最低需求约束。建议降低 contract_min 或增加机台产能。
                        </p>
                      ) : null}
                    </>
                  ) : (
                    <p>当前没有触发 LP 优化。</p>
                  )}
                </div>
                <div className="result-card" style={{ marginTop: "12px" }}>
                  <h3>DES Validation</h3>
                  {analysis?.des ? (
                    <>
                      <div className="mini-grid three" style={{ marginBottom: "12px" }}>
                        <MetricCard
                          label="平均利用率"
                          value={formatPercent(analysis.des.tool_group_stats?.[0]?.avg_utilization * 100)}
                          hint="机台实际占用时间比例"
                        />
                        <MetricCard
                          label="P95等待时间"
                          value={`${formatNumber(analysis.des.tool_group_stats?.[0]?.p95_wait_hours)}h`}
                          hint="95%产品等待时间上限"
                        />
                        <MetricCard
                          label="周期时间"
                          value={`${formatNumber(analysis.des.tool_group_stats?.[0]?.avg_cycle_time_hours)}h`}
                          hint="平均处理周期"
                        />
                      </div>
                      <p className="muted" style={{ marginTop: "8px" }}>
                        <strong>解读：</strong>
                        {analysis.des.tool_group_stats?.[0]?.avg_utilization < 0.5 ? (
                          <span style={{ color: "#2a9d8f" }}>低利用率表示产能有余量，RCCP标记的瓶颈可能是静态分析偏差。建议检查到达率参数。</span>
                        ) : analysis.des.tool_group_stats?.[0]?.avg_utilization > 0.85 ? (
                          <span style={{ color: "#e63946" }}>高利用率+等待时间，确认存在产能瓶颈。建议增加机台或优化调度。</span>
                        ) : (
                          <span style={{ color: "#475467" }}>利用率适中，产能基本平衡。关注P95等待时间是否影响交期。</span>
                        )}
                      </p>
                      <p className="muted">
                        仿真参数：到达率 {(analysis.des.simulation_params?.arrival_rate || 0.47).toFixed(2)} wph |
                        服务率 {(analysis.des.simulation_params?.service_rate || 2).toFixed(2)} wph |
                        机台数 {analysis.des.simulation_params?.n_machines || 2}
                      </p>
                    </>
                  ) : (
                    <p>当前没有 DES 验证结果。</p>
                  )}
                </div>
              </div>
            </div>
          </section>
        ) : null}

        {activeTab === "planning" ? (
          <section className="tab-stage">
            <div className="content-grid planning-grid">
              <div className="panel">
                <div className="panel-header">
                  <h2>LP优化配置</h2>
                  <span>控制生产计划收缩方式与求解器</span>
                </div>
                <label className="field">
                  <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <input
                      type="checkbox"
                      checked={lpConfig.enabled}
                      onChange={(e) => setLpConfig(prev => ({ ...prev, enabled: e.target.checked }))}
                    />
                    启用LP优化
                  </span>
                  <span className="muted">不可行时自动调用LP调整产量</span>
                </label>
                <label className="field">
                  <span>优化目标</span>
                  <select value={lpConfig.objective} onChange={(e) => setLpConfig(prev => ({ ...prev, objective: e.target.value }))}>
                    <option value="max_profit">最大化利润</option>
                    <option value="max_output">最大化产量</option>
                    <option value="max_balance">均衡分配</option>
                  </select>
                </label>
                <label className="field">
                  <span>产量下限</span>
                  <select value={lpConfig.demandMinType} onChange={(e) => setLpConfig(prev => ({ ...prev, demandMinType: e.target.value }))}>
                    <option value="none">无约束</option>
                    <option value="contract_min">合约最低量</option>
                  </select>
                  <span className="muted">contract_min: 必须满足最低合约需求</span>
                </label>
                <label className="field">
                  <span>产量上限</span>
                  <select value={lpConfig.demandMaxType} onChange={(e) => setLpConfig(prev => ({ ...prev, demandMaxType: e.target.value }))}>
                    <option value="market_max">市场最大量</option>
                    <option value="capacity_limit">产能上限</option>
                  </select>
                  <span className="muted">market_max: 不超过市场需求; capacity_limit: 不超过产能瓶颈</span>
                </label>
                <label className="field">
                  <span>求解器</span>
                  <select value={lpConfig.solver} onChange={(e) => setLpConfig(prev => ({ ...prev, solver: e.target.value }))}>
                    <option value="highs">HiGHS (推荐)</option>
                    <option value="cbc">CBC (开源)</option>
                    <option value="glpk">GLPK (开源)</option>
                  </select>
                </label>
                <label className="field">
                  <span>求解时限 (秒)</span>
                  <input
                    type="number"
                    value={lpConfig.timeLimit}
                    min={10}
                    max={300}
                    onChange={(e) => setLpConfig(prev => ({ ...prev, timeLimit: parseInt(e.target.value) || 60 }))}
                  />
                  <span className="muted">超时返回当前最优解</span>
                </label>
                <div className="result-card">
                  <h3>使用建议</h3>
                  <p className="muted">这里的配置会影响“运行完整分析”后的生产计划结果。建议先在总览页确认热点，再回到这里做计划优化。</p>
                </div>
              </div>

              <div className="panel">
                <div className="panel-header">
                  <h2>生产计划输出</h2>
                  <span>整合产能现况生成的结构化计划</span>
                </div>
                {productionPlan ? (
                  <>
                    <div className="mini-grid four">
                      <MetricCard
                        label="可行性"
                        value={productionPlan.feasible ? "可行" : "不可行"}
                        hint={productionPlan.metadata?.lp_adjusted ? "LP调整后可行" : `评分 ${productionPlan.feasibility_score?.toFixed(0) || "--"}/100`}
                      />
                      <MetricCard label="整体 Loading" value={formatPercent(productionPlan.overall_loading_pct)} hint="所有机台组平均" />
                      <MetricCard label="预估总利润" value={`¥${formatNumber(productionPlan.total_profit)}`} hint="基于可达产量计算" />
                      <MetricCard
                        label="需求调整"
                        value={productionPlan.metadata?.demand_reduction > 0 ? `-${formatNumber(productionPlan.metadata.demand_reduction)}` : "无需调整"}
                        hint={`原始 ${formatNumber(productionPlan.metadata?.original_demand_total || 0)} → 调整 ${formatNumber(productionPlan.metadata?.adjusted_demand_total || 0)}`}
                      />
                    </div>

                    {productionPlan.metadata?.wip_adjustment_enabled ? (
                      <div className="mini-grid four" style={{ marginTop: "12px" }}>
                        <MetricCard
                          label="原计划结论"
                          value={productionPlan.metadata?.original_feasible ? "可行" : "不可行"}
                          hint={`Loading ${formatPercent(productionPlan.metadata?.original_overall_loading_pct || 0)}`}
                        />
                        <MetricCard
                          label="WIP校正后"
                          value={productionPlan.metadata?.wip_adjusted_feasible ? "可行" : "不可行"}
                          hint={`Loading ${formatPercent(productionPlan.metadata?.wip_adjusted_overall_loading_pct || 0)}`}
                        />
                        <MetricCard label="WIP占机" value={`${formatNumber(productionPlan.metadata?.wip_total_hours || 0)}h`} hint="已并入主产能判断" />
                        <MetricCard
                          label="WIP影响"
                          value={formatPercent((productionPlan.metadata?.wip_adjusted_overall_loading_pct || 0) - (productionPlan.metadata?.original_overall_loading_pct || 0))}
                          hint="相对原计划的Loading增量"
                        />
                      </div>
                    ) : null}

                    {productionPlan.metadata?.lp_adjusted && !productionPlan.metadata?.original_feasible ? (
                      <div className="result-card emphasis-card" style={{ marginTop: "12px" }}>
                        <h3>LP优化调整成功</h3>
                        <p>原始需求无法满足，LP优化器已自动调整产量分配。</p>
                        <p className="muted">调整后总产量: {formatNumber(productionPlan.metadata?.adjusted_demand_total || 0)} wafers（原计划 {formatNumber(productionPlan.metadata?.original_demand_total || 0)}）</p>
                      </div>
                    ) : null}

                    {productionPlan.metadata?.wip_adjustment_enabled ? (
                      <div className="result-card note-card" style={{ marginTop: "12px" }}>
                        <h3>WIP 校正说明</h3>
                        <p>当前生产计划已同时考虑新需求负载与 WIP 后续工序负载。</p>
                        <p className="muted">
                          原计划可行性 {productionPlan.metadata?.original_feasible ? "可行" : "不可行"}，
                          WIP校正后 {productionPlan.metadata?.wip_adjusted_feasible ? "可行" : "不可行"}，
                          WIP累计占机 {formatNumber(productionPlan.metadata?.wip_total_hours || 0)} 小时。
                        </p>
                      </div>
                    ) : null}

                    <div className="result-card" style={{ marginTop: "12px" }}>
                      <h3>原计划 vs WIP校正后计划</h3>
                      <div className="table-wrap compact">
                        <table>
                          <thead>
                            <tr>
                              <th>产品</th>
                              <th>目标</th>
                              <th>原可达</th>
                              <th>WIP校正后</th>
                              <th>原缺口</th>
                              <th>校正后缺口</th>
                              <th>优先级</th>
                              <th>利润</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(productionPlan.weekly_plan || []).map((item) => (
                              <tr key={item.product_id}>
                                <td>{item.product_id}</td>
                                <td>{formatNumber(item.target_wafers)}</td>
                                <td style={{ color: item.original_achievable_wafers < item.target_wafers ? "#8d99ae" : "#2a9d8f" }}>{formatNumber(item.original_achievable_wafers)}</td>
                                <td style={{ color: item.achievable_wafers < item.target_wafers ? "#e63946" : "#2a9d8f" }}>{formatNumber(item.achievable_wafers)}</td>
                                <td className={item.original_gap_wafers > 0 ? "warn" : ""}>
                                  {item.original_gap_wafers > 0 ? `-${formatNumber(item.original_gap_wafers)}` : formatNumber(item.original_gap_wafers)}
                                </td>
                                <td className={item.gap_wafers > 0 ? "warn" : ""}>
                                  {item.gap_wafers > 0 ? `-${formatNumber(item.gap_wafers)}` : formatNumber(item.gap_wafers)}
                                </td>
                                <td>P{item.priority}</td>
                                <td>¥{formatNumber(item.total_profit)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                      <p className="muted" style={{ marginTop: "8px" }}>
                        口径说明：原可达基于未考虑WIP的计划口径，WIP校正后结果已将 WIP 后续工序占机并入主计划计算。
                      </p>
                    </div>

                    <div className="content-grid two-col nested-grid">
                      {productionPlan.bottlenecks?.length > 0 ? (
                        <div className="result-card">
                          <h3>瓶颈分析</h3>
                          <div className="list-stack">
                            {productionPlan.bottlenecks.slice(0, 5).map((item) => (
                              <div className="list-item" key={item.tool_group_id}>
                                <div>
                                  <strong>{item.tool_group_id}</strong>
                                  <div className="muted">Loading {formatPercent(item.loading_pct)} | 缺口 {formatNumber(item.gap_hours)}h</div>
                                  {item.recommended_actions?.length > 0 ? (
                                    <div className="muted" style={{ fontSize: "0.85em", marginTop: "4px" }}>{item.recommended_actions[0]}</div>
                                  ) : null}
                                </div>
                                <StatusPill label={item.loading_pct >= 100 ? "overload" : "critical"} tone="danger" />
                              </div>
                            ))}
                          </div>
                        </div>
                      ) : null}
                      {productionPlan.recommendations?.length > 0 ? (
                        <div className="result-card">
                          <h3>建议措施</h3>
                          <ul style={{ margin: 0, paddingLeft: "20px" }}>
                            {productionPlan.recommendations.map((rec, idx) => (
                              <li key={idx} style={{ marginBottom: "4px" }}>{rec}</li>
                            ))}
                          </ul>
                        </div>
                      ) : null}
                    </div>
                  </>
                ) : (
                  <div className="empty-state">先运行完整分析，这里会汇总 LP 收缩后的生产计划。</div>
                )}
              </div>
            </div>
          </section>
        ) : null}

        {activeTab === "output" ? (
          <section className="tab-stage">
            <div className="content-grid planning-grid">
              <div className="panel" style={{ borderLeft: outputActive ? "4px solid #2a9d8f" : "none" }}>
                <div className="panel-header">
                  <h2>Output 视角规划</h2>
                  <span>从产出目标反推需求与瓶颈</span>
                </div>
                <label className="field">
                  <span style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <input type="checkbox" checked={outputActive} onChange={(e) => setOutputActive(e.target.checked)} />
                    启用 Output 视角
                  </span>
                  <span className="muted">启用后将在分析中使用产出目标而非投入计划</span>
                </label>

                {outputActive ? (
                  <>
                    <label className="field">
                      <span>规划周</span>
                      <select value={outputConfig.outputTargetWeek} onChange={(e) => setOutputConfig(prev => ({ ...prev, outputTargetWeek: e.target.value }))}>
                        {(datasetSummary?.time_windows || []).map((week) => (
                          <option key={week} value={week}>{week}</option>
                        ))}
                      </select>
                      <span className="muted">从数据集时间窗加载</span>
                    </label>

                    <div className="result-card">
                      <h3>产出目标与WIP</h3>
                      <p className="muted">目标总量 {formatNumber(totalOutputTarget)} wafers，WIP 来源 {datasetSummary?.source_type === "excel" ? "导入工作簿" : "样例自动生成"}。</p>
                      <div className="target-list">
                        {Object.entries(outputConfig.outputTargets).length > 0 ? (
                          Object.entries(outputConfig.outputTargets).map(([product, target]) => (
                            <label className="field inline-field" key={product}>
                              <span>{product}</span>
                              <input
                                type="number"
                                value={target}
                                onChange={(e) => {
                                  const newTargets = { ...outputConfig.outputTargets, [product]: parseInt(e.target.value) || 0 };
                                  setOutputConfig(prev => ({ ...prev, outputTargets: newTargets }));
                                }}
                              />
                              <span className="muted">wafers</span>
                            </label>
                          ))
                        ) : (
                          <p className="muted">请先选择数据集，产出目标将自动从需求计划生成。</p>
                        )}
                      </div>
                    </div>

                    <label className="field">
                      <span>产出完成度阈值</span>
                      <input
                        type="number"
                        step="0.05"
                        min="0.5"
                        max="1.0"
                        value={outputConfig.completionThreshold}
                        onChange={(e) => setOutputConfig(prev => ({ ...prev, completionThreshold: parseFloat(e.target.value) || 0.8 }))}
                      />
                      <span className="muted">低于此阈值视为产能不足</span>
                    </label>

                    <label className="field">
                      <span>预测周数</span>
                      <input
                        type="number"
                        min="1"
                        max="16"
                        value={outputConfig.predictionWeeks}
                        onChange={(e) => setOutputConfig(prev => ({ ...prev, predictionWeeks: parseInt(e.target.value) || 8 }))}
                      />
                      <span className="muted">基于WIP预测未来N周产出</span>
                    </label>

                    <div className="result-card">
                      <h3>WIP Lot 数据</h3>
                      <p className="muted">
                        {datasetSummary?.source_type === "excel"
                          ? "来自 Excel 必填 sheet：wip_lot_detail"
                          : `基于数据集产品和工艺路线（${datasetSummary?.n_routes || 0}步）自动生成`}
                      </p>
                      {wipData.length > 0 ? (
                        <>
                          <p>已加载 {wipData.length} 个 Lot，共 {wipData.reduce((sum, l) => sum + l.wafer_count, 0)} 片晶圆</p>
                          <div className="table-wrap compact" style={{ maxHeight: "160px" }}>
                            <table>
                              <thead>
                                <tr><th>Lot</th><th>产品</th><th>工序</th><th>晶圆</th><th>完成度</th></tr>
                              </thead>
                              <tbody>
                                {wipData.slice(0, 5).map((lot) => (
                                  <tr key={lot.lot_id}>
                                    <td>{lot.lot_id}</td>
                                    <td>{lot.product_id}</td>
                                    <td>{lot.current_step_seq}</td>
                                    <td>{lot.wafer_count}</td>
                                    <td>{lot.percent_complete}%</td>
                                  </tr>
                                ))}
                                {wipData.length > 5 ? (
                                  <tr><td colSpan="5" className="muted">... 还有 {wipData.length - 5} 个 Lot</td></tr>
                                ) : null}
                              </tbody>
                            </table>
                          </div>
                          {datasetSummary?.source_type !== "excel" ? (
                            <button className="secondary-button" style={{ marginTop: "8px" }} onClick={regenerateWipData}>
                              重新生成 WIP
                            </button>
                          ) : null}
                        </>
                      ) : (
                        <p className="muted">{datasetSummary?.source_type === "excel" ? "导入的工作簿未加载到 WIP 数据" : "选择数据集后自动生成模拟 WIP"}</p>
                      )}
                    </div>

                    <button
                      className="primary-button"
                      onClick={async () => {
                        if (!outputConfig.outputTargetWeek || Object.keys(outputConfig.outputTargets).length === 0) {
                          setError("请设置规划周和产出目标");
                          return;
                        }
                        try {
                          setOutputLoading(true);
                          setError("");
                          const statusResponse = await api.toolGroupStatus(datasetId);
                          const availableHours = Object.fromEntries((statusResponse.tool_groups || []).map((item) => [item.tool_group_id, item.available_hours]));
                          const wipLotDetail = wipData.map((lot) => ({
                            lot_id: lot.lot_id,
                            product_id: lot.product_id,
                            current_step_seq: lot.current_step_seq,
                            wafer_count: lot.wafer_count,
                            percent_complete: lot.percent_complete
                          }));
                          const rccpResult = await api.outputRccp({
                            dataset_id: datasetId,
                            output_target: outputConfig.outputTargets,
                            output_target_week: outputConfig.outputTargetWeek,
                            wip_lot_detail: wipLotDetail,
                            available_hours: availableHours,
                            output_completion_threshold: outputConfig.completionThreshold
                          });
                          setOutputResult(rccpResult);
                          const predResult = await api.outputPrediction({
                            dataset_id: datasetId,
                            current_week: outputConfig.outputTargetWeek,
                            wip_lot_detail: wipLotDetail,
                            n_weeks: outputConfig.predictionWeeks
                          });
                          setOutputPrediction(predResult);
                        } catch (err) {
                          setError("Output RCCP失败: " + err.message);
                        } finally {
                          setOutputLoading(false);
                        }
                      }}
                      disabled={outputLoading || !outputConfig.outputTargetWeek}
                    >
                      {outputLoading ? "计算中..." : "运行 Output RCCP"}
                    </button>
                  </>
                ) : (
                  <div className="empty-state">启用 Output 视角后，可在这里配置产出目标和 WIP 预测。</div>
                )}
              </div>

              <div className="content-stack">
                {outputActive && outputResult ? (
                  <div className="panel" style={{ borderLeft: "4px solid #2a9d8f" }}>
                    <div className="panel-header">
                      <h2>Output RCCP 结果</h2>
                      <span>产出视角产能分析（含WIP后续工序）</span>
                    </div>
                    <div className="mini-grid four">
                      <MetricCard label="产出目标周" value={outputResult.output_target_week || outputConfig.outputTargetWeek} hint="规划周期" />
                      <MetricCard label="WIP占比" value={formatPercent(outputResult.overall_wip_share_pct || 0)} hint="WIP后续工序小时占比" />
                      <MetricCard
                        label="产出缺口"
                        value={outputResult.output_gap ? `${formatNumber(Object.values(outputResult.output_gap).reduce((a, b) => a + b, 0))}片` : "0片"}
                        hint={outputResult.feasible ? "产能充足" : "产能不足"}
                      />
                      <MetricCard
                        label="预测完成度"
                        value={outputResult.total_predicted_output > 0 ? formatPercent(outputResult.total_predicted_output / totalOutputTarget * 100) : "--"}
                        hint={`预测${outputResult.total_predicted_output || 0}片`}
                      />
                    </div>

                    {outputPrediction?.predictions ? (
                      <div className="result-card" style={{ marginTop: "12px" }}>
                        <h3>产出预测 (未来 {outputConfig.predictionWeeks} 周)</h3>
                        <div className="mini-grid three" style={{ marginBottom: "8px" }}>
                          <MetricCard label="总 WIP" value={`${outputPrediction.total_wip_wafers || 0} 片`} hint="当前在制库存" />
                          <MetricCard label="预测周数" value={Object.keys(outputPrediction.predictions_by_week || {}).length} hint="有产出的周数" />
                          <MetricCard label="产品数" value={Object.keys(outputPrediction.predictions_by_product || {}).length} hint="涉及产品数" />
                        </div>
                        <div className="table-wrap compact">
                          <table>
                            <thead>
                              <tr>
                                <th>周</th>
                                <th>产品</th>
                                <th>预测产出</th>
                                <th>Lot数</th>
                                <th>当前完成度</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(outputPrediction.predictions || []).slice(0, 12).map((item, idx) => (
                                <tr key={idx}>
                                  <td>{item.week_id}</td>
                                  <td>{item.product_id}</td>
                                  <td style={{ color: "#2a9d8f", fontWeight: "bold" }}>{item.predicted_wafers}</td>
                                  <td>{item.source_wip_lots}</td>
                                  <td>{Math.round(item.avg_percent_complete_now * 100)}%</td>
                                </tr>
                              ))}
                              {!outputPrediction.predictions?.length ? <tr><td colSpan="5" className="empty-cell">无预测数据</td></tr> : null}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    ) : null}

                    {outputResult.capacity_demand?.length > 0 ? (
                      <div className="result-card" style={{ marginTop: "12px" }}>
                        <h3>产能需求分解</h3>
                        <div className="table-wrap compact">
                          <table>
                            <thead>
                              <tr>
                                <th>机台组</th>
                                <th>WIP后续小时</th>
                                <th>新投入小时</th>
                                <th>总需求</th>
                                <th>可用产能</th>
                                <th>Loading</th>
                              </tr>
                            </thead>
                            <tbody>
                              {(outputResult.capacity_demand || []).slice(0, 10).map((item) => (
                                <tr key={item.tool_group_id}>
                                  <td>{item.tool_group_id}</td>
                                  <td>{formatNumber(item.wip_remaining_hours)}</td>
                                  <td>{formatNumber(item.new_input_hours)}</td>
                                  <td>{formatNumber(item.total_demand_hours)}</td>
                                  <td>{formatNumber(item.available_hours)}</td>
                                  <td className={item.loading_pct > 100 ? "warn" : ""}>{formatPercent(item.loading_pct)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                        <p className="muted" style={{ marginTop: "8px" }}>需求 = WIP后续工序小时 + 新投入本周工序小时</p>
                      </div>
                    ) : null}

                    {outputResult.input_recommendations && Object.keys(outputResult.input_recommendations).length > 0 ? (
                      <div className="result-card" style={{ marginTop: "12px" }}>
                        <h3>投入建议</h3>
                        <div className="mini-grid three">
                          {Object.entries(outputResult.input_recommendations || {}).slice(0, 6).map(([product, wafers]) => (
                            <MetricCard key={product} label={product} value={`${formatNumber(wafers)} wafers`} hint="建议投入量（填补产出缺口）" />
                          ))}
                        </div>
                      </div>
                    ) : null}

                    {!outputResult.feasible ? (
                      <div className="result-card warn" style={{ marginTop: "12px" }}>
                        <h3 style={{ color: "#e63946" }}>产能不足预警</h3>
                        <p>当前WIP+可用产能无法满足产出目标。</p>
                        <p className="muted">建议：增加新投入量或调整产出目标</p>
                      </div>
                    ) : null}
                  </div>
                ) : outputActive ? (
                  <div className="panel">
                    <div className="panel-header">
                      <h2>Output RCCP 结果</h2>
                      <span>请配置产出目标并运行计算</span>
                    </div>
                    <div className="empty-state">请在左侧配置产出目标，然后点击“运行 Output RCCP”。</div>
                  </div>
                ) : null}

                <div className="panel">
                  <div className="panel-header">
                    <h2>What-if</h2>
                    <span>基于基线 RCCP 快速重算</span>
                  </div>
                  <label className="field">
                    <span>扰动类型</span>
                    <select value={whatIfConfig.perturbationType} onChange={(event) => setWhatIfConfig((prev) => ({ ...prev, perturbationType: event.target.value }))}>
                      <option value="tool_down">机台停机</option>
                      <option value="demand_change">需求变化</option>
                    </select>
                  </label>
                  {whatIfConfig.perturbationType === "tool_down" ? (
                    <>
                      <label className="field">
                        <span>目标机台组</span>
                        <select value={whatIfConfig.toolGroupId} onChange={(event) => setWhatIfConfig((prev) => ({ ...prev, toolGroupId: event.target.value }))}>
                          {toolOptions.map((item) => (
                            <option key={item.tool_group_id} value={item.tool_group_id}>{item.tool_group_id}</option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>损失小时</span>
                        <input type="number" value={whatIfConfig.hoursLost} onChange={(event) => setWhatIfConfig((prev) => ({ ...prev, hoursLost: event.target.value }))} />
                      </label>
                    </>
                  ) : (
                    <>
                      <label className="field">
                        <span>目标产品</span>
                        <select value={whatIfConfig.productId} onChange={(event) => setWhatIfConfig((prev) => ({ ...prev, productId: event.target.value }))}>
                          {productOptions.map((item) => (
                            <option key={item.product_id} value={item.product_id}>{item.product_id}</option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>需求增量</span>
                        <input type="number" value={whatIfConfig.deltaWafers} onChange={(event) => setWhatIfConfig((prev) => ({ ...prev, deltaWafers: event.target.value }))} />
                      </label>
                    </>
                  )}
                  <button className="secondary-button" onClick={runWhatIf} disabled={!analysis || loading}>
                    运行 What-if
                  </button>

                  <div className="result-card" style={{ marginTop: "12px" }}>
                    <h3>What-if 输出</h3>
                    <div className="table-wrap compact">
                      <table>
                        <thead>
                          <tr>
                            <th>Tool Group</th>
                            <th>Baseline</th>
                            <th>Scenario</th>
                            <th>Delta</th>
                            <th>Changed</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(analysis?.whatIf?.diff_table || []).slice(0, 10).map((item) => (
                            <tr key={item.tool_group_id}>
                              <td>{item.tool_group_id}</td>
                              <td>{formatPercent(item.loading_baseline)}</td>
                              <td>{formatPercent(item.loading_scenario)}</td>
                              <td>{formatNumber(item.delta_pp)} pp</td>
                              <td>{item.status_changed ? "Yes" : "No"}</td>
                            </tr>
                          ))}
                          {!analysis?.whatIf?.diff_table?.length ? (
                            <tr><td colSpan="5" className="empty-cell">运行 What-if 后这里会出现差异结果。</td></tr>
                          ) : null}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </section>
        ) : null}

        {activeTab === "agent" ? (
          <section className="tab-stage">
            <div className="content-grid planning-grid">
              <div className="panel">
                <div className="panel-header">
                  <h2>LLM配置</h2>
                  <span>Agent问答模型设置</span>
                </div>
                <label className="field">
                  <span>API提供商</span>
                  <select
                    value={llmConfig.provider}
                    onChange={(e) => {
                      const provider = e.target.value;
                      const defaults = {
                        vllm: { model: "Qwen/Qwen2.5-32B-Instruct", baseUrl: "http://vllm:8000/v1" },
                        openai: { model: "gpt-4o", baseUrl: "https://api.openai.com/v1" },
                        volces: { model: "doubao-pro-32k", baseUrl: "https://ark.cn-beijing.volces.com/api/v3" },
                        dashscope: { model: "qwen-max", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
                        custom: { model: "", baseUrl: "" }
                      };
                      setLlmConfig(prev => ({
                        ...prev,
                        provider,
                        model: defaults[provider]?.model || prev.model,
                        baseUrl: defaults[provider]?.baseUrl || prev.baseUrl
                      }));
                    }}
                  >
                    <option value="vllm">本地 vLLM</option>
                    <option value="openai">OpenAI</option>
                    <option value="volces">火山引擎</option>
                    <option value="dashscope">通义千问</option>
                    <option value="custom">自定义</option>
                  </select>
                </label>
                <label className="field">
                  <span>模型ID</span>
                  <input type="text" value={llmConfig.model} onChange={(e) => setLlmConfig(prev => ({ ...prev, model: e.target.value }))} placeholder="如: gpt-4o, qwen-max" />
                </label>
                {llmConfig.provider !== "vllm" ? (
                  <label className="field">
                    <span>API密钥</span>
                    <input type="password" value={llmConfig.apiKey} onChange={(e) => setLlmConfig(prev => ({ ...prev, apiKey: e.target.value }))} placeholder="sk-xxx 或 API Key" />
                    <span className="muted">将保存至环境变量LLM_API_KEY</span>
                  </label>
                ) : null}
                {llmConfig.provider === "custom" ? (
                  <label className="field">
                    <span>API地址</span>
                    <input type="text" value={llmConfig.baseUrl} onChange={(e) => setLlmConfig(prev => ({ ...prev, baseUrl: e.target.value }))} placeholder="https://your-api.com/v1" />
                  </label>
                ) : null}
                <button
                  className="secondary-button"
                  onClick={async () => {
                    try {
                      const payload = {
                        provider: llmConfig.provider,
                        model: llmConfig.model,
                        api_key: llmConfig.apiKey,
                        base_url: llmConfig.baseUrl
                      };
                      const engineResult = await api.configureLlm(payload);
                      let agentResult = null;
                      let agentError = "";

                      try {
                        agentResult = await api.configureAgentLlm(payload);
                      } catch (error) {
                        agentError = error.message;
                      }

                      localStorage.setItem("llmConfig", JSON.stringify(llmConfig));
                      setError("");
                      if (agentResult) {
                        alert(
                          `LLM配置已保存！\n引擎模型: ${engineResult.model}\nAgent模型: ${agentResult.model}\n地址: ${agentResult.base_url}\n\nAgent问答将使用新配置。`
                        );
                      } else {
                        alert(
                          `引擎侧LLM配置已保存。\n引擎模型: ${engineResult.model}\n\n但 Agent 同步失败：${agentError}\n可稍后重试，或先确认 8000 Agent 服务是否可访问。`
                        );
                      }
                    } catch (err) {
                      setError("保存失败: " + err.message);
                    }
                  }}
                >
                  保存配置
                </button>
              </div>

              <div className="panel agent-panel">
                <div className="panel-header">
                  <h2>Agent Chat</h2>
                  <span>自然语言入口，适合探索性问题</span>
                </div>
                <form className="agent-form" onSubmit={askAgent}>
                  <textarea value={agentQuestion} onChange={(event) => setAgentQuestion(event.target.value)} rows={6} />
                  <button className="primary-button" type="submit" disabled={agentLoading}>
                    {agentLoading ? "提问中..." : "发送问题"}
                  </button>
                </form>
                <div className="result-card">
                  <h3>回答</h3>
                  <p className="pre-wrap">{agentResult?.answer || "这里会显示 Agent 的最终回答。"}</p>
                </div>
                <div className="result-card" style={{ marginTop: "12px" }}>
                  <h3>工具调用</h3>
                  <div className="list-stack">
                    {(agentResult?.tool_calls || []).map((item, index) => (
                      <div key={`${item.tool_name}-${index}`} className="list-item">
                        <div>
                          <strong>{item.tool_name}</strong>
                          <div className="muted">{item.elapsed_seconds}s</div>
                        </div>
                        <StatusPill label={item.success ? "success" : "failed"} tone={item.success ? "ok" : "danger"} />
                      </div>
                    ))}
                    {!agentResult?.tool_calls?.length ? <div className="empty-state">等待一次有效的 Agent 调用。</div> : null}
                  </div>
                </div>
              </div>
            </div>
          </section>
        ) : null}
      </main>
    </div>
  );
}

export default App;
