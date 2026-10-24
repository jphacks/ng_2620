// トーストの表示（右下、ステータスバーの上）

import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { ja } from "@/presentation/shared/i18n/ja";
import { useToastStore } from "@/presentation/shared/stores/toastStore";
import { Button, cx, IconButton } from "@/presentation/shared/ui";

export function ToastHost() {
  const toasts = useToastStore((s) => s.toasts);
  const dismiss = useToastStore((s) => s.dismiss);
  return (
    <div className="pointer-events-none fixed bottom-[calc(var(--statusbar-height)+12px)] right-3 z-50 flex w-[380px] max-w-[calc(100vw-24px)] flex-col gap-2" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={cx("pointer-events-auto flex gap-2.5 rounded-lg border bg-panel-2 p-3 shadow-xl", t.kind === "error" ? "border-danger/50" : "border-border")}>
          {t.kind === "success" ? (
            <CheckCircle2 size={16} className="mt-0.5 shrink-0 text-success" />
          ) : t.kind === "error" ? (
            <XCircle size={16} className="mt-0.5 shrink-0 text-danger" />
          ) : (
            <Info size={16} className="mt-0.5 shrink-0 text-muted" />
          )}
          <div className="min-w-0 flex-1">
            <div className="font-medium">{t.title}</div>
            {t.body && <div className="mt-0.5 text-sm text-muted">{t.body}</div>}
            {t.actions && t.actions.length > 0 && (
              <div className="mt-2 flex gap-2">
                {t.actions.map((a) => (
                  <Button
                    key={a.label}
                    size="sm"
                    onClick={() => {
                      a.run();
                      dismiss(t.id);
                    }}
                  >
                    {a.label}
                  </Button>
                ))}
              </div>
            )}
          </div>
          <IconButton label={ja.common.close} icon={<X size={14} />} onClick={() => dismiss(t.id)} />
        </div>
      ))}
    </div>
  );
}
