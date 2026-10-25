// ステータスバー中央：実行中ジョブの段階と %（クリックでジョブパネル）、キュー数

import { currentStage, isTerminal, overallProgress } from "@/domain/job";
import { ja } from "@/presentation/shared/i18n/ja";
import { useServerQueue } from "@/presentation/shared/queries";
import { useAppStore } from "@/presentation/shared/stores/appStore";
import { useJobsStore } from "@/presentation/shared/stores/jobsStore";

export function JobStatusSummary() {
  const toggle = useAppStore((s) => s.toggleJobDrawer);
  const jobsMap = useJobsStore((s) => s.jobs);
  const active = Object.values(jobsMap)
    .filter((j) => !isTerminal(j.status))
    .sort((a, b) =>
      a.status === "running" ? -1 : b.status === "running" ? 1 : a.createdAt.localeCompare(b.createdAt),
    );
  const top = active[0];
  const queue = useServerQueue();
  const queueLen = queue.data?.filter((q) => q.status === "queued").length ?? 0;

  let label: string = ja.status.noJobs;
  if (top) {
    const stage = currentStage(top);
    const eta = stage?.status === "running" && stage.etaSec !== undefined ? ` ${ja.time.eta(stage.etaSec)}` : "";
    label =
      top.status === "running" && stage
        ? `${ja.status.running(ja.jobType[top.type], ja.stage[stage.name], overallProgress(top))}${eta}`
        : ja.status.waiting(ja.jobType[top.type]);
  }

  return (
    <>
      <button
        type="button"
        onClick={toggle}
        title={ja.status.openJobs}
        className="min-w-0 truncate px-2 hover:bg-raised"
      >
        {label}
        {active.length > 1 && ` +${active.length - 1}`}
      </button>
      <span className="text-faint">│</span>
      <button type="button" onClick={toggle} className="px-2 hover:bg-raised">
        {ja.status.queue(queueLen)}
      </button>
    </>
  );
}
