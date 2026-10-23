# 検証記録

検証日: 2026-10-18（Asia/Tokyo）。初期MVPの記録です。

## 2026-10-23 実サービスでのループ検証（Issue #12 / #14 / #16 / #18、PR #13 / #15 / #17 / #19）

`dry_run = false` で実サービスに接続し、Issue #18 から実PRの修正ループを最後まで一周させました。fixtureは使っていません。**Issue → 実MiniMax生成 → 実commit → 実branch → 実PR → CI → MiniMaxレビュー → 外部レビュー → PASS → AWAITING_HUMAN（人手マージ待ち）** まで到達し、修正は0回でした。自動マージは行っていません。

- 実MiniMaxが 38,813 bytes のプロンプトで `docs/operator-runbook.md` を生成し、`validate_changes` を通って実commit `ea4b07cc`・実branch `ado/issue-12`・実PR #13 が作成されました。
- CI成功後、`judge()` がSHAに紐づく証拠（check-run / review / GraphQL review thread）を評価しREPAIRを返しました。指摘はADO自身のGraphQLクエリで取得できたことを確認しています。
- REPAIRで再生成し、新head `86c30852f1ce` でCI成功まで到達しました。
- `auto_merge` はグローバルとリポジトリの両方で `false` のままです。MiniMaxの自己reviewが自動マージを許可しないことはコードで保証されています。
- heartbeatは実MiniMax呼出し中に `worker.json` が48.6秒進み、経過0.1秒でした。

### 完全ループの実サービス動作（Issue #18 / PR #19）

- Issue #18 から実MiniMaxが `docs/glossary.md` と `README.md` を生成し、1回目の実装で受入条件をすべて満たしました（9語・引用先・1文制約・dry-runのローカル書き込み・PRの自動マージと低リスク条件・review threadと未解決スレッドの区別・READMEリンク）。機械的に検証して確認しています。
- 実commit `097a58ccb`・実branch `ado/issue-18`・実PR #19 を作成し、`checks / unit` はsuccess。
- MiniMaxレビューは `APPROVE`（指摘0件）。head SHA と変更digestに束縛された記録として保存されています。
- CodeRabbit も `APPROVED`。未解決スレッド0、PRの状態は `CLEAN` になりました。
- `judge()` がPASSを返し、ジョブは `AWAITING_HUMAN`（人手マージ待ち）に入りました。自動マージは行っていません。
- ジョブ5の記録: `repairs=0`、`calls=1`、レビュー `APPROVE`（sha `097a58ccb`）。修正0回で到達したのは、Issue #16 の4回の修復で仕様を詰めたためです。

### MiniMaxレビューの実サービス動作（Issue #16 / PR #17）

- 実MiniMaxレビューが 처음으로実サービス上で走りました。`5d50e9429` に対する判定は `APPROVE`（指摘0件）で、head SHA と変更内容のdigestの両方に束縛された記録として保存されました。
- Codexの指摘2件（check-runを検査実行の記録と定義する／dry-runでもローカル状態は記録する）を頭から直した `d3cd43b5a` に対し、同じMiniMaxレビューが `REQUEST_CHANGES` を返し、`dry-run` の説明が1文に収まっていないという受入条件違反を指摘しました。指摘は次の修復入力としてモデルに渡されます。
- レビューは2回目で200秒近くかかりました。heartbeatは露出一らず、呼び出しは正常に完了しました。応答時間の分布は未計測です。

### 実サービスで見つかった不具合（6件）

