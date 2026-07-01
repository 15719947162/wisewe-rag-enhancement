"use client";

import { useEffect, useMemo, useState } from "react";

export const PAGE_SIZE_OPTIONS = [20, 50, 100] as const;

type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];

type PaginationState<T> = {
  page: number;
  pageSize: number;
  total: number;
  pageCount: number;
  startIndex: number;
  endIndex: number;
  pageItems: T[];
  setPage: (page: number) => void;
  setPageSize: (pageSize: number) => void;
};

export function useClientPagination<T>(items: T[], initialPageSize: PageSize = 20): PaginationState<T> {
  const [page, setPageState] = useState(1);
  const [pageSize, setPageSizeState] = useState<number>(initialPageSize);

  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, pageCount);
  const startIndex = total === 0 ? 0 : (safePage - 1) * pageSize;
  const endIndex = Math.min(startIndex + pageSize, total);

  useEffect(() => {
    setPageState((current) => Math.min(current, pageCount));
  }, [pageCount]);

  const pageItems = useMemo(() => items.slice(startIndex, endIndex), [items, startIndex, endIndex]);

  return {
    page: safePage,
    pageSize,
    total,
    pageCount,
    startIndex,
    endIndex,
    pageItems,
    setPage: (nextPage: number) => {
      setPageState(Math.min(Math.max(nextPage, 1), pageCount));
    },
    setPageSize: (nextPageSize: number) => {
      setPageSizeState(nextPageSize);
      setPageState(1);
    },
  };
}

type TablePaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  pageCount: number;
  startIndex: number;
  endIndex: number;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  itemLabel?: string;
  variant?: "default" | "compact";
};

export function TablePagination({
  page,
  pageSize,
  total,
  pageCount,
  startIndex,
  endIndex,
  onPageChange,
  onPageSizeChange,
  itemLabel = "条",
  variant = "default",
}: TablePaginationProps) {
  if (total === 0) return null;

  const compact = variant === "compact";
  const containerClass = compact
    ? "grid grid-cols-[auto_minmax(44px,1fr)_auto] items-center gap-2 border-t border-border-subtle bg-white/72 px-3 py-2"
    : "flex flex-col gap-2 border-t border-border-subtle bg-white/72 px-4 py-3 sm:flex-row sm:items-center sm:justify-end";
  const buttonClass = compact
    ? "inline-flex h-7 w-7 items-center justify-center rounded-md border border-border-subtle bg-white text-ink-secondary shadow-sm transition-colors hover:border-border-focus hover:text-ink-primary disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-border-subtle disabled:hover:text-ink-secondary"
    : "inline-flex h-8 w-8 items-center justify-center rounded-md border border-border-subtle bg-white text-ink-secondary shadow-sm transition-colors hover:border-border-focus hover:text-ink-primary disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:border-border-subtle disabled:hover:text-ink-secondary";

  return (
    <div className={containerClass}>
      {!compact && (
        <span className="text-xs text-ink-tertiary">
          共 {total} {itemLabel}，当前 {startIndex + 1}-{endIndex}
        </span>
      )}
      <label className={compact ? "flex items-center gap-1 text-xs text-ink-secondary" : "flex items-center justify-end gap-2 text-xs text-ink-secondary"}>
        {!compact && <span>每页</span>}
        <select
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
          aria-label="每页条数"
          className={
            compact
              ? "h-7 w-[58px] rounded-md border border-border-subtle bg-white px-1.5 text-xs font-medium text-ink-primary shadow-sm transition-colors hover:border-border-focus focus:border-border-focus focus:outline-none focus:ring-2 focus:ring-border-focus/20"
              : "h-8 rounded-md border border-border-subtle bg-white px-2 text-xs font-medium text-ink-primary shadow-sm transition-colors hover:border-border-focus focus:border-border-focus focus:outline-none focus:ring-2 focus:ring-border-focus/20"
          }
        >
          {PAGE_SIZE_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
        {!compact && <span>{itemLabel}</span>}
      </label>
      {compact && (
        <span className="text-center font-mono text-xs text-ink-tertiary" title={`第 ${page} / ${pageCount} 页`}>
          {page}/{pageCount}
        </span>
      )}
      <div className={compact ? "flex items-center justify-end gap-1.5" : "flex items-center justify-end gap-2"}>
        <button
          type="button"
          aria-label="上一页"
          title="上一页"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
          className={buttonClass}
        >
          <ChevronLeftIcon />
        </button>
        <span className={compact ? "hidden" : "min-w-[72px] text-center font-mono text-xs text-ink-tertiary"}>
          {page} / {pageCount}
        </span>
        <button
          type="button"
          aria-label="下一页"
          title="下一页"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
          className={buttonClass}
        >
          <ChevronRightIcon />
        </button>
      </div>
    </div>
  );
}

function ChevronLeftIcon() {
  return (
    <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

function ChevronRightIcon() {
  return (
    <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}
