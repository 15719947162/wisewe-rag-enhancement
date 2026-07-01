"""Cleaning prompt presets for subject type and layout.

Provides SUBJECT_PRESETS, LAYOUT_PRESETS, and build_system_prompt()
for use in CLI and future interactive frontends.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Subject-type presets
# ---------------------------------------------------------------------------

_BASE_PROMPT = (
    "你是文档清洗助手。对给定的文本块列表，完成：\n"
    "1. 修正明显的 OCR 错误（错别字、乱码）\n"
    "2. 判断每个文本块的信息价值\n\n"
    "对每个文本块返回 JSON 对象：\n"
    '{"index": 序号, "action": "keep"|"discard"|"fix", '
    '"fixed_text": "修正后文本（仅fix时）", "reason": "简短说明"}\n\n'
    "丢弃标准：纯广告、版权声明、无实质信息的装饰文本、乱码无法修复\n"
    "保留标准：有知识价值的正文、表格数据、标题\n\n"
    "返回一个 JSON 数组，包含所有文本块的处理结果。"
)

_MEDICAL_SUFFIX = (
    "\n\n【医学教材专项说明】\n"
    "- 保留解剖术语、药品名称、剂量单位（如 mg、mmol/L、mmHg）\n"
    "- 修正常见医学 OCR 错误，例如：mg 误识为 rng、mmHg 误识为 mmllg、ug 误识为 ug\n"
    "- 保留病理描述、临床数据、检验指标及正常参考范围\n"
    "- 保留药物剂量、给药途径、疗程等临床信息"
)

_STEM_SUFFIX = (
    "\n\n【理工教材专项说明】\n"
    "- 保留公式符号、数学单位、变量名（如 alpha、beta、delta、sigma）\n"
    "- 修正数字 OCR 错误，例如：0 与 O 混淆、1 与 l 混淆\n"
    "- 保留图表引用编号（如图1-2、表3-1、式(2.5)）\n"
    "- 保留实验数据、测量单位、误差范围等定量信息"
)

_HUMANITIES_SUFFIX = (
    "\n\n【文史教材专项说明】\n"
    "- 保留古文引用和注释，不将文言文视为乱码\n"
    "- 修正繁简混用问题，统一为简体中文（除非原文明确使用繁体）\n"
    "- 保留人名、地名、年代信息（如朝代、公元纪年、历史事件名称）\n"
    "- 保留引用文献的出处标注和脚注内容"
)

SUBJECT_PRESETS: dict[str, str] = {
    "通用（默认）": _BASE_PROMPT,
    "医学教材": _BASE_PROMPT + _MEDICAL_SUFFIX,
    "理工教材": _BASE_PROMPT + _STEM_SUFFIX,
    "文史教材": _BASE_PROMPT + _HUMANITIES_SUFFIX,
}

# ---------------------------------------------------------------------------
# Layout presets (appended to subject prompt when non-empty)
# ---------------------------------------------------------------------------

LAYOUT_PRESETS: dict[str, str] = {
    "单列（默认）": "",
    "双列排版": (
        "【排版说明】文档为双列排版，相邻文本块可能是同一段落的左右两列，"
        "请结合上下文判断文本块之间的逻辑关系，避免将跨列断句误判为独立段落。"
    ),
    "图文混排": (
        "【排版说明】文档含大量图片和文字混排，图片说明文字可能被单独提取为文本块，"
        "请结合上下文判断信息价值，图注、图题等说明性文字应予以保留。"
    ),
}

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_system_prompt(subject_key: str, layout_key: str) -> str:
    """Combine subject preset and layout hint into a single system prompt.

    Args:
        subject_key: Key from SUBJECT_PRESETS (e.g. "医学教材").
        layout_key: Key from LAYOUT_PRESETS (e.g. "双列排版").

    Returns:
        Combined system prompt string.
    """
    base = SUBJECT_PRESETS.get(subject_key, SUBJECT_PRESETS["通用（默认）"])
    layout_hint = LAYOUT_PRESETS.get(layout_key, "")
    if layout_hint:
        return base + "\n\n" + layout_hint
    return base
