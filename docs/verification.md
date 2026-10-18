# 検証記録

検証日: 2026-10-18（Asia/Tokyo）。初期MVPの記録です。

## ローカル

Python 3.12.14、macOS、Node 26.3.1で`python -m unittest discover -v`の51件が成功。`compileall`も成功。

- ポリシー: 保護パス、機密、危険コマンド、glob、変更予算、strict JSON、依存表記、設定の安全な既定値。
- 状態ループ: Issue→変更→PR→レビュー→修正→合格→merge→close→次Issueをfake GitHub/agentで統合検証。
- 停止条件: 最大3回、タイムアウト、外部SHA変更、仕様編集、設定変更、保護ファイル出力、未承認レビュー、古いCI、欠けた証拠。
- 復旧: ref/PR作成後の応答消失、Issue close失敗、再起動、二重workerの排他、中断したモデル呼出しの再生禁止。
- OS隔離: 実際のsandbox-execとNodeのfixtureで外部ファイルの読取り/書込み・shell起動拒否、GH_TOKEN/OPENAI_API_KEY非継承を確認。このテストはLinux CIではskip。
- GitHub transport: dry-run書込み拒否、ページング、SHA条件付きmerge、ref/PR照合、GitHub Actions Appに紐づくbranch protection検査。

## 実サービス受入の境界

MiniMax CLIがこのMacに未導入のため、実契約のモデル呼出し・修正は未検証。CodeRabbit Appの新規接続・承認権限・実際のAPPROVED発行も未検証。これらをfixtureで確認した結果と混同しないでください。自動マージ・常駐サービスの有効化は行いません。

本ツール専用の新規公開リポジトリだけを使って、GitHub APIと標準Runnerの受入検証を行います。既存プロジェクトのコード・設定は変更しません。実施結果は同リポジトリの検証PRに追記します。

## GitHub実サービスの確認

新規[専用リポジトリ](https://github.com/SMRI2170/autonomous-dev-orchestrator)を作成し、初期commit `9d558b1`をpushしました。初期mainの[標準Runner CI](https://github.com/SMRI2170/autonomous-dev-orchestrator/actions/runs/39086578289)も成功しました。

続いて[Issue #1](https://github.com/SMRI2170/autonomous-dev-orchestrator/issues/1)を作成し、productionのEngine/Store/GitHubクラスを使って処理しました。モデルだけは固定の安全なMarkdownを返すfixtureです。

1. dry-run: ELIGIBLE、agent_called=false、github_writes=false、ライブclaimなし。
2. ライブ1回目: PUBLISHING（変更生成・検査・意図保存）。
3. ライブ2回目: REVIEW。実際のGit Data APIでcommit `b839991621fe27aef6e5724ee91c3fde7c6adada`、専用branch `ado/issue-1`、[PR #2](https://github.com/SMRI2170/autonomous-dev-orchestrator/pull/2)を作成。
4. 現在SHAの[PR CI](https://github.com/SMRI2170/autonomous-dev-orchestrator/actions/runs/39086614674)成功。check名は`checks / unit`と確認。
5. 次pollでRESTのcheck/reviewとGraphQL reviewThreadsを取得し、CodeRabbit承認がないためREVIEWに留まることを確認。merge/Issue closeは実行していません。

この初期セットアップ検証のみ、ユーザーが許可した新規repo作成に使った既存gh認証をプロセス内で利用しました。トークンは保存・表示していません。常駐運用には対象限定fine-grained PATを別途用意してください。

ローカルvenvへのeditable install成功、launchd plist生成と`plutil -lint`成功。LaunchAgentのOS登録・起動はしていません。Flutter再利用workflowは用意しましたが、対象Flutterプロジェクトには導入せず、実際のFlutter SDKダウンロード/テストも未実施です。
