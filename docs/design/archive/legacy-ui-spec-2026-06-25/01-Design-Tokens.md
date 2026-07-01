# 01 Design Tokens

## 1. 色彩系统

### 1.1 品牌主色

| Token | Hex | 用途 |
|------|-----|------|
| `--color-brand-primary` | `#0F3D5E` | 品牌识别、主按钮、激活导航 |
| `--color-brand-primary-hover` | `#14557F` | 主交互 hover |
| `--color-brand-secondary` | `#5B6C7D` | 辅助信息、二级视觉支撑 |
| `--color-brand-accent` | `#0F9F8C` | 强调可信、健康、验证通过 |

### 1.2 中性色

| Token | Hex | 用途 |
|------|-----|------|
| `--color-bg-canvas` | `#F5F7FA` | 页面大背景 |
| `--color-bg-panel` | `#FFFFFF` | 卡片、面板 |
| `--color-bg-elevated` | `#FCFDFE` | 顶部栏、抽屉、悬浮层 |
| `--color-border-subtle` | `#D9E1E8` | 常规边框 |
| `--color-border-strong` | `#B7C3CF` | 强分组边界 |
| `--color-text-primary` | `#102A43` | 主文本 |
| `--color-text-secondary` | `#486581` | 次级文本 |
| `--color-text-tertiary` | `#7B8794` | 辅助说明与元信息 |

### 1.3 状态色

| Token | Hex | 用途 |
|------|-----|------|
| `--color-success` | `#1F9D55` | 成功 |
| `--color-warning` | `#D97706` | 警告 |
| `--color-danger` | `#C2410C` | 失败 |
| `--color-info` | `#2563EB` | 信息 |
| `--color-pending` | `#94A3B8` | 待执行 |
| `--color-running` | `#0F9F8C` | 运行中 |
| `--color-degraded` | `#B45309` | 降级完成 |

## 2. 链路阶段色

| 阶段 | Token / Hex |
|------|-------------|
| 解析 | `#2563EB` |
| 清洗 | `#0F9F8C` |
| 切片 | `#7C3AED` |
| 质量过滤 | `#D97706` |
| 向量化 | `#14557F` |
| 导出/入库 | `#1F9D55` |
| 召回 | `#2563EB` |
| 重排 | `#EA580C` |
| 生成 | `#0F3D5E` |
| 评分 | `#1F9D55` |

## 3. 检索通道色

| 通道 | Hex |
|------|-----|
| Dense | `#2563EB` |
| Sparse | `#0F9F8C` |
| Structured | `#7C3AED` |
| RRF | `#14557F` |
| Related Expansion | `#D97706` |

## 4. 字体系统

### 4.1 字体角色

- `Heading Serif`: `Newsreader`
- `UI Sans`: `Public Sans`
- `Data Mono`: `IBM Plex Mono`

### 4.2 应用原则

- 页面标题、章节标题、证据阅读标题：衬线标题字体
- 导航、表格、表单、正文：无衬线
- 分数、模型名、chunk id、页码、trace：等宽

## 5. 字号系统

| Token | 值 | 用途 |
|------|----|------|
| `--text-display` | `40/48` | 顶层总览指标 |
| `--text-h1` | `30/38` | 页面标题 |
| `--text-h2` | `24/32` | 一级区块标题 |
| `--text-h3` | `20/28` | 二级区块标题 |
| `--text-title` | `16/24` | 卡片标题 |
| `--text-body` | `14/22` | 正文 |
| `--text-meta` | `12/18` | 元信息 |
| `--text-code` | `12/18` | 等宽数据显示 |

## 6. 间距系统

| Token | 值 |
|------|----|
| `--space-1` | `4px` |
| `--space-2` | `8px` |
| `--space-3` | `12px` |
| `--space-4` | `16px` |
| `--space-5` | `24px` |
| `--space-6` | `32px` |
| `--space-7` | `48px` |
| `--space-8` | `64px` |

## 7. 圆角与阴影

### 圆角

| Token | 值 |
|------|----|
| `--radius-sm` | `8px` |
| `--radius-md` | `12px` |
| `--radius-lg` | `16px` |
| `--radius-pill` | `999px` |

### 阴影

| Token | 值 |
|------|----|
| `--shadow-sm` | `0 1px 2px rgba(16,42,67,0.06)` |
| `--shadow-md` | `0 8px 20px rgba(16,42,67,0.08)` |
| `--shadow-lg` | `0 18px 36px rgba(16,42,67,0.12)` |

## 8. 线框与边框

- 默认边框：`1px solid var(--color-border-subtle)`
- 强边框：`1px solid var(--color-border-strong)`
- 焦点边框：建议 `2px` 外环，不挤压布局

## 9. 动效 Token

| Token | 值 |
|------|----|
| `--motion-fast` | `150ms` |
| `--motion-base` | `200ms` |
| `--motion-slow` | `250ms` |

## 10. 数据密度 Token

| 模式 | 行高 / 内边距 |
|------|---------------|
| Comfortable | `48-56px` |
| Standard | `40-48px` |
| Dense | `32-40px` |
