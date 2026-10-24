// エディタの差し込み口。中身は features/editor（F09）と features/occlusion-edit（F10）。左パネルの「キャスト」は A（F06）が持つ。
//   Viewer     ：合成プレビュー（WebGL2）。開いているプロジェクトのレイヤーを読み込み、ショートカットも登録する
//   Inspector  ：選択中のユニットと人物の前後（F10）
//   Timeline   ：再生ヘッド・人物・選択中の物・手修正の帯（F09 / F10）
//   LayersPanel：表示モードとレイヤーの表示切替（左パネル）
//   DevEditor  ：/#/dev/editor?fixture=v0 の単独起動（合成フィクスチャ v0 を開く）

import type { ComponentType } from "react";
import { DevEditor } from "@/presentation/features/editor/DevEditor";
import { LayersPanel } from "@/presentation/features/editor/LayersPanel";
import { Timeline } from "@/presentation/features/editor/Timeline";
import { Viewer } from "@/presentation/features/editor/Viewer";
import { UnitInspector } from "@/presentation/features/occlusion-edit/UnitInspector";

export interface EditorSlots {
  Viewer: ComponentType;
  Inspector: ComponentType;
  Timeline: ComponentType;
  LayersPanel: ComponentType;
  DevEditor: ComponentType;
}

export const editorSlots: EditorSlots = {
  Viewer,
  Inspector: UnitInspector,
  Timeline,
  LayersPanel,
  DevEditor,
};
