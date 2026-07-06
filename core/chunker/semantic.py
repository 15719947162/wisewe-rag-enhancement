"""
【语义切片策略模块 - semantic.py】

本模块实现了基于文档结构（标题层级）的语义切片策略。
通过识别文档中的标题（TITLE 类型块），将内容按章节/段落进行自然分组。

================================================================================
核心原理：基于标题层级的结构感知切片
================================================================================

MinerU 解析器会将 PDF 中的标题识别为 BlockType.TITLE 类型的 ContentBlock，
并设置 text_level 字段标识标题层级（1=H1, 2=H2, 3=H3...）。

语义切片策略的工作流程：
1. 遇到 TITLE 块时，开始一个新的"章节组"
2. 将该标题下的所有文本块累积到当前组
3. 遇到下一个标题时，刷新当前组，开始新的组
4. 表格和图片独立成块，不与文本混合

================================================================================
text_level 字段的作用
================================================================================

ContentBlock.text_level 字段记录标题的层级：
- text_level=1: 一级标题（H1），如 "第一章 绪论"
- text_level=2: 二级标题（H2），如 "1.1 研究背景"
- text_level=3: 三级标题（H3），如 "1.1.1 国内外研究现状"

当前实现特点：
- 本策略按标题类型（TITLE）切分，不区分标题层级
- 所有标题都会触发新切片的开始
- 未来可扩展：根据 text_level 实现层次化切片

================================================================================
数据流示例
================================================================================

输入 ContentBlock 列表（来自 PDF 解析）：
    [
        ContentBlock(type=TITLE, text="第一章 概述", text_level=1, page_idx=0),
        ContentBlock(type=TEXT, text="本章介绍...", page_idx=0),
        ContentBlock(type=TEXT, text="研究背景是...", page_idx=0),
        ContentBlock(type=TITLE, text="1.1 研究目的", text_level=2, page_idx=0),
        ContentBlock(type=TEXT, text="本研究的目的...", page_idx=0),
        ContentBlock(type=TABLE, text="表1", is_table=True, table_html="<table>...", page_idx=1),
        ContentBlock(type=TITLE, text="第二章 方法", text_level=1, page_idx=1),
        ContentBlock(type=TEXT, text="本章介绍方法...", page_idx=1),
    ]

输出 Chunk 列表：
    [
        Chunk(content="本章介绍...\\n研究背景是...", title="第一章 概述", chunk_index=0),
        Chunk(content="本研究的目的...", title="1.1 研究目的", chunk_index=1),
        Chunk(content="<table>...", title="1.1 研究目的", is_table_chunk=True, chunk_index=2),
        Chunk(content="本章介绍方法...", title="第二章 方法", chunk_index=3),
    ]

================================================================================
与其他策略的对比
================================================================================

| 策略           | 切分依据           | 优点                   | 缺点             |
|----------------|--------------------|-----------------------|------------------|
| fixed_length   | 字符数硬切         | 简单可控               | 可能截断语义     |
| semantic       | 标题/章节边界      | 保持语义完整性         | 依赖标题识别质量 |
| hierarchical   | 三层（章节/段落/增强）| 支持多粒度检索       | 复杂度高         |
| llm            | LLM 判断语义边界   | 智能识别               | 成本高、速度慢   |

================================================================================
使用示例
================================================================================

    from core.chunker import get_strategy

    # 获取语义切片策略实例
    strategy = get_strategy("semantic", max_chunk_size=1000)

    # 执行切片
    chunks = strategy.chunk(content_blocks)

    # 输出结果
    for chunk in chunks:
        print(f"[{chunk.title}] {chunk.content[:50]}...")

================================================================================
注意事项
================================================================================

1. 依赖解析器正确识别标题：
   - MinerU 需要正确设置 parse_method 才能识别标题
   - 如果标题识别失败，会导致切片边界不准确

2. 表格和图片的特殊处理：
   - 表格会独立成块，保留 HTML 格式
   - 图片会独立成块，记录 image_path

3. 最大切片长度限制：
   - 即使标题下内容很长，也会在达到 max_chunk_size 时切分
   - 避免单个切片过大影响检索效果

作者：RAG 项目组
创建时间：2024
"""

