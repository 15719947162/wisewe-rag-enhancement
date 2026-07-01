"use client";

import { useEffect, useState } from "react";

type ToastVariant = "success" | "warning" | "danger" | "info";

type ToastItem = {
  id: string;
  variant: ToastVariant;
  message: string;
};

const variantStyles: Record<ToastVariant, { bar: string; icon: React.ReactNode }> = {
  success: {
    bar: "bg-status-success",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#1F9D55" strokeWidth="2">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" /><polyline points="22 4 12 14.01 9 11.01" />
      </svg>
    ),
  },
  warning: {
    bar: "bg-status-warning",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#D97706" strokeWidth="2">
        <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" /><line x1="12" x2="12" y1="9" y2="13" /><line x1="12" x2="12.01" y1="17" y2="17" />
      </svg>
    ),
  },
  danger: {
    bar: "bg-status-danger",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#C2410C" strokeWidth="2">
        <circle cx="12" cy="12" r="10" /><line x1="15" x2="9" y1="9" y2="15" /><line x1="9" x2="15" y1="9" y2="15" />
      </svg>
    ),
  },
  info: {
    bar: "bg-status-info",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#2563EB" strokeWidth="2">
        <circle cx="12" cy="12" r="10" /><line x1="12" x2="12" y1="8" y2="12" /><line x1="12" x2="12.01" y1="16" y2="16" />
      </svg>
    ),
  },
};

let toastListeners: Array<(item: ToastItem) => void> = [];

export function toast(variant: ToastVariant, message: string) {
  const item: ToastItem = { id: Math.random().toString(36).slice(2), variant, message };
  toastListeners.forEach((fn) => fn(item));
}

export function ToastContainer() {
  const [items, setItems] = useState<ToastItem[]>([]);

  useEffect(() => {
    const handler = (item: ToastItem) => {
      setItems((prev) => [...prev, item]);
      const ttl = item.variant === "danger" || item.variant === "warning" ? 6000 : 4000;
      setTimeout(() => setItems((prev) => prev.filter((i) => i.id !== item.id)), ttl);
    };
    toastListeners.push(handler);
    return () => { toastListeners = toastListeners.filter((fn) => fn !== handler); };
  }, []);

  return (
    <div className="fixed bottom-6 right-6 z-[100] flex flex-col gap-2" aria-live="polite">
      {items.map((item) => {
        const { bar, icon } = variantStyles[item.variant];
        return (
          <div
            key={item.id}
            className="flex w-80 items-start gap-3 overflow-hidden rounded-md bg-panel shadow-drawer animate-rise"
          >
            <div className={`w-1 self-stretch shrink-0 ${bar}`} />
            <div className="flex items-start gap-2 py-3 pr-4">
              <span className="mt-0.5 shrink-0">{icon}</span>
              <p className="text-sm text-ink-primary">{item.message}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
