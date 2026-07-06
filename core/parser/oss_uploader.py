"""
OSS 上传模块 - 阿里云对象存储服务集成

本模块封装了阿里云 OSS（Object Storage Service）的上传和签名功能，
用于在 PDF 解析管道中上传待解析的 PDF 文件，并生成临时访问 URL。

## 阿里云 OSS 的作用

在 RAG 知识库构建管道中，MinerU 云端解析服务需要一个可访问的 PDF URL。
本模块负责：
1. 将本地 PDF 文件上传至阿里云 OSS
2. 生成有时效性的签名 URL，供 MinerU 解析服务下载

这种设计的优势：
- 避免在本地搭建文件服务器
- 利用 OSS 的 CDN 加速全球访问
- 通过签名 URL 实现安全的临时访问控制
- URL 过期后自动失效，保护文件安全

## 使用示例

```python
from core.config import load_config
from core.parser.oss_uploader import upload_to_oss, sign_oss_download_url

# 加载配置
config = load_config()

# 上传文件并获取签名 URL
signed_url = upload_to_oss(
    file_path="data/input/sample.pdf",
    config=config,
    log_fn=print,  # 可选的日志回调
    original_name="重要文档.pdf"  # 可选，用于保留原始文件名
)
print(f"签名 URL: {signed_url}")

# 后续可使用签名 URL 重新生成（如果已过期）
new_url = sign_oss_download_url("mineru-uploads/document.pdf", config)
```

## 环境变量依赖

- OSS_ACCESS_KEY_ID: 阿里云 AccessKey ID
- OSS_ACCESS_KEY_SECRET: 阿里云 AccessKey Secret
- OSS_ENDPOINT: OSS 区域端点（如 oss-cn-hangzhou.aliyuncs.com）
- OSS_BUCKET: OSS Bucket 名称

## 配置文件参数（config.yaml）

```yaml
parser:
  oss:
    prefix: "mineru-uploads"  # OSS 对象名前缀
    url_expiry: 3600          # 签名 URL 有效期（秒），默认 1 小时
```
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Callable, Optional


def _normalize_endpoint(endpoint: str) -> str:
    """
    标准化 OSS 端点 URL，提取域名部分。

    用户可能传入各种格式的端点：
    - oss-cn-hangzhou.aliyuncs.com
    - https://oss-cn-hangzhou.aliyuncs.com
    - http://oss-cn-hangzhou.aliyuncs.com/

    此函数统一转换为纯域名格式：oss-cn-hangzhou.aliyuncs.com

    Args:
        endpoint: 原始端点字符串，可能包含协议和尾部斜杠

    Returns:
        标准化后的域名，不包含协议和尾部斜杠

    Example:
        >>> _normalize_endpoint("https://oss-cn-hangzhou.aliyuncs.com/")
        'oss-cn-hangzhou.aliyuncs.com'
    """
    endpoint = endpoint.strip()
    endpoint = re.sub(r"^https?://", "", endpoint, flags=re.IGNORECASE)
    return endpoint.rstrip("/")


def _safe_object_name(filename: str) -> str:
    """
    将文件名转换为 OSS 安全的对象名。

    OSS 对象名有严格的命名规范：
    - 必须使用 UTF-8 编码
    - 避免特殊字符（如中文、空格等）可能导致访问问题

    此函数执行以下转换：
    1. 提取文件名（去除路径）
    2. 将 Unicode 字符转换为 ASCII（如中文转为拼音或移除）
    3. 将非字母数字字符替换为连字符
    4. 确保文件名非空且包含扩展名

    Args:
        filename: 原始文件名，可能包含路径或特殊字符

    Returns:
        符合 OSS 规范的安全文件名

    Example:
        >>> _safe_object_name("/path/to/重要文档 v1.0.pdf")
        'document-v1-0.pdf'
        >>> _safe_object_name("中文文件名.pdf")
        'document.pdf'
    """
    name = Path(filename).name.strip()
    stem = Path(name).stem
    ext = Path(name).suffix.lower() or ".pdf"
    normalized = unicodedata.normalize("NFKD", stem)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_name).strip("-._")
    return f"{ascii_name or 'document'}{ext}"


def build_oss_object_key(config: dict, filename: str) -> str:
    """
    构建完整的 OSS 对象键（Object Key）。

    OSS 对象键类似于文件路径，用于唯一标识存储桶中的对象。
    格式：{prefix}/{filename}
    例如：mineru-uploads/document.pdf

    Args:
        config: 配置字典，从 config.yaml 加载
        filename: 原始文件名，会被转换为安全格式

    Returns:
        完整的 OSS 对象键

    Example:
        >>> config = {"parser": {"oss": {"prefix": "mineru-uploads"}}}
        >>> build_oss_object_key(config, "测试文档.pdf")
        'mineru-uploads/document.pdf'
    """
    oss_cfg = config.get("parser", {}).get("oss", {})
    prefix = str(oss_cfg.get("prefix", "mineru-uploads")).strip().strip("/")
    upload_name = _safe_object_name(filename)
    return f"{prefix}/{upload_name}" if prefix else upload_name


def _get_oss_bucket(config: dict):
    """
    初始化并返回 OSS Bucket 客户端对象。

    此函数负责：
    1. 从环境变量读取 OSS 认证信息
    2. 验证必需的环境变量是否已配置
    3. 创建 oss2.Auth 认证对象
    4. 返回 Bucket 对象及相关信息

    环境变量要求：
    - OSS_ACCESS_KEY_ID: 阿里云 AccessKey ID
    - OSS_ACCESS_KEY_SECRET: 阿里云 AccessKey Secret
    - OSS_ENDPOINT: OSS 区域端点
    - OSS_BUCKET: Bucket 名称

    Args:
        config: 配置字典（当前未使用，保留用于未来扩展）

    Returns:
        元组 (bucket, endpoint, bucket_name):
        - bucket: oss2.Bucket 对象，用于执行上传/下载操作
        - endpoint: 标准化后的端点域名
        - bucket_name: Bucket 名称

    Raises:
        ImportError: 未安装 oss2 库时抛出
        ValueError: 缺少必需的环境变量时抛出

    Example:
        >>> bucket, endpoint, name = _get_oss_bucket(config)
        >>> bucket.put_object("test.txt", b"hello")
    """
    try:
        import oss2
    except ImportError as exc:
        raise ImportError("oss2 is not installed. Run: pip install 'oss2>=2.18.0'") from exc

    access_key_id = os.getenv("OSS_ACCESS_KEY_ID", "")
    access_key_secret = os.getenv("OSS_ACCESS_KEY_SECRET", "")
    endpoint_raw = os.getenv("OSS_ENDPOINT", "")
    bucket_name = os.getenv("OSS_BUCKET", "")

    missing = [
        key
        for key, value in {
            "OSS_ACCESS_KEY_ID": access_key_id,
            "OSS_ACCESS_KEY_SECRET": access_key_secret,
            "OSS_ENDPOINT": endpoint_raw,
            "OSS_BUCKET": bucket_name,
        }.items()
        if not value
    ]
    if missing:
        raise ValueError(f"Missing OSS credentials: {', '.join(missing)}")

    endpoint = _normalize_endpoint(endpoint_raw)
    auth = oss2.Auth(access_key_id, access_key_secret)
    bucket = oss2.Bucket(auth, f"https://{endpoint}", bucket_name)
    return bucket, endpoint, bucket_name


def sign_oss_download_url(object_key: str, config: dict, expiry: Optional[int] = None) -> str:
    """
    为 OSS 对象生成带签名的临时下载 URL。

    ## 签名 URL 的作用

    OSS 对象默认是私有的，不能直接通过 URL 访问。签名 URL 通过以下机制实现安全访问：

    1. **临时授权**: URL 包含加密签名，仅在一定时间内有效
    2. **权限控制**: 签名基于 AccessKey 生成，确保只有授权用户可创建
    3. **无需暴露密钥**: URL 不包含 AccessKey Secret，过期后无法访问

    ## 签名 URL 原理

    签名 URL 格式示例：
    https://bucket.oss-cn-hangzhou.aliyuncs.com/object.pdf?
        OSSAccessKeyId=xxx&
        Expires=1234567890&
        Signature=xxx

    参数说明：
    - OSSAccessKeyId: 标识访问者身份
    - Expires: URL 过期时间戳（Unix 时间戳）
    - Signature: 基于 Secret + 对象键 + 过期时间计算的 HMAC 签名

    当 OSS 服务器收到请求时，会：
    1. 检查当前时间是否在过期时间之前
    2. 使用相同算法验证签名是否正确
    3. 验证通过则返回对象内容

    Args:
        object_key: OSS 对象键，如 "mineru-uploads/document.pdf"
        config: 配置字典，包含 url_expiry 参数
        expiry: URL 有效期（秒），None 则使用配置文件中的值，默认 3600 秒（1 小时）

    Returns:
        带签名的临时下载 URL

    Example:
        >>> url = sign_oss_download_url("mineru-uploads/doc.pdf", config)
        >>> print(url)
        'https://bucket.oss-cn-hangzhou.aliyuncs.com/mineru-uploads/doc.pdf?OSSAccessKeyId=...'

        # 自定义过期时间（10 分钟）
        >>> url = sign_oss_download_url("mineru-uploads/doc.pdf", config, expiry=600)
    """
    bucket, _, _ = _get_oss_bucket(config)
    oss_cfg = config.get("parser", {}).get("oss", {})
    url_expiry = int(expiry if expiry is not None else oss_cfg.get("url_expiry", 3600))
    return str(bucket.sign_url("GET", object_key, url_expiry))


def oss_object_exists(object_key: str, config: dict) -> bool:
    """
    检查 OSS 对象是否存在。

    通过 HEAD 请求检查对象，不下载实际内容。
    适用于：
    - 检查文件是否已上传，避免重复上传
    - 验证对象键是否正确

    Args:
        object_key: OSS 对象键
        config: 配置字典

    Returns:
        True 表示对象存在，False 表示不存在或无权限访问

    Example:
        >>> if oss_object_exists("mineru-uploads/doc.pdf", config):
        ...     print("文件已存在")
        ... else:
        ...     upload_to_oss("local.pdf", config)
    """
    bucket, _, _ = _get_oss_bucket(config)
    try:
        bucket.head_object(object_key)
        return True
    except Exception:
        return False


def upload_to_oss(
    file_path: str,
    config: dict,
    log_fn: Optional[Callable[[str], None]] = None,
    original_name: Optional[str] = None,
) -> str:
    """Upload a file to Aliyun OSS and return a signed temporary URL."""

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    oss_cfg = config.get("parser", {}).get("oss", {})
    url_expiry = int(oss_cfg.get("url_expiry", 3600))

    upload_name = _safe_object_name(original_name or path.name)
    object_key = build_oss_object_key(config, original_name or path.name)
    file_size_mb = path.stat().st_size / 1024 / 1024

    bucket, endpoint, bucket_name = _get_oss_bucket(config)
    _log(f"OSS endpoint: {endpoint}  bucket: {bucket_name}")
    _log(f"Uploading file: {upload_name} ({file_size_mb:.2f} MB) -> {object_key}")

    bucket.put_object_from_file(object_key, str(path))
    _log(f"Upload complete; signed URL expiry={url_expiry}s")

    return str(bucket.sign_url("GET", object_key, url_expiry))
