PR — 専用ブランチで変更をGitHubに公開し、CI・レビュー・人間確認を経てマージする単位。（README.md）
check-run — PRのSHAに対してCIのcheck summaryとconclusionを取得し、判定に使う実行結果。（docs/architecture.md、docs/verification.md）
review thread — PRレビューから取得した未解決スレッドで、修正やマージの可否を判定するための指摘。（docs/architecture.md、docs/verification.md）
head SHA — PRの最新のコミットを一意に識別し、CIとレビュー判定を同じ変更へ結び付けるSHA。（README.md、docs/verification.md）
dry-run — Issue取得と仕様・依存検査だけを行い、モデル呼出し・GitHub書込み・ライブジョブの予約を行わない検査モード。（README.md、docs/dashboard.md）
fail-closed — 証拠が欠けている場合や安全条件を満たさない場合に合格扱いせず、停止する判定方針。（docs/architecture.md、README.md）
AWAITING_HUMAN — 判定には合格したが、自動マージが無効な場合などに人間によるマージ待ちとなる状態。（docs/architecture.md、README.md）
修復（repair） — PRレビューやCIで必要な修正点を反映し、その変更について判定をやり直す工程。（README.md、docs/verification.md）
再生禁止（中断したモデル呼出しを自動で再実行しないこと） — 中断したモデル呼出しを自動では再実行しない運用規則。（docs/verification.md）
