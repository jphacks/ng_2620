# F12 demo-mode — 処理済みサンプルプロジェクトとオフライン動作

> **注（ADR-0015、2026-10-25）**：この文書の Tauri・Rust・`src-tauri` に関する記述は、Tauri 廃止（ブラウザ ＋ ローカルアプリサーバー）より前のものです。現在の構成は [ADR-0015](../../adr/0015-web-first-windows-local-server.md)・[tauri-migration.md](../../architecture/tauri-migration.md)・[mode-matrix.md](../mode-matrix.md) を正とします。


| 項目 | 内容 |
|---|---|
| レーン | B（サンプルの作成は C と協力） |
| 優先度 | P0 |
| 関連 | FR-17 / [ADR-0009](../../adr/0009-demo-resilience.md) / [ux/demo-script](../../ux/demo-script.md) |
| 依存 | F09・F10（エディタ）、F01（プロジェクトを開く）、フィクスチャ v1（F14） |

## ユーザーストーリー
- 初めての人として、サーバーを用意しなくても、完成例を開いて編集を試したい。
- デモ担当として、通信や GPU が落ちても、編集体験と完成動画を見せたい。

## 受け入れ条件
- [ ] ホームの「サンプルを開く」で、同梱のサンプルプロジェクトがサーバー接続なしで開く（`OfflineComputeBackend`）。
- [ ] 開くと、アプリデータ配下に**コピー**してから開く（同梱物を書き換えない）。2 回目以降は既存のコピーを開くか、初期状態に戻すかを選べる。
- [ ] エディタの全機能（再生、レイヤー表示、手修正、元に戻す）が動く。
- [ ] 書き出しタブ：「サンプルでは書き出し済みの動画を再生できます」として、同梱の `exports/sample_fixed.mp4`（デモで直す修正を適用済み）を再生。接続していれば通常の書き出しも可能（サーバー側に同じプロジェクトが無いので、[サーバーに送って解析] を出す）。
- [ ] 同梱サイズは 200MB 以下（プロキシ・WebP・10 秒・2 人まで）。
- [ ] サンプルには VRM ファイルを含めない（描画済みレイヤーのみ）。使用した VRM の名前・作者・ライセンスを「このサンプルについて」に表示。

## 設計
- 同梱：`apps/desktop/src-tauri/resources/samples/<name>.vvlog/`（Tauri の `bundle.resources`）。中身は通常のプロジェクトと同じ形式（[ADR-0007](../../adr/0007-project-file-format.md)）＋`cache/` 済み。`project.json` に `"sample": { "id": "cafe", "license": "..." }`。
- usecases：`OpenSample(id)`（コピー → `OpenProject`、接続は Offline に固定）、`ResetSample(id)`。
- 作り方（C と B）：
  1. チームで撮影した 10 秒の動画と、現在使用している VRM を用意。
  2. 実サーバーで analyze → render → export（手修正なし / あり）。
  3. `services/gpu_server/scripts/pack_sample.py <projectId>` が成果物を `.vvlog/` 形式にまとめる（`project.json` を生成、`cache/` に展開済みのレイヤー）。
  4. B が開いて確認し、`resources/samples/` に置く。
- フィクスチャ v1（F14）と同じデータを使い回してよい。

## タスク分解
1. `OpenSample` / `ResetSample`、`project.json` の `sample` フィールド（契約に追加）。
2. ホームのサンプルカード、「このサンプルについて」ダイアログ。
3. 書き出しタブの「書き出し済みを再生」分岐。
4. （C）`pack_sample.py`。
5. 素材の撮影・権利確認・同梱（D2 +4h まで）。

## P1 / スコープ外
- 複数サンプル（P1）、チュートリアル（ガイドツアー）（P2）。
