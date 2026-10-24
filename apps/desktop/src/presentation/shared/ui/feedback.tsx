// 表示部品：進捗バー、バッジ、状態の点、空状態、エラー表示、キー表示

import { AlertTriangle, Info, Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { cx } from "./controls";

export function ProgressBar({
  value,
  indeterminate,
  tone = "accent",
  className,
}: {
  value: number;
  indeterminate?: boolean;
  tone?: "accent" | "success" | "danger" | "muted";
  className?: string;
}) {
  const color = { accent: "bg-accent", success: "bg-success", danger: "bg-danger", muted: "bg-faint" }[tone];
  return (
    <div
      className={cx("relative h-1.5 w-full overflow-hidden rounded-full bg-raised", className)}
      role="progressbar"
      aria-valuenow={Math.round(value * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      {indeterminate ? (
        <div className={cx("absolute inset-y-0 w-1/3 animate-pulse rounded-full", color)} style={{ left: "33%" }} />
      ) : (
        <div
          className={cx("h-full rounded-full transition-[width] duration-300", color)}
          style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }}
        />
      )}
    </div>
  );
}

export function Badge({
  tone = "muted",
  children,
  title,
}: {
  tone?: "muted" | "accent" | "success" | "warning" | "danger";
  children: ReactNode;
  title?: string;
}) {
  const cls = {
    muted: "bg-raised text-muted",
    accent: "bg-accent/20 text-accent",
    success: "bg-success/20 text-success",
    warning: "bg-warning/20 text-warning",
    danger: "bg-danger/20 text-danger",
  }[tone];
  return (
    <span
      title={title}
      className={cx("inline-flex h-5 items-center whitespace-nowrap rounded-sm px-1.5 text-xs font-medium", cls)}
    >
      {children}
    </span>
  );
}

export function StatusDot({ tone }: { tone: "success" | "warning" | "muted" | "danger" }) {
  const cls = { success: "bg-success", warning: "bg-warning", muted: "bg-faint", danger: "bg-danger" }[tone];
  return <span aria-hidden className={cx("inline-block h-2 w-2 shrink-0 rounded-full", cls)} />;
}

export function Spinner({ size = 14, className }: { size?: number; className?: string }) {
  return <Loader2 size={size} className={cx("animate-spin text-muted", className)} aria-hidden />;
}

export function EmptyState({
  icon,
  title,
  body,
  children,
}: {
  icon?: ReactNode;
  title: string;
  body?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-6 text-center">
      {icon && <div className="text-faint">{icon}</div>}
      <div className="text-lg text-fg">{title}</div>
      {body && <div className="max-w-sm text-sm text-muted">{body}</div>}
      {children && <div className="mt-2 flex gap-2">{children}</div>}
    </div>
  );
}

/** 「何が起きたか（見出し）＋なぜ／どうすれば（本文）＋操作」（ux/interaction §5） */
export function ErrorNotice({
  title,
  body,
  children,
  tone = "danger",
}: {
  title: string;
  body?: string;
  children?: ReactNode;
  tone?: "danger" | "warning" | "info";
}) {
  const cls = {
    danger: "border-danger/50 bg-danger/10",
    warning: "border-warning/50 bg-warning/10",
    info: "border-border bg-panel-2",
  }[tone];
  const icon =
    tone === "info" ? (
      <Info size={16} className="mt-0.5 shrink-0 text-muted" />
    ) : (
      <AlertTriangle size={16} className={cx("mt-0.5 shrink-0", tone === "danger" ? "text-danger" : "text-warning")} />
    );
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cx("flex gap-2.5 rounded border p-3", cls)}>
      {icon}
      <div className="min-w-0 flex-1">
        <div className="font-medium text-fg">{title}</div>
        {body && <div className="mt-0.5 text-sm text-muted">{body}</div>}
        {children && <div className="mt-2 flex flex-wrap gap-2">{children}</div>}
      </div>
    </div>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-sm border border-border bg-panel-2 px-1 font-mono text-xs text-muted">{children}</kbd>;
}
