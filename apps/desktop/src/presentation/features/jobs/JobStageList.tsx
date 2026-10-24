// ステージ行の一覧（S2-2 / S2-4 / ジョブパネルで共通）。① 準備 ✓ 4 秒 / ② 人物の追跡 ██░░ 62% 残り約 40 秒 …

import { Check, Circle, X } from "lucide-react";
import type { Job, StageName, StageState } from "@/domain/job";
import { orderedStages } from "@/domain/job";
import { ja } from "@/presentation/shared/i18n/ja";
import { cx, ProgressBar, Spinner } from "@/presentation/shared/ui";

const CIRCLED = ["①", "②", "③", "④", "⑤", "⑥"];

function StageRow({ index, stage, dim }: { index: number; stage: StageState; dim?: boolean }) {
  const label = ja.stage[stage.name];
  const icon =
    stage.status === "done" || stage.status === "skipped" ? (
      <Check size={14} className="text-success" />
    ) : stage.status === "failed" ? (
      <X size={14} className="text-danger" />
    ) : stage.status === "running" ? (
      <Spinner size={14} className="text-accent" />
    ) : (
      <Circle size={10} className="text-faint" />
    );
  return (
    <li className={cx("grid grid-cols-[1.25rem_1.25rem_7.5rem_1fr_9rem] items-center gap-2 py-1", dim && "opacity-60")}>
      <span className="text-muted">{CIRCLED[index] ?? index + 1}</span>
      <span className="flex justify-center">{icon}</span>
      <span className={stage.status === "pending" ? "text-muted" : "text-fg"}>{label}</span>
      <span className="min-w-0">
        {stage.status === "running" && <ProgressBar value={stage.progress} />}
        {stage.status === "failed" && <ProgressBar value={stage.progress} tone="danger" />}
      </span>
      <span className="truncate text-right text-sm text-muted">
        {stage.status === "running" && (
          <>
            {Math.round(stage.progress * 100)}%{" "}
            {stage.etaSec !== undefined ? ja.time.eta(stage.etaSec) : ja.common.calculating}
          </>
        )}
        {stage.status === "done" && stage.durationSec !== undefined && ja.time.took(stage.durationSec)}
        {stage.status === "pending" && ja.stageStatus.pending}
        {stage.status === "failed" && ja.stageStatus.failed}
      </span>
    </li>
  );
}

export function JobStageList({ job, only, startIndex = 0 }: { job: Job; only?: StageName[]; startIndex?: number }) {
  const stages = orderedStages(job).filter((s) => !only || only.includes(s.name));
  return (
    <div>
      {job.status === "queued" && (
        <div className="mb-1 text-sm text-muted">
          {job.dependsOn
            ? job.type === "export"
              ? ja.job.waitingRender
              : ja.job.waitingDependency
            : ja.job.queuePosition(job.queuePosition ?? 0)}
        </div>
      )}
      <ol>
        {stages.map((s, i) => (
          <StageRow key={s.name} index={startIndex + i} stage={s} dim={job.status === "canceled"} />
        ))}
      </ol>
    </div>
  );
}
