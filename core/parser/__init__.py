"""
Parser - PDF 解析模块

本包负责将 PDF 文件解析为结构化的内容块列表。
可以把它理解为"扫描仪"，把难以处理的 PDF 变成好用的结构化数据。

## 解析流程

```
PDF 文件
    ↓ 上传到 OSS
签名 URL（供云端解析）
    ↓ 提交 MinerU 任务
云端解析中...
    ↓ 轮询等待完成
下载结果 ZIP
    ↓ 解压提取
*_content_list.json + images/
    ↓ 转换映射
list[ContentBlock]  ← 最终输出
```

## 支持的解析器

| 解析器 | 文件 | 说明 |
|--------|------|------|
| MinerU 云端 | mineru_parser.py | 默认，通过 302.ai API 调用 MinerU 云服务 |
| MinerU 官方 | mineru_official_parser.py | 直接调用官方 magic-pdf（需本地安装） |
| Document Mind | document_mind_parser.py | 阿里云 Document Mind 服务 |
| Provider | provider.py | 解析器工厂，根据配置自动选择 |

## 核心入口函数

| 函数 | 说明 |
|------|------|
| parse_pdf() | 便捷入口：自动选择解析器完成解析 |
| upload_pdf_to_oss() | 上传 PDF 到阿里云 OSS，返回签名 URL |
| parse_pdf_from_url() | 用签名 URL 提交 MinerU 云端解析任务 |

## 使用示例

### 基础用法（自动选择解析器）

```python
from core.parser import parse_pdf

# 解析 PDF，输出到指定目录
blocks = parse_pdf("data/input/sample.pdf")

for block in blocks:
    print(f"[{block.type}] 第{block.page_idx}页: {block.text[:50]}...")
```

### 指定解析器

```python
# 方式一：通过环境变量
# export PDF_PARSER_PROVIDER=mineru_official

# 方式二：直接导入特定解析器
from core.parser.mineru_parser import parse_pdf as mineru_parse
blocks = mineru_parse("data/input/sample.pdf")
```

### 分步调用（更多控制）

```python
from core.parser.mineru_parser import upload_pdf_to_oss, parse_pdf_from_url

# 步骤 1：上传到 OSS
signed_url = upload_pdf_to_oss("data/input/sample.pdf")
print(f"上传成功，URL: {signed_url[:50]}...")

# 步骤 2：提交解析任务
blocks = parse_pdf_from_url(
    pdf_url=signed_url,
    pdf_path="data/input/sample.pdf",
    output_dir="data/output/sample/",
)

print(f"解析完成，共 {len(blocks)} 个内容块")
```

## 输出类型说明

解析器输出的 ContentBlock 有三种主要类型：

| type | 说明 | 特殊字段 |
|------|------|----------|
| text | 文本段落 | text: 段落内容 |
| table | 表格 | table_html: 表格 HTML 代码 |
| image | 图片 | image_path: 图片本地路径 |

## 环境配置

```bash
# .env 文件

# 选择解析器（可选值：mineru / mineru_official / ali_document_mind）
PDF_PARSER_PROVIDER=mineru

# 302.ai API（MinerU 云端）
302AI_API_KEY=xxx
302AI_API_BASE=https://api.302.ai

# 阿里云 OSS（文件上传）
OSS_ACCESS_KEY_ID=xxx
OSS_ACCESS_KEY_SECRET=xxx
OSS_ENDPOINT=oss-cn-hangzhou.aliyuncs.com
OSS_BUCKET=your-bucket-name
```

## 相关文件

| 文件 | 职责 |
|------|------|
| mineru_parser.py | 主解析器，对接 302.ai 云端 API |
| oss_uploader.py | 阿里云 OSS 上传 |
| column_reorder.py | 多栏布局重排 |
| pdf_sharding.py | 大文件分片处理 |
| provider.py | 解析器工厂，按配置选择解析器 |
| verify_parse.py | 解析管道验证脚本 |
"""
from __future__ import annotations

from core.parser.provider import parse_pdf

__all__ = ["parse_pdf"]