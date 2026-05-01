"""
DES Validator (Discrete Event Simulation)
==========================================

用 SimPy 对 top 瓶颈机台做局部仿真,验证 RCCP/LP 输出的可行性。

不做全厂 DES (太慢),只在关键瓶颈机台上仿真 1-4 周,得到:
  - 实际 cycle time 分布 (mean, P50, P95)
  - 队列长度时间序列
  - 利用率 vs 吞吐量曲线
  - 是否会出现 WIP 堆积

依赖: pip install simpy
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

try:
    import simpy
    SIMPY_AVAILABLE = True
except ImportError:
    SIMPY_AVAILABLE = False

logger = logging.getLogger(__name__)


# ============================================================
# Data classes
# ============================================================
@dataclass
class DESToolGroup:
    """单个被仿真的 tool group"""
    tool_group_id: str
    n_machines: int                          # 设备数 (parallel servers)
    service_rate: float                      # μ wafers/hour 单机
    service_cv: float = 0.5                  # 服务时间 CV (default 0.5)
    availability: float = 0.85               # 可用率 (1-downtime%)


@dataclass
class DESJobArrival:
    """到达过程定义"""
    product_id: str
    arrival_rate: float                      # λ wafers/hour
    arrival_cv: float = 1.0                  # 到达 CV (Poisson=1)
    target_tool_groups: list[str] = field(default_factory=list)
    service_time_per_tg: dict[str, float] = field(default_factory=dict)


@dataclass
class DESInput:
    tool_groups: list[DESToolGroup]
    arrivals: list[DESJobArrival]
    sim_duration_hours: float = 168.0       # 1 week default
    warmup_hours: float = 24.0              # warmup 不统计
    n_replications: int = 5                 # 多次仿真取均值


@dataclass
class DESToolGroupStat:
    tool_group_id: str
    avg_utilization: float
    avg_queue_length: float
    max_queue_length: int
    avg_wait_hours: float
    p95_wait_hours: float
    avg_cycle_hours: float
    p95_cycle_hours: float
    throughput_wph: float                    # wafers per hour
    completed_jobs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_group_id": self.tool_group_id,
            "avg_utilization": round(self.avg_utilization, 3),
            "avg_queue_length": round(self.avg_queue_length, 2),
            "max_queue_length": int(self.max_queue_length),
            "avg_wait_hours": round(self.avg_wait_hours, 2),
            "p95_wait_hours": round(self.p95_wait_hours, 2),
            "avg_cycle_hours": round(self.avg_cycle_hours, 2),
            "p95_cycle_hours": round(self.p95_cycle_hours, 2),
            "throughput_wph": round(self.throughput_wph, 2),
            "completed_jobs": self.completed_jobs,
        }


@dataclass
class DESResult:
    feasible: bool
    tool_group_stats: list[DESToolGroupStat]
    risk_flags: list[str]
    sim_metadata: dict[str, Any]
    computed_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "feasible": self.feasible,
            "tool_group_stats": [s.to_dict() for s in self.tool_group_stats],
            "risk_flags": self.risk_flags,
            "sim_metadata": self.sim_metadata,
            "computed_at": self.computed_at.isoformat(),
        }


# ============================================================
# SimPy 模型
# ============================================================
class WaferFab:
    """简化的多机台组车间模型"""

    def __init__(self, env: simpy.Environment, tg_specs: list[DESToolGroup]):
        self.env = env
        self.resources: dict[str, simpy.Resource] = {}
        self.specs: dict[str, DESToolGroup] = {tg.tool_group_id: tg for tg in tg_specs}

        for tg in tg_specs:
            # 考虑 availability: 等效设备数 = n_machines * availability
            effective_cap = max(1, int(round(tg.n_machines * tg.availability)))
            self.resources[tg.tool_group_id] = simpy.Resource(env, capacity=effective_cap)

        # 统计
        self.queue_log: dict[str, list[tuple[float, int]]] = {tg.tool_group_id: [] for tg in tg_specs}
        self.wait_log: dict[str, list[float]] = {tg.tool_group_id: [] for tg in tg_specs}
        self.busy_time: dict[str, float] = {tg.tool_group_id: 0.0 for tg in tg_specs}
        self.last_busy_check: dict[str, float] = {tg.tool_group_id: 0.0 for tg in tg_specs}
        self.completed_jobs: dict[str, int] = {tg.tool_group_id: 0 for tg in tg_specs}

    def process_wafer(self, product_id: str, route: list[str], service_times: dict[str, float], rng):
        """单个 wafer 走完路径"""
        cycle_start = self.env.now

        for tg_id in route:
            res = self.resources[tg_id]
            spec = self.specs[tg_id]

            # 记录到达队列时刻
            arrive = self.env.now

            with res.request() as req:
                # 队列长度记录
                self.queue_log[tg_id].append((self.env.now, len(res.queue)))
                yield req

                # 等待时间
                wait = self.env.now - arrive
                self.wait_log[tg_id].append(wait)

                # 服务时间 (lognormal 用 cv)
                base_st = service_times.get(tg_id, 1.0 / spec.service_rate)
                cv = spec.service_cv
                if cv > 0:
                    sigma = np.sqrt(np.log(1 + cv ** 2))
                    mu = np.log(base_st) - sigma ** 2 / 2
                    st = float(rng.lognormal(mu, sigma))
                else:
                    st = base_st

                yield self.env.timeout(st)
                self.busy_time[tg_id] += st
                self.completed_jobs[tg_id] += 1

        return self.env.now - cycle_start


def _arrival_process(env, fab: WaferFab, arrival: DESJobArrival, rng):
    """到达流: 按指定 rate 持续生成 wafers"""
    inter_mean = 1.0 / arrival.arrival_rate     # hours between arrivals
    cv = arrival.arrival_cv

    while True:
        # 到达间隔
        if cv == 1.0:
            inter = rng.exponential(inter_mean)
        elif cv > 0:
            sigma = np.sqrt(np.log(1 + cv ** 2))
            mu = np.log(inter_mean) - sigma ** 2 / 2
            inter = float(rng.lognormal(mu, sigma))
        else:
            inter = inter_mean

        yield env.timeout(inter)
        env.process(fab.process_wafer(
            arrival.product_id, arrival.target_tool_groups, arrival.service_time_per_tg, rng,
        ))


# ============================================================
# 主仿真函数
# ============================================================
def run_des(inp: DESInput) -> DESResult:
    if not SIMPY_AVAILABLE:
        raise RuntimeError("SimPy not installed. Run: pip install simpy")

    t0 = datetime.utcnow()

    all_replications: list[dict[str, DESToolGroupStat]] = []

    for rep in range(inp.n_replications):
        rng = np.random.default_rng(seed=42 + rep)
        env = simpy.Environment()
        fab = WaferFab(env, inp.tool_groups)

        for arr in inp.arrivals:
            env.process(_arrival_process(env, fab, arr, rng))

        env.run(until=inp.sim_duration_hours)

        # 提取统计
        rep_stats: dict[str, DESToolGroupStat] = {}
        for tg in inp.tool_groups:
            tg_id = tg.tool_group_id
            effective_cap = max(1, int(round(tg.n_machines * tg.availability)))

            wait_arr = np.array(fab.wait_log[tg_id]) if fab.wait_log[tg_id] else np.array([0.0])

            queue_records = fab.queue_log[tg_id]
            queue_lengths = [q for _, q in queue_records] if queue_records else [0]

            elapsed = max(inp.sim_duration_hours - inp.warmup_hours, 1.0)
            util = (fab.busy_time[tg_id] / (effective_cap * inp.sim_duration_hours))
            throughput = fab.completed_jobs[tg_id] / inp.sim_duration_hours

            rep_stats[tg_id] = DESToolGroupStat(
                tool_group_id=tg_id,
                avg_utilization=float(min(util, 1.0)),
                avg_queue_length=float(np.mean(queue_lengths)),
                max_queue_length=int(np.max(queue_lengths)),
                avg_wait_hours=float(np.mean(wait_arr)),
                p95_wait_hours=float(np.percentile(wait_arr, 95)),
                avg_cycle_hours=float(np.mean(wait_arr)) + 1.0 / tg.service_rate,
                p95_cycle_hours=float(np.percentile(wait_arr, 95)) + 1.0 / tg.service_rate,
                throughput_wph=float(throughput),
                completed_jobs=fab.completed_jobs[tg_id],
            )
        all_replications.append(rep_stats)

    # 聚合 N 次 replication 的均值
    final_stats: list[DESToolGroupStat] = []
    risk_flags: list[str] = []

    for tg in inp.tool_groups:
        tg_id = tg.tool_group_id
        runs = [rep[tg_id] for rep in all_replications]
        agg = DESToolGroupStat(
            tool_group_id=tg_id,
            avg_utilization=float(np.mean([r.avg_utilization for r in runs])),
            avg_queue_length=float(np.mean([r.avg_queue_length for r in runs])),
            max_queue_length=int(np.mean([r.max_queue_length for r in runs])),
            avg_wait_hours=float(np.mean([r.avg_wait_hours for r in runs])),
            p95_wait_hours=float(np.mean([r.p95_wait_hours for r in runs])),
            avg_cycle_hours=float(np.mean([r.avg_cycle_hours for r in runs])),
            p95_cycle_hours=float(np.mean([r.p95_cycle_hours for r in runs])),
            throughput_wph=float(np.mean([r.throughput_wph for r in runs])),
            completed_jobs=int(np.mean([r.completed_jobs for r in runs])),
        )
        final_stats.append(agg)

        # 风险检查
        if agg.avg_utilization > 0.95:
            risk_flags.append(f"{tg_id}: 利用率 {agg.avg_utilization*100:.1f}% 超过 95%, 队列将持续累积")
        if agg.p95_wait_hours > 24:
            risk_flags.append(f"{tg_id}: P95 等待 {agg.p95_wait_hours:.1f}h, cycle time 严重恶化")
        if agg.max_queue_length > 100:
            risk_flags.append(f"{tg_id}: 队列峰值 {agg.max_queue_length} wafers, WIP 风险")

    feasible = not risk_flags

    return DESResult(
        feasible=feasible,
        tool_group_stats=final_stats,
        risk_flags=risk_flags,
        sim_metadata={
            "sim_duration_hours": inp.sim_duration_hours,
            "warmup_hours": inp.warmup_hours,
            "n_replications": inp.n_replications,
            "n_tool_groups": len(inp.tool_groups),
            "wall_time_seconds": (datetime.utcnow() - t0).total_seconds(),
        },
        computed_at=datetime.utcnow(),
    )


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    if not SIMPY_AVAILABLE:
        print("SimPy not installed. Run: pip install simpy")
    else:
        # 模拟光刻区瓶颈场景
        tool_groups = [
            DESToolGroup("LITHO_193i", n_machines=8, service_rate=2.0, service_cv=0.5, availability=0.85),
            DESToolGroup("ETCH_DRY_A", n_machines=12, service_rate=3.0, service_cv=0.4, availability=0.9),
        ]

        # 到达: 模拟 1500 wafer/week 的需求
        arrivals = [
            DESJobArrival(
                product_id="28nm_DRAM",
                arrival_rate=8.5,   # ~1428 wafers/week
                arrival_cv=1.0,
                target_tool_groups=["LITHO_193i", "ETCH_DRY_A", "LITHO_193i"],   # reentrant
                service_time_per_tg={"LITHO_193i": 0.5, "ETCH_DRY_A": 0.33},
            ),
        ]

        inp = DESInput(
            tool_groups=tool_groups,
            arrivals=arrivals,
            sim_duration_hours=168.0,   # 1 week
            warmup_hours=24.0,
            n_replications=3,
        )

        result = run_des(inp)
        print(f"Feasible: {result.feasible}")
        print(f"Sim wall time: {result.sim_metadata['wall_time_seconds']:.1f}s")
        print()
        for s in result.tool_group_stats:
            print(f"{s.tool_group_id}:")
            print(f"  utilization     {s.avg_utilization*100:6.1f}%")
            print(f"  queue (avg/max) {s.avg_queue_length:5.1f} / {s.max_queue_length}")
            print(f"  wait avg/p95    {s.avg_wait_hours:5.2f}h / {s.p95_wait_hours:5.2f}h")
            print(f"  throughput      {s.throughput_wph:5.2f} wph")
            print(f"  completed       {s.completed_jobs}")
            print()
        if result.risk_flags:
            print("Risks:")
            for r in result.risk_flags:
                print(f"  ⚠ {r}")
