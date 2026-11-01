import type { ReactNode } from "react";

export function safeExternalUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

export function SourceLink({ url, children }: { url: string | null | undefined; children: ReactNode }) {
  const href = safeExternalUrl(url);
  if (!href) return null;
  return <a href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer">{children}<span aria-hidden="true"> ↗</span></a>;
}

export function sourceTime(value: string | null | undefined): string {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}
