// S2-3 キャスト（F06）：人物一覧から置き換える人物を選び、アバターを割り当てて「アバターを描画」。
// track の完了後に操作でき、mesh / occlusion は裏で進む

import { Pencil, UserRound } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import type { CastDraftRow, Person } from "@/domain/cast";
import { canRender, MAX_TARGETS } from "@/domain/cast";
import { isAppError, toAppError } from "@/domain/errors";
import { isStageDone, overallProgress } from "@/domain/job";
import { renderState } from "@/domain/project";
import { errorText, jobErrorText } from "@/presentation/shared/errors";
import { formatFrameRange } from "@/presentation/shared/format";
import { useDropZone } from "@/presentation/shared/hooks/useDropZone";
import { ja } from "@/presentation/shared/i18n/ja";
import { useAvatars } from "@/presentation/shared/queries";
import { useServices } from "@/presentation/shared/services";
import { useConnectionStore, currentBackend } from "@/presentation/shared/stores/connectionStore";
import { useJobsStore, useProjectJob } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { toast } from "@/presentation/shared/stores/toastStore";
import { Badge, Button, Checkbox, cx, ErrorNotice, Spinner, TextInput } from "@/presentation/shared/ui";
import { useCastDraftStore, useSyncDraft } from "./castStore";

const T = ja.cast;
const isVrm = (p: string) => /\.vrm$/i.test(p);

export interface CastSlots {
  /** アバターの選択部品（F07 の AvatarPicker） */
  renderAvatarPicker(args: { value: string | null; onChange(avatarId: string | null): void }): ReactNode;
  /** 選んだアバターのライセンス概要 */
  renderLicense(avatarId: string): ReactNode;
  /** 追跡マスクのプレビュー（F05 の ProgressivePreview） */
  renderPreview(highlightPersonId: number | null): ReactNode;
  /** .vrm を取り込んで avatarId を返す（行へのドロップ） */
  importVrm(path: string): Promise<string | null>;
}

export function useCastDraft() {
  const project = useProjectStore((s) => s.current?.project);
  const rows = useCastDraftStore((s) => (project ? s.drafts[project.id] : undefined));
  useSyncDraft(project?.id, project?.persons ?? EMPTY, project?.cast ?? EMPTY_CAST);
  return rows ?? EMPTY_ROWS;
}
const EMPTY: Person[] = [];
const EMPTY_CAST: never[] = [];
const EMPTY_ROWS: CastDraftRow[] = [];

export function CastStep({ slots, onBack, onRenderStarted, onPickOtherVideo }: { slots: CastSlots; onBack(): void; onRenderStarted(): void; onPickOtherVideo(): void }) {
  const { uc } = useServices();
  const project = useProjectStore((s) => s.current?.project);
  const update = useProjectStore((s) => s.update);
  const analyze = useProjectJob("analyze");
  const accept = useJobsStore((s) => s.accept);
  const avatars = useAvatars();
  const connected = useConnectionStore((s) => s.state.status === "connected");
  const draft = useCastDraft();
  const setDraft = useCastDraftStore((s) => s.set);
  const [hover, setHover] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (!project) return null;
  const trackDone = isStageDone(analyze, "track") || project.jobs.analyze?.status === "succeeded";
  const noPerson = analyze?.status === "failed" && analyze.error?.code === "NO_PERSON_FOUND";
  const check = canRender(draft, MAX_TARGETS);
  const rstate = renderState(project);

  const change = (personId: number, patch: Partial<CastDraftRow>) => {
    const next = draft.map((r) => (r.personId === personId ? { ...r, ...patch } : r));
    setDraft(project.id, next);
    update(uc.updateCast.execute({ draft: next, avatars: avatars.data ?? [] }));
  };

  const startRender = async () => {
    setSubmitting(true);
    try {
      accept(await uc.startRender.execute({ project, backend: currentBackend(), draft }));
      onRenderStarted();
    } catch (e) {
      const t = errorText(e);
      toast({ kind: "error", title: t.title, body: isAppError(e, "VALIDATION_FAILED") ? undefined : t.body });
    } finally {
      setSubmitting(false);
    }
  };

  if (noPerson) {
    const t = jobErrorText(analyze?.error);
    return (
      <div className="mx-auto max-w-2xl p-8">
        <ErrorNotice title={t.title} body={t.body}>
          <Button onClick={onPickOtherVideo}>{ja.errorActions.pickOtherVideo}</Button>
        </ErrorNotice>
      </div>
    );
  }

  const reason = !check.ok
    ? check.reason === "none_selected"
      ? T.reasons.none_selected
      : check.reason === "missing_avatar"
        ? T.reasons.missing_avatar
        : T.reasons.too_many(check.limit)
    : null;

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(0,1fr)_minmax(280px,38%)] gap-4 p-4">
      <div className="flex min-h-0 flex-col">
        <div className="mb-3">
          <span className="font-medium">{T.title}</span>
          {analyze && analyze.status !== "succeeded" && <span className="ml-1 text-sm text-muted">{T.backgroundProgress(overallProgress(analyze))}</span>}
        </div>
        {!trackDone ? (
          <div className="flex items-center gap-2 text-muted">
            <Spinner /> {T.noPersonsYet}
          </div>
        ) : (
          <ul className="min-h-0 flex-1 space-y-2 overflow-auto pr-1">
            {project.persons.map((p) => {
              const row = draft.find((r) => r.personId === p.personId) ?? { personId: p.personId, selected: false, avatarId: null };
              return (
                <PersonRow
                  key={p.personId}
                  person={p}
                  row={row}
                  fps={project.meta?.fps ?? project.source.fps}
                  slots={slots}
                  onHover={setHover}
                  onChange={(patch) => change(p.personId, patch)}
                  onRename={(label) => update(uc.renamePerson.execute({ personId: p.personId, label, fallback: T.defaultLabel(p.personId) }))}
                />
              );
            })}
          </ul>
        )}
        <div className="mt-3 flex items-center gap-2 border-t border-border pt-3">
          <Button onClick={onBack}>{ja.common.back}</Button>
          <div className="ml-auto flex items-center gap-2">
            {rstate === "stale" && <Badge tone="warning">{T.needsRender}</Badge>}
            {reason && trackDone && <span className="text-sm text-muted">{reason}</span>}
            <Button variant="primary" disabled={!check.ok || submitting || !connected || !trackDone} onClick={() => void startRender()} icon={submitting ? <Spinner /> : undefined}>
              {rstate === "stale" || rstate === "fresh" ? T.rerender : T.render} ▶
            </Button>
          </div>
        </div>
      </div>
      <div className="flex min-h-0 flex-col">
        {slots.renderPreview(hover)}
        <div className="mt-2 text-xs text-faint">{T.playMask}</div>
      </div>
    </div>
  );
}

