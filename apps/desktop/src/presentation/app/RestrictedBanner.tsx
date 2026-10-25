// 制限モード（ローカルサーバーなし）の案内：データの置き場所と、zip で保存する理由、ローカルアプリの利点（#27、#28）。
// Pages（https）で開いているときは、この PC のローカルアプリを探す／開く案内も出す（#38）。自動では切り替えない

import { ExternalLink, Info, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { LocalAppProbe } from "@/application/ports/system";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { Button, IconButton } from "@/presentation/shared/ui";

const KEY = "vvlog.restrictedBanner.dismissed";

function wasDismissed(): boolean {
  try {
    return sessionStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function RestrictedBanner() {
  const { env, platform } = useServices();
  const [hidden, setHidden] = useState(wasDismissed);
  const [persisted, setPersisted] = useState<boolean | null>(null);
  const [probe, setProbe] = useState<LocalAppProbe | "searching" | null>(null);
  useEffect(() => {
    void navigator.storage?.persisted?.().then(setPersisted, () => setPersisted(null));
  }, []);
  if (env.mode !== "restricted" || hidden) return null;
  const T = ja.restricted;
  const app = platform.localApp;

  const find = async () => {
    if (!app) return;
    setProbe("searching");
    setProbe(await app.probe());
  };

  return (
    <div role="status" className="flex items-start gap-2.5 border-b border-border bg-panel-2 px-4 py-2 text-sm">
      <Info size={16} className="mt-0.5 shrink-0 text-muted" />
      <div className="min-w-0 flex-1">
        <span className="font-medium">{T.title}</span>
        <span className="ml-2 text-muted">
          {T.body}
          {persisted === false && T.notPersisted} {T.install}
        </span>
        {app && (
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            {app.canProbe && probe === null && (
              <Button size="sm" onClick={() => void find()}>
                {T.find}
              </Button>
            )}
            {probe === "searching" && <span className="text-muted">{T.finding}</span>}
            {probe !== null && probe !== "searching" && probe.found && (
              <>
                <span className="text-success">{T.foundBody}</span>
                <a
                  href={probe.url}
                  className="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5 hover:border-accent"
                >
                  <ExternalLink size={12} />
                  {T.open}
                </a>
              </>
            )}
            {probe !== null && probe !== "searching" && !probe.found && (
              <span className="text-muted">{T.notFound}</span>
            )}
            {(!app.canProbe || (probe !== null && probe !== "searching" && !probe.found)) && (
              <span className="text-muted">{T.openByAddress(app.url)}</span>
            )}
          </div>
        )}
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
