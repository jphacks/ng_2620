# F09 editor-viewer — エディタ（ビューア・タイムライン・プレビュー合成）

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | B |
| 優先度 | P0 |
| 関連 | FR-13 / [ADR-0005](../../adr/0005-preview-composite-client-export-server.md) / [contracts §4–5](../../architecture/contracts.md#4-成果物レイヤー形式) / [ux/screens S3](../../ux/screens.md#s3-エディタf09f10) / [ux/interaction §1, §3](../../ux/interaction.md) |
| 依存 | F15（レイアウト枠）、レイヤー（フィクスチャ v0 → v1 → 実サーバー） |

## ユーザーストーリー
- プロとして、置換結果をフレーム単位で確認し、元動画と比べたい。
- どこに問題があるかを見つけるため、遮蔽物・隠れマスク・追跡マスクを重ねて表示したい。

## 受け入れ条件
- [ ] レイヤー（`cache/<renderJobId>/layers/`）と `proxy_frames`・`units` を読み、WebGL2 で合成して表示する。
- [ ] 再生 30fps（プロキシ 540x960、M1 Mac）、スクラブ、コマ送り、J/K/L、Home/End、時刻表示（`m:ss.ff`）とフレーム番号。
- [ ] 表示モード：合成 / 元動画 / 分割（左右にワイプ、境界をドラッグ）。`\` 押下中は元動画。
- [ ] レイヤー表示の切替：アバター、遮蔽物ユニット（ユニットごとに色分け、半透明）、隠れマスク（赤）、追跡マスク（人物色）。
- [ ] ズーム（25〜400%、`⌘+` / `⌘-` / `⌘0` でフィット、トラックパッドのピンチ）とパン（トラックパッドの 2 本指スクロール、マウスは中ボタンのドラッグ）。`Space` は再生、`Alt` は遮蔽物のハイライト（F10）に使うので、パンには割り当てない。
- [ ] タイムライン：時間目盛、再生ヘッド、映像トラック（サムネイル間引き）、人物トラック（出現区間）、修正トラック（F10）、選択中ユニットの出現区間。ズーム（`⌘` + ホイール）。
- [ ] 手修正の変更から 0.5 秒以内に再合成（現在フレーム）。
- [ ] レイヤーが一部しか届いていないときは、届いたフレームまで再生でき、タイムラインに取得済み範囲を表示。
- [ ] 手修正なしの合成が `preview_video` と一致（|差| > 24/255 の画素が 0.5% 未満）。

## 設計
- domain/composite.ts：合成規則の CPU 参照実装（`compositePixel` / `compositeFrame(ImageData...)`）、`effectiveHide`。**シェーダーと同じ式**で、テストの正解を作る。
- application：
  - ports：`FrameSourcePort { frame(f): Promise<ImageBitmap>; layer(kind, personId, f): Promise<ImageBitmap>; units(f): Promise<Uint16Array|ImageBitmap> }`
  - usecases：`OpenEditor`（必要なキャッシュの存在確認、無ければ F05 の `FetchArtifact`）、`Seek`、`Play/Pause`、`SetViewMode`、`ToggleLayer`。
- infrastructure/cache/`TauriFrameSource`：`convertFileSrc` で WebP / JPEG / PNG を `createImageBitmap`。LRU（300 フレーム × 必要レイヤー）、再生ヘッド前後 60 フレームの先読み。16bit PNG の `units` は `createImageBitmap` では 8bit に丸められるので、**`units` は WebGL の `R16UI` テクスチャに入れるため PNG を自前でデコード**（`fast-png` などの純 JS デコーダ）するか、サーバー側で 8bit×2 枚（上位・下位）に分けて配信する（契約上は 16bit PNG。デコードはアプリ側の責務）。
- presentation/features/editor：
  - `compositor/`：WebGL2。テクスチャ：frame、clean、avatar[p]、hide[p]、units（R16UI）、overrideTable（`R8UI` の 2D テクスチャ：横 = unitId、縦 = 人物 × 区間の解決済みモード。フレームごとに CPU で「このフレームで有効なモード」を計算して 1 行に詰め、`texSubImage2D` で更新）。フラグメントシェーダーで §5 の式。人物は最大 4 人なのでループ展開。
  - `viewer/`：キャンバス、ズーム・パン、ホバー時のユニット判定（CPU 側に `units` の配列を保持して座標→unitId）、選択輪郭（ユニットのマスクから輪郭をシェーダーでエッジ抽出）。
  - `timeline/`：Canvas 2D。トラックは登録式（`TimelineTrack` インターフェース）で、修正トラックは F10 が登録する。
  - `inspector/`：選択中のものの詳細（中身は F10 が差し込む）。
  - `useEditorStore`：playhead、playing、viewMode、visibleLayers、zoom、selection。
- 再生ループ：`requestAnimationFrame` で経過時間からフレームを決め、取得済みなら描画、未取得なら直前を表示（フレーム落ち）。

## タスク分解
1. domain/composite.ts ＋テスト（フィクスチャ v0 の正解画像と一致）。— **D1 +2h**
2. フィクスチャ v0 生成スクリプト（`fixtures/synth/make_synth.ts` か `.py`：動く円のアバター 2 人、四角の遮蔽物ユニット 2 つ、自動判定と正解合成）。— F14 と共有、B が作る
3. `TauriFrameSource`（LRU、先読み）と 16bit PNG デコード。
4. WebGL 合成（シェーダー、テクスチャ管理、overrideTable）。
5. ビューア（ズーム・パン・表示モード・分割）。
6. タイムライン（目盛・再生ヘッド・トラック登録式・ズーム）。
7. 再生制御とショートカット。
8. `preview_video` との一致確認（フィクスチャ v1）。

## テスト
- `compositeFrame` とシェーダーの出力（`readPixels`）が v0 で一致。
- 再生中に 300 フレームを通して FPS を計測（開発用オーバーレイ）。

## P1 / スコープ外
- 再生時の音声（P1）、人物ごとの深度による z バッファ（P2）、フル解像度のプレビュー（P2）。
