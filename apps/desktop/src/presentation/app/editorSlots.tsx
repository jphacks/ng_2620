// エディタの差し込み口（レーン B 用）。
// F09 editor-viewer / F10 occlusion-edit の部品ができたら、下のプレースホルダーを features/editor・features/occlusion-edit の
// コンポーネントに置き換える（このファイルだけを変えればよい）。左パネルの「キャスト」は A（F06）が持つ。
//   Viewer     ：合成プレビュー（WebGL2）。layersDir / framesDir などは useProjectStore と cache から読む
//   Inspector  ：選択中のユニットと人物の関係（F10）
//   Timeline   ：再生ヘッド・トラック（F09 / F10）
//   LayersPanel：レイヤーの表示切替（左パネル）
//   DevEditor  ：/#/dev/editor?fixture=v0 の単独起動（InMemoryProjectRepository＋フィクスチャ）

import type { ComponentType } from "react";
import { ja } from "@/presentation/shared/i18n/ja";

function Placeholder({ title }: { title: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1 p-4 text-center">
      <div className="text-sm font-semibold text-muted">{title}</div>
      <div className="text-xs text-faint">{ja.editor.laneB}</div>
    </div>
  );
}

export interface EditorSlots {
  Viewer: ComponentType;
  Inspector: ComponentType;
  Timeline: ComponentType;
  LayersPanel: ComponentType;
  DevEditor: ComponentType;
}

export const editorSlots: EditorSlots = {
  Viewer: () => <Placeholder title={ja.editor.viewer} />,
  Inspector: () => <Placeholder title={ja.editor.inspector} />,
  Timeline: () => <Placeholder title={ja.editor.timeline} />,
  LayersPanel: () => (
    <div className="p-2">
      <div className="text-sm font-semibold text-muted">▾ {ja.editor.layers}</div>
      <div className="pl-3 pt-1 text-xs text-faint">{ja.editor.laneB}</div>
    </div>
  ),
  DevEditor: () => <Placeholder title="/dev/editor?fixture=v0" />,
};
