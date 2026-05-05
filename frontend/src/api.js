const LOCAL_HOST = typeof window !== "undefined" ? (window.location.hostname || "127.0.0.1") : "127.0.0.1";
const ENGINE_BASE = import.meta.env.VITE_ENGINE_URL || `http://${LOCAL_HOST}:8002`;
const AGENT_BASE = import.meta.env.VITE_AGENT_URL || `http://${LOCAL_HOST}:8000`;

async function request(url, options = {}) {
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw new Error(`无法连接服务: ${url}`);
  }
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) {
    throw new Error(data.detail || data.message || "Request failed");
  }
  return data;
}

export const api = {
  listDatasets: () => request(`${ENGINE_BASE}/data/datasets`),
  datasetSummary: (datasetId) => request(`${ENGINE_BASE}/data/dataset_summary?dataset_id=${encodeURIComponent(datasetId)}`),
  wipLotDetail: (datasetId) => request(`${ENGINE_BASE}/data/wip_lot_detail?dataset_id=${encodeURIComponent(datasetId)}`),
  importExcel: async (file) => {
    const formData = new FormData();
    formData.append("file", file);
    return request(`${ENGINE_BASE}/data/excel/import`, { method: "POST", body: formData });
  },
  toolGroupStatus: (datasetId, timeWindow = "") =>
    request(
      `${ENGINE_BASE}/data/tool_group_status?dataset_id=${encodeURIComponent(datasetId)}&time_range=current&time_window=${encodeURIComponent(timeWindow)}`
    ),
  demandPlan: (datasetId, timeWindow) =>
    request(
      `${ENGINE_BASE}/data/demand_plan?dataset_id=${encodeURIComponent(datasetId)}&time_window=${encodeURIComponent(
        timeWindow
      )}`
    ),
  capacityMatrix: (datasetId, products = null) =>
    request(`${ENGINE_BASE}/data/capacity_matrix`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: datasetId, products, route_version: "current" })
    }),
  historicalLoading: (datasetId, toolGroups) =>
    request(`${ENGINE_BASE}/data/historical_loading`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: datasetId, tool_groups: toolGroups, n_weeks: 4 })
    }),
  computeRccp: (payload) =>
    request(`${ENGINE_BASE}/rccp/compute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  analyzeBottleneck: (payload) =>
    request(`${ENGINE_BASE}/bottleneck/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  optimizeLp: (payload) =>
    request(`${ENGINE_BASE}/lp/optimize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  runDes: (payload) =>
    request(`${ENGINE_BASE}/des/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  whatIf: (payload) =>
    request(`${ENGINE_BASE}/whatif/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  agentQuery: (query) =>
    request(`${AGENT_BASE}/agent/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query })
    }),
  configureAgentLlm: (payload) =>
    request(`${AGENT_BASE}/llm/configure`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  getAgentLlmStatus: () => request(`${AGENT_BASE}/llm/status`),
  
  // NEW: Scenario Classification
  classifyScenario: (payload) =>
    request(`${ENGINE_BASE}/scenario/classify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Standard Capacity Calculation
  computeStandardCapacity: (payload) =>
    request(`${ENGINE_BASE}/capacity/standard_compute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Batch Capacity Calculation
  batchComputeCapacity: (payload) =>
    request(`${ENGINE_BASE}/capacity/batch_compute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Allocation Optimization
  optimizeAllocation: (payload) =>
    request(`${ENGINE_BASE}/allocation/optimize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Unified Capacity Analysis (智能路由)
  unifiedCapacityAnalyze: (payload) =>
    request(`${ENGINE_BASE}/capacity/unified_analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Download Excel Template
  downloadExcelTemplate: async () => {
    const response = await fetch(`${ENGINE_BASE}/data/excel/template`);
    if (!response.ok) {
      throw new Error("Failed to download template");
    }
    const blob = await response.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "capacity_import_template.xlsx";
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  },
  
  // NEW: Generate Production Plan
  generateProductionPlan: (payload) =>
    request(`${ENGINE_BASE}/plan/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Generate Production Plan from Dataset
  generateProductionPlanFromDataset: (payload) =>
    request(`${ENGINE_BASE}/plan/generate_from_dataset`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Configure LLM
  configureLlm: (payload) =>
    request(`${ENGINE_BASE}/llm/configure`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // NEW: Get LLM status
  getLlmStatus: () => request(`${ENGINE_BASE}/llm/status`),
  
  // === Output Perspective APIs - 产出视角产能规划 ===
  
  // Output RCCP - 从产出目标计算产能需求
  outputRccp: (payload) =>
    request(`${ENGINE_BASE}/output/rccp/compute`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // Output Prediction - 基于WIP位置预测未来N周产出
  outputPrediction: (payload) =>
    request(`${ENGINE_BASE}/output/prediction/weekly`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }),
  
  // Input Plan - 从产出目标反推投入时间和量
  inputPlan: (payload) =>
    request(`${ENGINE_BASE}/input/plan`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
};
