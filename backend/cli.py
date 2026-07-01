"""CLI pipeline entry point.

Usage:
    python backend/cli.py --pdf data/input/sample.pdf --strategy all --clean --clean-llm
    python backend/cli.py --pdf data/input/sample.pdf --strategy fixed_length --clean
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    from core.chunker import list_strategies

    parser = argparse.ArgumentParser(description="WiseWe RAG CLI Pipeline")
    parser.add_argument("--pdf", required=True, help="Path to input PDF file")
    parser.add_argument(
        "--strategy", default="all",
        help=f"Chunking strategy: {', '.join(list_strategies())} or 'all'",
    )
    parser.add_argument("--output-dir", default="data/output")
    parser.add_argument("--embedding-model", default="")
    parser.add_argument("--clean", action="store_true")
    parser.add_argument("--clean-llm", action="store_true")
    parser.add_argument("--no-quality-gate", action="store_true")
    parser.add_argument("--llm-base-url", default="")
    parser.add_argument("--llm-api-key", default="")
    parser.add_argument("--llm-model", default="")
    parser.add_argument("--llm-system-prompt", default="")
    parser.add_argument("--enhanced-system-prompt", default="")
    return parser.parse_args()


def main() -> None:
    from core.chunker import get_strategy, list_strategies
    from core.config import load_config
    from core.embedding.client import embed_texts
    from core.llm_config import set_global_llm_config
    from core.output.csv_writer import write_knowledge_csv
    from core.output.stats import compute_stats, format_stats_report

    args = parse_args()
    _ = load_config()

    set_global_llm_config(
        base_url=args.llm_base_url,
        api_key=args.llm_api_key,
        model=args.llm_model,
        system_prompt=args.llm_system_prompt,
    )

    pdf_path = args.pdf
    if not Path(pdf_path).exists():
        print(f"Error: PDF not found: {pdf_path}")
        sys.exit(1)

    output_dir = args.output_dir
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    print(f"\n  PDF: {pdf_path}")
    print("  Mode: real pipeline")
    print(f"  Output: {output_dir}\n")

    # Stage 1: Parse
    from core.parser.mineru_parser import parse_pdf
    blocks = parse_pdf(pdf_path)
    print(f"  [1/6 解析] {len(blocks)} content blocks\n")

    # Stage 2: Clean
    if args.clean or args.clean_llm:
        from core.cleaner import clean_blocks
        result = clean_blocks(blocks, use_rules=args.clean, use_llm=args.clean_llm)
        print(f"  [2/6 清洗] {len(blocks)} -> {len(result.blocks)} blocks (removed {result.removed_count})")
        for d in result.details:
            print(f"        {d}")
        blocks = result.blocks
        print()
    else:
        print("  [2/6 清洗] skipped\n")

    # Stages 3-6: Chunk, Quality Gate, Embed, Export
    strategies = list_strategies() if args.strategy == "all" else [args.strategy]
    all_stats = []

    for name in strategies:
        extra = {"enhanced_system_prompt": args.enhanced_system_prompt} if name == "hierarchical" else {}
        strategy = get_strategy(name, **extra)
        chunks = strategy.chunk(blocks)
        print(f"  [3/6 切片] [{name}] {len(chunks)} chunks")

        if not args.no_quality_gate and chunks:
            from core.cleaner.quality_gate import apply_quality_gate
            qg = apply_quality_gate(chunks)
            if qg.discarded_count:
                print(f"  [4/6 质量] {len(chunks)} -> {len(qg.chunks)} (discarded {qg.discarded_count})")
            chunks = qg.chunks

        if not chunks:
            print("        No chunks after filtering, skipping.")
            all_stats.append(compute_stats([]))
            continue

        texts = [c.content for c in chunks]
        embeddings = embed_texts(texts, model=args.embedding_model or None)
        print(f"  [5/6 向量] {len(embeddings)} vectors")

        stem = Path(pdf_path).stem
        csv_path = f"{output_dir}/{stem}_{name}.csv"
        write_knowledge_csv(chunks, embeddings, csv_path)
        print(f"  [6/6 导出] {csv_path}\n")

        all_stats.append(compute_stats(chunks))

    print()
    print(format_stats_report(all_stats))


if __name__ == "__main__":
    main()
