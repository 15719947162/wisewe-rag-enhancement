"""
Prompts - LLM 提示词模板库

本包集中管理所有 LLM 相关的提示词模板，让"教 AI 怎么做事"变得可配置、可维护。
可以把它理解为"指令书"，告诉 LLM 在不同场景下应该如何工作。

## 为什么需要独立的 Prompts 模块？

```
之前的做法（分散在各处）：
cleaner/llm_cleaner.py    → 提示词写死在代码里
chunker/llm_chunker.py    → 提示词写死在代码里
rag/generator.py          → 提示词写死在代码里

问题：
- 修改提示词需要改代码
- 不同场景的提示词难以管理
- 无法动态切换提示词策略

现在的做法（集中管理）：
core/prompts/__init__.py  → 所有提示词模板
各模块 → 从这里导入，统一管理
```

## 提示词分类

### 1. 学科类型预设（SUBJECT_PRESETS）

针对不同学科特点定制的清洗提示词：

| 键名 | 中文显示名 | 特殊处理 |
|------|-----------|----------|
| general | 通用（默认） | 基础清洗规则 |
| medical | 医学教材 | 保留医学术语、药品名、剂量单位 |
| stem | 理工教材 | 保留公式符号、变量名、单位 |
| humanities | 文史教材 | 保留古文、人名地名、年代信息 |

### 2. 排版类型预设（LAYOUT_PRESETS）

针对不同文档排版特点的提示词补充：

| 键名 | 中文显示名 | 特殊处理 |
|------|-----------|----------|
| single_column | 单列（默认） | 无特殊处理 |
| double_column | 双列排版 | 提醒注意跨列断句问题 |
| mixed | 图文混排 | 提醒保留图注、图题 |

## 使用示例

### 基础用法

```python
from core.prompts import build_system_prompt

# 构建医学教材 + 双列排版的提示词
prompt = build_system_prompt(
    subject_key="medical",
    layout_key="double_column",
)

print(prompt)
# 输出：基础提示词 + 医学专项说明 + 双列排版说明
```

### 查看所有预设

```python
from core.prompts import SUBJECT_PRESETS, LAYOUT_PRESETS

print("可用学科预设:", list(SUBJECT_PRESETS.keys()))
print("可用排版预设:", list(LAYOUT_PRESETS.keys()))
```

### 在 Cleaner 中使用

```python
from core.cleaner import clean_blocks
from core.prompts import build_system_prompt

# 构建适合医学教材的提示词
system_prompt = build_system_prompt(
    subject_key="medical",
    layout_key="single_column",
)

result = clean_blocks(
    blocks,
    use_llm=True,
    llm_system_prompt=system_prompt,
    llm_api_key=os.getenv("LLM_API_KEY"),
)
```

## API 键名映射

为了方便 API 调用，提供了稳定的英文键名：

```python
# 这些两种写法等价：
build_system_prompt("medical", "double_column")
build_system_prompt("医学教材", "双列排版")

# 键名映射表：
SUBJECT_KEY_MAP = {
    "general": "通用（默认）",
    "medical": "医学教材",
    "stem": "理工教材",
    "humanities": "文史教材",
}

LAYOUT_KEY_MAP = {
    "single_column": "单列（默认）",
    "double_column": "双列排版",
    "mixed": "图文混排",
}
```

## 如何添加新预设

1. 在本文件中添加新的 `_XXX_SUFFIX` 变量
2. 在 `SUBJECT_PRESETS` 或 `LAYOUT_PRESETS` 中注册
3. 如需 API 支持，在 `XXX_KEY_MAP` 中添加映射
"""

from __future__ import annotations

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

# Stable key aliases for API/frontend use
SUBJECT_KEY_MAP: dict[str, str] = {
    "general": "通用（默认）",
    "medical": "医学教材",
    "stem": "理工教材",
    "humanities": "文史教材",
}

LAYOUT_KEY_MAP: dict[str, str] = {
    "single_column": "单列（默认）",
    "double_column": "双列排版",
    "mixed": "图文混排",
}


def build_system_prompt(subject_key: str, layout_key: str) -> str:
    """构建完整的系统提示词。

    根据学科类型和排版类型，组合出适合的提示词。
    支持中文显示名和英文 API 键名两种输入方式。

    Args:
        subject_key: 学科类型，如 "medical" 或 "医学教材"
        layout_key: 排版类型，如 "double_column" 或 "双列排版"

    Returns:
        组合后的完整提示词字符串

    Example:
        >>> prompt = build_system_prompt("medical", "double_column")
        >>> print(prompt)  # 包含基础提示词 + 医学说明 + 双列排版说明
    """
    subject_display = SUBJECT_KEY_MAP.get(subject_key, subject_key)
    layout_display = LAYOUT_KEY_MAP.get(layout_key, layout_key)
    base = SUBJECT_PRESETS.get(subject_display, SUBJECT_PRESETS["通用（默认）"])
    layout_hint = LAYOUT_PRESETS.get(layout_display, "")
    if layout_hint:
        return base + "\n\n" + layout_hint
    return base


__all__ = [
    # 预设字典
    "SUBJECT_PRESETS",      # 学科类型提示词预设
    "LAYOUT_PRESETS",       # 排版类型提示词预设
    # 键名映射
    "SUBJECT_KEY_MAP",      # 学科英文键 → 中文显示名
    "LAYOUT_KEY_MAP",       # 排版英文键 → 中文显示名
    # 构建函数
    "build_system_prompt",  # 构建完整提示词
]

