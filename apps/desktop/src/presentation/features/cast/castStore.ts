// キャストの下書き（選んだがアバター未割当の行も持つ）。確定した行は project.cast に反映する

import { useEffect } from "react";
import { create } from "zustand";
import type { CastDraftRow, Person } from "@/domain/cast";
import { draftFromCast } from "@/domain/cast";
import type { CastEntry } from "@/domain/cast";

interface CastDraftStore {
  drafts: Record<string, CastDraftRow[]>;
  set(projectId: string, rows: CastDraftRow[]): void;
}

export const useCastDraftStore = create<CastDraftStore>((set) => ({
  drafts: {},
  set: (projectId, rows) => set((s) => ({ drafts: { ...s.drafts, [projectId]: rows } })),
}));

/** 人物一覧が届いたら下書きを作る／増えた人物を足す（既存の選択は残す） */
export function useSyncDraft(projectId: string | undefined, persons: readonly Person[], cast: readonly CastEntry[]) {
  useEffect(() => {
    if (!projectId) return;
    const { drafts, set } = useCastDraftStore.getState();
    const cur = drafts[projectId];
    const base = draftFromCast(persons, cast);
    if (!cur) return set(projectId, base);
    const merged = base.map((b) => cur.find((r) => r.personId === b.personId) ?? b);
    if (merged.length !== cur.length || merged.some((r, i) => r !== cur[i])) set(projectId, merged);
  }, [projectId, persons, cast]);
}
