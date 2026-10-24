# ADR-0012 通信は HTTP(S)＋SSE、認証は Bearer トークン。リモートはトンネル経由の HTTPS

- 状態：採択
- 日付：2026-10-24

## 文脈
- 経路はローカル（127.0.0.1）とリモート（Mac → Windows 機 / クラウド GPU）。リモートはインターネットを越える。
- デモまで 2 日。認証基盤は作らない（[ADR-0010](0010-out-of-scope-enterprise-and-later.md)）が、無防備な GPU サーバーを公開はしない。

## 決定
1. API は REST（JSON）、進捗は **SSE**（`text/event-stream`）。SSE は `fetch` のストリームで読む（ヘッダーにトークンを載せるため。`EventSource` はヘッダーを付けられない）。切れたら `Last-Event-ID` で再開し、それも駄目ならポーリング（`GET /v1/jobs/{id}`）に落とす。
2. 認証は `Authorization: Bearer <token>`。サーバーは環境変数 `VVLOG_API_TOKEN` の値と照合する（P0）。トークン無しで通るのは `GET /v1/health` だけ。
3. リモートは **HTTPS 必須**。**今回のデモは Tailscale に限る**：チームの Windows GPU 機とデモ用の Mac を同じ tailnet に参加させ、Windows 機で `tailscale serve --https=443 http://127.0.0.1:8765` を実行し、Mac から `https://<機名>.<tailnet>.ts.net` に接続する（配備手順は F13）。tailnet の外には公開しない（Funnel は使わない）。クラウド VM・Cloudflare Tunnel などは将来の選択肢として API 上は変わらない。
4. 大きなファイル：
   - アップロードは**分割（8MB チャンク）＋再開可能**（`PUT /v1/uploads/{id}/chunks/{n}`）。
   - ダウンロードは成果物ごとの URL に `Range` 対応で GET。プレビュー用レイヤーは **zip でまとめて**取り寄せる（数百ファイルの往復を避ける）。
5. Tauri の CSP / HTTP 許可：接続先 URL はユーザー設定なので、HTTP は Rust 側の `tauri-plugin-http` を通し、許可スコープはプロファイル登録時に追加する（または開発中は広く許可し、P1 で絞る）。

## 却下した案
| 案 | 却下理由 |
|---|---|
| WebSocket | 双方向は不要。SSE の方がプロキシ・トンネル越しで扱いやすい |
| 認証なし（ローカル前提） | リモートで GPU サーバーが公開される |
| mTLS / OAuth | 2 日間では過剰。将来のエンタープライズで SSO と合わせて検討 |
