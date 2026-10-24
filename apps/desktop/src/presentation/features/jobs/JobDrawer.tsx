// ジョブパネル（S5）：右からのドロワー。サーバーのキュー全体（自分のプロジェクトは強調）、各ジョブの段階・%・経過時間、
// [取り消し]（確認あり）、失敗時は [再試行] [ログ]

import { RotateCcw, ScrollText, Square } from "lucide-react";
import { useState } from "react";
import type { Job } from "@/domain/job";
import { currentStage, isTerminal, overallProgress } from "@/domain/job";
import { jobErrorText } from "@/presentation/shared/errors";
import { formatPercent } from "@/presentation/shared/format";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServerQueue } from "@/presentation/shared/queries";
import { useServices } from "@/presentation/shared/services";
import { useAppStore } from "@/presentation/shared/stores/appStore";
import { useJobsStore } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import {
  Badge,
  Button,
  ConfirmDialog,
  cx,
  Drawer,
  EmptyState,
  ErrorNotice,
  ProgressBar,
} from "@/presentation/shared/ui";
import { JobStageList } from "./JobStageList";
import { LogDialog } from "./LogDialog";

const T = ja.jobsPanel;

function useElapsed(job: Job) {
  const { platform } = useServices();
  const start = job.startedAt ? Date.parse(job.startedAt) : NaN;
  const end = job.finishedAt ? Date.parse(job.finishedAt) : platform.clock.now().getTime();
  return Number.isFinite(start) ? Math.max(0, (end - start) / 1000) : null;
}

function JobCard({ job }: { job: Job }) {
  const cancel = useJobsStore((s) => s.cancel);
  const retry = useJobsStore((s) => s.retry);
  const polling = useJobsStore((s) => !!s.polling[job.jobId]);
  const [confirm, setConfirm] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const elapsed = useElapsed(job);
  const stage = currentStage(job);
  const err = job.status === "failed" ? jobErrorText(job.error) : null;
  const tone =
    job.status === "succeeded"
      ? "success"
      : job.status === "failed"
        ? "danger"
        : job.status === "running"
          ? "accent"
          : "muted";

  return (
    <div className="rounded border border-border bg-panel-2 p-3">
      <div className="flex items-center gap-2">
        <span className="font-medium">{ja.jobType[job.type]}</span>
        <Badge tone={tone}>{ja.jobStatus[job.status]}</Badge>
        {polling && !isTerminal(job.status) && <span className="text-xs text-faint">{ja.status.polling}</span>}
        <span className="ml-auto text-xs text-muted">
          {elapsed !== null && T.elapsed(elapsed)} {!isTerminal(job.status) && formatPercent(overallProgress(job))}
        </span>
      </div>
      {!isTerminal(job.status) && <ProgressBar value={overallProgress(job)} className="mt-2" />}
      <div className="mt-2">
        <JobStageList job={job} />
      </div>
      {err && (
        <div className="mt-2">
          <ErrorNotice title={err.title} body={stage ? `${ja.stage[stage.name]}：${err.body}` : err.body} />
        </div>
      )}
      <div className="mt-2 flex justify-end gap-2">
        {!isTerminal(job.status) && (
          <Button size="sm" icon={<Square size={12} />} onClick={() => setConfirm(true)}>
            {T.cancel}
          </Button>
        )}
        {(job.status === "failed" || job.status === "canceled") && (
          <Button size="sm" icon={<RotateCcw size={12} />} onClick={() => void retry(job.jobId)}>
            {T.retry}
          </Button>
        )}
        <Button size="sm" variant="ghost" icon={<ScrollText size={12} />} onClick={() => setLogOpen(true)}>
          {T.logs}
        </Button>
      </div>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title={T.cancelTitle}
        body={T.cancelBody}
        confirmLabel={T.cancel}
        danger
        onConfirm={() => void cancel(job.jobId)}
      />
      <LogDialog open={logOpen} onOpenChange={setLogOpen} job={job} />
    </div>
  );
}

function QueueSection() {
  const open = useAppStore((s) => s.jobDrawerOpen);
  const q = useServerQueue(open);
  const myServerProject = useProjectStore((s) => s.current?.project.compute.serverProjectId);
  if (q.isError) return <div className="px-3 text-sm text-muted">{T.queueUnavailable}</div>;
  const items = q.data ?? [];
  if (!items.length) return <div className="px-3 text-sm text-faint">{T.empty}</div>;
  return (
    <ol className="space-y-1 px-3">
      {items.map((it) => {
        const mine = it.projectId === myServerProject;
        return (
          <li
            key={it.jobId}
            className={cx("flex items-center gap-2 rounded px-2 py-1.5 text-sm", mine ? "bg-accent/15" : "bg-panel-2")}
          >
            <span className="w-6 text-right font-mono text-muted">{it.position + 1}</span>
            <span>{ja.jobType[it.type]}</span>
            <Badge tone={it.status === "running" ? "accent" : "muted"}>{ja.jobStatus[it.status]}</Badge>
            <span className="ml-auto truncate text-xs text-faint">{mine ? T.mine : T.otherProject}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function JobDrawer() {
  const open = useAppStore((s) => s.jobDrawerOpen);
  const setOpen = useAppStore((s) => s.setJobDrawerOpen);
  const jobsMap = useJobsStore((s) => s.jobs);
  const jobs = Object.values(jobsMap).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return (
    <Drawer open={open} onOpenChange={setOpen} title={T.title}>
      <section className="py-3">
        <h3 className="px-3 pb-2 text-sm font-semibold text-muted">{T.mine}</h3>
        {jobs.length === 0 ? (
          <EmptyState title={T.empty} />
        ) : (
          <div className="space-y-2 px-3">
            {jobs.map((j) => (
              <JobCard key={j.jobId} job={j} />
            ))}
          </div>
        )}
      </section>
      <section className="border-t border-border py-3">
        <h3 className="px-3 pb-2 text-sm font-semibold text-muted">{T.queue}</h3>
        <QueueSection />
      </section>
    </Drawer>
  );
}
