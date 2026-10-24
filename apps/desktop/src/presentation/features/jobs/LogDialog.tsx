// ログ表示（GET /v1/jobs/{id}/logs?tail=200）とコピー

import { useQuery } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import type { Job } from "@/domain/job";
import { errorText } from "@/presentation/shared/errors";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServices } from "@/presentation/shared/services";
import { currentBackend } from "@/presentation/shared/stores/connectionStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import { Button, Dialog, ErrorNotice, Spinner } from "@/presentation/shared/ui";

export function LogDialog({ open, onOpenChange, job }: { open: boolean; onOpenChange(o: boolean): void; job: Job }) {
  const { uc } = useServices();
  const q = useQuery({
    queryKey: ["logs", job.jobId, job.status],
    queryFn: () => uc.getLogs.execute({ backend: currentBackend(), jobId: job.jobId, tail: 200 }),
    enabled: open,
  });
  const fallback = typeof job.error?.detail?.logTail === "string" ? job.error.detail.logTail : "";
  const text = q.data ?? fallback;
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={ja.jobsPanel.logTitle(ja.jobType[job.type])}
      width="w-[760px]"
      footer={
        <Button
          icon={<Copy size={14} />}
          disabled={!text}
          onClick={async () => {
            await navigator.clipboard.writeText(`${job.jobId} (${job.type})\n${text}`);
            toast({ kind: "info", title: ja.common.copied });
          }}
        >
          {ja.jobsPanel.copyLogs}
        </Button>
      }
    >
      {q.isLoading && <Spinner />}
      {q.isError && !fallback && <ErrorNotice {...errorText(q.error)} />}
      {text && <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded bg-bg p-3 font-mono text-xs text-muted">{text}</pre>}
    </Dialog>
  );
}
