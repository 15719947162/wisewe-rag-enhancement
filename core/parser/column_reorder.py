"""
多栏布局内容块重排序模块
=========================

本模块处理 PDF 解析后多栏布局的内容块顺序问题。

## 问题背景

PDF 解析器（如 MinerU）按文档流顺序提取内容块，但对于多栏布局（如学术论文、报纸杂志），
解析顺序可能是"Z字形"（先上后下再换栏）或按视觉位置，而非阅读顺序（先左栏再右栏）。
这会导致语义不连贯：左栏末尾的内容与右栏开头的内容被错误地拼接在一起。

## 解决方案

通过边界框（bounding box）的 x 坐标分栏，y 坐标排序，重构阅读顺序：

1. **分页处理**：先按 page_idx 分组，每页独立重排
2. **坐标分栏**：计算块的 x 中心点 ((x0+x2)/2)，与页面宽度的分界线比较
3. **栏内排序**：同一栏内的块按 y 坐标（顶部位置）升序排列
4. **栏间组合**：按阅读顺序拼接各栏内容

## 支持的布局模式

- `single`：单栏布局，无需重排
- `two_col_lr`：双栏，从左到右阅读（先左栏后右栏）
- `two_col_rl`：双栏，从右到左阅读（如阿拉伯语排版）
- `three_col`：三栏，从左到右阅读

## 布局示例

### 双栏从左到右（two_col_lr）示例：

    ┌─────────────┬─────────────┐
    │   左栏      │   右栏      │
    │  Block 1    │  Block 4    │
    │  Block 2    │  Block 5    │
    │  Block 3    │  Block 6    │
    └─────────────┴─────────────┘

    原始顺序: [1, 4, 2, 5, 3, 6]（按视觉位置）
    重排后:   [1, 2, 3, 4, 5, 6]（阅读顺序）

### 三栏布局（three_col）示例：

    ┌──────────┬──────────┬──────────┐
    │  第1栏   │  第2栏   │  第3栏   │
    │ Block 1  │ Block 4  │ Block 7  │
    │ Block 2  │ Block 5  │ Block 8  │
    │ Block 3  │ Block 6  │ Block 9  │
    └──────────┴──────────┴──────────┘

    阅读顺序: [1, 2, 3, 4, 5, 6, 7, 8, 9]

## 边界框（bbox）说明

bbox 格式为 [x0, y0, x2, y2]，其中：
- x0: 左边界坐标
- y0: 上边界坐标
- x2: 右边界坐标
- y2: 下边界坐标

块的 x 中心点 = (x0 + x2) / 2，用于判断块属于哪一栏。

## 注意事项

- 无 bbox 的块（如跨栏标题）会被追加到页面末尾
- 假设栏宽大致相等（分界线位于页面宽度的 1/2 或 1/3 处）
- 对于不规则布局，可能需要更复杂的启发式算法
"""
from __future__ import annotations

from core.models.content_block import ContentBlock

# 布局选项映射表
# 键：布局模式标识符（用于 API/CLI 参数）
# 值：中文描述（用于日志输出和用户界面展示）
LAYOUT_OPTIONS = {
    "single": "单列（默认）",        # 普通文档，无需重排
    "two_col_lr": "双列-从左到右",   # 学术论文、技术报告等常见双栏布局
    "two_col_rl": "双列-从右到左",   # 特殊排版（如阿拉伯语、希伯来语等从右向左阅读的语言）
    "three_col": "三列-从左到右",    # 报纸、简报等多栏布局
}


