// 設定 > 一般（S6、F15）：キャッシュの場所と [キャッシュを消す]、言語（日本語のみ）、自己診断（WebGL2・SSE）

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { useJobsStore } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import { Badge, Button, ConfirmDialog, Label, Select, Spinner } from "@/presentation/shared/ui";

const T = ja.general;

export function GeneralSettings() {
  const { uc, paths, env, diagnose } = useServices();
  const project = useProjectStore((s) => s.current?.project);
  const [confirm, setConfirm] = useState(false);
  const diag = useQuery({ queryKey: ["diagnostics"], queryFn: diagnose, staleTime: Infinity });
  const cacheDir = project ? paths.resolve(project.id, "cache") : null;

  const clear = async () => {
    if (!project) return;
    try {
      await uc.clearCache(project);
      // 取得済みの印も消えたので、表示中の成果物の状態を作り直す
      useJobsStore.setState({ artifacts: {} });
      toast({ kind: "success", title: T.cacheCleared });
    } catch (e) {
      const t = errorText(e);
      toast({ kind: "error", title: t.title, body: t.body });
    }
  };

  const yesNo = (v: boolean | null | undefined) =>
    v === null || v === undefined ? (
      <Badge>{T.notChecked}</Badge>
    ) : v ? (
      <Badge tone="success">{T.yes}</Badge>
    ) : (
      <Badge tone="danger">{T.no}</Badge>
    );

  return (
    <div className="max-w-2xl space-y-8 p-6">
      <section>
        <h3 className="mb-3 font-semibold">{T.cache}</h3>
        <div className="grid grid-cols-[8rem_1fr] items-center gap-3">
          <Label>{T.cacheLocation}</Label>
          <span className="truncate font-mono text-sm text-muted">{cacheDir ?? T.noProject}</span>
        </div>
        <div className="mt-2 text-sm text-faint">{T.clearCacheBody}</div>
        <Button className="mt-3" disabled={!project || !!project.sample} onClick={() => setConfirm(true)}>
          {T.clearCache}
        </Button>
      </section>

      <section>
        <h3 className="mb-3 font-semibold">{T.language}</h3>
        <Select value="ja" onChange={() => {}} options={[{ value: "ja", label: T.japanese }]} aria-label={T.language} />
      </section>

      <section>
        <h3 className="mb-3 font-semibold">{T.diagnostics}</h3>
        {diag.isLoading ? (
          <Spinner />
        ) : (
          <dl className="grid grid-cols-[12rem_1fr] gap-y-2 text-sm">
            <dt className="text-muted">{T.runtime}</dt>
            <dd>{env.isTauri ? T.runtimeTauri : T.runtimeBrowser}</dd>
            <dt className="text-muted">{T.backendMode}</dt>
            <dd className="font-mono">{env.backendMode}</dd>
            <dt className="text-muted">{T.webgl2}</dt>
            <dd>{yesNo(diag.data?.webgl2)}</dd>
            <dt className="text-muted">{T.sse}</dt>
            <dd>{yesNo(diag.data?.stream)}</dd>
            <dt className="text-muted">{T.version}</dt>
            <dd className="font-mono">{env.version}</dd>
          </dl>
        )}
      </section>

      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={T.clearCache}
        body={T.clearCacheBody}
        confirmLabel={T.clearCache}
        danger
        onConfirm={() => void clear()}
      />
    </div>
  );
}
