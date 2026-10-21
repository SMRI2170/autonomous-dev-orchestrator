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

## Developer配下への実環境セットアップ（2026-10-18追記）

Mac mini M2 / 16GBのDeveloper/autonomous-dev-orchestratorに配置。Homebrew Python 3.12.13でvenvを作成しeditable installを実施。PR #3をCI成功確認後に明示的にsquash mergeし、mainに取り込みました（GitHubのauto-merge機能は有効化していません）。

既存CLI 0.6.3はlightweight非対応のため変更せず、専用の非公開ローカル.state/minimax-runtimeにnpm版0.6.12を導入。ユーザーが専用OAuth認証を完了し、`provider use token-plan`を明示。APIキー設定は既存profileからコピーしていません。

実CLIで初期化時に判明した2点を修正しました。

- proper-lockfileがprofileの隣に作る正確な`.lock`ディレクトリだけを許可。領域外ファイルとshellの拒否は維持。
- OSのファイルサイズ2MB上限が内部SQLite作成を止めていたため、出力を独立して2MBに監視し、OSファイル上限は64MBに変更。期限/出力超過時は子プロセスごと停止。

変更後、実MiniMaxから`docs/pilot-glossary.md`の日本語用語集（325 UTF-8 bytes）を生成し、strict JSONとパス/内容検査を通過しました。これはfixtureではありません。実アカウントの連続修正・CodeRabbit承認・マージは未検証です。

ローカル56テスト成功（Mac固有3件を含む）。ログインユーザーのLaunchAgentをdry-runで起動し、GitHub読み取りのみ・agent_called=false・github_writes=falseを確認。登録対象はこの新規オーケストレーターrepoのみで、既存関連repoは未登録です。

### 実MiniMax → 実PR → 実CIの縦断検証

[Issue #4](https://github.com/SMRI2170/autonomous-dev-orchestrator/issues/4)を新規repo内に作成し、production Engine/Store/GitHub/MiniMaxをそのまま使用しました。専用OAuth Token Planと、Keychainに保存した対象限定fine-grained PATで実行しています。

- model_calls=1、repairs=0。fixture/キャッシュした提案の再生は使っていません。
- MiniMaxが402 bytesの`docs/pilot-glossary.md`を生成し、検査後に[PR #5](https://github.com/SMRI2170/autonomous-dev-orchestrator/pull/5)を作成。
- head SHAは`60296e1d3f5235bab20787ac5ff93cadcb23d3ba`。
- 同じSHAの[標準Runner CI](https://github.com/SMRI2170/autonomous-dev-orchestrator/actions/runs/39088062928)が成功。
- PATでChecks APIとGraphQL reviewThreadsの取得を確認。GitHub UIでChecks権限が出ない場合も、この公開repoの結果は取得できました。
- CodeRabbit承認がまだない段階はREVIEWを維持し、merge/Issue closeは行っていません。

認証情報はリポジトリ・TOML・plist・監査イベントに保存せず、MiniMax用専用profileとmacOS login Keychainにのみ保管しています。

CodeRabbit Appのこの新規repoへの接続もユーザーが完了し、PR #6のレビュー開始を確認しました。既存PR #5には固定の`@coderabbitai review`を一度だけ投稿してレビューを依頼しました。正式承認用のrequest_changes_workflowをrepo設定と配布用サンプルに追加しています。作者が承認コマンドで自身のPRを通す機能は無効です。

追加レビューの指摘を反映し、トークン保存を固定repoからローカル設定の全対象へ一般化しました。readback失敗/タイムアウト時の新規Keychain項目のロールバックをテストしています。Issues/PR書込み権限は読み取りだけで確認済みと扱わず、UI確認とpilot実測が必要なことを明記しました。また、人間への引継ぎではbaseの更新だけで停止せず、base不変条件を自動マージ時に必須とする動作を2テストで確認しています。

最終ローカル検証は61テスト成功。配布用examples/config.tomlが.gitignoreで除外される不具合も修正し、安全な初期値で読み込めるテストを追加しました。