from __future__ import annotations

from core.models.content_block import BlockType, Chunk, ContentBlock

from .base import ChunkingStrategy, register_strategy


@register_strategy
class SemanticStrategy(ChunkingStrategy):
    """
    【语义切片策略类】

    基于文档标题结构进行语义感知的切片策略。
    通过识别 TITLE 类型的 ContentBlock，将文档按章节自然分组。

    继承关系：
        SemanticStrategy -> ChunkingStrategy (ABC)

    注册机制：
        通过 @register_strategy 装饰器注册到策略工厂，
        可通过 get_strategy("semantic") 获取实例。

    核心属性：
        max_chunk_size: 单个切片的最大字符数限制
                       超过此限制会强制切分，避免切片过大
    """

    # 策略名称，用于工厂方法 get_strategy("semantic")
    name = "semantic"

    def __init__(self, max_chunk_size: int = 1000):
        """
        初始化语义切片策略。

        参数：
            max_chunk_size: 单个切片的最大字符数，默认 1000
                           如果章节内容超过此限制，会被拆分成多个切片
                           建议值：500-2000 字符，根据检索精度需求调整
        """
        self.max_chunk_size = max_chunk_size

    def chunk(self, blocks: list[ContentBlock]) -> list[Chunk]:
        """
        【核心切片方法】

        将 ContentBlock 列表转换为 Chunk 列表。
        按标题边界进行语义分组，保持章节内容的完整性。

        参数：
            blocks: ContentBlock 列表，来自 PDF 解析器

        返回：
            Chunk 列表，每个切片包含一段语义完整的内容

        处理流程：
        ---------------------------------------------------------------------

        ┌─────────────────────────────────────────────────────────────────┐
        │  遍历每个 ContentBlock                                          │
        └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │  1. 图片块 (type=IMAGE)                                         │
        │     → 立即创建图片切片，不与文本混合                            │
        └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │  2. 表格块 (is_table=True)                                      │
        │     → 先刷新当前累积的文本                                      │
        │     → 创建独立的表格切片，保留 HTML 格式                         │
        └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │  3. 标题块 (type=TITLE)                                         │
        │     → 标志新章节开始                                            │
        │     → 刷新当前累积的文本                                        │
        │     → 记录新标题，后续切片将关联此标题                          │
        └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │  4. 文本块 (type=TEXT)                                          │
        │     → 累积到当前组                                              │
        │     → 如果超过 max_chunk_size，刷新当前组                       │
        └─────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────────────────────┐
        │  5. 遍历结束后，刷新剩余的累积文本                              │
        └─────────────────────────────────────────────────────────────────┘

        状态变量说明：
        - current_title: 当前章节的标题文本（上一个遇到的 TITLE 块）
        - current_texts: 当前章节累积的文本段落列表
        - current_page: 当前章节主要所在的页码
        - current_source: 当前内容的来源文件名

        示例：
            输入 blocks:
                [TITLE("第一章"), TEXT("内容A"), TEXT("内容B"), TITLE("第二章"), TEXT("内容C")]

            输出 chunks:
                [Chunk(title="第一章", content="内容A\\n内容B"),
                 Chunk(title="第二章", content="内容C")]
        """
        # 输出结果列表
        chunks: list[Chunk] = []

        # 切片序号计数器
        idx = 0

        # 当前章节的标题（上一个遇到的 TITLE 块的文本）
        # 初始为 None，表示还没有遇到任何标题
        current_title: str | None = None

        # 当前章节累积的文本段落列表
        # 多个 TEXT 块会累积到这里，最后合并成一个切片
        current_texts: list[str] = []

        # 当前章节主要所在的页码
        # 取最后一个文本块的页码
        current_page: int = 0

        # 当前内容的来源文件名
        current_source: str = ""

        # 遍历所有内容块
        for block in blocks:
            # --------------------------------------------------------------
            # 1. 图片块：独立处理，不与文本混合
            # --------------------------------------------------------------
            if block.type.value == "image":
                # 图片块立即生成一个独立的切片
                # 图片切片包含图片路径，后续可用于多模态检索
                chunks.append(self._make_image_chunk(block, idx))
                idx += 1
                continue

            # --------------------------------------------------------------
            # 2. 表格块：独立处理，保留 HTML 格式
            # --------------------------------------------------------------
            if block.is_table:
                # 如果当前有累积的文本，先刷新成切片
                # 表格前后的文本不应该与表格混在一起
                if current_texts:
                    chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))
                    idx += 1
                    current_texts = []  # 清空累积

                # 创建独立的表格切片
                # 优先使用 table_html（保留格式），如果没有则使用 text
                chunks.append(self._make_chunk(
                    content=block.table_html or block.text,
                    source=block.source_file,
                    page=block.page_idx,
                    chunk_index=idx,
                    title=current_title,  # 表格关联当前章节标题
                    is_table_chunk=True,  # 标记为表格切片
                ))
                idx += 1
                continue

            # --------------------------------------------------------------
            # 3. 标题块：标志新章节开始
            # --------------------------------------------------------------
            if block.type == BlockType.TITLE:
                # 如果当前有累积的文本，先刷新成切片
                # 这表示上一个章节结束，新章节开始
                if current_texts:
                    chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))
                    idx += 1
                    current_texts = []  # 清空，准备接收新章节内容

                # 记录新标题，后续的文本切片都会关联这个标题
                current_title = block.text

                # 记录标题所在页码，作为新章节的主要页码
                current_page = block.page_idx

                # 记录来源文件
                current_source = block.source_file
                continue

            # --------------------------------------------------------------
            # 4. 文本块：累积到当前章节组
            # --------------------------------------------------------------
            # 更新来源和页码（取最后一个文本块的信息）
            current_source = block.source_file
            current_page = block.page_idx

            # 获取文本内容并去除首尾空白
            text = block.text.strip()
            if not text:
                # 空文本跳过
                continue

            # 检查如果加入当前文本，是否会超过最大长度限制
            combined = "\n".join(current_texts + [text])
            if len(combined) > self.max_chunk_size and current_texts:
                # 超过限制，先刷新当前累积的文本
                chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))
                idx += 1
                # 新文本作为新切片的开始
                current_texts = [text]
            else:
                # 未超过限制，累积到当前组
                current_texts.append(text)

        # --------------------------------------------------------------
        # 5. 处理剩余的累积文本
        # --------------------------------------------------------------
        # 遍历结束后，如果还有未刷新的文本，创建最后一个切片
        if current_texts:
            chunks.append(self._flush(current_texts, current_source, current_page, idx, current_title))

        return chunks

    def _flush(self, texts: list[str], source: str, page: int, idx: int, title: str | None) -> Chunk:
        """
        【内部方法：刷新累积文本为切片】

        将累积的文本段落列表合并成一个 Chunk 对象。
        这是一个辅助方法，在遇到标题/表格或遍历结束时调用。

        参数：
            texts: 文本段落列表，每个元素是一个 TEXT 块的内容
            source: 来源文件名
            page: 页码
            idx: 切片序号
            title: 关联的章节标题（可能为 None）

        返回：
            合并后的 Chunk 对象

        示例：
            texts = ["第一段内容", "第二段内容"]
            → Chunk(content="第一段内容\\n第二段内容")
        """
        return self._make_chunk(
            content="\n".join(texts),  # 多个段落用换行符连接
            source=source,
            page=page,
            chunk_index=idx,
            title=title,
        )
