// ダイアログ・確認・ドロワー・ツールチップ・コンテキストメニュー

import * as ContextMenuPrimitive from "@radix-ui/react-context-menu";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import * as TooltipPrimitive from "@radix-ui/react-tooltip";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { ja } from "../i18n/ja";
import { Button, cx, IconButton } from "./controls";

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  width = "w-[480px]",
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  width?: string;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-overlay" />
        <DialogPrimitive.Content
          className={cx("fixed left-1/2 top-1/2 z-50 max-h-[85vh] max-w-[calc(100vw-32px)] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-lg border border-border bg-panel p-5 shadow-2xl", width)}
        >
          <div className="flex items-start justify-between gap-4">
            <DialogPrimitive.Title className="text-lg font-semibold text-fg">{title}</DialogPrimitive.Title>
            <DialogPrimitive.Close asChild>
              <IconButton label={ja.common.close} icon={<X size={16} />} />
            </DialogPrimitive.Close>
          </div>
          {description ? (
            <DialogPrimitive.Description className="mt-1 text-sm text-muted">{description}</DialogPrimitive.Description>
          ) : (
            <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
          )}
          {children && <div className="mt-4">{children}</div>}
          {footer && <div className="mt-5 flex justify-end gap-2">{footer}</div>}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  body,
  confirmLabel,
  danger,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange(open: boolean): void;
  title: string;
  body?: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm(): void;
  children?: ReactNode;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={body}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{ja.common.cancel}</Button>
          <Button
            variant={danger ? "danger" : "primary"}
            onClick={() => {
              onConfirm();
              onOpenChange(false);
            }}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}

/** 右から出るドロワー（ジョブパネル S5） */
export function Drawer({ open, onOpenChange, title, children, actions }: { open: boolean; onOpenChange(open: boolean): void; title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-30 bg-black/20" />
        <DialogPrimitive.Content className="fixed bottom-statusbar right-0 top-titlebar z-40 flex w-[420px] max-w-full flex-col border-l border-border bg-panel shadow-2xl">
          <div className="flex h-10 shrink-0 items-center justify-between border-b border-border px-3">
            <DialogPrimitive.Title className="font-semibold">{title}</DialogPrimitive.Title>
            <div className="flex items-center gap-1">
              {actions}
              <DialogPrimitive.Close asChild>
                <IconButton label={ja.common.close} icon={<X size={16} />} />
              </DialogPrimitive.Close>
            </div>
          </div>
          <DialogPrimitive.Description className="sr-only">{title}</DialogPrimitive.Description>
          <div className="min-h-0 flex-1 overflow-auto">{children}</div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export const TooltipProvider = TooltipPrimitive.Provider;

export function Tooltip({ content, children }: { content: ReactNode; children: ReactNode }) {
  return (
    <TooltipPrimitive.Root delayDuration={400}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content sideOffset={4} className="z-50 max-w-xs rounded-sm border border-border bg-panel-2 px-2 py-1 text-xs text-fg shadow-lg">
          {content}
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}

export function ContextMenu({ children, items }: { children: ReactNode; items: { label: string; onSelect(): void; danger?: boolean; disabled?: boolean }[] }) {
  return (
    <ContextMenuPrimitive.Root>
      <ContextMenuPrimitive.Trigger asChild>{children}</ContextMenuPrimitive.Trigger>
      <ContextMenuPrimitive.Portal>
        <ContextMenuPrimitive.Content className="z-50 min-w-[12rem] rounded border border-border bg-panel-2 p-1 shadow-xl">
          {items.map((it) => (
            <ContextMenuPrimitive.Item
              key={it.label}
              disabled={it.disabled}
              onSelect={it.onSelect}
              className={cx(
                "cursor-pointer select-none rounded-sm px-2.5 py-1.5 text-base outline-none data-[disabled]:cursor-not-allowed data-[highlighted]:bg-accent data-[highlighted]:text-accent-fg data-[disabled]:text-faint",
                it.danger ? "text-danger" : "text-fg",
              )}
            >
              {it.label}
            </ContextMenuPrimitive.Item>
          ))}
        </ContextMenuPrimitive.Content>
      </ContextMenuPrimitive.Portal>
    </ContextMenuPrimitive.Root>
  );
}
