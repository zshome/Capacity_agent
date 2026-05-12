from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "assets"
PNG = OUT_DIR / "capacity_agent_business_architecture.png"

W, H = 2400, 1720
MARGIN_X = 80
TOP = 220
COL_W = 380
COL_GAP = 75
CARD_H = 128
CARD_GAP = 26

BG = "#f5f8fb"
INK = "#16324f"
MUTED = "#637083"
BLUE = "#1f6feb"
TEAL = "#0f9f92"
AMBER = "#d79028"
GREEN = "#2e7d32"
PURPLE = "#6f42c1"
LINE = "#b7c6d8"


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
F_LAYER = font(28, True)
F_CARD_TITLE = font(25, True)
F_CARD_BODY = font(22)
F_NOTE = font(20)


LAYERS = [
    {
        "title": "业务数据层",
        "color": BLUE,
        "cards": [
            ("R6 主生产计划", "产品 / 月份 / 投片量"),
            ("MES WIP 明细", "Lot / 产品 / 当前工序 / 状态"),
            ("工艺路线", "产品 / 工序 / 机台组 / 工时"),
            ("设备产能", "机台组 / 台数 / OEE / 可用小时"),
        ],
    },
    {
        "title": "数据治理与导入层",
        "color": TEAL,
        "cards": [
            ("Excel 工作簿导入", "5 个必填 Sheet"),
            ("字段校验", "Sheet / 必填列 / 类型转换"),
            ("时间窗识别", "R6 月度 / 周计划"),
            ("统一数据集", "前后端共享上下文"),
        ],
    },
    {
        "title": "算法分析层",
        "color": AMBER,
        "cards": [
            ("RCCP 产能审查", "计划负载与可用产能对比"),
            ("WIP 后续负载分摊", "跨周 / 月分摊剩余占机"),
            ("瓶颈评分", "Loading / Queue / Drift"),
            ("LP 投片优化", "约束下调整产品投片量"),
            ("DES 仿真验证", "排队与周期时间风险"),
            ("Output 产出预测", "从产出目标反推 WIP"),
            ("What-if 情景推演", "机台损失 / 需求变化"),
        ],
    },
    {
        "title": "决策输出层",
        "color": PURPLE,
        "cards": [
            ("整体 Loading", "全局产能紧张度"),
            ("RCCP 热点", "高负载机台组排序"),
            ("主瓶颈", "优先处理对象"),
            ("需求调整量", "原计划 → 优化计划"),
            ("WIP 占机", "在制品剩余负载"),
            ("承诺可行性", "commit_feasible"),
            ("冻结状态", "可冻结 / 待确认 / 不可冻结"),
        ],
    },
    {
        "title": "业务闭环层",
        "color": GREEN,
        "cards": [
            ("R6 计划评审", "月度主生产计划审查"),
            ("投片决策", "产品 wafer start 建议"),
            ("产能承诺", "对产品/项目组承诺"),
            ("周计划冻结", "冻结前风险校验"),
            ("瓶颈改善", "设备 / 排产 / 外协动作"),
        ],
    },
]


def rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def text_center(draw, xy, text, fnt, fill):
    x1, y1, x2, y2 = xy
    bbox = draw.multiline_textbbox((0, 0), text, font=fnt, spacing=8, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text((x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2), text, font=fnt, fill=fill, spacing=8, align="center")


def draw_arrow(draw, start, end, color=LINE):
    sx, sy = start
    ex, ey = end
    draw.line((sx, sy, ex, ey), fill=color, width=5)
    # Arrow head
    draw.polygon([(ex, ey), (ex - 18, ey - 12), (ex - 18, ey + 12)], fill=color)


def draw_card(draw, x, y, title, body, color):
    shadow = (x + 5, y + 7, x + COL_W + 5, y + CARD_H + 7)
    rounded_rect(draw, shadow, 22, "#dce5ef")
    rounded_rect(draw, (x, y, x + COL_W, y + CARD_H), 22, "white", "#d8e1ec", 2)
    draw.rounded_rectangle((x + 18, y + 18, x + 28, y + CARD_H - 18), radius=5, fill=color)
    draw.text((x + 48, y + 27), title, font=F_CARD_TITLE, fill=INK)
    draw.text((x + 48, y + 70), body, font=F_CARD_BODY, fill=MUTED)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    # Header
    draw.text((MARGIN_X, 48), "Capacity Agent 业务架构图", font=F_TITLE, fill=INK)
    draw.text((MARGIN_X, 116), "面向存储芯片制造厂 R6 投片计划、WIP-aware 产能审查与计划冻结闭环", font=F_SUB, fill=MUTED)
    draw.rounded_rectangle((1770, 52, 2308, 112), radius=24, fill="#eaf3ff", outline="#c8dcf5", width=2)
    text_center(draw, (1770, 52, 2308, 112), "R6计划 · WIP · RCCP · LP · DES", F_NOTE, BLUE)

    # Layer panels and cards
    max_col_h = TOP + 7 * (CARD_H + CARD_GAP) + 76
    for i, layer in enumerate(LAYERS):
        x = MARGIN_X + i * (COL_W + COL_GAP)
        color = layer["color"]
        panel_h = max_col_h - TOP
        rounded_rect(draw, (x - 18, TOP - 24, x + COL_W + 18, TOP + panel_h), 30, "#eef4fa", "#d6e2ef", 2)
        rounded_rect(draw, (x - 18, TOP - 24, x + COL_W + 18, TOP + 36), 30, color)
        text_center(draw, (x - 18, TOP - 24, x + COL_W + 18, TOP + 36), layer["title"], F_LAYER, "white")

        y = TOP + 72
        for title, body in layer["cards"]:
            draw_card(draw, x, y, title, body, color)
            y += CARD_H + CARD_GAP

        if i < len(LAYERS) - 1:
            sx = x + COL_W + 24
            ex = x + COL_W + COL_GAP - 22
            draw_arrow(draw, (sx, TOP + 450), (ex, TOP + 450))

    # Bottom decision spine
    spine_y = H - 165
    draw.rounded_rectangle((MARGIN_X, spine_y - 38, W - MARGIN_X, spine_y + 38), radius=26, fill="#16324f")
    text_center(
        draw,
        (MARGIN_X, spine_y - 38, W - MARGIN_X, spine_y + 38),
        "业务闭环：导入资料 → 运行完整分析 → 审查瓶颈与WIP → 优化投片量 → 形成承诺/冻结结论 → 留档复盘",
        F_NOTE,
        "white",
    )

    # Watermark-like note
    draw.text((W - 650, H - 54), "Generated for Capacity Agent project documentation", font=font(18), fill="#9aa8b7")

    img.save(PNG, quality=96)
    print(PNG)


if __name__ == "__main__":
    main()
