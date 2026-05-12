from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "assets"
PNG = OUT_DIR / "capacity_agent_horizontal_layered_architecture.png"

W, H = 2400, 1500
BG = "#f6f9fc"
INK = "#17324d"
MUTED = "#657386"
LINE = "#b7c8d9"
WHITE = "#ffffff"

COLORS = {
    "business": "#2d7dd2",
    "app": "#14a098",
    "engine": "#dc8f20",
    "service": "#6f42c1",
    "data": "#2e7d32",
}


def font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


F_TITLE = font(54, True)
F_SUB = font(26)
F_LAYER = font(30, True)
F_CARD = font(25, True)
F_BODY = font(21)
F_SMALL = font(18)


LAYERS = [
    {
        "key": "business",
        "name": "业务决策层",
        "desc": "评审 · 决策 · 承诺 · 冻结",
        "cards": [
            ("R6 计划评审", "月度产品投片量审查"),
            ("投片决策", "优化后 wafer start 建议"),
            ("产能承诺", "对产品/项目组形成承诺"),
            ("周计划冻结", "冻结前风险校验"),
            ("瓶颈改善", "设备 / 排产 / 外协动作"),
        ],
    },
    {
        "key": "app",
        "name": "前端应用层",
        "desc": "导入 · 分析 · 计划 · 问答",
        "cards": [
            ("全局控制台", "数据集 / 时间窗 / Excel 导入"),
            ("总览页", "Loading / 可行性 / 主瓶颈"),
            ("生产计划页", "冻结状态 / 承诺可行性"),
            ("Output & What-if", "产出预测 / 扰动推演"),
            ("Agent Chat", "自然语言解释与查询"),
        ],
    },
    {
        "key": "engine",
        "name": "算法分析层",
        "desc": "审查 · 优化 · 验证 · 预测",
        "cards": [
            ("RCCP", "计划负载 vs 可用产能"),
            ("WIP 分摊", "后续工序跨周/月占机"),
            ("瓶颈评分", "Loading / Queue / Drift"),
            ("LP 优化", "产能约束下调整投片"),
            ("DES 校验", "排队与周期风险仿真"),
            ("Output 预测", "从产出目标反推"),
        ],
    },
    {
        "key": "service",
        "name": "服务接口层",
        "desc": "数据 · 引擎 · 计划 · 问答",
        "cards": [
            ("Dataset API", "导入 / 模板 / 摘要"),
            ("Engine API", "RCCP / LP / DES / What-if"),
            ("Plan API", "生产计划生成"),
            ("Agent API", "LLM 配置与问答"),
            ("配置与校验", "求解器 / CORS / 字段校验"),
        ],
    },
    {
        "key": "data",
        "name": "数据基础层",
        "desc": "计划 · WIP · 路线 · 产能",
        "cards": [
            ("demand_plan", "R6 月度投片需求"),
            ("wip_lot_detail", "Lot 位置 / 状态 / WIP"),
            ("route_master", "产品路线 / 工时 / 机台组"),
            ("tool_groups", "机台组 / 台数 / 区域"),
            ("oee", "OEE / available hours 日历"),
        ],
    },
]


def draw_round(draw: ImageDraw.ImageDraw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def text_center(draw: ImageDraw.ImageDraw, xy, text, fnt, fill):
    x1, y1, x2, y2 = xy
    bbox = draw.multiline_textbbox((0, 0), text, font=fnt, spacing=6, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text(
        (x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2),
        text,
        font=fnt,
        fill=fill,
        spacing=6,
        align="center",
    )


def wrap_text(draw: ImageDraw.ImageDraw, text: str, fnt, max_width: int) -> str:
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        bbox = draw.textbbox((0, 0), test, font=fnt)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = test
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return "\n".join(lines)


def arrow_down(draw: ImageDraw.ImageDraw, x, y1, y2):
    draw.line((x, y1, x, y2), fill=LINE, width=5)
    draw.polygon([(x, y2), (x - 14, y2 - 20), (x + 14, y2 - 20)], fill=LINE)


def draw_card(draw, x, y, w, h, title, body, color):
    draw_round(draw, (x + 4, y + 6, x + w + 4, y + h + 6), 20, "#dfe8f2")
    draw_round(draw, (x, y, x + w, y + h), 20, WHITE, "#d4e0ec", 2)
    draw_round(draw, (x + 16, y + 16, x + 26, y + h - 16), 5, color)
    draw.text((x + 44, y + 20), title, font=F_CARD, fill=INK)
    draw.text((x + 44, y + 58), body, font=F_BODY, fill=MUTED)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.text((90, 48), "Capacity Agent 横向分层业务架构图", font=F_TITLE, fill=INK)
    draw.text((92, 118), "从数据基础到业务决策，支撑存储芯片制造厂 R6 投片计划与 WIP-aware 产能承诺", font=F_SUB, fill=MUTED)
    draw_round(draw, (1760, 58, 2308, 116), 24, "#eaf3ff", "#c6d9f3", 2)
    text_center(draw, (1760, 58, 2308, 116), "数据 → 服务 → 算法 → 应用 → 决策", F_SMALL, COLORS["business"])

    left = 90
    right = W - 90
    layer_w = right - left
    layer_h = 205
    gap = 38
    start_y = 198
    label_w = 330
    card_gap = 24

    for idx, layer in enumerate(LAYERS):
        y = start_y + idx * (layer_h + gap)
        color = COLORS[layer["key"]]

        draw_round(draw, (left, y, right, y + layer_h), 32, "#edf4fa", "#d4e2ef", 2)
        draw_round(draw, (left, y, left + label_w, y + layer_h), 32, color)
        text_center(draw, (left + 26, y + 52, left + label_w - 26, y + layer_h - 52), layer["name"], F_LAYER, WHITE)

        cards = layer["cards"]
        content_x = left + label_w + 34
        available = right - content_x - 28
        card_w = int((available - card_gap * (len(cards) - 1)) / len(cards))
        card_h = 118
        card_y = y + 44
        for cidx, (title, body) in enumerate(cards):
            x = content_x + cidx * (card_w + card_gap)
            draw_card(draw, x, card_y, card_w, card_h, title, body, color)

        if idx < len(LAYERS) - 1:
            arrow_down(draw, W // 2, y + layer_h + 8, y + layer_h + gap - 8)

    # Governance strip
    strip_y = H - 122
    draw_round(draw, (90, strip_y, W - 90, strip_y + 54), 22, "#16324f")
    text_center(
        draw,
        (90, strip_y, W - 90, strip_y + 54),
        "统一治理口径：WIP-aware · 可解释 · 可追溯 · 可优化 · 支撑正式投片承诺与周计划冻结",
        F_SMALL,
        WHITE,
    )

    draw.text((90, H - 40), "建议放置位置：系统介绍 PPT 的“业务架构”页，或 Word 项目说明中的“系统定位/业务闭环”章节。", font=F_SMALL, fill=MUTED)
    draw.text((W - 575, H - 40), "Generated for Capacity Agent project documentation", font=F_SMALL, fill="#9aa8b7")

    img.save(PNG, quality=96)
    print(PNG)


if __name__ == "__main__":
    main()
