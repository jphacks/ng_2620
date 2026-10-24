# Changelog

## [0.1.1](https://github.com/jphacks/ng_2620/compare/vvlog-v0.1.0...vvlog-v0.1.1) (2026-10-24)


### Bug Fixes

* pnpm install で pre-push の hook が確実に入るように、ルートの prepare で lefthook install を呼ぶ ([c30a0c2](https://github.com/jphacks/ng_2620/commit/c30a0c202371355ee7484c7d0d6180ac6d3cf9a5))
* pre-push の自動修正で、add して差分が残らないときは commit せず push を止めない ([fa857af](https://github.com/jphacks/ng_2620/commit/fa857aff056dd3ac3efd5f77abc27d437fe09595))
* スライドの PDF を 1 本ずつ書き出す（--output にディレクトリを渡すと Marp が落ちる） ([1f1d45f](https://github.com/jphacks/ng_2620/commit/1f1d45fd64befa30c70203034f21410cdca0d014))