def reorder_blocks_by_columns(
    blocks: list[ContentBlock],
    layout: str = "single",
) -> list[ContentBlock]:
    """
    根据多栏布局重排内容块顺序。

    此函数是本模块的核心入口，将解析器输出的内容块按阅读顺序重新排列，
    解决多栏 PDF 文档中"语义断裂"的问题。

    Args:
        blocks: 待重排的内容块列表，每个块包含 bbox 边界框信息。
                bbox 格式为 [x0, y0, x2, y2]，表示左上角和右下角坐标。
        layout: 布局模式，可选值见 LAYOUT_OPTIONS。默认为 "single"（单栏）。

    Returns:
        重排后的内容块列表。对于单栏布局，直接返回原列表；对于多栏布局，
        返回按阅读顺序排列的新列表。

    Example:
        >>> blocks = parse_pdf("paper.pdf")  # 假设解析出 6 个块
        >>> # 双栏布局，原始顺序可能是 [左1, 右1, 左2, 右2, 左3, 右3]
        >>> reordered = reorder_blocks_by_columns(blocks, layout="two_col_lr")
        >>> # 重排后变为 [左1, 左2, 左3, 右1, 右2, 右3]

    算法流程:
        1. 按 page_idx 分组，每页独立处理
        2. 分离有 bbox 和无 bbox 的块
        3. 计算页面宽度（取所有块的最大 x2 坐标）
        4. 根据布局模式确定分栏数和分界线位置
        5. 按块的 x 中心点分栏，栏内按 y 坐标排序
        6. 按阅读顺序拼接各栏
        7. 追加无 bbox 的块（通常是跨栏元素）
    """
    # 单栏布局无需处理，直接返回
    if layout == "single" or not blocks:
        return blocks

    # ===== 第一步：按页分组 =====
    # 使用字典按 page_idx 聚合内容块
    # key: 页码, value: 该页的所有内容块
    pages: dict[int, list[ContentBlock]] = {}
    for b in blocks:
        pages.setdefault(b.page_idx, []).append(b)

    # 用于收集重排后的结果
    result: list[ContentBlock] = []

    # 按页码顺序处理每一页
    for page_idx in sorted(pages.keys()):
        page_blocks = pages[page_idx]

        # ===== 第二步：分离有/无 bbox 的块 =====
        # 有 bbox 的块可以定位到具体栏，无 bbox 的块（如跨栏图表、标题）单独处理
        blocks_with_bbox = [(b, b.bbox) for b in page_blocks if b.bbox]
        blocks_no_bbox = [b for b in page_blocks if not b.bbox]

        # 如果该页没有带 bbox 的块，保持原顺序
        if not blocks_with_bbox:
            result.extend(page_blocks)
            continue

        # ===== 第三步：计算页面宽度 =====
        # 页面宽度 = 所有块中最大的右边界坐标
        # 用于确定分栏的分界线位置
        page_width = max(bb[2] for _, bb in blocks_with_bbox)

        # ===== 第四步：根据布局模式分栏重排 =====
        if layout == "two_col_lr":
            # 双栏从左到右模式
            # 分界线在页面中间（page_width / 2）
            mid = page_width / 2

            # 左栏：x 中心点 < 分界线的块
            # 按 y 坐标（顶部位置）升序排序，确保同一栏内从上到下阅读
            left = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 < mid],
                key=lambda x: x[1][1],  # bb[1] 是 y0（顶部坐标）
            )

            # 右栏：x 中心点 >= 分界线的块
            right = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 >= mid],
                key=lambda x: x[1][1],
            )

            # 从左到右阅读：先左栏，后右栏
            result.extend(b for b, _ in left)
            result.extend(b for b, _ in right)

        elif layout == "two_col_rl":
            # 双栏从右到左模式
            # 分界线同样在页面中间
            mid = page_width / 2

            # 分栏逻辑与 two_col_lr 相同
            left = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 < mid],
                key=lambda x: x[1][1],
            )
            right = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 >= mid],
                key=lambda x: x[1][1],
            )

            # 从右到左阅读：先右栏，后左栏
            result.extend(b for b, _ in right)
            result.extend(b for b, _ in left)

        elif layout == "three_col":
            # 三栏从左到右模式
            # 分界线在页面宽度的 1/3 和 2/3 处
            t1 = page_width / 3        # 第 1、2 栏的分界线
            t2 = page_width * 2 / 3    # 第 2、3 栏的分界线

            # 第 1 栏：x 中心点 < 1/3 宽度
            col1 = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 < t1],
                key=lambda x: x[1][1],
            )

            # 第 2 栏：1/3 <= x 中心点 < 2/3 宽度
            col2 = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if t1 <= (bb[0] + bb[2]) / 2 < t2],
                key=lambda x: x[1][1],
            )

            # 第 3 栏：x 中心点 >= 2/3 宽度
            col3 = sorted(
                [(b, bb) for b, bb in blocks_with_bbox if (bb[0] + bb[2]) / 2 >= t2],
                key=lambda x: x[1][1],
            )

            # 从左到右阅读：第 1 栏 → 第 2 栏 → 第 3 栏
            result.extend(b for b, _ in col1)
            result.extend(b for b, _ in col2)
            result.extend(b for b, _ in col3)

        # ===== 第五步：追加无 bbox 的块 =====
        # 这些块可能是跨栏标题、跨页图表等，放在页面最后
        result.extend(blocks_no_bbox)

    return result