1. **入力検査と出力検査が同一だった。** 本リポジトリ自身の `docs/verification.md` が `subprocess.run` という語を含むため、コンテキスト読み取りが拒否してジョブを開始できませんでした。入力側を `inspect_source`（機密のみ拒否）に分離し、出力側の `inspect_text` はより厳しいまま残しました。回帰テスト2件を追加し、3つの変異（入力側を `inspect_text` に戻す・出力側から危険語を削る・入力側から機密検査を削る）で検査が機能することを確認しています。
2. **Issue #11 の受入条件が満たせなかった。** 条件が `engine.py` の全reason文字列を要求していますが、`engine.py` は `allowed_paths` にも `context_paths` にもないためモデルから見えません。モデルは `{"files":{}}` を返し、これは `Blocked("Empty/oversized change set")` として報告されました。#11 に説明コメントを付けて閉じ、`docs/architecture.md` の状態表だけを根拠にした #12 を作り直しました。
3. **完了した呼び出しの不正出力が終端だった。** strict JSON に違反した出力（2件目）が `resume="BUILDING"` により「中断」扱いとなり、ジョブは二度と復帰せず、公開済みのPR #13 の作業も捨てられていました。`UnusableOutput(Blocked)` を導入し「呼び出しは完了したが使えない出力」と「中断」を区別しました。中断は従来どおり再生禁止のまま、完了した出力だけが修復予算を1回消費して自動再試行します。
4. **出力の再試行でレビュー指摘を落としていた。** 上記3の自動再試行は、理由だけをモデルに渡し、元のレビュー指摘を捨てていました。実ジョブ3で、修正すべき内容をモデルが知らされずに再生成されることを確認しました。指摘を先頭に、拒否理由を末尾に足して渡し直します。
5. **旧headの指摘が修正入力から消えていた。** `judge()` は未解決スレッドが現在のSHA以外でraisedされていると `unresolved old review thread` としてWAIT扱いだけにし、修復入力には入れていませんでした。実ジョブ3で、Codex/CodeRabbitが最初のheadに付けた指摘がモデルに一度も渡らず、修正が永久に進まないことを確認しました。古い指摘も修復入力に含め、PASSは引き続き人がスレッドを解決するまで出さないようにしました。
6. **MiniMaxレビューが一度も実行されていなかった。** レビュー開始の条件に `mergeable_state == "clean"` がありました。本リポジトリの ruleset `protect-main` は `required_review_thread_resolution` を要求するため、スレッドが1件でも残ったPRは `blocked` になり、CodeRabbitのstatusが実行中だと `unstable` になります。結果としてレビューは永久に起動せず、実ジョブ1〜4のどのデータにも `minimax_review` が記録されていませんでした。条件を `reviewable()` に分け、draft・conflicted・behind・hook保留だけを出典として、第三者ボットのstatus待ちや未解決スレッドではレビューを止めないようにしました。あわせて、レビューは「証拠が完全にきれい」のときではなく「headがレビュー可能でCIがgreen」のときに実行するようにし、返ってきた指摘は次の修復入力に合流させます。

### 修正ループの到達点と残件

