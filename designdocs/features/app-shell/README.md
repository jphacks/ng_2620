# F15 app-shell — アプリ枠（ワークスペース・パネル・設定・テーマ）

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | A |
| 優先度 | P0（**最初に作る**：他の画面の置き場所） |
| 関連 | FR-18 / [ux/screens S0](../../ux/screens.md#s0-共通の枠f15) / [ADR-0001](../../adr/0001-desktop-framework-tauri.md) / [ADR-0013](../../adr/0013-frontend-stack.md) |
| 依存 | なし（M0 の契約から生成した型だけ） |

## ユーザーストーリー
- ユーザーとして、取り込み・編集・書き出しを上部のタブで切り替え、どの画面でも接続状態とジョブの進捗が見えるようにしたい。
- プロとして、パネルの幅を調整でき、次に開いたときも同じ配置であってほしい。

## 受け入れ条件
- [ ] `pnpm tauri dev` で macOS 上に起動し、ホームが出る（Windows でも起動する）。
- [ ] タイトルバーにプロジェクト名とワークスペースタブ（取り込み / 編集 / 書き出し）。`⌘1/2/3` で切替。
- [ ] ステータスバー：接続状態・実行中ジョブ・キュー数・保存状態。クリックでそれぞれ接続設定・ジョブパネルが開く。
- [ ] エディタのパネル（左・中央・右・下）の幅・高さをドラッグで変えられ、`project.json` の `ui.panelLayout` か アプリ設定に保存される。
- [ ] ダークテーマ、デザイントークン（CSS 変数）、日本語文言が `i18n/ja.ts` に集約されている。
- [ ] グローバルショートカット（[interaction §1](../../ux/interaction.md#1-キーボードショートカットmacoswindows-は--ctrl) の「全体」）が動く。
- [ ] **WebView の確認**：WebGL2 が使える、`fetch` のストリームで SSE を読める（モックサーバー相手）ことを起動時の自己診断でログに出す。

## 設計
- presentation/app：
  - `AppRoot`（ルーティング：`/` ホーム、`/project/:id/{import|edit|export}`、`/settings`、`/#/dev/editor?fixture=v0`（B の単独起動用））
  - `WorkspaceTabs`、`StatusBar`、`JobDrawer`（中身は F05）、`ToastHost`、`CommandRegistry`（ショートカット → コマンド。feature がコマンドを登録する）
- presentation/shared：Radix ベースの部品（Button、Dialog、Tabs、Select、Tooltip、Slider、Toggle、ContextMenu）、`tokens.css`、`i18n/ja.ts`、`useHotkey`。
- `container.ts`（composition root）：ポート実装の組み立て。`import.meta.env.VITE_BACKEND=mock|http` で切替、テストでは InMemory。
- 状態：`useAppStore`（現在のプロジェクト ID、ワークスペース、接続状態）。feature ごとのストアは各 feature が持つ。

## タスク分解
1. Tauri 2 + Vite + React + TS（strict）の雛形、pnpm、ESLint（`import/no-restricted-paths` で層の依存規則を強制）、Vitest。
2. `tokens.css`・基本部品・`i18n/ja.ts`。
3. ルーティングとワークスペースタブ、`CommandRegistry` と `useHotkey`。
4. ステータスバーとジョブドロワーの枠（データは F03 / F05 のストアをつなぐだけ）。
5. `react-resizable-panels` でエディタ用のレイアウト枠（中身は B が差し込む `EditorLayout` のスロット）。
6. 起動時の自己診断（WebGL2、SSE ストリーム）。
7. `container.ts` と `VITE_BACKEND` 切替。

## テスト
- 層の依存規則の lint が CI（ローカルの `pnpm lint`）で通る。
- `CommandRegistry` のユニットテスト（重複キー、ワークスペースごとの有効化）。

## P1 / スコープ外
- パネルのドラッグによる自由なドッキング（P2）、ライトテーマ（P2）、英語 UI（P2）。
