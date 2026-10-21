# 検証記録

検証日: 2026-10-18（Asia/Tokyo）。初期MVPの記録です。

## 2026-10-20 追加検証

- ローカルの読み取り専用フロー監視画面を追加。工程・Issueタイトル・待機理由・変更ファイル名・履歴とworker稼働情報を表示。ブラウザで実データ/dry-runとデモの切替、工程クリック、実装中表示を確認。新規dashboardテスト7件でモデル実行中の工程取得、読取りAPI、機密と本文の除外、dry-run候補、部分ログ、heartbeat、ライブstateを変更しないデモを確認。

- Python 3.12.13、macOSで52テスト成功。sandboxの実テストで、専用profileの隣接`.lock`ディレクトリ作成・削除を許可しつつ、外部ファイルの読み書きとshell起動の拒否を確認。
- `examples/config.toml`がgitignoreに巻き込まれて欠落していたため追加し、コピー・設定読込み・doctorの導入経路を修復。
- 専用venvへのeditable install成功。専用stateを使い、本リポジトリのIssue #1をdry-runでELIGIBLEと判定。モデル呼出し・GitHub書込みなし。
- 既存MiniMax Code 0.6.3は`--mode lightweight`非対応。専用ローカル領域に固定版0.6.9を導入し、sandbox内help起動とlightweight対応を確認。`doctor`にもモデルを呼ばない互換性診断を追加。
- 実CLIでprofileロック拒否を再現し、profile隣接の`.lock`だけを許可して解消。専用profileのOAuthログイン成功をCLIで確認。
- 0.6.9の既定モデル`MiniMax-M3.1-Flash-Preview`がmanaged token planの一覧に存在しない問題を確認。既存profileと同じMiniMax-M3を選択し、実モデルの応答を取得。
- 従来の`RLIMIT_FSIZE=2MB`がCLIのSQLite WALやモデルカタログにも適用されるため失敗する問題を確認。stdout/stderrをそれぞれ2MBに制限する収集処理へ変更し、超過時にプロセス群を停止。実sandboxテストで2MB超のprofileファイル書込み、stdout/stderr超過時の拒否を確認。
- productionのMiniMaxアダプターで`docs/ado-glossary.md`の変更JSONを生成し、`validate_changes`で合格。GitHubへは公開せず、生成結果はローカルの非公開stateに保存。実PRのモデル修正ループは未検証。
- GitHub本人確認後、ブラウザでCodeRabbit AppがAll repositoriesに導入済みであることを確認。CodeRabbitの一覧エラーはセッション切れであり、本人の再ログイン後に本リポジトリのInstalled表示とレビュー設定画面の表示に成功。
- 既存[PR #6](https://github.com/SMRI2170/autonomous-dev-orchestrator/pull/6)のhead `89e50d143dc7ea80fa7616cfb2be072c60ccce5c`について、productionのGitHub/evidence/judgeで実サービスの証拠を評価。現在SHAへのCodeRabbit Botの正式APPROVED、`checks / unit`成功、未解決スレッド0、mergeable cleanによりPASS。読み取りのみで、マージは未実施。このPRには別途進められていた追加実装も含まれ、本ローカル作業の変更が公開済みという意味ではない。
- 既存[PR #5](https://github.com/SMRI2170/autonomous-dev-orchestrator/pull/5)はCodeRabbitのCOMMENTEDと未解決スレッド1件によりREPAIR。CIの定義が全コミットでの実行を断定する点への指摘を取得でき、正式承認なしで合格させないことも実証。PR #6のPASSと混同しないこと。
- 自動マージ・常駐サービスは無効のまま。以下の初期検証と区別すること。

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
