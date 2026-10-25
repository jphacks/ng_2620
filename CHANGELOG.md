# Changelog

## [0.2.0](https://github.com/jphacks/ng_2620/compare/vvlog-v0.1.1...vvlog-v0.2.0) (2026-10-25)


### Features

* **desktop:** 取り込み画面に「既定のアバターで自動仕上げ」（解析 → 描画 → エディタで再生まで自動） ([51db35d](https://github.com/jphacks/ng_2620/commit/51db35dbecb19f1cbf54ee8993904c572d01c9c6))
* 既定のアバターで自動仕上げ（解析 → 描画 → エディタで再生まで操作なし） ([c04c7c3](https://github.com/jphacks/ng_2620/commit/c04c7c33bf2d714256e7c80c44ea9e48b122ca7a))


### Bug Fixes

* **desktop:** 編集画面のビューアに再生・停止とコマ送りのボタンを付ける（これまで Space キーだけだった） ([bdcdb6c](https://github.com/jphacks/ng_2620/commit/bdcdb6c79fe350b8ef6c839f095f1a6e06037f2b))
* **pipeline:** 編集用のアバターレイヤーを RGBA→BGRA にして書き出す（赤と青が入れ替わって見えていた） ([c8c7872](https://github.com/jphacks/ng_2620/commit/c8c78721315bec482466d5695a2f0a187fcb8ba1))
* 編集画面のアバターの色と、再生ボタンがない問題 ([ee042ed](https://github.com/jphacks/ng_2620/commit/ee042ed0cbce4a48faa6418e861e4c21ebe6ace3))

## [0.1.1](https://github.com/jphacks/ng_2620/compare/vvlog-v0.1.0...vvlog-v0.1.1) (2026-10-24)


### Bug Fixes

* pnpm install で pre-push の hook が確実に入るように、ルートの prepare で lefthook install を呼ぶ ([c30a0c2](https://github.com/jphacks/ng_2620/commit/c30a0c202371355ee7484c7d0d6180ac6d3cf9a5))
* pre-push の自動修正で、add して差分が残らないときは commit せず push を止めない ([fa857af](https://github.com/jphacks/ng_2620/commit/fa857aff056dd3ac3efd5f77abc27d437fe09595))
* スライドの PDF を 1 本ずつ書き出す（--output にディレクトリを渡すと Marp が落ちる） ([1f1d45f](https://github.com/jphacks/ng_2620/commit/1f1d45fd64befa30c70203034f21410cdca0d014))
