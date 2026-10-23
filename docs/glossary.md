# 用語集

PR — プルリクエスト（Pull Request）の略であり、自動マージが無効な場合や低リスク条件を満たさない場合は人間によるマージ確認を要する（README.md、docs/architecture.md、docs/dashboard.md、docs/verification.md）

check-run — CIの特定の検査実行を表すGitHubの記録で、check summaryとconclusionを保持する（docs/architecture.md）

review thread — PRレビューで使われる会話スレッドで、未解決のものだけがマージの可否を阻止する（docs/verification.md）

head SHA — PRのheadのコミットのSHAで、CIとレビュー判定を同じ変更へ結び付ける（README.md、docs/verification.md）

dry-run — モデル呼出し・GitHub書き込み・ライブジョブの予約をしないモードで、ローカルの監査イベントと稼働状態は更新する（README.md、docs/dashboard.md、docs/launchd.md、docs/verification.md）

fail-closed — 合格扱いにせず停止する方針

AWAITING_HUMAN — 人間によるマージ待ちの状態（docs/architecture.md）

修復（repair） — 修正点を反映して判定をやり直す工程（docs/verification.md）

再生禁止 — 中断したモデル呼出しを自動で再実行しないこと（docs/verification.md）
