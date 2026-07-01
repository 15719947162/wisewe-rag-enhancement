# 01 Design Tokens — 设计令牌

---

## 一、色彩系统

### 品牌色

| 令牌 | Hex | 用途 |
|------|-----|------|
| `--color-brand` | `#0F3D5E` | 主色：导航激活、主按钮、关键锚点 |
| `--color-brand-hover` | `#14557F` | 主色悬停态 |
| `--color-brand-secondary` | `#5B6C7D` | 次级界面强调 |
| `--color-accent` | `#0F9F8C` | 强调：成功、健康、关联状态、最佳结果 |

### 背景层级

| 令牌 | Hex | 用途 |
|------|-----|------|
| `--color-bg-canvas` | `#F5F7FA` | 应用底层背景 |
| `--color-bg-panel` | `#FFFFFF` | 卡片、抽屉、面板 |
| `--color-bg-elevated` | `#FCFDFE` | 吸顶栏、顶栏、浮层 |
| `--color-bg-subtle` | `#F0F4F8` | 斑马纹、悬停背景、次级区域 |
| `--color-bg-active` | `#EBF4FF` | 激活态背景（导航项、选中行） |

### 文字层级

| 令牌 | Hex | 用途 |
|------|-----|------|
| `--color-text-primary` | `#102A43` | 标题、正文 |
| `--color-text-secondary` | `#486581` | 标签、描述 |
| `--color-text-tertiary` | `#7B8794` | 元数据、低强调文字 |
| `--color-text-disabled` | `#B0BEC5` | 禁用态文字 |
| `--color-text-inverse` | `#FFFFFF` | 深色背景上的文字 |

### 边框

| 令牌 | Hex | 用途 |
|------|-----|------|
| `--color-border-subtle` | `#D9E1E8` | 卡片边框、分割线（默认） |
| `--color-border-strong` | `#B7C3CF` | 聚焦区域边界、悬停加深 |
| `--color-border-focus` | `#0F3D5E` | 输入框聚焦态 |

### 状态色

| 令牌 | Hex | 背景色 | 用途 |
|------|-----|--------|------|
| `--color-success` | `#1F9D55` | `#F0FDF4` | 阶段完成、状态有效 |
| `--color-warning` | `#D97706` | `#FFFBEB` | 部分问题、降级状态 |
| `--color-danger` | `#C2410C` | `#FEF2F2` | 失败、阻塞问题 |
| `--color-info` | `#2563EB` | `#EBF4FF` | 信息提示 |
| `--color-pending` | `#94A3B8` | `#F1F5F9` | 排队中、未开始 |
| `--color-running` | `#0F9F8C` | `#EBF8FF` | 正在处理 |
| `--color-degraded` | `#B45309` | `#FFFBEB` | 已完成但存在降级 |

### 流水线阶段专属色

| 阶段 | Hex | 用途 |
|------|-----|------|
| Parse（解析） | `#2563EB` | PDF 解析、MinerU 处理 |
| Clean（清洗） | `#0F9F8C` | 清洗与规范化 |
| Chunk（切片） | `#7C3AED` | 分块策略与层级结构 |
| Quality Gate（质量门控） | `#D97706` | 质量过滤与评分 |
| Embedding（向量化） | `#14557F` | 向量化 |
| Export / Index（落库） | `#1F9D55` | CSV 导出与 pgvector 写入 |

### 召回通道专属色

| 通道 | Hex | 用途 |
|------|-----|------|
| Dense Recall（稠密召回） | `#2563EB` | pgvector 向量召回 |
| Sparse Recall（稀疏召回） | `#0F9F8C` | BM25 关键词召回 |
| Structured Recall（结构化召回） | `#7C3AED` | 结构化过滤召回 |
| RRF Merge（融合） | `#14557F` | 合并候选集 |
| Related Expansion（关联扩展） | `#D97706` | 相关 chunk 扩展 |

---

## 二、字体系统

### 字体栈

```css
--font-ui:   'Public Sans', 'Inter', system-ui, sans-serif;
--font-mono: 'IBM Plex Mono', 'Consolas', monospace;
```

### 使用规则

