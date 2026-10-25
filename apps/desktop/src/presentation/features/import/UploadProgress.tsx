// アップロード進捗（% と速度）。S2-2 の先頭に出す

import { Check } from "lucide-react";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, ErrorNotice, ProgressBar } from "@/presentation/shared/ui";
import { useImportStore } from "./importStore";

export function UploadProgress({ onOpenConnection }: { onOpenConnection(): void }) {
  const project = useProjectStore((s) => s.current?.project);
  const run = useImportStore((s) => (project ? s.runs[project.id] : undefined));
  const start = useImportStore((s) => s.start);
  if (!project) return null;
  const uploaded = !!project.compute.mediaId;

  if (run?.status === "error" && run.error) {
    const t = errorText(run.error);
    return (
      <ErrorNotice title={t.title} body={t.body}>
        <Button size="sm" onClick={() => void start()}>
          {ja.common.retry}
        </Button>
        {run.error.code === "UNAUTHORIZED" && (
          <Button size="sm" onClick={onOpenConnection}>
            {ja.errorActions.openConnection}
          </Button>
        )}
      </ErrorNotice>
    );
  }

  const p = run?.progress;
  const ratio = uploaded ? 1 : p?.phase === "uploading" ? p.ratio : p?.phase === "finalizing" ? 1 : 0;
  return (
    <div className="grid grid-cols-[7.5rem_1fr_12rem] items-center gap-2">
      <span>{ja.upload.label}</span>
      <ProgressBar
        value={ratio}
        tone={uploaded ? "success" : "accent"}
        indeterminate={!uploaded && p?.phase === "hashing"}
      />
      <span className="flex items-center justify-end gap-1 text-sm text-muted">
        {uploaded ? (
          <>
            <Check size={14} className="text-success" /> {ja.upload.done}
          </>
        ) : p ? (
          <>
            {ja.upload.phase[p.phase]} {Math.round(ratio * 100)}%
            {p.bytesPerSec ? `・${ja.upload.speed(p.bytesPerSec)}` : ""}
          </>
        ) : project.compute.upload ? (
          ja.importStep.resumeTitle
        ) : (
          ja.job.notStarted
        )}
      </span>
    </div>
  );
}
