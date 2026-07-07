"""
【命令行管道入口 - cli.py】

本文件是 RAG 系统的命令行工具，用于批量处理 PDF 文件。
相比 HTTP 服务，CLI 更适合批量脚本和自动化流程。

主要职责：
1. 解析命令行参数
2. 执行完整的 RAG 管道流程
3. 对比不同切片策略的效果

完整管道流程：
[解析] → [清洗] → [切片] → [质量门控] → [向量化] → [导出]

使用场景：
- 批量处理多个 PDF 文件
- 测试和对比切片策略
- 生成实验对比数据
- 自动化脚本执行

运行方式：
    # Mock 模式（无需 MinerU 或 API Key，快速测试）
    python backend/cli.py --pdf data/input/sample.pdf --strategy all --mock

    # 真实解析 + Mock 向量化（节省 API 成本）
    python backend/cli.py --pdf data/input/sample.pdf --strategy fixed_length --mock-embedding

    # 完整真实运行（全流程）
    python backend/cli.py --pdf data/input/sample.pdf --strategy all --clean

    # LLM 清洗和增强
    python backend/cli.py --pdf data/input/sample.pdf --strategy hierarchical --clean --clean-llm

知识点 - CLI vs HTTP 服务：
- CLI: 批量处理，适合脚本和自动化
- HTTP: 实时处理，适合交互式应用
- CLI 更容易调试和测试
- HTTP 服务更适合生产环境

作者：RAG 项目组
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """
    【解析命令行参数】

    知识点 - argparse 模块：
    - Python 标准库的命令行解析工具
    - 支持可选参数、必选参数、默认值
    - 自动生成 --help 帮助信息

    参数分类：
    1. 必选参数：--pdf（输入文件）
    2. 可选参数：--strategy、--output-dir 等
    3. 开关参数：--clean、--clean-llm 等

    返回：
        argparse.Namespace: 解析后的参数对象

    使用示例：
        args = parse_args()
        print(args.pdf)        # PDF 文件路径
        print(args.strategy)   # 切片策略
    """
    # 导入切片策略列表函数
    # 放在函数内部避免循环导入
    from core.chunker import list_strategies

    # 创建参数解析器
    parser = argparse.ArgumentParser(
        description="WiseWe RAG CLI Pipeline"  # 工具描述
    )

    # ============================================================================
    # 必选参数
    # ============================================================================
    # --pdf: 输入的 PDF 文件路径（必选）
    parser.add_argument(
        "--pdf",
        required=True,  # 必选参数
        help="Path to input PDF file"  # 帮助文本
    )

    # ============================================================================
    # 切片策略参数
    # ============================================================================
    # --strategy: 切片策略名称
    parser.add_argument(
        "--strategy",
        default="all",  # 默认运行所有策略
        help=f"Chunking strategy: {', '.join(list_strategies())} or 'all'",
        # help 信息包含所有可用的策略名称
    )

    # ============================================================================
    # 输出配置参数
    # ============================================================================
    # --output-dir: 输出目录路径
    parser.add_argument(
        "--output-dir",
        default="data/output",  # 默认输出目录
        help="输出目录路径"
    )

    # --embedding-model: 向量化模型名称
    parser.add_argument(
        "--embedding-model",
        default="",  # 默认使用配置文件中的模型
        help="向量化模型名称（如 text-embedding-v1）"
    )

    # ============================================================================
    # 清洗配置参数
    # ============================================================================
    # --clean: 启用规则清洗
    parser.add_argument(
        "--clean",
        action="store_true",  # 开关参数，出现即为 True
        help="启用规则清洗（移除空白块、短块等）"
    )

    # --clean-llm: 启用 LLM 清洗
    parser.add_argument(
        "--clean-llm",
        action="store_true",
        help="启用 LLM 清洗（使用大语言模型优化内容）"
    )

    # --no-quality-gate: 禁用质量门控
    parser.add_argument(
        "--no-quality-gate",
        action="store_true",
        help="禁用质量门控（不过滤低质量切片）"
    )

    # ============================================================================
    # LLM 配置参数
    # ============================================================================
    # --llm-base-url: LLM API 地址
    parser.add_argument(
        "--llm-base-url",
        default="",  # 默认使用配置文件中的地址
        help="LLM API 地址（如 https://api.openai.com/v1）"
    )

    # --llm-api-key: LLM API 密钥
    parser.add_argument(
        "--llm-api-key",
        default="",  # 默认使用环境变量中的密钥
        help="LLM API 密钥"
    )

    # --llm-model: LLM 模型名称
    parser.add_argument(
        "--llm-model",
        default="",  # 默认使用配置文件中的模型
        help="LLM 模型名称（如 gpt-4、qwen-max）"
    )

    # --llm-system-prompt: LLM 系统提示词
    parser.add_argument(
        "--llm-system-prompt",
        default="",  # 默认使用内置提示词
        help="LLM 系统提示词（用于清洗和增强）"
    )

    # --enhanced-system-prompt: 增强切片的系统提示词
    parser.add_argument(
        "--enhanced-system-prompt",
        default="",  # 默认使用内置提示词
        help="增强切片的系统提示词（仅用于层次化切片）"
    )

    # 解析并返回参数
    return parser.parse_args()


def main() -> None:
    """
    【主函数 - 执行完整 RAG 管道】

    知识点 - RAG 管道流程：
    一个完整的 RAG 系统包含多个阶段，每个阶段处理不同的任务：

    1. 解析（Parse）：
       - 将 PDF 解析成结构化内容
       - 使用 MinerU 云端 API
       - 输出：ContentBlock 列表

    2. 清洗（Clean）：
       - 移除无用的内容块
       - 可选 LLM 优化
       - 输出：清洗后的 ContentBlock 列表

    3. 切片（Chunk）：
       - 将长文本拆分成适合检索的小段落
       - 多种策略可选
       - 输出：Chunk 列表

    4. 质量门控（Quality Gate）：
       - 过滤低质量切片
       - 可选 LLM 评分
       - 输出：高质量 Chunk 列表

    5. 向量化（Embed）：
       - 将文本转换成向量
       - 用于语义检索
       - 输出：向量列表

    6. 导出（Export）：
       - 导出到 CSV 文件
       - 或写入 pgvector 数据库
       - 输出：持久化数据

    执行流程：
    1. 解析命令行参数
    2. 加载配置文件
    3. 设置 LLM 配置
    4. 执行各阶段处理
    5. 生成统计报告

    特点：
    - 支持所有切片策略对比
    - 每个策略独立运行完整流程
    - 最后生成对比统计报告
    """
    # ============================================================================
    # 导入核心模块
    # ============================================================================
    # 这些导入放在函数内部，避免启动时的循环依赖
    from core.chunker import get_strategy, list_strategies
    from core.config import load_config
    from core.embedding.client import embed_texts
    from core.llm_config import set_global_llm_config
    from core.output.csv_writer import write_knowledge_csv
    from core.output.stats import compute_stats, format_stats_report

    # ============================================================================
    # 初始化
    # ============================================================================
    # 解析命令行参数
    args = parse_args()

    # 加载配置文件（config.yaml）
    # 返回配置字典，但这里不使用返回值
    # 加载过程会设置环境变量和默认值
    _ = load_config()

    # 设置全局 LLM 配置
    # 这些配置会被所有使用 LLM 的模块使用
    # 参数优先级：命令行参数 > 配置文件 > 环境变量
    set_global_llm_config(
        base_url=args.llm_base_url,      # API 地址
        api_key=args.llm_api_key,        # API 密钥
        model=args.llm_model,            # 模型名称
        system_prompt=args.llm_system_prompt,  # 系统提示词
    )

    # ============================================================================
    # 验证输入文件
    # ============================================================================
    # 检查 PDF 文件是否存在
    pdf_path = args.pdf
    if not Path(pdf_path).exists():
        # 文件不存在，打印错误并退出
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)  # 非零退出码表示错误

    # ============================================================================
    # 准备输出目录
    # ============================================================================
    # 创建输出目录（如果不存在）
    output_dir = args.output_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    # parents=True: 创建父目录（如 data/output 会创建 data）
    # exist_ok=True: 目录已存在时不报错

    # ============================================================================
    # 打印执行信息
    # ============================================================================
    print(f"\n  PDF: {pdf_path}")               # 输入文件路径
    print("  Mode: real pipeline")              # 运行模式
    print(f"  Output: {output_dir}\n")          # 输出目录

    # ============================================================================
    # Stage 1: 解析（Parse）
    # ============================================================================
    # 知识点 - PDF 解析：
    # - MinerU 是专业的 PDF 解析工具
    # - 能提取文本、表格、图片、公式等
    # - 输出结构化的 ContentBlock 列表
    print("  [1/6 解析] 开始解析 PDF...")

    # 导入解析函数
    from core.parser.mineru_parser import parse_pdf

    # 执行解析
    # parse_pdf 会：
    # 1. 上传 PDF 到 OSS
    # 2. 提交 MinerU 任务
    # 3. 轮询等待完成
    # 4. 下载解析结果
    # 5. 转换成 ContentBlock 列表
    blocks = parse_pdf(pdf_path)

    # 打印解析结果统计
    print(f"  [1/6 解析] {len(blocks)} content blocks\n")
    # blocks 数量示例：
    # - 10 页 PDF 可能产生 50-100 个 blocks
    # - 包含文本、表格、图片、标题等

    # ============================================================================
    # Stage 2: 清洗（Clean）
    # ============================================================================
    # 知识点 - 内容清洗：
    # - 移除空白、过短、无意义的内容块
    # - 可选 LLM 优化（更智能但成本高）
    # - 提高后续切片和检索质量
    if args.clean or args.clean_llm:
        print("  [2/6 清洗] 开始清洗内容...")

        # 导入清洗函数
        from core.cleaner import clean_blocks

        # 执行清洗
        # clean_blocks 参数：
        # - blocks: 内容块列表
        # - use_rules: 是否使用规则清洗
        # - use_llm: 是否使用 LLM 清洗
        result = clean_blocks(
            blocks,
            use_rules=args.clean,      # 规则清洗（快速、低成本）
            use_llm=args.clean_llm     # LLM 清洗（智能、高成本）
        )

        # 打印清洗结果统计
        # 示例输出：[2/6 清洗] 85 -> 78 blocks (removed 7)
        print(f"  [2/6 清洗] {len(blocks)} -> {len(result.blocks)} blocks (removed {result.removed_count})")

        # 打印详细清洗信息
        for d in result.details:
            print(f"        {d}")  # 每条规则的执行情况

        # 使用清洗后的内容块
        blocks = result.blocks
        print()  # 空行分隔
    else:
        # 未启用清洗，跳过此阶段
        print("  [2/6 清洗] skipped\n")

    # ============================================================================
    # Stage 3-6: 切片、质量门控、向量化、导出
    # ============================================================================
    # 知识点 - 多策略对比：
    # - 不同切片策略适合不同场景
    # - 通过对比选择最佳策略
    # - 每个策略独立运行完整流程

    # 获取要运行的策略列表
    strategies = list_strategies() if args.strategy == "all" else [args.strategy]
    # 如果 --strategy all，运行所有策略
    # 如果 --strategy fixed_length，只运行指定策略

    # 存储所有策略的统计数据（用于对比）
    all_stats = []

    # 遍历每个策略
    for name in strategies:
        # 层次化切片需要额外参数（增强提示词）
        extra = {"enhanced_system_prompt": args.enhanced_system_prompt} if name == "hierarchical" else {}

        # 创建策略实例
        strategy = get_strategy(name, **extra)

        # 执行切片
        # strategy.chunk 会：
        # 1. 分析内容块结构
        # 2. 根据策略规则切分
        # 3. 生成 Chunk 列表
        chunks = strategy.chunk(blocks)

        # 打印切片结果统计
        print(f"  [3/6 切片] [{name}] {len(chunks)} chunks")

        # ============================================================================
        # Stage 4: 质量门控（Quality Gate）
        # ============================================================================
        # 知识点 - 质量门控：
        # - 过滤低质量切片（标点过多、内容空洞等）
        # - 可选 LLM 评分（更准确但成本高）
        # - 提高检索质量

        # 检查是否启用质量门控且有切片
        if not args.no_quality_gate and chunks:
            # 导入质量门控函数
            from core.cleaner.quality_gate import apply_quality_gate

            # 执行质量门控
            # apply_quality_gate 会：
            # 1. 检查每个切片的质量
            # 2. 过滤低质量切片
            # 3. 可选 LLM 评分（如果配置了）
            qg = apply_quality_gate(chunks)

            # 如果有切片被丢弃，打印统计
            if qg.discarded_count:
                print(f"  [4/6 质量] {len(chunks)} -> {len(qg.chunks)} (discarded {qg.discarded_count})")

            # 使用过滤后的切片
            chunks = qg.chunks

        # ============================================================================
        # 检查是否有切片
        # ============================================================================
        # 如果所有切片都被过滤，跳过后续处理
        if not chunks:
            print("        No chunks after filtering, skipping.")
            all_stats.append(compute_stats([]))  # 添加空统计
            continue  # 跳到下一个策略

        # ============================================================================
        # Stage 5: 向量化（Embed）
        # ============================================================================
        # 知识点 - 向量化：
        # - 将文本转换成高维向量（如 1024 维）
        # - 向量表示文本的语义信息
        # - 用于语义相似度检索

        # 提取所有切片的文本内容
        texts = [c.content for c in chunks]

        # 执行向量化
        # embed_texts 会：
        # 1. 调用 Embedding API（如 OpenAI、DashScope）
        # 2. 批量处理文本（提高效率）
        # 3. 返回向量列表
        embeddings = embed_texts(
            texts,
            model=args.embedding_model or None  # 使用指定模型或默认模型
        )

        # 打印向量化结果统计
        print(f"  [5/6 向量] {len(embeddings)} vectors")

        # ============================================================================
        # Stage 6: 导出（Export）
        # ============================================================================
        # 知识点 - 数据导出：
        # - 导出成 CSV 文件（用于离线分析）
        # - 或写入 pgvector 数据库（用于在线检索）

        # 生成输出文件名
        # 格式：<PDF文件名>_<策略名>.csv
        stem = Path(pdf_path).stem  # PDF 文件名（不含扩展名）
        csv_path = f"{output_dir}/{stem}_{name}.csv"
        # 示例：data/output/sample_semantic.csv

        # 导出 CSV 文件
        # write_knowledge_csv 会：
        # 1. 组装切片和向量数据
        # 2. 写入 CSV 文件
        # 3. 包含 id、content、source、page、embedding 等字段
        write_knowledge_csv(chunks, embeddings, csv_path)

        # 打印导出结果
        print(f"  [6/6 导出] {csv_path}\n")

        # 计算统计数据（用于对比）
        all_stats.append(compute_stats(chunks))

    # ============================================================================
    # 打印对比统计报告
    # ============================================================================
    # format_stats_report 会生成对比表格，包含：
    # - 切片数量
    # - 平均长度
    # - 覆盖率
    # - 其他指标
    print()
    print(format_stats_report(all_stats))


# ============================================================================
# 程序入口
# ============================================================================
# 当直接运行此文件时，执行 main 函数
if __name__ == "__main__":
    main()