- `--font-ui`：导航、表单、卡片、表格、筛选器、所有常规界面文字
- `--font-mono`：分数、模型名、ID、页码、chunk 元数据、日志、代码

### 字号层级

| 令牌 | 尺寸 / 行高 | 字重 | 用途 |
|------|-------------|------|------|
| `--text-display` | `40px / 48px` | 700 | 看板级核心指标数字 |
| `--text-h1` | `30px / 38px` | 700 | 页面标题 |
| `--text-h2` | `24px / 32px` | 600 | 主分区标题 |
| `--text-h3` | `20px / 28px` | 600 | 面板标题 |
| `--text-title` | `16px / 24px` | 600 | 卡片标题 |
| `--text-body` | `14px / 22px` | 400 | 正文 |
| `--text-label` | `13px / 20px` | 500 | 标签、导航项 |
| `--text-meta` | `12px / 18px` | 400 | 元数据、低强调文字 |
| `--text-caption` | `11px / 16px` | 500 | 大写字母标签、分组标题 |
| `--text-code` | `12px / 18px` | 400 | 等宽文本、ID、分数 |

---

## 三、间距系统

| 令牌 | 值 | 用途 |
|------|----|------|
| `--space-1` | `4px` | 极小间距、图标内边距 |
| `--space-2` | `8px` | 图标与标签间距、紧凑行内间距 |
| `--space-3` | `12px` | 紧凑卡片内边距、导航项内边距 |
| `--space-4` | `16px` | 标准内边距、卡片内边距 |
| `--space-5` | `24px` | 面板间距、页面内分区间距 |
| `--space-6` | `32px` | 分区间距 |
| `--space-7` | `48px` | 大模块分隔 |
| `--space-8` | `64px` | 概览级留白 |

---

## 四、圆角系统

| 令牌 | 值 | 用途 |
|------|----|------|
| `--radius-xs` | `4px` | 徽标、小标签 |
| `--radius-sm` | `8px` | 输入框、按钮、小卡片 |
| `--radius-md` | `12px` | 卡片、面板（主要使用） |
| `--radius-lg` | `16px` | 抽屉、弹层、大卡片 |
| `--radius-pill` | `999px` | 状态徽标、胶囊标签 |

---

## 五、阴影系统

| 令牌 | 值 | 用途 |
|------|----|------|
| `--shadow-none` | `none` | 扁平卡片（Linear 风格默认） |
| `--shadow-sm` | `0 1px 2px rgba(16,42,67,0.06)` | 轻微抬升 |
| `--shadow-md` | `0 4px 12px rgba(16,42,67,0.08)` | 悬停卡片 |
| `--shadow-lg` | `0 12px 28px rgba(16,42,67,0.12)` | 抽屉、浮层、弹窗 |

> **Linear 风格原则**：默认卡片使用边框而非阴影，悬停时才出现轻微阴影。

---

## 六、动效系统

### 时长

| 令牌 | 值 | 用途 |
|------|----|------|
| `--duration-fast` | `150ms` | 悬停反馈、颜色变化 |
| `--duration-normal` | `200ms` | 标签页切换、筛选、抽屉 |
| `--duration-slow` | `250ms` | 阶段进度更新、骨架屏过渡 |

### 缓动

```css
--ease-out: cubic-bezier(0, 0, 0.2, 1);   /* 进入动画 */
--ease-in:  cubic-bezier(0.4, 0, 1, 1);   /* 退出动画 */
```

### 允许使用的动效

- 抽屉滑入（translateX + opacity）
- 进度状态切换（颜色过渡）
- 筛选结果淡入（opacity）
- 骨架屏过渡到内容（opacity）
- 状态点脉冲（进行中任务）

### 禁止使用

- 弹跳式弹簧动效
- 大范围模糊形变
- 自动播放媒体
- 装饰性视差
- 超过 300ms 的 UI 过渡

---

## 七、图标系统

- 图标库：**Lucide**（统一，不混用其他图标库）
- 标准尺寸：`16px`、`18px`、`20px`、`24px`
- 纯图标按钮必须有 `aria-label`
- 禁止用 emoji 充当图标
- 图标颜色继承文字颜色，不单独设置
