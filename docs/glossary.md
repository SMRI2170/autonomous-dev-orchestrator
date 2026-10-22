PR — 専用ブランチで変更をGitHubに公開し、CI・レビュー・人間確認を経てマージする単位。（README.md、docs/verification.md）
check-run — CIのcheck summaryとconclusionを取得し、PRのSHAに対する判定に使う実行結果。（docs/architecture.md、docs/verification.md）
review thread — PRレビューから取得した、修正やマージの可否判定に使う未解決スレッド。（docs/architecture.md、docs/verification.md）
head SHA — CIとレビュー判定を同じコミットへ結び付ける、PRの最新のコミットを一意に識別するSHA。（README.md、docs/verification.md）
dry-run — Issueの取得と仕様・依存検査だけを行い、モデル呼出し・GitHub書き込み・ライブジョブの予約を行わないモード。（README.md、docs/dashboard.md、docs/launchd.md、docs/verification.md）
fail-closed — 証拠が欠けている場合や安全条件を満たさない場合に、合格扱いせず停止する判定方針。（README.md、docs/architecture.md）
AWAITING_HUMAN — 判定には合格したが、自動マージが無効な場合などに人間によるマージ待ちとなる状態。（README.md、docs/architecture.md）
repair（修復） — PRレビューやCIで必要な修正点を反映し、その変更について判定をやり直す工程。（README.md、docs/verification.md）
再生禁止（中断したモデル呼出しを自動で再実行しないこと） — 中断したモデル呼出しを自動では再実行しない運用規則。（README.md、docs/verification.md）
