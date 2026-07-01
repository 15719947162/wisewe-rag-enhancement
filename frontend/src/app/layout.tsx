import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "WiseWe RAG 控制台",
  description: "用于解析、切块、检索与证据追踪的中文运维控制台。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
