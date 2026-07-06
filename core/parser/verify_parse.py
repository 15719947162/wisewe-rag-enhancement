"""
PDF 解析验证脚本

这个脚本用于验证 PDF 解析管道是否正常工作。
它会完整运行: PDF -> 解析器 -> ContentBlock 列表,并检查结果质量。

使用方法:
    python -m core.parser.verify_parse data/input/sample.pdf

验证内容:
1. 是否成功解析出内容块
2. 是否包含文本块
3. 所有内容块是否有正确的元数据(source_file, page_idx)
4. 表格块是否包含 HTML 内容

输出:
- 解析统计信息(总块数、类型分布、页码范围)
- 前几个内容块的示例
- JSON 格式的详细结果(保存到 data/output/)
- 验证通过/失败信息

适用场景:
- 新部署时验证解析器配置
- 测试不同 PDF 文件的解析效果
- 调试解析问题时查看详细信息
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from core.models.content_block import BlockType, ContentBlock


def real_parse(pdf_path: str) -> list[ContentBlock]:
    """
    使用当前配置的解析器解析 PDF

    调用 mineru_parser 的 parse_pdf 函数进行实际解析。
    输出目录固定为 data/output。

    参数:
        pdf_path: PDF 文件路径

    返回:
        ContentBlock 列表
    """
    from core.parser.mineru_parser import parse_pdf

    return parse_pdf(pdf_path, output_dir="data/output")


def print_stats(blocks: list[ContentBlock]) -> None:
    """
    打印解析统计信息

    统计并显示:
    - 总内容块数量
    - 覆盖的页码范围
    - 各类型内容块的数量分布
    - 独立表格块的数量

    参数:
        blocks: ContentBlock 列表

    输出格式:
        ==================================================
          PDF Parse Results
        ==================================================
          Total blocks: 120
          Pages covered: 15 (idx 0-14)
          Type distribution:
            - text: 85
            - title: 20
            - table: 10
            - image: 5
          Table chunks (independent): 10
        ==================================================
    """
    type_counts = Counter(b.type.value for b in blocks)
    table_count = sum(1 for b in blocks if b.is_table)
    pages = set(b.page_idx for b in blocks)

    print(f"\n{'=' * 50}")
    print("  PDF Parse Results")
    print(f"{'=' * 50}")
    print(f"  Total blocks: {len(blocks)}")
    print(f"  Pages covered: {len(pages)} (idx {min(pages)}-{max(pages)})")
    print("  Type distribution:")
    for block_type, count in sorted(type_counts.items()):
        print(f"    - {block_type}: {count}")
    print(f"  Table chunks (independent): {table_count}")
    print(f"{'=' * 50}\n")


def print_sample(blocks: list[ContentBlock], n: int = 3) -> None:
    """
    打印前几个内容块的示例

    显示前 n 个内容块的详细信息,帮助快速了解解析结果的质量。

    参数:
        blocks: ContentBlock 列表
        n: 显示数量,默认 3

    显示内容:
    - 内容块类型(text/title/table/image)
    - 页码和标题级别
    - 文本内容预览(前 80 字符)
    - 表格块特殊标记
    """
    print(f"  First {min(n, len(blocks))} blocks:\n")
    for i, block in enumerate(blocks[:n]):
        print(f"  [{i}] type={block.type.value}, page={block.page_idx}, level={block.text_level}")
        text_preview = block.text[:80] + "..." if len(block.text) > 80 else block.text
        print(f"      text: {text_preview}")
        if block.is_table:
            print("      [TABLE - independent chunk candidate]")
        print()


def save_results(blocks: list[ContentBlock], output_path: str) -> None:
    """
    保存解析结果为 JSON 文件

    将所有 ContentBlock 转换为 JSON 格式并保存到文件。
    可以用于后续分析或导入到其他系统。

    参数:
        blocks: ContentBlock 列表
        output_path: 输出 JSON 文件路径

    输出格式:
        [
          {
            "type": "text",
            "text": "...",
            "page_idx": 0,
            "source_file": "sample.pdf",
            ...
          },
          ...
        ]
    """
    data = [block.model_dump() for block in blocks]
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
    print(f"  Results saved to: {output_path}")


def main() -> None:
    """
    主函数 - 执行完整的验证流程

    流程:
    1. 检查命令行参数(需要传入 PDF 文件路径)
    2. 调用 real_parse 解析 PDF
    3. 打印统计信息和示例内容
    4. 保存结果到 JSON 文件
    5. 执行一系列验证断言:
       - 必须有内容块
       - 必须有文本块
       - 所有块必须有 source_file 元数据
       - 所有块的 page_idx 必须 >= 0
       - 表格块必须有 table_html
    6. 输出验证通过信息

    异常:
        如果验证失败,抛出 AssertionError
        如果缺少参数,输出使用说明并退出
    """
    if len(sys.argv) < 2:
        print("Usage: python -m core.parser.verify_parse <pdf_path>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    print(f"  Parsing: {pdf_path}")
    blocks = real_parse(pdf_path)

    print_stats(blocks)
    print_sample(blocks)

    output_json = f"data/output/{Path(pdf_path).stem}_blocks.json"
    save_results(blocks, output_json)

    assert len(blocks) > 0, "No blocks parsed"
    assert any(block.type == BlockType.TEXT for block in blocks), "No text blocks found"
    assert all(block.source_file for block in blocks), "Missing source_file metadata"
    assert all(block.page_idx >= 0 for block in blocks), "Invalid page_idx"

    table_blocks = [block for block in blocks if block.is_table]
    if table_blocks:
        assert all(block.table_html for block in table_blocks), "Table blocks missing HTML"
        print(f"  [PASS] {len(table_blocks)} table(s) marked as independent chunks")

    print("\n  [PASS] All verification checks passed!")


if __name__ == "__main__":
    main()
