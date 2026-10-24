// エディタのレイアウト枠（F15）：左・中央・右・下のパネル。幅・高さはドラッグで変えられ、アプリ設定に保存される。
// 中身はレーン B（F09 / F10）が editorSlots.tsx から差し込む

import type { ReactNode } from "react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { panelStorage } from "@/presentation/shared/stores/prefsStore";

function Handle({ direction }: { direction: "horizontal" | "vertical" }) {
  return (
    <PanelResizeHandle
      className={
        direction === "horizontal"
          ? "w-1 bg-border transition-colors hover:bg-accent data-[resize-handle-state=drag]:bg-accent"
          : "h-1 bg-border transition-colors hover:bg-accent data-[resize-handle-state=drag]:bg-accent"
      }
    />
  );
}

export interface EditorLayoutProps {
  left: ReactNode;
  center: ReactNode;
  right: ReactNode;
  bottom: ReactNode;
}

export function EditorLayout({ left, center, right, bottom }: EditorLayoutProps) {
  return (
    <PanelGroup direction="vertical" autoSaveId="editor.vertical" storage={panelStorage} className="h-full">
      <Panel id="editor-top" order={1} defaultSize={70} minSize={30}>
        <PanelGroup direction="horizontal" autoSaveId="editor.horizontal" storage={panelStorage}>
          <Panel id="editor-left" order={1} defaultSize={18} minSize={12} className="overflow-auto bg-panel">
            {left}
          </Panel>
          <Handle direction="horizontal" />
          <Panel id="editor-center" order={2} defaultSize={60} minSize={30} className="bg-bg">
            {center}
          </Panel>
          <Handle direction="horizontal" />
          <Panel id="editor-right" order={3} defaultSize={22} minSize={14} className="overflow-auto bg-panel">
            {right}
          </Panel>
        </PanelGroup>
      </Panel>
      <Handle direction="vertical" />
      <Panel id="editor-bottom" order={2} defaultSize={30} minSize={12} className="overflow-hidden bg-panel">
        {bottom}
      </Panel>
    </PanelGroup>
  );
}