function PersonRow({
  person,
  row,
  fps,
  slots,
  onHover,
  onChange,
  onRename,
}: {
  person: Person;
  row: CastDraftRow;
  fps: number;
  slots: CastSlots;
  onHover(id: number | null): void;
  onChange(patch: Partial<CastDraftRow>): void;
  onRename(label: string): void;
}) {
  const { platform, paths } = useServices();
  const projectId = useProjectStore((s) => s.current?.project.id);
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(person.label);
  const drop = useDropZone({
    accept: isVrm,
    onDrop: async ([path]) => {
      if (!path) return;
      try {
        const id = await slots.importVrm(path);
        if (id) onChange({ selected: true, avatarId: id });
      } catch (e) {
        const t = errorText(toAppError(e));
        toast({ kind: "error", title: t.title, body: t.body });
      }
    },
  });
  const thumb = person.thumb && projectId ? platform.files.toUrl(paths.resolve(projectId, person.thumb)) : null;

  return (
    <li
      {...drop.props}
      onMouseEnter={() => onHover(person.personId)}
      onMouseLeave={() => onHover(null)}
      className={cx("flex gap-3 rounded border p-2", row.selected ? "border-accent/60 bg-accent/5" : "border-border bg-panel-2")}
    >
      {thumb ? (
        <img src={thumb} alt="" className="h-20 w-20 shrink-0 rounded bg-raised object-cover" draggable={false} />
      ) : (
        <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded bg-raised text-faint">
          <UserRound size={28} />
        </div>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <Checkbox checked={row.selected} onChange={(selected) => onChange({ selected })} label={editing ? undefined : <span className="font-medium">{person.label}</span>} />
          {editing ? (
            <TextInput
              autoFocus
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              onBlur={() => {
                setEditing(false);
                onRename(label);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                if (e.key === "Escape") {
                  setLabel(person.label);
                  setEditing(false);
                }
              }}
              className="!h-7 max-w-[12rem]"
            />
          ) : (
            <button type="button" title={T.rename} onClick={() => setEditing(true)} className="text-faint hover:text-fg">
              <Pencil size={12} />
            </button>
          )}
        </div>
        <div className="mt-1 text-sm text-muted">
          <span className="mr-3">{T.visibleRatio(person.visibleRatio)}</span>
          {formatFrameRange(person.firstFrame, person.lastFrame, fps)}
        </div>
        {drop.state === "active" && <div className="mt-1 text-xs text-accent">{T.dropVrm}</div>}
      </div>
      <div className="flex w-[15rem] shrink-0 flex-col items-start gap-1">
        {row.selected ? (
          <>
            {slots.renderAvatarPicker({ value: row.avatarId, onChange: (avatarId) => onChange({ avatarId }) })}
            {row.avatarId && slots.renderLicense(row.avatarId)}
          </>
        ) : (
          <span className="pt-2 text-sm text-faint">{T.keepReal}</span>
        )}
      </div>
    </li>
  );
}