- Issue #12 のジョブは修復上限（3）に達し `resume="BUILDING"` でBLOCKEDになりました。PR #13 は head `86c30852f1ce` でopen のまま、Codex/CodeRabbit の未解決スレッドが残っています。自動マージは行いません。
- Issue #14 のジョブ（PR #15）も修復上限に達し、head `3865ec369` でBLOCKEDです。3回目の修復で引用元の誤りはすべて直りましたが、Codex/CodeRabbitの指摘のうち「PRの略語を展開する」「人間確認が条件付きである」「項目間に空行入れる」は、5の不具合でモデルに届いていませんでした。
- `judge()` の不具合を直した後も、ジョブ3は修復予算（3回）と呼出し予算（4回）を使い切っているため再実行できません。同じ内容を明示的に書いた新Issue #16を作り、予算のある新しいジョブで作り直します。
- Issue #16 のジョブ（PR #17）は4回の修復で head `5d50e9429` → `d3cd43b5a` → `f8d3151bc` → `b6c238eed` と進み、MiniMaxレビューは `APPROVE` のあと2回目で `REQUEST_CHANGES`（dry-runの説明が1文に収まっていない）を返し、その指摘は次の修復入力に入りました。Codexの2件（dry-runでもローカルに書き込む／check-runは検査実行の記録）も直りましたが、Codexが次のheadに付けた2件（自動マージが有効でも低リスク条件を満たさない場合は人間確認が要る／review threadと未解決スレッドの区別）は残りました。修復上限（3）に達してBLOCKEDです。
- 残った2件は未修正なので、解決済と偽ってスレッドを閉じることはしません。製品が設計した復旧手順どおり、2件を反映した新Issue #18 を作り、予算のある新しいジョブで作り直します。
- `Store.retry()` の修復予算が `3` をハードコードしていて設定値（`max_repairs`）と食い違っていました。設定が4のときに許可される1回が実際には到達できませんでした。ストアを設定値に従うよう直しています。
- CIのcheck-runが `in_progress` のまま完了せず `timeout-minutes` を超えることがありました（全11ステップがsuccessで `Complete job` まで到達）。`gh run rerun` で解消し、`judge()` の最新check-run優先の設計が実データで機能しました。原因はまだ特定できていません。
- `docs/verification.md` の「LaunchAgentのOS登録・起動はしていません」という記述は、2026-10-23時点の `local.ado.worker`（約48時間稼働、`dry_run = true`、`auto_merge = false`、対象は `SMRI2170/animal-shadow-match`）と一致しません。この記述の更新は未実施です。

### 運用者が介入した操作（PR #15 / PR #17）

- 旧head `806bbe086` の未解決スレッド11件を（現head `0e2ecc3f` に同じ内容を再投稿したうえで）解決しました。判定規則は変えていません。
- 1件（READMEから用語集へリンクする指摘）は、Issue #14 自身の受入条件「他のファイルを変更しない」と `allowed_paths` の両方が README の変更を禁じているため実装不能です。新Issue #16 では `README.md` を `allowed_paths` に追加して、この指摘を実装可能な形に変えました。
- PR #17 では、検証済みの2件（dry-runのローカル書き込み、check-runの定義が head `b6c238eed` で直っていることを確認）を返信付きで解決しました。未修正の2件は解決していません。
- スレッドを人が解決しない限り `judge()` はPASSを返さないため、自動化の上限は「良いPRを出し、人を待つ」です。これは fail-closed の設計どおりの挙動です。

### 未解決・既知の限界

- `{"files":{}}` というモデルの明示的な拒否が、不正なJSONと同じ `Empty/oversized change set` として報告されます。拒否を別の終了型として切り出す対策は未実施です。
- `MiniMaxReviewer.review()` の出力不備も `REVIEWING` に残って再生不能です。無制限に再試行すると1ポーリングごとに新しいモデル呼出しが発生し、利用量に上限がないため意図的に未修正です。
- 15秒を超えるGitHubリクエストでのダッシュボード表示は依然として未実施です（上記2026-10-22節を参照）。
- `snapshot()` は保護パス判定を CONTEXT にも適用するため、`docs/setup.md` と `docs/security.md` はモデルコンテキストから永久に除外されます。後者は意図どおりとして、前者は偽陽性です（ファイル名に `setup` が含まれるため）。Issue #14 の実コンテキストは5ファイルでした。
- 未解決の指摘がすべて旧SHAのスレッドだけだと、修正すべきものが無いので実装は「変更なし」になります。実装には「変更なし」を示す手段がありませんので、1回だけ実装させて同内容を確認し、以降はそのheadでは実装し直させないようにしました。その間、人はスレッドを解決するまで待つことになります。
- ブラウザによるキーボード操作とフォーカス復帰の実操作確認は引き続き未実施です。

### テスト

- `python -m unittest discover` は136件成功。新規の回帰テスト（入力/出力の検査分離、完了出力の再試行、中断の再生禁止、出力再試行での指摘保持、旧headの指摘の受け渡し、変更なし時の待機）はそれぞれ変異で検査が機能することを確認済みです。

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
