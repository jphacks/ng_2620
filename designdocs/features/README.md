# features/

機能要件ごとの設計。各文書は同じ構成：

1. 概要（レーン・優先度・関連 FR・依存）
2. ユーザーストーリー
3. 受け入れ条件（デモ前に手で確認するチェックリスト）
4. 設計（domain / application / infrastructure / presentation、サーバー側があれば server）
5. 契約（API・成果物・形式への参照）
6. 状態とエラー
7. タスク分解（**層ごとに独立着手できる単位**。サブエージェントにそのまま渡せる）
8. テスト
9. P1 / スコープ外

一覧と担当は [docs/README.md](../README.md#機能一覧features)。

フルモード（ローカルサーバー）と制限モード（Pages のみ）の機能差分は [mode-matrix.md](mode-matrix.md)（[ADR-0015](../adr/0015-web-first-windows-local-server.md)）。
