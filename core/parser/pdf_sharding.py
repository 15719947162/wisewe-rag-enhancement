"""
PDF 分片模块 - 大文件处理与并发解析支持

本模块实现了 PDF 文件的智能分片功能，用于解决大文件解析的性能瓶颈问题。

## 核心原理

当处理大型 PDF 文件（如数百页的技术文档、学术论文集）时，直接解析存在以下问题：
1. **内存压力**：一次性加载所有页面内容占用大量内存
2. **处理超时**：单任务处理时间过长，容易触发云端 API 超时限制
3. **失败代价高**：解析失败后需要从头开始，无法增量重试

分片策略将大文件拆分为多个小文件，实现：
- **并行处理**：多个分片可同时提交解析，显著缩短总耗时
- **容错重试**：单个分片失败不影响其他分片，可独立重试
- **资源优化**：合理控制每个任务的规模，避免资源浪费

## 分片策略

### 1. 固定页数分片（split_pdf_to_shards）
- 简单直接，按固定页数切分
- 适用于内容均匀的文档（如纯文本书籍）
- 缺点：无法适应复杂页面（图片密集型 vs 纯文本型）

### 2. 加权智能分片（split_pdf_to_weighted_shards）
- 根据每页的内容复杂度（文本量、图片数、绘图数）计算权重
- 使用动态规划算法优化分片边界，使各分片的"处理难度"均衡
- 适用于混合内容文档（图文混排、扫描件与文本混合）

## 并发处理机制

分片本身不直接实现并发，而是为上层调用提供并发友好的数据结构：

```python
# 典型并发处理流程
shards = split_pdf_to_weighted_shards(pdf_path, shard_dir, ...)

# 使用 ThreadPoolExecutor 或 asyncio 并行处理
with ThreadPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(parse_shard, shard) for shard in shards]
    results = [f.result() for f in futures]

# 合并结果（自动处理页码偏移）
all_blocks = merge_shard_records(shard_records)
```

## 数据流

PDF 文件 → inspect_pdf() → 分析页面特征 → 计算权重
    → split_pdf_to_weighted_shards() → 动态规划分片 → 多个 PDF 分片文件
    → 并发解析 → offset_shard_blocks() → 调整页码偏移
    → merge_shard_records() → 按页码排序合并 → 最终 ContentBlock 列表
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.models.content_block import ContentBlock


@dataclass(frozen=True)
class PdfPageProfile:
    """
    单个 PDF 页面的特征配置。

    用于记录页面的内容复杂度信息，作为加权分片的决策依据。
    权重值越高，表示该页面处理复杂度越大，应在分片时给予更多资源。

    Attributes:
        page_index: 页面索引（从 0 开始）
        text_chars: 文本字符总数
        text_blocks: 文本块数量（段落级）
        image_count: 图片数量
        drawing_count: 绘图对象数量（矢量图形）
        likely_scanned: 是否可能是扫描件（无文本但含图片/绘图）
        weight: 综合权重值（用于分片均衡）
    """
    page_index: int
    text_chars: int
    text_blocks: int
    image_count: int
    drawing_count: int
    likely_scanned: bool
    weight: int


@dataclass(frozen=True)
class PdfInspection:
    """
    PDF 文件整体检查结果。

    在决定是否分片以及如何分片之前，先对 PDF 进行快速检查，
    获取文件大小、页数、文本密度等关键信息。

    Attributes:
        page_count: 总页数
        file_size_bytes: 文件大小（字节）
        sampled_text_chars: 采样页面的文本字符总数
        sampled_pages: 实际采样的页数
        likely_scanned: 整体是否可能是扫描文档
        page_profiles: 各页面的详细配置（可选）
    """
    page_count: int
    file_size_bytes: int
    sampled_text_chars: int
    sampled_pages: int
    likely_scanned: bool
    page_profiles: tuple[PdfPageProfile, ...] = ()

    @property
    def file_size_mb(self) -> float:
        """文件大小（MB 单位），便于日志输出和阈值判断。"""
        return self.file_size_bytes / (1024 * 1024)


@dataclass(frozen=True)
class PdfShard:
    """
    单个 PDF 分片的元信息。

    记录分片在原始文档中的位置范围，以及分片文件的存储路径。
    用于追踪分片来源，便于后续合并结果时正确调整页码偏移。

    Attributes:
        index: 分片序号（从 1 开始，便于人类阅读）
        start_page: 起始页码（从 0 开始，左闭）
        end_page: 结束页码（从 0 开始，右开）
        path: 分片文件的存储路径
        weight: 该分片的总权重（加权分片时计算）
    """
    index: int
    start_page: int
    end_page: int
    path: Path
    weight: int = 0

    @property
    def page_count(self) -> int:
        """分片包含的页数。"""
        return self.end_page - self.start_page

    @property
    def display_range(self) -> str:
        """人类可读的页码范围字符串（如 "P1-10"）。"""
        return f"P{self.start_page + 1}-{self.end_page}"


@dataclass(frozen=True)
class PdfShardSaveOptions:
    """
    PDF 分片保存选项。

    控制分片文件的压缩和优化级别，平衡文件大小与生成速度。

    Attributes:
        garbage: 垃圾回收级别（0-4），值越高压缩越彻底，但耗时更长
        deflate: 是否启用 Deflate 压缩（ZIP 标准压缩算法）
    """
    garbage: int = 4
    deflate: bool = True

    def save_kwargs(self) -> dict[str, object]:
        """
        转换为 PyMuPDF 的 save() 方法参数字典。

        Returns:
            适合传递给 fitz.Document.save() 的参数字典
        """
        return {
            "garbage": min(4, max(0, int(self.garbage))),
            "deflate": bool(self.deflate),
        }


ShardBlockRecord = tuple[int, int, int, ContentBlock]
"""
分片解析结果记录。

