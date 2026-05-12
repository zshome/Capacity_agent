from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "assets"
PNG = OUT_DIR / "capacity_agent_house_business_architecture.png"

W, H = 2400, 1500
BG = "#f6f9fc"
INK = "#17324d"
MUTED = "#657386"
WHITE = "#ffffff"
LINE = "#bfd0e2"
ROOF = "#1f6feb"
APP = "#14a098"
ENGINE = "#dc8f20"
SERVICE = "#6f42c1"
DATA = "#2e7d32"
FOUNDATION = "#16324f"


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


F_TITLE = font(56, True)
F_SUB = font(26)
F_BIG = font(34, True)
F_LAYER = font(30, True)
F_CARD = font(24, True)
F_BODY = font(20)
F_SMALL = font(18)
F_SIDE = font(24, True)
F_BOTTOM = font(23)


def round_rect(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def center_text(draw, xy, text, fnt, fill, spacing=6):
    x1, y1, x2, y2 = xy
    bbox = draw.multiline_textbbox((0, 0), text, font=fnt, spacing=spacing, align="center")
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text(
        (x1 + (x2 - x1 - tw) / 2, y1 + (y2 - y1 - th) / 2),
        text,
        font=fnt,
        fill=fill,
        spacing=spacing,
        align="center",
    )


def fit_font_size(draw, text: str, max_width: int, start_size: int, bold: bool = False, min_size: int = 16):
    size = start_size
    while size >= min_size:
        fnt = font(size, bold)
        bbox = draw.textbbox((0, 0), text, font=fnt)
        if bbox[2] - bbox[0] <= max_width:
            return fnt
        size -= 1
    return font(min_size, bold)


def draw_card(draw, x, y, w, h, title, body, color):
    round_rect(draw, (x + 5, y + 7, x + w + 5, y + h + 7), 18, "#dce6f0")
    round_rect(draw, (x, y, x + w, y + h), 18, WHITE, "#d5e1ec", 2)
    round_rect(draw, (x + 16, y + 16, x + 26, y + h - 16), 5, color)
    max_text_width = int(w - 62)
    title_font = fit_font_size(draw, title, max_text_width, 24, bold=True, min_size=18)
    body_font = fit_font_size(draw, body, max_text_width, 20, bold=False, min_size=15)
    draw.text((x + 46, y + 20), title, font=title_font, fill=INK)
    draw.text((x + 46, y + 58), body, font=body_font, fill=MUTED)


def draw_relation_badge(draw, x_center, y_top, y_bottom, label):
    badge_w, badge_h = 214, 34
    badge_x = x_center - badge_w // 2
    badge_y = y_top + 8
    draw.line((x_center, y_top, x_center, badge_y), fill="#8fb2d4", width=3)
    round_rect(draw, (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h), 16, "#fff8eb", "#efc27d", 2)
    center_text(draw, (badge_x + 8, badge_y, badge_x + badge_w - 8, badge_y + badge_h), label, F_SMALL, ENGINE)
    line_start = badge_y + badge_h
    draw.line((x_center, line_start, x_center, y_bottom - 10), fill="#8fb2d4", width=3)
    draw.polygon(
        [(x_center, y_bottom), (x_center - 10, y_bottom - 14), (x_center + 10, y_bottom - 14)],
        fill="#8fb2d4",
    )


def draw_layer(draw, x, y, w, h, label, color, cards):
    round_rect(draw, (x, y, x + w, y + h), 24, "#edf4fa", LINE, 2)
    round_rect(draw, (x, y, x + 260, y + h), 24, color)
    center_text(draw, (x + 24, y + 16, x + 236, y + h - 16), label, F_LAYER, WHITE)
    card_gap = 26
    content_x = x + 300
    card_w = int((w - 340 - card_gap * (len(cards) - 1)) / len(cards))
    card_h = h - 54
    for idx, (title, body) in enumerate(cards):
        draw_card(draw, content_x + idx * (card_w + card_gap), y + 27, card_w, card_h, title, body, color)
    return {
        "content_x": content_x,
        "card_w": card_w,
        "card_h": card_h,
        "card_gap": card_gap,
        "card_y": y + 27,
        "centers": [content_x + idx * (card_w + card_gap) + card_w / 2 for idx in range(len(cards))],
    }


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)

    draw.text((90, 48), "Capacity Agent 业务架构图", font=F_TITLE, fill=INK)

    house_left, house_right = 145, 2255
    roof_top = 190
    roof_base = 430
    house_mid = (house_left + house_right) // 2

    # Roof shadow and body shell
    roof = [(house_mid, roof_top), (house_right, roof_base), (house_left, roof_base)]
    shadow = [(house_mid + 8, roof_top + 10), (house_right + 8, roof_base + 10), (house_left + 8, roof_base + 10)]
    draw.polygon(shadow, fill="#dce6f0")
    draw.polygon(roof, fill=ROOF)
    draw.line(roof + [roof[0]], fill="#155ab6", width=4)
    center_text(
        draw,
        (house_left + 220, roof_top + 80, house_right - 220, roof_base - 30),
        "业务目标层\nR6计划评审 · 投片决策 · 产能承诺 · 周计划冻结 · 瓶颈改善",
        F_BIG,
        WHITE,
        spacing=12,
    )

    body_x, body_y, body_w = house_left, roof_base, house_right - house_left
    round_rect(draw, (body_x, body_y, body_x + body_w, 1215), 30, "#eaf2f8", LINE, 3)

    app_metrics = draw_layer(
        draw,
        body_x + 70,
        470,
        body_w - 140,
        178,
        "前端应用层",
        APP,
        [
            ("全局控制台", "数据集 / 时间窗 / Excel"),
            ("总览页", "Loading / 可行性 / 主瓶颈"),
            ("生产计划页", "冻结状态 / 承诺可行性"),
            ("Output & What-if", "产出预测 / 扰动推演"),
            ("Agent Chat", "自然语言解释与查询"),
        ],
    )

    for x_center, label in zip(
        app_metrics["centers"],
        ["RCCP / WIP", "RCCP / 瓶颈 / DES", "WIP / LP", "WIP / RCCP", "结果解释"],
    ):
        draw_relation_badge(draw, x_center, 626, 704, label)

    draw_layer(
        draw,
        body_x + 70,
        710,
        body_w - 140,
        178,
        "算法分析层",
        ENGINE,
        [
            ("RCCP", "计划负载 vs 可用产能"),
            ("WIP 分摊", "后续工序跨周/月占机"),
            ("瓶颈评分", "Loading / Queue / Drift"),
            ("LP 优化", "产能约束下调整投片"),
            ("DES 校验", "排队与周期风险仿真"),
        ],
    )

    draw_layer(
        draw,
        body_x + 70,
        940,
        body_w - 140,
        178,
        "服务接口层",
        SERVICE,
        [
            ("Dataset API", "导入 / 模板 / 摘要"),
            ("Engine API", "RCCP / LP / DES"),
            ("Plan API", "生产计划生成"),
            ("Agent API", "LLM 配置与问答"),
            ("配置与校验", "求解器 / CORS"),
        ],
    )
    round_rect(draw, (body_x + 355, 1098, body_x + 835, 1138), 18, "#f0e9ff", "#bda7ef", 2)
    center_text(draw, (body_x + 365, 1098, body_x + 825, 1138), "当前服务接口：33 个（Engine 28 + Agent/LLM 5）", F_SMALL, SERVICE)

    # Light structural side beams, kept outside the content cards.
    for x, label in [(165, "数据治理"), (2200, "决策闭环")]:
        round_rect(draw, (x, 470, x + 36, 1088), 18, "#cfe0f1")
        center_text(draw, (x - 48, 500, x + 84, 1058), "\n".join(label), F_SIDE, MUTED, spacing=8)

    # Foundation
    foundation_y = 1225
    round_rect(draw, (145, foundation_y, 2255, foundation_y + 178), 30, DATA)
    center_text(draw, (175, foundation_y + 28, 480, foundation_y + 150), "数据基础层", F_LAYER, WHITE)
    data_cards = [
        ("demand_plan", "R6 月度投片需求"),
        ("wip_lot_detail", "Lot 位置 / 状态 / WIP"),
        ("route_master", "产品路线 / 工时 / 机台组"),
        ("tool_groups", "机台组 / 台数 / 区域"),
        ("oee", "OEE / available hours 日历"),
    ]
    cx = 520
    cw = 310
    for idx, (title, body) in enumerate(data_cards):
        draw_card(draw, cx + idx * (cw + 26), foundation_y + 32, cw, 114, title, body, DATA)

    # Bottom strip
    round_rect(draw, (145, 1420, 2255, 1472), 22, FOUNDATION)
    center_text(
        draw,
        (145, 1420, 2255, 1472),
        "闭环路径：资料导入 → 运行完整分析 → 审查瓶颈与WIP → 优化投片量 → 形成承诺/冻结结论 → 留档复盘",
        F_BOTTOM,
        WHITE,
    )

    draw.text((W - 575, H - 38), "Generated for Capacity Agent project documentation", font=F_SMALL, fill="#9aa8b7")

    img.save(PNG, quality=96)
    print(PNG)


if __name__ == "__main__":
    main()
