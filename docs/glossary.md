PR — プルリクエスト（Pull Request）の略で、自動マージが無効なときのみ人間確認を要するPRです。（README.md、docs/architecture.md、docs/dashboard.md、docs/verification.md）

check-run — CIの特定の検査実行を表すGitHubの記録で、check summaryとconclusionを保持します。（docs/architecture.md）

review thread — PRレビューで残った未解決スレッドです。（docs/verification.md）

head SHA — PRのheadのコミットのSHAです。（README.md、docs/verification.md）

dry-run — 検査だけで書き込みをしないモードで、モデル呼出し・GitHub書き込み・ライブジョブの予約は行いません。（README.md、docs/dashboard.md、docs/launchd.md、docs/verification.md）

fail-closed — 合格扱いにせず停止する方針です。

AWAITING_HUMAN — 人間によるマージ待ちの状態です。（docs/architecture.md）

修復（repair） — 修正点を反映して判定をやり直す工程です。（docs/verification.md）

再生禁止 — 中断したモデル呼出しを自動で再実行しないことです。（docs/verification.md）
