// 動画から新規プロジェクトを作る（ドロップ・ファイル選択・⌘N 共通）。事前検証（30 秒・1920px・形式）で弾く

import { useState } from "react";
import { mergeLimits, DEFAULT_LIMITS, isAcceptedVideoPath, ACCEPTED_EXTENSIONS } from "@/domain/validation";
import type { Project } from "@/domain/project";
import type { ErrorText } from "@/presentation/shared/errors";
import { errorText, violationText } from "@/presentation/shared/errors";
import { useServices } from "@/presentation/shared/services";
import { useConnectionStore } from "@/presentation/shared/stores/connectionStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";

export type NewProjectState =
  { kind: "idle" } | { kind: "probing" } | { kind: "creating" } | { kind: "rejected"; error: ErrorText };

export function useNewProject(onCreated: (p: Project) => void) {
  const { uc, platform } = useServices();
  const createFrom = useProjectStore((s) => s.createFrom);
  const limits = useConnectionStore((s) => s.capabilities?.limits);
  const [state, setState] = useState<NewProjectState>({ kind: "idle" });

  const fromPath = async (path: string) => {
    setState({ kind: "probing" });
    try {
      const { info, violations } = await uc.probeMedia.execute(path, mergeLimits(DEFAULT_LIMITS, limits));
      if (violations.length || !info) {
        setState({
          kind: "rejected",
          error: violationText(violations[0] ?? { code: "MEDIA_UNSUPPORTED", reason: "extension", value: path }),
        });
        return;
      }
      setState({ kind: "creating" });
      const project = await createFrom(path, info);
      setState({ kind: "idle" });
      onCreated(project);
    } catch (e) {
      setState({ kind: "rejected", error: errorText(e) });
    }
  };

  const pick = async () => {
    const [path] = await platform.dialog.pickFiles({
      multiple: false,
      filters: [{ name: "Video", extensions: [...ACCEPTED_EXTENSIONS] }],
    });
    if (path) await fromPath(path);
  };

  return { state, fromPath, pick, reset: () => setState({ kind: "idle" }), accept: isAcceptedVideoPath };
}
