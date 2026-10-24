# ADR-0001 デスクトップ基盤は Tauri 2 + React + TypeScript

- 状態：採択
- 日付：2026-10-24

## 文脈
- 製品の将来像は Adobe 系のプロ向けデスクトップアプリ。デモは Mac（Apple Silicon）、将来は Windows（NVIDIA GPU 搭載機でローカル推論）も対象。
- 2 日間で 3 レーン並列に UI を作る。UI の部品化・並列化がしやすく、情報の多い技術が望ましい。
- 推論そのものはアプリに載せない（[ADR-0002](0002-gpu-server-and-compute-backend-port.md)）。アプリに要るのはファイル操作、HTTP、子プロセス起動（Windows ローカルサーバー）、高速な画像合成（WebGL）。

## 決定
**Tauri 2 + React 18 + TypeScript（Vite）** を採用する。Rust 側は薄く保ち、ファイル操作・子プロセス・キーチェーンなど OS 機能だけを `#[tauri::command]` として公開する。

## 却下した案
| 案 | 却下理由 |
|---|---|
| Electron | 実績は最多だがバンドルが重い（数百 MB）。プロ向け製品として配布サイズとメモリが不利。Tauri で必要機能が揃う |
| SwiftUI | Mac での質感は最良だが、Windows 版が別実装になる。3 レーン並列の人数に対して二重実装は不可 |
| PySide6（Qt） | 既存 Python と同居できるが、推論はサーバーに分離するので利点が薄い。プロ向け UI の作り込みが遅い |

## 影響
- UI は Web 技術（React）で書くので、レーン A / B が独立した画面・部品を並列に作れる。
- WebView 差異：macOS は WKWebView、Windows は WebView2。**WebGL2 と `fetch` の SSE ストリーミング**は両方で使えることを Day 1 午前に確認する（F15 のタスク）。
- 大きなファイル（動画・フレーム列）は WebView に直接渡さず、Rust 側でディスクに保存し `convertFileSrc`（asset プロトコル）で読む。
- 署名・公証・自動更新はスコープ外（[ADR-0010](0010-out-of-scope-enterprise-and-later.md)）。デモは `tauri dev` または未署名ビルド。
