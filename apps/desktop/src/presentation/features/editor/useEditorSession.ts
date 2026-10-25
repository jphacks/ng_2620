// 開いているプロジェクトのレイヤーをエディタに読み込む。レイヤーの取得が終わったとき・プロジェクトが変わったときに読み直す

import { useEffect } from "react";
import { useServices } from "@/presentation/shared/services";
import { useArtifactState } from "@/presentation/shared/stores/jobsStore";
import { useAutoFinishStore } from "@/presentation/shared/stores/autoFinishStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { useEditorStore } from "@/presentation/shared/stores/editorStore";

export function useEditorSession() {
  const { uc } = useServices();
  const project = useProjectStore((s) => s.current?.project);
  const layers = useArtifactState(project?.jobs.render?.jobId, "layers");
  const units = useArtifactState(project?.jobs.analyze?.jobId, "units");
  const key = `${project?.id}|${project?.jobs.render?.jobId}|${project?.meta?.frameCount}|${layers?.status}|${units?.status}`;

  useEffect(() => {
    if (!project) return;
    let alive = true;
    (async () => {
      try {
        const [data, doc] = await Promise.all([
          uc.loadEditorData.execute(project),
          uc.loadOverrides.execute(project.id),
        ]);
        if (!alive) return;
        useEditorStore.getState().open(project.id, data, doc.items);
        // 自動仕上げで描画したプロジェクトは、レイヤーが揃ったら先頭から再生する
        if (layers?.status === "ready" && useAutoFinishStore.getState().takeAutoplay(project.id))
          useEditorStore.getState().setPlaying(true);
      } catch (e) {
        if (alive) useEditorStore.getState().fail(e);
      }
    })();
    return () => {
      alive = false;
    };
    // key がレイヤーの取得状態とプロジェクトの切り替えをまとめて表す
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  useEffect(() => () => useEditorStore.getState().close(), []);
}
