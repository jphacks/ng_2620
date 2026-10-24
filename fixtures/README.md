# fixtures

| 名前 | 中身 | 作り方 |
|---|---|---|
| `synth/synth.vvlog` | 合成フィクスチャ v0：96 フレーム・540x960、動く円のアバター 2 人、四角の遮蔽物ユニット 2 つ（ユニット 1 は人物 1 の「前」、ユニット 2 は人物 2 の「後ろ」と自動判定）。解析・描画の成果物を取得済みのプロジェクトそのもの | `services/gpu_server/.venv/bin/python fixtures/synth/make_synth.py` |
| `synth/synth.vvlog/expected/` | フレーム 48 の正解合成（contracts §5）：手修正なし / ユニット 2 を人物 2 の前に | 同上 |

エディタの開発・確認用（GPU もサーバーも不要）。アプリでは「プロジェクトを開く」でこのフォルダを選ぶか、開発用ルート `/#/dev/editor` で開く。
実データのフィクスチャ v1 は、実サーバーの記録モード（`VVLOG_RECORD_FIXTURE`）で作る（services/mock_server/README.md）。
