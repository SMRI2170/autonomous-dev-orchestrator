# 設計判断・今後の連携

## 常駐方式

| 候補 | M2 16GBでの負荷/運用 | 判断 |
|---|---|---|
| Python単一worker + SQLite + launchd | コンテナ/サーバー不要。1ジョブと永続状態を同じプロセスで管理 | MVPに採用 |
| n8nを中心に構成 | 図で編集できるが、状態・lock・再試行・機密境界を別途維持する必要 | 現時点では追加しない |
| GitHub Actions self-hosted runner | 公開PRのコードをMac上で実行する境界が必要 | 使用しない |
| 標準ホストRunner + Macポーリング | CIとMac認証を分離。Web公開不要 | 採用 |

n8nを追加する場合もSQLiteを唯一の状態元に保ちます。読み取り専用のstatus JSON/JSONLを集計する構成を先に検討します。n8nがモデルやworkerを重複起動する構成、任意shellのWebhook、公開DB、追加AI API課金は導入しません。n8nのworkflow JSONやWebhookは未実装です。

## 可視化とProjects

MVPは`ado dashboard`の[ローカルフロー監視画面](dashboard.md)、`ado status`、events.jsonl、Issue/PRのリンクで状態を確認します。監視画面は既存SQLiteの読み取り専用APIとローカルのHTML/CSS/JavaScriptで構成し、外部依存や別の実行エンジンを追加しません。DBの状態:

| 状態 | 意味 |
|---|---|
| QUEUED | 適格なIssueをローカルで予約 |
| BUILDING | 呼出し予算を確保済み、変更生成中 |
| PUBLISHING | 変更とcommit意図を保存済み、公開を再開可能 |
| REVIEW | CI/CodeRabbit/修正ループ待ち |
| AWAITING_HUMAN | 判定合格、自動マージ無効等により人間待ち |
| BLOCKED | 失敗/期限/安全基準/上限で停止 |
| DONE | マージを確認しIssueを完了済み |

GitHub Projects v2への状態書込みは未実装です。Project単位の追加権限・Status field IDの設定が必要なため、初期段階では手動追加、または利用可能ならProjectsの組込みauto-add機能を運用者が設定する方法を推奨します。GitHub上のBlockedラベル更新も現時点では行いません。

## Codexの役割

Codexは常時起動しません。大きなIssueの仕様整理、Blockedの原因調査、公開前の人間のレビュー補助として手動で使用できます。既存ChatGPT契約でCLIへログインし、[公式non-interactive案内](https://developers.openai.com/codex/noninteractive)を確認してください。

例として、機密を含まない設計やPR差分を独立した空の作業ディレクトリに保存し、`codex exec --sandbox read-only --ephemeral`にレビューを依頼できます。read-onlyは情報流出の完全防止を意味しないため、渡す情報を先に確認します。Codexの自然言語の回答だけでマージ基準を満たした扱いにはしません。

自動のCodexアダプター、モデル選択、利用枠取得は未実装です。MiniMax/Codex間で従量課金APIへ自動フォールバックしません。

## MVPの意図的な制限

- 小さな公開テキスト変更のみ。リポジトリ全体のビルド依存解決や大規模リファクタリングには未対応。
- 依存関係はIssue本文の同一repo番号だけ。クロスrepo DAG、循環検出、設計LLM、外部仕様取り込みは未実装。
- MiniMaxには許可したファイルとcontextだけを渡す。保護された依存マニフェストは渡さず、依存追加・設定変更はBlockedにする。
- 変更の出力は2形態です。既存ファイルは差分（patch）で書き換えて、触らない行を動かしません。全文（files）は新規ファイルにだけ使います。1つの回答で両方を使うこともでき、同じパスが両方に現れる場合は拒否します。差分のアンカーは対象ファイルに一意に現れないと拒否され、推測では書き換えません。変更バイト予算は、差分ならそのパッチの長さ、全文ならそのファイルの長さで測ります。1Jobの予算はパス毎のコストをラウンドをまたいで累積した合計で判定します。
- trusted authorのIssue編集・ラベル解除を毎段階で確認。GitHub API全体のトランザクションはないため管理者との同時操作は避ける。
- CodeRabbitが無料枠でAPPROVEDを返さない場合、承認の意味を自然言語から推測しない。
- CI全文ログ/成果物ダウンロードは未実装。check summary、conclusion、レビュー本文、未解決スレッドを修正入力として収集する。
- 通知配信、n8n、Projects書込み、Web上でのフロー編集・ジョブ操作、複数ホスト、fair scheduling、ログrotationは未実装。

## 一次資料

- [MiniMax Code CLI・lightweight](https://github.com/MiniMax-AI/minimax-code)
- [CLI output契約](https://github.com/MiniMax-AI/minimax-code/blob/main/packages/tui/src/headless/output.ts)
- [GitHub PR/merge API](https://docs.github.com/en/rest/pulls/pulls#merge-a-pull-request)
- [GitHub check-runs API](https://docs.github.com/en/rest/checks/runs)
- [GitHub hosted runners](https://docs.github.com/en/actions/concepts/runners/github-hosted-runners)
- [CodeRabbitプラン](https://www.coderabbit.ai/pricing)
- [Codex non-interactive](https://developers.openai.com/codex/noninteractive)

外部サービスの契約・機能・無料対象は変更され得ます。実際のアカウント設定は導入時に確認してください。
