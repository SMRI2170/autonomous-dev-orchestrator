# 検証記録

検証日: 2026-10-18（Asia/Tokyo）。初期MVPの記録です。

## 2026-10-22 追加検証

- GitHubのlivenessギャップ（[Issue #9](https://github.com/SMRI2170/autonomous-dev-orchestrator/issues/9)）を修正。124テスト成功、`compileall`成功、`ado doctor`は実MiniMax 0.6.12に対して`compatible: true`・`model_called: false`。実Seatbeltのテストも成功。

### 指摘と対処

- `GitHub.api()`が`subprocess.run(..., timeout=45)`でブロックし`pulse`を呼ばないため、ダッシュボードの15秒ウィンドウを超えるリクエストでは稼働中のworkerが最大約45秒 offline と表示されていた。selectorのループを`ado/process.py`へ移し`drain()`と`run()`に分離して両トランスポートで共有。GitHub側は独自の出力上限8,000,000バイト（ミニマixedの2,000,000ではない）を持つため、ページ分割された証拠を拒否しない。
- 呼び出し側が重複して持っていたPopen・期限待ち・プロセス群SIGKILL・回収・ストリームクローズを`run()`が一本化して持つようになった。例外時に呼び出し側が後始末を忘れる経路がなくなるため、MiniMax側の`finally`ブロックは削除できた。
- `test_transport_does_not_leak_failure_body`が`subprocess.run`をpatchしていたが、トランスポートはその関数を使わなくなったため、実`gh`を起動して実ネットワーク応答に対して通過していた。テスト対象をトランスポート自身の接合点に差し替え、`tests/test_process.py`で「出力を伴って失敗するプロセスの出力がBlockedメッセージに入らないこと」を直接検証。

### 実測

- 遅い`gh`（2秒）に対する`worker.json`は、リクエスト中に2.03秒進み、21回の更新が観測された。`pulse`なしの同一条件では、リクエスト中にスタンプが一度も動かない。
- 15秒を超えるリクエストを実際に発生させる検証は未実施。上記は短縮した時間軸での確認であり、15秒ウィンドウの境界そのものは未実施。閾値の判定そのものは`tests/test_dashboard.py`で別途検証済み。

### 未検証

- ブラウザによるキーボード操作とフォーカス復帰の実操作確認は2026-10-21節のとおり未実施。

## 2026-10-21 追加検証

- PR #7のCodeRabbit（CHANGES_REQUESTED）とCodex（P2 3件）の未解決指摘5件を修正し、`compileall -q ado scripts tests`成功、`node --check ado/web/app.js`成功。ダッシュボードはHTTP 200と実データ投影を再確認。
- 追加の指摘4件（`REVIEWING`の工程表示、heartbeatスレッド、待機中のliveness、そのテスト）を修正した上でPR #7をsquash mergeしました（`f0669cb`）。CodeRabbitの最終レビューはCOMMENTED、未解決スレッド0、CI成功、mergeStateStatus CLEAN。main上で116テスト成功、`compileall`と`node --check`成功、`ado doctor`は実MiniMax 0.6.12に対して`compatible: true`・`model_called: false`。

### 指摘と対処

- キーボードフォーカスが再描画で失われる。工程ノードに`data-stage`、ジョブボタンに`dataset.job`を付け、再構築前にフォーカス要素を記録し一致する新要素へ戻す。
- `/api/status`の取得失敗が無言で古い表示が残る。ライブ/demo両方で接続エラーを表示し、未取得時と取得済み時で文言を分ける。
- 相対時刻（`0秒前`など）が変更なしポーリングで更新されない。`.job-age`を毎回の取得成功時に再計算する。
- `ado doctor`のhelpプローブ出力が無制限だった。`generate`と同じ、stdoutとstderrの合計2,000,000バイトの上限とプロセスグループ停止を共有ヘルパー`drain()`に統一し、`while(true)`で書き続けるCLIでも停止することをNode fixtureで実証。
- `docs/setup.md`と`examples/config.toml`で`entry`がソースビルドの`dist/cli.js`固定だった。npm版とソースビルド版の対応表を追加し、`runtime_root`とのずれがdoctorで失敗する旨を明記。
- 履歴ベースの工程上書きに`REVIEWING`が含まれ、レビュアー呼び出し直前の`checks`イベントが勝って、レビューに時間がかかるあいだCIの工程と表示されていた。`REVIEWING`を上書き対象から外し、`Engine.step`は`REVIEWING`を保存する前にreview工程のイベントを記録。
- heartbeatスレッドを残したままだと`Popen(preexec_fn=...)`のforkでCPythonが警告する危険があり、`Popen`が返らない間に子プロセスの停止も期限も機能しない。スレッドを廃止し、長時間呼出しは自前のI/Oループの`pulse`から、`Store.touch()`と`Store.idle()`から生存通知を出す形に変更。
- スレッド廃止で次のポーリングまでの間に`worker.json`が更新されず、稼働中のworkerが15秒後にofflineと表示された。`ado/cli.py`の`time.sleep`を`Store.idle()`に置き換えた。
- `Store.idle()`のテストが待ち終わった後に一度だけ読んでおり、待ちの最後にだけheartbeatする退行でも通っていた。待ちの終わりから十分前の観測値に対して検証する形に修正し、終端時のみの`touch()`に戻すと失敗することを確認。

### 未検証

- ブラウザによるキーボード操作とフォーカス復帰の実操作確認は未実施です。JSにはCIの自動テストがなく、PythonのテストはマーカーとHTTP応答までを確認します。
- 上記helper`drain()`は`selectors.DefaultSelector`でstdin/stdout/stderrを同一スレッドがまとめて読み書きし、期限超過と合計出力上限の超過を検出します。プロセス群へのSIGKILLと回収は`drain()`ではなく、それを呼び出す`doctor`と`generate`の各`finally`が行います。readerスレッドは存在しないため、旧形式のように停止シグナルが届かない経路はありません。以前の実装（readerスレッドがSIGKILLを送り主スレッドの`finally`が回収を担保する形式）では15秒タイムアウトが1回だけ観測されましたが、現行形式では10回連続で再現せず、原因は特定できていません。旧形式での初回観測の記録であり、現行実装の不具合としては再現していません。
- 上記のlivenessはMiniMaxの呼出しのみを対象としています。`GitHub.api()`は`subprocess.run(..., timeout=45)`で、待機中は`pulse`を呼ばないため、GitHubリクエストが15秒を超えると稼働中のworkerがofflineと表示されることがあります（最大で約45秒）。2026-10-22に修正（上記参照）。

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
