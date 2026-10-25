// ドロップした動画をローカルサーバーへ取り込んでいる間の進捗（右下、トーストの上）

import { Loader2, X } from "lucide-react";
import { formatBytes } from "@/presentation/shared/format";
import { ja } from "@/presentation/shared/i18n/ja";
import { useDropUploadStore } from "@/presentation/shared/stores/dropUploadStore";
import { Button, cx, IconButton } from "@/presentation/shared/ui";

export function DropUploadHost() {
  const items = useDropUploadStore((s) => s.items);
  const dismiss = useDropUploadStore((s) => s.dismiss);
  if (items.length === 0) return null;
  return (
    <div
      className="pointer-events-none fixed bottom-[calc(var(--statusbar-height)+12px)] left-3 z-50 flex w-[380px] max-w-[calc(100vw-24px)] flex-col gap-2"
      aria-live="polite"
    >
      {items.map((it) => {
        const ratio = it.total > 0 ? Math.min(1, it.sent / it.total) : 1;
        const t = ja.dropUpload;
        return (
          <div
            key={it.id}
            className={cx(
              "pointer-events-auto rounded-lg border bg-panel-2 p-3 shadow-xl",
              it.state === "error" ? "border-danger/50" : "border-border",
            )}
          >
            <div className="flex items-center gap-2">
              {it.state === "uploading" && <Loader2 size={14} className="shrink-0 animate-spin text-muted" />}
              <div className="min-w-0 flex-1 truncate font-medium">
                {it.state === "uploading"
                  ? t.uploading(it.name)
                  : it.state === "done"
                    ? t.done(it.name)
                    : it.state === "canceled"
                      ? t.canceled(it.name)
                      : t.failed(it.name)}
              </div>
              {it.state === "uploading" && it.cancel ? (
                <Button size="sm" onClick={it.cancel}>
                  {t.cancel}
                </Button>
              ) : (
                <IconButton label={ja.common.close} icon={<X size={14} />} onClick={() => dismiss(it.id)} />
              )}
            </div>
            {it.state === "uploading" && (
              <>
                <div className="mt-2 h-1.5 overflow-hidden rounded bg-border">
                  <div className="h-full bg-accent" style={{ width: `${Math.round(ratio * 100)}%` }} />
                </div>
                <div className="mt-1 text-xs text-muted">
                  {formatBytes(it.sent)} / {formatBytes(it.total)}
                </div>
              </>
            )}
            {it.state === "error" && it.error && <div className="mt-1 text-sm text-muted">{it.error}</div>}
          </div>
        );
      })}
    </div>
  );
}
