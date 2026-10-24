// S3 エディタ（枠は A、中身は B）。描画前は「アバターを描画すると編集できます」、レイヤーの取得中は進捗

import { useNavigate } from "react-router-dom";
import { renderState } from "@/domain/project";
import { CastPanel } from "@/presentation/features/cast";
import { ja } from "@/presentation/shared/i18n/ja";
import { useArtifactState } from "@/presentation/shared/stores/jobsStore";
import { useProjectStore } from "@/presentation/shared/stores/projectStore";
import { Button, EmptyState, ProgressBar } from "@/presentation/shared/ui";
import { EditorLayout } from "../EditorLayout";
import { editorSlots } from "../editorSlots";

export function EditRoute() {
  const navigate = useNavigate();
  const project = useProjectStore((s) => s.current?.project);
  const layers = useArtifactState(project?.jobs.render?.jobId, "layers");
  if (!project) return null;
  const { Viewer, Inspector, Timeline, LayersPanel } = editorSlots;
  const rendered = project.jobs.render?.status === "succeeded" || !!project.sample;
  const goCast = () => navigate(`/project/${project.id}/import`);

  const center = !rendered ? (
    <EmptyState title={ja.editor.needsRender}>
      <Button variant="primary" onClick={goCast}>
        {ja.editor.render}
      </Button>
    </EmptyState>
  ) : (
    <div className="relative h-full">
      {layers?.status === "downloading" && (
        <div className="absolute inset-x-0 top-0 z-10">
          <ProgressBar
            value={layers.total ? layers.received / layers.total : 0}
            indeterminate={!layers.total}
            className="!h-0.5 !rounded-none"
          />
          <div className="px-2 py-1 text-xs text-muted">{ja.editor.downloadingLayers}</div>
        </div>
      )}
      <Viewer />
    </div>
  );

  return (
    <EditorLayout
      left={
        <div className="divide-y divide-border">
          <CastPanel onEditCast={goCast} />
          {renderState(project) !== "none" && <LayersPanel />}
        </div>
      }
      center={center}
      right={rendered ? <Inspector /> : <div className="p-3 text-sm text-faint">{ja.editor.needsRender}</div>}
      bottom={<Timeline />}
    />
  );
}

export function DevEditorRoute() {
  const { DevEditor } = editorSlots;
  return <DevEditor />;
}
