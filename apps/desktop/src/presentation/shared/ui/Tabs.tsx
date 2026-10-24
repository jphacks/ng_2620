// タブ（設定画面など）

import * as TabsPrimitive from "@radix-ui/react-tabs";
import type { ReactNode } from "react";
import { cx } from "./controls";

export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  children,
  className,
}: {
  value: T;
  onChange(v: T): void;
  tabs: { value: T; label: ReactNode }[];
  children: ReactNode;
  className?: string;
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={(v) => onChange(v as T)} className={cx("flex min-h-0 flex-col", className)}>
      <TabsPrimitive.List className="flex shrink-0 gap-1 border-b border-border px-4">
        {tabs.map((t) => (
          <TabsPrimitive.Trigger
            key={t.value}
            value={t.value}
            className="-mb-px border-b-2 border-transparent px-3 py-2 text-base text-muted hover:text-fg data-[state=active]:border-accent data-[state=active]:text-fg"
          >
            {t.label}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
      {children}
    </TabsPrimitive.Root>
  );
}

export function TabPanel({ value, children, className }: { value: string; children: ReactNode; className?: string }) {
  return (
    <TabsPrimitive.Content value={value} className={cx("min-h-0 flex-1 overflow-auto outline-none", className)}>
      {children}
    </TabsPrimitive.Content>
  );
}
