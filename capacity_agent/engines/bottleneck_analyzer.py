"""
Bottleneck Analyzer
===================

三维瓶颈识别:
  1. Loading 维度: loading > threshold
  2. 队列维度: Kingman G/G/c 公式估计 cycle time 恶化
  3. 漂移维度: 近 N 周 loading 趋势

Kingman 近似 (G/G/c 队列等待时间):
  Wq ≈ (ρ / (1-ρ)) * ((Ca² + Cs²) / 2) * (1/μ)

  其中:
    ρ = utilization (loading 率)
    Ca = arrival CV (到达过程变异系数)
    Cs = service CV (服务时间变异系数)
    μ = service rate
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ============================================================
# Data classes
# ============================================================
@dataclass
class BottleneckScore:
    """单个 tool group 的瓶颈评分"""
    tool_group_id: str
    loading_pct: float
    queue_score: float          # 0-100, 基于 Kingman 估计
    drift_score: float          # 0-100, 基于历史趋势
    composite_score: float      # 加权综合得分
    rank: int                   # 排名
    severity: str               # "minor" | "moderate" | "severe" | "critical"
    expected_wait_hours: float  # 预估等待时间
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_group_id": self.tool_group_id,
            "loading_pct": round(self.loading_pct, 2),
            "queue_score": round(self.queue_score, 2),
            "drift_score": round(self.drift_score, 2),
            "composite_score": round(self.composite_score, 2),
            "rank": self.rank,
            "severity": self.severity,
            "expected_wait_hours": round(self.expected_wait_hours, 2),
            "notes": self.notes,
        }


@dataclass
class BottleneckResult:
    bottlenecks: list[BottleneckScore]
    primary_bottleneck: str | None
    secondary_bottlenecks: list[str]
    emerging_bottlenecks: list[str]    # 漂移分析出的次瓶颈
    computed_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bottlenecks": [b.to_dict() for b in self.bottlenecks],
            "primary_bottleneck": self.primary_bottleneck,
            "secondary_bottlenecks": self.secondary_bottlenecks,
            "emerging_bottlenecks": self.emerging_bottlenecks,
            "computed_at": self.computed_at.isoformat(),
            "metadata": self.metadata,
        }


# ============================================================
# Kingman G/G/c 排队近似
# ============================================================
def kingman_wait_time(
    utilization: float,
    service_rate: float,       # μ, units/hour
    n_servers: int = 1,
    ca_squared: float = 1.0,   # 到达 CV² (Poisson 到达 = 1)
    cs_squared: float = 0.5,   # 服务 CV² (半导体加工通常 0.3-0.7)
) -> float:
    """
    Kingman G/G/c 公式估计平均等待时间

    单服务器 (G/G/1):
        Wq = (ρ/(1-ρ)) * ((Ca²+Cs²)/2) * (1/μ)

    多服务器 (G/G/c) 近似:
        Wq = (ρ^(sqrt(2(c+1))) / (c*(1-ρ))) * ((Ca²+Cs²)/2) * (1/μ)

    返回: 平均等待时间 (hours)
    """
    if utilization >= 0.999:
        # 接近 100% 时等待时间趋于无穷,返回大数值
        return 999.0

    if utilization <= 0:
        return 0.0

    rho = utilization
    c = max(1, n_servers)
    mu = max(service_rate, 1e-6)

    if c == 1:
        wq = (rho / (1 - rho)) * ((ca_squared + cs_squared) / 2.0) * (1.0 / mu)
    else:
        # Sakasegawa 近似 for G/G/c
        power = np.sqrt(2 * (c + 1))
        wq = (rho ** power / (c * (1 - rho))) * ((ca_squared + cs_squared) / 2.0) * (1.0 / mu)

    return float(wq)


def queue_score_from_wait(wait_hours: float) -> float:
    """
    把等待时间映射到 0-100 的评分
      wait < 1h    → 0-20
      wait 1-4h    → 20-50
      wait 4-12h   → 50-80
      wait > 12h   → 80-100
    """
    if wait_hours < 1:
        return wait_hours * 20.0
    elif wait_hours < 4:
        return 20.0 + (wait_hours - 1) * 10.0
    elif wait_hours < 12:
        return 50.0 + (wait_hours - 4) * 3.75
    else:
        return min(80.0 + (wait_hours - 12) * 1.0, 100.0)


# ============================================================
# Drift 分析 (历史趋势)
# ============================================================
def compute_drift_score(loading_history: list[float]) -> tuple[float, list[str]]:
    """
    根据历史 loading 序列计算漂移得分 (0-100)
    评分逻辑: 趋势斜率 + 加速度

    返回 (score, notes)
    """
    notes = []

    if len(loading_history) < 3:
        return 0.0, ["历史数据不足,无法分析漂移"]

    arr = np.array(loading_history)
    n = len(arr)

    # 1. 线性斜率 (per period)
    x = np.arange(n)
    slope = np.polyfit(x, arr, 1)[0]

    # 2. 末尾加速度 (后半段斜率 - 前半段斜率)
    mid = n // 2
    if mid >= 2:
        s1 = np.polyfit(np.arange(mid), arr[:mid], 1)[0]
        s2 = np.polyfit(np.arange(n - mid), arr[mid:], 1)[0]
        accel = s2 - s1
    else:
        accel = 0.0

    # 3. 评分
    # 斜率 > 2 pp/period 显著上升
    # 加速度 > 1 pp/period² 表示恶化
    slope_score = max(0, min(50, slope * 10))
    accel_score = max(0, min(50, accel * 25))
    score = slope_score + accel_score

    # 4. 解释性 notes
    if slope > 2:
        notes.append(f"loading 周环比上升 {slope:.1f}pp")
    if accel > 1:
        notes.append(f"loading 上升加速 (二阶 +{accel:.1f}pp)")
    if arr[-1] > 85 and slope > 0:
        notes.append("loading 已超 85% 且仍在上升,需提前干预")

    return float(score), notes


# ============================================================
# 主分析函数
# ============================================================
@dataclass
class BottleneckInput:
    loading_table: list[dict[str, Any]]   # 来自 RCCP 输出
    historical_loading: dict[str, list[float]] = field(default_factory=dict)
    # {tool_group_id: [last 4-8 weeks loading_pct]}

    service_rates: dict[str, float] = field(default_factory=dict)
    # {tool_group_id: wafers/hour}, 默认 1.0

    n_servers: dict[str, int] = field(default_factory=dict)
    # {tool_group_id: 该 tool group 的设备数}, 默认 1

    weights: dict[str, float] = field(default_factory=lambda: {
        "loading": 0.5, "queue": 0.3, "drift": 0.2,
    })


def severity_from_score(score: float) -> str:
    if score >= 80:
        return "critical"
    elif score >= 60:
        return "severe"
    elif score >= 40:
        return "moderate"
    else:
        return "minor"


def analyze_bottleneck(inp: BottleneckInput) -> BottleneckResult:
    """三维瓶颈识别"""
    bottlenecks: list[BottleneckScore] = []

    for row in inp.loading_table:
        tg_id = row["tool_group_id"]
        loading = float(row["loading_pct"])
        rho = min(loading / 100.0, 0.999)

        # 1. Loading score (直接映射)
        loading_score = min(loading, 100.0)

        # 2. Queue score (Kingman)
        mu = inp.service_rates.get(tg_id, 1.0)
        c = inp.n_servers.get(tg_id, 1)
        wait = kingman_wait_time(rho, mu, c)
        q_score = queue_score_from_wait(wait)

        # 3. Drift score
        history = inp.historical_loading.get(tg_id, [])
        d_score, d_notes = compute_drift_score(history)

        # 4. 综合得分
        w = inp.weights
        composite = (
            w["loading"] * loading_score
            + w["queue"] * q_score
            + w["drift"] * d_score
        )

        notes = []
        if loading > 85:
            notes.append(f"利用率 {loading:.1f}% 超过 85% 警戒线")
        if wait > 8:
            notes.append(f"预估等待时间 {wait:.1f}h, cycle time 已显著恶化")
        notes.extend(d_notes)

        bottlenecks.append(BottleneckScore(
            tool_group_id=tg_id,
            loading_pct=loading,
            queue_score=q_score,
            drift_score=d_score,
            composite_score=composite,
            rank=0,  # 稍后填
            severity=severity_from_score(composite),
            expected_wait_hours=wait,
            notes=notes,
        ))

    # 按综合得分排序并填入 rank
    bottlenecks.sort(key=lambda x: x.composite_score, reverse=True)
    for i, b in enumerate(bottlenecks, 1):
        b.rank = i

    primary = bottlenecks[0].tool_group_id if bottlenecks and bottlenecks[0].severity in ("critical", "severe") else None
    secondary = [b.tool_group_id for b in bottlenecks[1:4] if b.severity in ("severe", "moderate")]
    emerging = [b.tool_group_id for b in bottlenecks if b.drift_score > 30 and b.loading_pct < 85]

    return BottleneckResult(
        bottlenecks=bottlenecks,
        primary_bottleneck=primary,
        secondary_bottlenecks=secondary,
        emerging_bottlenecks=emerging,
        computed_at=datetime.utcnow(),
        metadata={"weights": inp.weights, "n_analyzed": len(bottlenecks)},
    )


# ============================================================
# CLI 测试
# ============================================================
if __name__ == "__main__":
    # 模拟 RCCP 输出
    loading_table = [
        {"tool_group_id": "LITHO_193i", "loading_pct": 97.9, "available_hours": 672, "demand_hours": 658},
        {"tool_group_id": "ETCH_DRY_A", "loading_pct": 88.5, "available_hours": 1008, "demand_hours": 892},
        {"tool_group_id": "DEPO_CVD_3", "loading_pct": 75.9, "available_hours": 672, "demand_hours": 510},
        {"tool_group_id": "CMP_OXIDE",  "loading_pct": 70.0, "available_hours": 480, "demand_hours": 336},
    ]

    historical = {
        "LITHO_193i": [88, 90, 92, 95, 97.9],     # 显著上升
        "ETCH_DRY_A": [85, 86, 87, 88, 88.5],     # 缓慢上升
        "DEPO_CVD_3": [78, 76, 75, 74, 75.9],     # 平稳
        "CMP_OXIDE":  [60, 63, 66, 68, 70.0],     # 缓慢上升 (emerging)
    }

    inp = BottleneckInput(
        loading_table=loading_table,
        historical_loading=historical,
        service_rates={"LITHO_193i": 2.0, "ETCH_DRY_A": 3.0, "DEPO_CVD_3": 5.0, "CMP_OXIDE": 4.0},
        n_servers={"LITHO_193i": 8, "ETCH_DRY_A": 12, "DEPO_CVD_3": 6, "CMP_OXIDE": 4},
    )

    result = analyze_bottleneck(inp)
    print(f"Primary bottleneck: {result.primary_bottleneck}")
    print(f"Secondary: {result.secondary_bottlenecks}")
    print(f"Emerging: {result.emerging_bottlenecks}")
    print()
    print(f"{'Rank':<5}{'Tool Group':<15}{'Load%':>7}{'Queue':>7}{'Drift':>7}{'Score':>7}  Severity")
    print("-" * 70)
    for b in result.bottlenecks:
        print(f"{b.rank:<5}{b.tool_group_id:<15}{b.loading_pct:>7.1f}{b.queue_score:>7.1f}{b.drift_score:>7.1f}{b.composite_score:>7.1f}  {b.severity}")
        for note in b.notes:
            print(f"     └─ {note}")
