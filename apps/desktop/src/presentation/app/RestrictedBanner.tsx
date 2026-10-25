// 制限モード（ローカルサーバーなし）の案内：データの置き場所と、zip で保存する理由、ローカルアプリの利点（#27、#28）

import { Info, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { IconButton } from "@/presentation/shared/ui";

const KEY = "vvlog.restrictedBanner.dismissed";

function wasDismissed(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function RestrictedBanner() {
  const { env } = useServices();
  const [hidden, setHidden] = useState(wasDismissed);
  const [persisted, setPersisted] = useState<boolean | null>(null);
  useEffect(() => {
    void navigator.storage?.persisted?.().then(setPersisted, () => setPersisted(null));
  }, []);
  if (env.mode !== "restricted" || hidden) return null;
  const T = ja.restricted;
  return (
    <div role="status" className="flex items-start gap-2.5 border-b border-border bg-panel-2 px-4 py-2 text-sm">
      <Info size={16} className="mt-0.5 shrink-0 text-muted" />
      <div className="min-w-0 flex-1">
        <span className="font-medium">{T.title}</span>
        <span className="ml-2 text-muted">
          {T.body}
          {persisted === false && T.notPersisted} {T.install}
        </span>
      </div>
      <IconButton
        label={T.dismiss}
        icon={<X size={14} />}
        onClick={() => {
          setHidden(true);
          try {
            sessionStorage.setItem(KEY, "1");
          } catch {
            // 記憶できなくても、今回だけ閉じればよい
          }
        }}
      />
    </div>
  );
}
