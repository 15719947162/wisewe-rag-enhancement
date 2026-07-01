"use client";

import { useEffect, useRef } from "react";
import { Button } from "./button";

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title: string;
  size?: "sm" | "md" | "lg" | "xl";
  children: React.ReactNode;
  footer?: React.ReactNode;
};

const sizeClasses = {
  sm: "max-w-[480px]",
  md: "max-w-[640px]",
  lg: "max-w-[800px]",
  xl: "max-w-[1040px]",
};

export function Modal({ open, onClose, title, size = "md", children, footer }: ModalProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto p-3 sm:items-center sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="modal-title"
    >
      <div className="absolute inset-0 bg-[rgba(17,24,39,0.42)] backdrop-blur-sm" onClick={onClose} />
      <div
        ref={panelRef}
        className={[
          "relative mt-2 flex max-h-[calc(100dvh-24px)] w-full flex-col overflow-hidden rounded-lg border border-border-subtle bg-white shadow-drawer animate-rise sm:mt-0 sm:max-h-[calc(100dvh-32px)]",
          sizeClasses[size],
        ].join(" ")}
      >
        <div className="flex shrink-0 items-center justify-between border-b border-border-subtle px-6 py-5">
          <h2 id="modal-title" className="text-xl font-semibold text-ink-primary">
            {title}
          </h2>
          <Button
            variant="ghost"
            size="sm"
            iconOnly
            aria-label="关闭"
            onClick={onClose}
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </Button>
        </div>
        <div className="overflow-y-auto p-6 text-sm text-ink-secondary">{children}</div>
        {footer && (
          <div className="flex shrink-0 justify-end gap-2 border-t border-border-subtle px-6 py-4">
            {footer}
          </div>
        )}
      </div>
    </div>
  );
}