用于临时存储分片解析后的 ContentBlock，包含排序所需的元信息：
- 元素 0: 全局页码（调整偏移后的原始文档页码）
- 元素 1: 分片序号
- 元素 2: 块在分片内的顺序
- 元素 3: 调整后的 ContentBlock 对象

合并时按 (页码, 分片序号, 块顺序) 排序，确保结果顺序正确。
"""


def import_fitz():
    """
    延迟导入 PyMuPDF（fitz）库。

    采用延迟导入策略，避免在不需要分片功能时强制安装 PyMuPDF。
    PyMuPDF 是一个较重的依赖（约 50MB），仅在实际分片时才需要。

    Returns:
        fitz 模块对象

    Raises:
        RuntimeError: 未安装 pymupdf 包时抛出，提示用户安装

    Example:
        >>> fitz = import_fitz()
        >>> doc = fitz.open("document.pdf")
    """
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PDF sharding requires PyMuPDF. Install pymupdf from requirements.txt."
        ) from exc
    return fitz


def inspect_pdf(
    pdf_path: str,
    text_sample_pages: int = 5,
    *,
    profile_pages: bool = False,
) -> PdfInspection:
    """
    检查 PDF 文件的基本信息，为分片决策提供依据。

    快速扫描 PDF 文件，获取页数、文件大小、文本密度等信息。
    通过采样前几页判断文档类型（文本文档 vs 扫描件），为后续处理策略提供参考。

    Args:
        pdf_path: PDF 文件路径
        text_sample_pages: 采样页数（用于计算文本密度），默认 5 页
        profile_pages: 是否为所有页面生成详细配置（用于加权分片）
            设为 True 时会遍历所有页面，计算每页权重，耗时较长
            设为 False 时仅采样前 N 页，适合快速检查

    Returns:
        PdfInspection 对象，包含文件大小、页数、文本密度、扫描判断等信息

    Raises:
        FileNotFoundError: PDF 文件不存在

    Example:
        >>> info = inspect_pdf("large_doc.pdf", profile_pages=True)
        >>> print(f"共 {info.page_count} 页，{info.file_size_mb:.1f} MB")
        >>> if info.likely_scanned:
        ...     print("检测到扫描文档，建议使用 OCR")
    """
    fitz = import_fitz()
    pdf_path_obj = Path(pdf_path)
    if not pdf_path_obj.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    file_size_bytes = pdf_path_obj.stat().st_size
    sampled_text_chars = 0
    sampled_pages = 0
    page_profiles: list[PdfPageProfile] = []

    doc = fitz.open(str(pdf_path_obj))
    try:
        page_count = int(doc.page_count)
        sampled_pages = min(max(int(text_sample_pages), 0), page_count)
        if profile_pages:
            # 详细模式：遍历所有页面，生成完整配置
            for page_index in range(page_count):
                page = doc.load_page(page_index)
                profile = _profile_pdf_page(page, page_index)
                page_profiles.append(profile)
                if page_index < sampled_pages:
                    sampled_text_chars += profile.text_chars
        else:
            # 快速模式：仅采样前 N 页
            for page_index in range(sampled_pages):
                text = doc.load_page(page_index).get_text("text") or ""
                sampled_text_chars += len(text.strip())
    finally:
        doc.close()

    return PdfInspection(
        page_count=page_count,
        file_size_bytes=file_size_bytes,
        sampled_text_chars=sampled_text_chars,
        sampled_pages=sampled_pages,
        likely_scanned=sampled_pages > 0 and sampled_text_chars == 0,
        page_profiles=tuple(page_profiles),
    )


def _profile_pdf_page(page: object, page_index: int) -> PdfPageProfile:
    """
    分析单个 PDF 页面的内容特征并计算权重。

    提取页面的文本量、图片数、绘图数等信息，综合判断页面复杂度。
    复杂度高的页面（如包含大量图片或绘图）会被分配更高的权重，
    在分片时考虑这些权重，避免某个分片承担过多复杂页面。

    Args:
        page: PyMuPDF 的页面对象
        page_index: 页面索引

    Returns:
        PdfPageProfile 对象，包含页面特征和计算出的权重
    """
    text = _safe_page_text(page)
    text_chars = len(text.strip())
    text_blocks = _safe_text_block_count(page)
    image_count = _safe_len_call(page, "get_images")
    drawing_count = _safe_len_call(page, "get_drawings")
    likely_scanned = text_chars == 0 and (image_count > 0 or drawing_count > 0)
    weight = _score_pdf_page(
        text_chars=text_chars,
        text_blocks=text_blocks,
        image_count=image_count,
        drawing_count=drawing_count,
        likely_scanned=likely_scanned,
    )
    return PdfPageProfile(
        page_index=page_index,
        text_chars=text_chars,
        text_blocks=text_blocks,
        image_count=image_count,
        drawing_count=drawing_count,
        likely_scanned=likely_scanned,
        weight=weight,
    )


def _safe_page_text(page: object) -> str:
    """
    安全获取页面文本内容。

    封装 PyMuPDF 的 get_text() 方法，捕获异常返回空字符串。
    某些特殊页面（如加密页面、损坏页面）可能无法正常提取文本。

    Args:
        page: PyMuPDF 页面对象

    Returns:
        页面文本内容，失败时返回空字符串
    """
    try:
        return str(page.get_text("text") or "")  # type: ignore[attr-defined]
    except Exception:
        return ""


def _safe_text_block_count(page: object) -> int:
    """
    计算页面中的非空文本块数量。

    文本块是 PyMuPDF 的概念，表示逻辑上连续的文本区域（如段落）。
    块数量越多，表示页面排版越复杂。

    Args:
        page: PyMuPDF 页面对象

    Returns:
        非空文本块的数量
    """
    try:
        blocks = page.get_text("blocks") or []  # type: ignore[attr-defined]
    except Exception:
        return 0
    count = 0
    for block in blocks:
        try:
            text = str(block[4] or "")
        except Exception:
            text = ""
        if text.strip():
            count += 1
    return count


def _safe_len_call(page: object, method_name: str) -> int:
    """
    安全调用返回列表的页面方法并返回长度。

    用于安全获取 get_images()、get_drawings() 等方法的返回值长度。
    这些方法在某些 PDF 版本或页面类型可能失败。

    Args:
        page: PyMuPDF 页面对象
        method_name: 方法名称（如 "get_images"、"get_drawings"）

    Returns:
        返回列表的长度，失败时返回 0
    """
    method = getattr(page, method_name, None)
    if method is None:
        return 0
    try:
        return len(method())
    except Exception:
        return 0


def _score_pdf_page(
    *,
    text_chars: int,
    text_blocks: int,
    image_count: int,
    drawing_count: int,
    likely_scanned: bool,
) -> int:
    """
    计算 PDF 页面的综合处理权重。

    权重值反映页面的"解析复杂度"，用于指导分片边界划分。
    权重越高表示该页面需要更多处理时间或资源。

    权重计算规则：
    - 文本权重：每 600 字符加 1 分，最多 6 分
    - 块权重：每 8 个文本块加 1 分，最多 4 分
    - 图片权重：每张图片加 4 分，最多 16 分
    - 绘图权重：每 4 个绘图加 1 分，最多 8 分
    - 扫描权重：扫描页额外加 8 分（需 OCR 处理）
    - 基础权重：每页至少 1 分

    图片和扫描页权重最高，因为这些内容通常需要更多的处理时间
    （图片提取、OCR 识别等）。

    Args:
        text_chars: 文本字符数
        text_blocks: 文本块数
        image_count: 图片数
        drawing_count: 绘图对象数
        likely_scanned: 是否为扫描页

    Returns:
        综合权重值（至少为 1）
    """
    text_weight = min(max(text_chars, 0) // 600, 6)
    block_weight = min(max(text_blocks, 0) // 8, 4)
    image_weight = min(max(image_count, 0) * 4, 16)
    drawing_weight = min(max(drawing_count, 0) // 4, 8)
    scanned_weight = 8 if likely_scanned else 0
    return max(1, 1 + text_weight + block_weight + image_weight + drawing_weight + scanned_weight)


def split_pdf_to_shards(
    pdf_path: str,
    shard_dir: Path,
    pages_per_shard: int,
    *,
    save_options: PdfShardSaveOptions | None = None,
) -> list[PdfShard]:
    """
    按固定页数将 PDF 切分为多个分片文件。

    这是最简单的分片策略：按固定页数切分，不考虑页面内容复杂度。
    适用于内容均匀的文档，或对分片均衡性要求不高的场景。

    分片文件命名规则：shard_{序号:03d}_p{起始页:04d}-{结束页:04d}.pdf
    例如：shard_001_p0001-0010.pdf（第 1 个分片，包含第 1-10 页）

    Args:
        pdf_path: 源 PDF 文件路径
        shard_dir: 分片文件输出目录（自动创建）
        pages_per_shard: 每个分片包含的页数
        save_options: 分片保存选项（控制压缩级别）

    Returns:
        PdfShard 列表，按分片序号排序

    Example:
        >>> shards = split_pdf_to_shards("doc.pdf", Path("shards/"), pages_per_shard=20)
        >>> print(f"生成了 {len(shards)} 个分片文件")
        >>> for shard in shards:
        ...     print(f"  {shard.display_range}: {shard.page_count} 页")
    """
    fitz = import_fitz()
    shard_dir.mkdir(parents=True, exist_ok=True)
    source = fitz.open(str(pdf_path))
    shards: list[PdfShard] = []
    save_kwargs = (save_options or PdfShardSaveOptions()).save_kwargs()
    try:
        page_count = int(source.page_count)
        for index, start_page in enumerate(range(0, page_count, max(1, pages_per_shard)), start=1):
            end_page = min(start_page + max(1, pages_per_shard), page_count)
            shard_path = shard_dir / f"shard_{index:03d}_p{start_page + 1:04d}-{end_page:04d}.pdf"
            shard_doc = fitz.open()
            try:
                # insert_pdf 的 to_page 参数是包含的（inclusive），所以需要 -1
                shard_doc.insert_pdf(source, from_page=start_page, to_page=end_page - 1)
                shard_doc.save(str(shard_path), **save_kwargs)
            finally:
                shard_doc.close()
            shards.append(
                PdfShard(
                    index=index,
                    start_page=start_page,
                    end_page=end_page,
                    path=shard_path,
                )
            )
    finally:
        source.close()
    return shards


def split_pdf_to_weighted_shards(
    pdf_path: str,
    shard_dir: Path,
    *,
    page_profiles: tuple[PdfPageProfile, ...] | list[PdfPageProfile],
    max_pages_per_shard: int,
    save_options: PdfShardSaveOptions | None = None,
) -> list[PdfShard]:
    """
    按加权策略将 PDF 切分为处理负载均衡的分片。

    这是更智能的分片策略：根据每页的内容复杂度计算权重，
    使用动态规划算法优化分片边界，使各分片的"总权重"尽可能均衡。

    适用场景：
    - 图文混排文档（部分页面含大量图片，部分为纯文本）
    - 扫描件与文本文档混合（扫描页需 OCR，处理时间更长）
    - 对并发处理时间有严格要求的场景

    权重均衡的优势：
    - 避免某些分片"过重"（包含大量复杂页面），导致并行处理时等待时间过长
    - 总体处理时间由最重的分片决定，均衡策略可缩短总耗时

    Args:
        pdf_path: 源 PDF 文件路径
        shard_dir: 分片文件输出目录（自动创建）
        page_profiles: 各页面的权重配置（通过 inspect_pdf(profile_pages=True) 获取）
        max_pages_per_shard: 单个分片的最大页数（硬限制）
        save_options: 分片保存选项

    Returns:
        PdfShard 列表，包含每个分片的权重值

    Example:
        >>> info = inspect_pdf("doc.pdf", profile_pages=True)
        >>> shards = split_pdf_to_weighted_shards(
        ...     "doc.pdf",
        ...     Path("shards/"),
        ...     page_profiles=info.page_profiles,
        ...     max_pages_per_shard=30,
        ... )
        >>> for shard in shards:
        ...     print(f"{shard.display_range}: 权重 {shard.weight}")
    """
    fitz = import_fitz()
    shard_dir.mkdir(parents=True, exist_ok=True)
    source = fitz.open(str(pdf_path))
    shards: list[PdfShard] = []
    save_kwargs = (save_options or PdfShardSaveOptions()).save_kwargs()
    try:
        page_count = int(source.page_count)
        # 使用动态规划规划分片边界
        ranges = plan_weighted_page_ranges(
            page_profiles,
            page_count=page_count,
            max_pages_per_shard=max_pages_per_shard,
        )
        for index, (start_page, end_page, weight) in enumerate(ranges, start=1):
            shard_path = shard_dir / f"shard_{index:03d}_p{start_page + 1:04d}-{end_page:04d}.pdf"
            shard_doc = fitz.open()
            try:
                shard_doc.insert_pdf(source, from_page=start_page, to_page=end_page - 1)
                shard_doc.save(str(shard_path), **save_kwargs)
            finally:
                shard_doc.close()
            shards.append(
                PdfShard(
                    index=index,
                    start_page=start_page,
                    end_page=end_page,
                    path=shard_path,
                    weight=weight,
                )
            )
    finally:
        source.close()
    return shards


def plan_weighted_page_ranges(
    page_profiles: tuple[PdfPageProfile, ...] | list[PdfPageProfile],
    *,
    page_count: int,
    max_pages_per_shard: int,
) -> list[tuple[int, int, int]]:
    """
    使用动态规划算法规划最优分片边界。

    这是加权分片的核心算法：在满足页数限制的前提下，找到使各分片权重
    最均衡的切分方案。

    ## 算法原理

    问题建模：
    - 输入：N 个页面，每页有权重 w[i]
    - 约束：每个分片最多 M 页
    - 目标：将 N 页分成 K 个分片，使"最大分片权重"最小化
    - 次要目标：在最大权重相同时，使"权重方差"最小化

    动态规划状态：
    - costs[k][i] = 将前 i 页分成 k 个分片的最优代价
    - 代价定义为 (最大分片权重, 权重平方和) 的二元组
    - 通过比较二元组实现双重优化目标

    状态转移：
    ```
    costs[k][i] = min(
        max(costs[k-1][j], weight(j,i)) + weight(j,i)² 的累加
    ) for j in [i-M, i-1]
    ```

    时间复杂度：O(K * N * M)，其中 K 是分片数，N 是总页数，M 是最大页数限制

    Args:
        page_profiles: 各页面的权重配置
        page_count: 总页数
        max_pages_per_shard: 单个分片的最大页数

    Returns:
        分片边界列表 [(start, end, weight), ...]
        - start: 起始页码（左闭）
        - end: 结束页码（右开）
        - weight: 该分片的总权重

    Note:
        如果 page_profiles 不完整（页数不匹配），会自动降级为固定页数分片
    """
    pages = max(0, int(page_count))
    max_pages = max(1, int(max_pages_per_shard))
    if pages <= 0:
        return []

    # 构建页码到权重的映射
    profiles_by_page = {profile.page_index: profile for profile in page_profiles}
    # 如果配置不完整，降级为固定分片
    if any(index not in profiles_by_page for index in range(pages)):
        return _fixed_page_ranges(pages, max_pages)

    # 计算目标分片数（与固定分片保持一致，便于对比）
    target_shard_count = max(1, (pages + max_pages - 1) // max_pages)

    # 构建权重数组和前缀和（用于 O(1) 计算区间权重）
    weights = [max(1, int(profiles_by_page[index].weight)) for index in range(pages)]
    prefix_weights = [0]
    for weight in weights:
        prefix_weights.append(prefix_weights[-1] + weight)

    # 动态规划：寻找最优分片边界
    # 目标：保持分片数与固定分片相同，但通过调整边界使权重更均衡
    # costs[k][i] = 将前 i 页分成 k 个分片的最优代价 (最大权重, 平方和)
    costs: list[dict[int, tuple[int, int]]] = [{0: (0, 0)}] + [dict() for _ in range(target_shard_count)]
    previous: list[dict[int, int]] = [dict() for _ in range(target_shard_count + 1)]

    # 按分片数递推
    for shard_count in range(1, target_shard_count + 1):
        # 第 k 个分片的结束页范围
        min_end = shard_count  # 至少需要 k 页才能分成 k 个分片
        max_end = pages - (target_shard_count - shard_count)  # 为后续分片预留足够页面
        for end_page in range(min_end, max_end + 1):
            best_cost: tuple[int, int] | None = None
            best_start: int | None = None
            # 第 k 个分片的起始页范围（受最大页数限制）
            min_start = max(shard_count - 1, end_page - max_pages)
            max_start = end_page - 1
            for start_page in range(min_start, max_start + 1):
                prior = costs[shard_count - 1].get(start_page)
                if prior is None:
                    continue
                # 计算当前分片权重
                shard_weight = prefix_weights[end_page] - prefix_weights[start_page]
                # 代价 = (最大分片权重, 所有分片权重平方和)
                # 平方和用于衡量整体不均衡程度
                candidate = (
                    max(prior[0], shard_weight),
                    prior[1] + shard_weight * shard_weight,
                )
                # 比较代价：优先最小化最大权重，其次最小化平方和
                if best_cost is None or candidate < best_cost:
                    best_cost = candidate
                    best_start = start_page
            if best_cost is not None and best_start is not None:
                costs[shard_count][end_page] = best_cost
                previous[shard_count][end_page] = best_start

    # 如果动态规划失败（理论上不应发生），降级为固定分片
    if pages not in costs[target_shard_count]:
        return _fixed_page_ranges(pages, max_pages)

    # 回溯重建分片边界
    ranges: list[tuple[int, int, int]] = []
    end_page = pages
    for shard_count in range(target_shard_count, 0, -1):
        start_page = previous[shard_count][end_page]
        weight = prefix_weights[end_page] - prefix_weights[start_page]
        ranges.append((start_page, end_page, weight))
        end_page = start_page
    ranges.reverse()
    return ranges


def _fixed_page_ranges(page_count: int, pages_per_shard: int) -> list[tuple[int, int, int]]:
    """
    生成固定页数的分片边界（降级方案）。

    当动态规划失败或缺少页面配置时，使用简单的固定分片策略。
    每个分片的权重简单地等于页数（忽略实际内容复杂度）。

    Args:
        page_count: 总页数
        pages_per_shard: 每个分片的页数

    Returns:
        分片边界列表 [(start, end, weight), ...]
    """
    pages = max(0, int(page_count))
    max_pages = max(1, int(pages_per_shard))
    return [
        (start_page, min(start_page + max_pages, pages), min(start_page + max_pages, pages) - start_page)
        for start_page in range(0, pages, max_pages)
    ]


def offset_shard_blocks(
    blocks: list[ContentBlock],
    shard: PdfShard,
    source_name: str,
) -> list[ShardBlockRecord]:
    """
    调整分片解析结果的页码偏移，并标记来源信息。

    分片解析后，ContentBlock 的 page_idx 是相对于分片的（从 0 开始）。
    需要加上分片的起始页码，转换为原始文档的全局页码。

    同时更新 source_file 字段，便于追溯内容来源。

    Args:
        blocks: 分片解析后的 ContentBlock 列表
        shard: 分片元信息（包含起始页码）
        source_name: 原始 PDF 文件名

    Returns:
        ShardBlockRecord 列表，包含全局页码和调整后的 ContentBlock

    Example:
        >>> shard = PdfShard(index=1, start_page=10, end_page=20, path=...)
        >>> blocks = parse_shard(shard.path)  # 假设返回第 0-9 页的内容
        >>> records = offset_shard_blocks(blocks, shard, "original.pdf")
        >>> # records 中的页码已调整为 10-19
    """
    records: list[ShardBlockRecord] = []
    for order, block in enumerate(blocks):
        # 将相对页码转换为全局页码
        global_page_idx = int(block.page_idx) + shard.start_page
        # 复制并更新字段
        adjusted = block.model_copy(
            update={
                "page_idx": global_page_idx,
                "source_file": source_name,
            }
        )
        records.append((global_page_idx, shard.index, order, adjusted))
    return records


def merge_shard_records(records: list[ShardBlockRecord]) -> list[ContentBlock]:
    """
    合并多个分片的解析结果，按页码和顺序重排。

    并发解析多个分片后，需要将结果合并为统一列表。
    合并时按 (全局页码, 分片序号, 块顺序) 排序，确保内容顺序正确。

    Args:
        records: 所有分片的 ShardBlockRecord 列表

    Returns:
        排序后的 ContentBlock 列表

    Example:
        >>> all_records = []
        >>> for shard in shards:
        ...     blocks = parse_shard(shard.path)
        ...     records = offset_shard_blocks(blocks, shard, "doc.pdf")
        ...     all_records.extend(records)
        >>> final_blocks = merge_shard_records(all_records)
    """
    records.sort(key=lambda item: (item[0], item[1], item[2]))
    return [record[3] for record in records]
