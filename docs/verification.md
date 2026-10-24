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
- 2026-10-24、PR #19 のmergeを検知してジョブ5は `DONE` になり、Issue #18 は `COMPLETED` で閉じられました。この再開は、そのジョブが記録した `allowed_paths` と同じ設定（`docs/**/*.md` と `README.md`）で行いました。設定が変わると `Repository policy changed during job` で止まるためです。

### MiniMaxレビューの実サービス動作（Issue #16 / PR #17）

- 実MiniMaxレビューが 처음으로実サービス上で走りました。`5d50e9429` に対する判定は `APPROVE`（指摘0件）で、head SHA と変更内容のdigestの両方に束縛された記録として保存されました。
- Codexの指摘2件（check-runを検査実行の記録と定義する／dry-runでもローカル状態は記録する）を頭から直した `d3cd43b5a` に対し、同じMiniMaxレビューが `REQUEST_CHANGES` を返し、`dry-run` の説明が1文に収まっていないという受入条件違反を指摘しました。指摘は次の修復入力としてモデルに渡されます。
- レビューは2回目で200秒近くかかりました。heartbeatは露出一らず、呼び出しは正常に完了しました。応答時間の分布は未計測です。

### 実サービスで見つかった不具合（8件）

1. **入力検査と出力検査が同一だった。** 本リポジトリ自身の `docs/verification.md` が `subprocess.run` という語を含むため、コンテキスト読み取りが拒否してジョブを開始できませんでした。入力側を `inspect_source`（機密のみ拒否）に分離し、出力側の `inspect_text` はより厳しいまま残しました。回帰テスト2件を追加し、3つの変異（入力側を `inspect_text` に戻す・出力側から危険語を削る・入力側から機密検査を削る）で検査が機能することを確認しています。
2. **Issue #11 の受入条件が満たせなかった。** 条件が `engine.py` の全reason文字列を要求していますが、`engine.py` は `allowed_paths` にも `context_paths` にもないためモデルから見えません。モデルは `{"files":{}}` を返し、これは `Blocked("Empty/oversized change set")` として報告されました。#11 に説明コメントを付けて閉じ、`docs/architecture.md` の状態表だけを根拠にした #12 を作り直しました。
3. **完了した呼び出しの不正出力が終端だった。** strict JSON に違反した出力（2件目）が `resume="BUILDING"` により「中断」扱いとなり、ジョブは二度と復帰せず、公開済みのPR #13 の作業も捨てられていました。`UnusableOutput(Blocked)` を導入し「呼び出しは完了したが使えない出力」と「中断」を区別しました。中断は従来どおり再生禁止のまま、完了した出力だけが修復予算を1回消費して自動再試行します。
4. **出力の再試行でレビュー指摘を落としていた。** 上記3の自動再試行は、理由だけをモデルに渡し、元のレビュー指摘を捨てていました。実ジョブ3で、修正すべき内容をモデルが知らされずに再生成されることを確認しました。指摘を先頭に、拒否理由を末尾に足して渡し直します。
5. **旧headの指摘が修正入力から消えていた。** `judge()` は未解決スレッドが現在のSHA以外でraisedされていると `unresolved old review thread` としてWAIT扱いだけにし、修復入力には入れていませんでした。実ジョブ3で、Codex/CodeRabbitが最初のheadに付けた指摘がモデルに一度も渡らず、修正が永久に進まないことを確認しました。古い指摘も修復入力に含め、PASSは引き続き人がスレッドを解決するまで出さないようにしました。
6. **MiniMaxレビューが一度も実行されていなかった。** レビュー開始の条件に `mergeable_state == "clean"` がありました。本リポジトリの ruleset `protect-main` は `required_review_thread_resolution` を要求するため、スレッドが1件でも残ったPRは `blocked` になり、CodeRabbitのstatusが実行中だと `unstable` になります。結果としてレビューは永久に起動せず、実ジョブ1〜4のどのデータにも `minimax_review` が記録されていませんでした。条件を `reviewable()` に分け、draft・conflicted・behind・hook保留だけを出典として、第三者ボットのstatus待ちや未解決スレッドではレビューを止めないようにしました。あわせて、レビューは「証拠が完全にきれい」のときではなく「headがレビュー可能でCIがgreen」のときに実行するようにし、返ってきた指摘は次の修復入力に合流させます。

8. **ファイル名に `setup` が含まれるだけで保護ファイルに判定されていた。** `PROTECTED` のキーワードに素の `setup` が入っていたため、`docs/setup.md` が保護ファイル扱いになり、運用者が `allowed_paths` に `docs/**/*.md` を指定していてもモデルコンテキストから永久に外れていました。キーワードを `setup\.(?:py|cfg)` に絞りました。`setup.py` と `setup.cfg` は保護されたままです。

### 修正Pull Requestのレビュー（PR #20）

上の修正をまとめた PR #20 に対する外部レビューで、Codex 4件とCodeRabbit 4件（後にCodex 3件、CodeRabbit 1件が追加）が見つかりました。いずれも実際に再現する不具合で、このPull Request自体に直入金っています。

- 解決済みのheadで待つたびにモデル呼出し予算を消費していた（`calls` が減らないので数ポーリングで `Model call budget exhausted` になり、`retry()` でも戻らない）。判定を予算確保より前に移動。
- 不正出力の自動再試行が、再試行の事前処理中に失敗するとジョブを `BUILDING` に残し、`retry()` が「中断した呼出し」として拒否した。完了した呼出しのあとで再試行可能な工程に復帰するように。
- ストアが修復予算を固定値3としか比較しておらず、修復予算を使わない失敗（publishや証拠取得の一時失敗）が再試行不能になっていた。予算の判断はエンジン側へ移動。
- 修復上限に達したあと、旧headの指摘だけが残ると `REPAIR` の判定でジョブが即座にBLOCKEDになり、人がスレッドを解決する余地がなかった。旧headの指摘だけなら待機する。
- 解決済みのheadと結びつけたのがheadのSHAだけだったため、同じheadに後から届いた指摘がモデルに渡らず、ジョブが期限まで待った。解決時の指摘のdigestも 함께記録。
- 「変更なし」と判定する前に変更内容そのものを検査しておらず、値がnullの不正な応答が「変更なし」と判定されてheadが解決済みになっていた。パスと値の型を先に検査。
- GraphQLの `commit` がnullのスレッド（force pushで参照先が消えた場合）で `AttributeError` になり `judge()` が落ちていた。nullを空辞書として扱う。
- 再開したジョブの予算がリセットされ、課金済みの呼出しを数えずに予算の枠を超える可能性があった。再開時にカウンタを保持。

### PR #20 の再レビュー（第2〜4round）

PR #20 には外部レビューが複数回血脂する。ここに書いたものは実際に再現する不具合で、すべてこのPull Requestに直入金しています。

- **解決済みのheadでも予算の判定が先に来ていた。** 解決済みは呼出しを消費しないので、判定は予算確保より前に置きます。予算を使い切った（`calls` が上限）ジョブが、人がスレッドを解決するのを待つべきところで `Model call budget exhausted` によりBLOCKEDしていました。`retry()` でも戻りません。
- **解決済みの記録が指摘全体のdigestだった。** 人が古いheadのスレッドを1本だけ解決すると残りの指摘が別のものになり、解決済みの判定が崩れて呼出しが1回ずつ消費されていました（4本のスレッドなら `calls` が2→4と進み、1本残ったまま予算切れ）。解決済みの記録は指摘を1件ずつ保持し、現在の指摘がその集合の部分集合なら解決済みと判定します。消えた指摘は新しい指摘ではありません。
- **UTF-8に符号化できない値でジョブが復帰不能になっていた。** `validate_changes()` がバイト予算を計算するときに `UnicodeEncodeError` が送出され、これは `Blocked` ではないため投稿後の窓から漏れていました。ジョブは `BUILDING` に残り、再試行は「中断した呼出し」として恒久的に拒否されます。完了した応答なので `UnusableOutput` に正規化し、修復予算1回で復帰させます。
- **PRが無い状態でも「変更なし」を解決済みとして扱っていた。** 応答が既存ファイルをそのまま返した場合、PRを作る前の応答でも解決済み扱いになり、`REVIEW` に着地して次のポーリングが `data["pr"]` を参照して落ちました。PRの存在を要求し、PRの無い段階の「変更なし」は拒否として扱います。
- **拒否理由が未解決の指摘として数えられていた。** 出力の拒否理由（出力理由）はモデルのための情報ですが、指摘と同じリストに入れていたため `outstanding()` を真にし、解決済みのdigestも壊していました。指摘と拒否理由を分け、前者を `outstanding()` と解決済みの照合に使い、後者をモデルへの入力にだけ足します。
- **同じ文言の別スレッドが解決済みと誤認されていた。** 解決済みの照合は指摘の本文とcommitだけでした。別の行についての同じ文言の別スレッドは同一とみなされ、モデルに一度も渡らないまま消えていました。GraphQLのレビュー証拠にスレッドIDを載せ、IDで照合します。

上記6件はそれぞれ回帰テストを足し、変異（判定順序の入れ替え・部分集合照合の等価照合化・型検査の削除・項目記録の削除・IDの除去）で検査が機能することを確認済みです。

### 修正ループの到達点と残件

- Issue #12 のジョブは修復上限（3）に達し `resume="BUILDING"` でBLOCKEDになりました。PR #13 は head `86c30852f1ce` でopen のまま、Codex/CodeRabbit の未解決スレッドが残っています。自動マージは行いません。
- Issue #14 のジョブ（PR #15）も修復上限に達し、head `3865ec369` でBLOCKEDです。3回目の修復で引用元の誤りはすべて直りましたが、Codex/CodeRabbitの指摘のうち「PRの略語を展開する」「人間確認が条件付きである」「項目間に空行入れる」は、5の不具合でモデルに届いていませんでした。
- `judge()` の不具合を直した後も、ジョブ3は修復予算（3回）と呼出し予算（4回）を使い切っているため再実行できません。同じ内容を明示的に書いた新Issue #16を作り、予算のある新しいジョブで作り直します。
- Issue #16 のジョブ（PR #17）は3回の修復で head `5d50e9429` → `d3cd43b5a` → `f8d3151bc` → `b6c238eed` と進み、MiniMaxレビューは `APPROVE` のあと2回目で `REQUEST_CHANGES`（dry-runの説明が1文に収まっていない）を返し、その指摘は次の修復入力に入りました。Codexの2件（dry-runでもローカルに書き込む／check-runは検査実行の記録）も直りましたが、Codexが次のheadに付けた2件（自動マージが有効でも低リスク条件を満たさない場合は人間確認が要る／review threadと未解決スレッドの区別）は残りました。修復上限（3）に達してBLOCKEDです。
- 残った2件は未修正なので、解決済と偽ってスレッドを閉じることはしません。製品が設計した復旧手順どおり、2件を反映した新Issue #18 を作り、予算のある新しいジョブで作り直します。
- `Store.retry()` が修復予算をストア内で固定値3としか比較しておらず、設定値（`max_repairs`、上限0〜3）と食い違っていました。上限を3未満にした設定では、エンジンなら許す再試行をストアが拒否していました。予算の判断はエンジン側に一本化し、ストアは予算を持ちません。
- CIのcheck-runが `in_progress` のまま完了せず `timeout-minutes` を超えることがありました（全11ステップがsuccessで `Complete job` まで到達）。`gh run rerun` で解消し、`judge()` の最新check-run優先の設計が実データで機能しました。原因はまだ特定できていません。
- `docs/verification.md` の2026-10-18時点の「LaunchAgentのOS登録・起動はしていません」という記述は、2026-10-24時点の `local.ado.worker`（OSに登録済みで稼働中、`dry_run = true`、`auto_merge = false`、対象は `SMRI2170/animal-shadow-match`）と一致しません。本文末尾の2026-10-24付の訂正で、実際の登録状態、起動コマンド、設定内容を追記済みです。元のジョブ（8）は出力検査の緩和 전에予算を使い切って再開不能なので、[Issue #26](https://github.com/SMRI2170/autonomous-dev-orchestrator/issues/26) として作り直しました。実装待ちです。

### 運用者が介入した操作（PR #15 / PR #17）

- 旧head `806bbe086` の未解決スレッド11件を（現head `0e2ecc3f` に同じ内容を再投稿したうえで）解決しました。判定規則は変えていません。
- 1件（READMEから用語集へリンクする指摘）は、Issue #14 自身の受入条件「他のファイルを変更しない」と `allowed_paths` の両方が README の変更を禁じているため実装不能です。新Issue #16 では `README.md` を `allowed_paths` に追加して、この指摘を実装可能な形に変えました。
- PR #17 では、検証済みの2件（dry-runのローカル書き込み、check-runの定義が head `b6c238eed` で直っていることを確認）を返信付きで解決しました。未修正の2件は解決していません。
- スレッドを人が解決しない限り `judge()` はPASSを返さないため、自動化の上限は「良いPRを出し、人を待つ」です。これは fail-closed の設計どおりの挙動です。

### 未解決・既知の限界

- `{"files":{}}` というモデルの明示的な拒否が、不正なJSONと同じ `Empty/oversized change set` として報告されます。2026-10-24 に [Issue #25](https://github.com/SMRI2170/autonomous-dev-orchestrator/issues/25) として起票し、[Issue #34](https://github.com/SMRI2170/autonomous-dev-orchestrator/issues/34) で修正して PR #35 としてマージ済みです。
- `MiniMaxReviewer.review()` の出力不備が `REVIEWING` に残って再生不能だったのは、Issue #24 の修正前の状態でした。2026-10-24 に [Issue #24](https://github.com/SMRI2170/autonomous-dev-orchestrator/issues/24) として起票しました（1回だけ再試行する方針）。無制限に再試行すると1ポーリングごとに新しいモデル呼出しが発生し、利用量に上限がないため、再試行は1回に 제한します。
- 15秒を超えるGitHubリクエストでのダッシュボード表示は、2026-10-25に実環境で確認しました（上記2026-10-22節の実装が効いていることを含む）。`gh` 呼出しを20秒遅延させる擬似スクリプトをPATHの先頭に置いて実際に15秒を超える要求を発生させ、45秒間の観測で `worker.json` の時刻が連続的に進み（経過0〜3秒）、ダッシュボードの表示は常に `worker応答あり` でした。
- `snapshot()` は保護パス判定を CONTEXT にも適用するため、`docs/security.md` はモデルコンテキストから永久に除外されます。これは意図どおりです。
- 未解決の指摘がすべて旧SHAのスレッドだけだと、修正すべきものが無いので実装は「変更なし」になります。実装には「変更なし」を示す手段がありませんので、1回だけ実装させて同内容を確認し、以降はそのheadでは実装し直させないようにしました。その間、人はスレッドを解決するまで待つことになります。
- ブラウザによるキーボード操作とフォーカス復帰の実操作確認は、2026-10-25に実ブラウザで実施しました。工程ノードとジョブボタンの双方で Tab と Enter による操作が有効で、3秒ごとの自動更新でDOMが作り直された後もフォーカスした要素が復元され、Enter が同じ対象を再び作動させることを確認しました。

### テスト

- `python -m unittest discover` は159件成功（PR #20 merge時点）。新規の回帰テスト（入力/出力の検査分離、完了出力の再試行、中断の再生禁止、出力再試行での指摘保持、旧headの指摘の受け渡し、変更なし時の待機）はそれぞれ変異で検査が機能することを確認済みです。

## 2026-10-24 MiniMax M3 による実装ループ（Issue #24 / #25 / #26、PR #28）

PR #20 を main に merge したあと（merge commit `82cb7c25`）、書き込み対象を実装ファイル（`ado/*.py` と `tests/test_issue*.py`）まで広げ、Issue #24 / #25 / #26 を起票しました。MiniMax M3 が実装したのは Issue #24 だけで、PR #28 としてマージ済みです。Issue #24 ではモデルが実装し、Codex / CodeRabbit と製品自身の MiniMax レビューがゲートし、人が merge する loop を通しました。自動マージは使っていません。Issue #25 のジョブ（10）は実モデルの呼出しを1回行い PR #29 を作りましたが、PR を merge しないまま閉じました。Issue #26 のジョブ（11）は PR #32 を作りましたが、修正のための再試行が MiniMax の実行期限に達して `BUILDING` に残って再開不能になり、PR #32 も閉じています。

### 実サービスで見つかった不具合（2件）

1. **コンテキスト予算が、書き込み対象がコードのリポジトリを止めていた。** コンテキストは、`allowed_paths` に一致する編集可能ファイルと、`context_paths` に一致する読み取り専用ファイルの合計です。書き込み対象をコードに広げると、このリポジトリの編集可能ファイルは 168,479 バイトで、150,000 バイトの既定予算を超えてしまいます。Issue #24 / #25 のジョブ（9 / 10）は、モデル呼出しを1度も行わないまま `Context budget exceeded` で止まりました。既定予算を 300,000 バイトに上げ、メッセージに数値と対処方法（`allowed_paths` / `context_paths` を狭めるか `max_context_bytes` を上げる）を含むようにしました（[PR #27](https://github.com/SMRI2170/autonomous-dev-orchestrator/pull/27)）。
2. **main を動かすと、進行中のジョブのPRが「behind」で止まる。** ジョブのPRは生成時の main を起点に作られ、`mergeable_state` が `behind` になるとレビュー判定が `clean` にならずWAITのままです。自動の rebase は実装していません。PRのheadはSHAに束縛した証跡なので、PRのheadを変えるとジョブは `PR identity/head changed externally` で止まります。運用者は、ジョブが `AWAITING_HUMAN` で人手を待っている間に main を動かさない順序を守る必要があります。

### Issue #24 / PR #28（MiniMax M3 の実装）

- MiniMax M3 が `ado/minimax_review.py`、`ado/engine.py`、`tests/test_issue24_reviewer.py` の3ファイルを生成しました（Issue の指定どおり、他のファイルには触れていません）。実プロンプトは 179,373 バイト、モデル呼出しは約5分でした。
- 製品自身の MiniMax レビューが `APPROVE`（指摘0件、head SHA と変更digestに束縛）。
- 新規テスト5件を含む 165 テストが成功。再試行しない・2回目は人が扱える状態Interrupted は再試行しない・拒否した出力は UnusableOutput になる・CLI失敗は素のBlocked の4点を変異で検査し、すべて殺せました。
- [PR #28](https://github.com/SMRI2170/autonomous-dev-orchestrator/pull/28) は CI 成功・未解決スレッド0・レビュー通過のうえで merge 済み（merge commit `d971ad44`）。Issue #24 は `COMPLETED` で閉じ、ジョブ9は `DONE` です。

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
## 2026-10-24 LaunchAgent状態の訂正

2026-10-18時点の記録は削除せず残します。2026-10-24時点の読み取り確認では、`local.ado.worker` はOSに登録済みで、稼働中でした。既存の登録の停止や再登録は行わず、状態の読み取りとこの記録の更新だけを行いました。

2026-10-24 23時ごろ、同じ手順でライブ用の2本目 `local.ado.worker.live` を追加登録しました。既存の `local.ado.worker` は停止も再登録も行っていません。2本は別の `state_dir` を使い、同じ `state_dir` を共有する二重起動はしません。

- plist: `~/Library/LaunchAgents/local.ado.worker.plist`（pilot）と `~/Library/LaunchAgents/local.ado.worker.live.plist`（live）
- ラベル: `local.ado.worker`（pilot）と `local.ado.worker.live`（live）。どちらも `launchctl list` に表示され、実行中です
- 起動コマンド: 各置き場の `.venv/bin/python` + `scripts/launch.py` + 同じ置き場の `config.toml`
- 参照する設定:
  - pilot: `dry_run = true`、`auto_merge = false`、対象は `SMRI2170/animal-shadow-match`、`allowed_paths = ["docs/ADO_PILOT_GLOSSARY_JA.md"]`
  - live: `dry_run = false`、`auto_merge = false`、対象は `SMRI2170/autonomous-dev-orchestrator`
- GitHubトークンは plist に保存せず、`scripts/launch.py` が macOS login Keychain（service `ado.github-token`、account `ado`）から読み出してプロセス環境変数として渡します。
- live 側は `KeepAlive` で、異常終了すると60秒の抑制時間を置いて再起動します。ミニMaxの呼出し中に落ちた場合、そのジョブは `BUILDING` のまま再開不能になり、人が新しいIssueで作り直す運用になります。

## 2026-10-25 完了に向けた記録

この節は、2026-10-25時点の常駐・実行環境、未検証だった項目の実施結果、バックログの整理、そして残った既知の限界を記録します。

### 常駐と実行環境

- ライブ用の LaunchAgent `local.ado.worker.live` を追加登録しました。pilot の `local.ado.worker`（`dry_run = true`、対象 `SMRI2170/animal-shadow-match`）は停止も再登録も行っていません。
- ライブ側が使う MiniMax Code の runtime を 0.6.12 に整合しました（それまでは 0.6.9）。同じ runtime で `mcode exec --mode lightweight` の実呼び出しが6.4秒で成功し、認証済みプロファイルで応答が返ることを確認しました。認証は専用の `data_dir` にあり、plist にも環境変数にもトークンを保存しません。
- `ado dashboard` の既定ポート 8765 は、このMacの他のローカルアプリが既に使っていました。ADO 側は別ポートで起動してください。ポートが埋まっている場合は `Address already in use` で起動に失敗します。

### 未検証だった2項目（2026-10-25に実施）

- **ブラウザによるキーボード操作とフォーカス復帰**: 実ブラウザで、工程ノードとジョブボタンの双方について Tab と Enter による操作が有効であることを確認しました。3秒ごとの自動更新でDOMが作り直された後も、フォーカスしていた要素が復元され、Enter が同じ対象を再び作動させます（工程は `validate` から `publish` へ、ジョブは Issue #37 から Issue #34 へ遷移して確認）。
- **15秒を超えるGitHubリクエストでのダッシュボード表示**: `gh` を20秒遅延させる擬似スクリプトをPATHの先頭に置いて、実際に15秒を超えるGitHub要求を発生させました。45秒間の観測で `worker.json` の時刻は連続的に進み（経過0〜3秒）、ダッシュボードの表示は常に `worker応答あり` でした。2026-10-22節の修正が実環境で効いていることの確認になります。

### バックログの整理

- 重複していた Issue 11件（#12 / #14 / #16 / #21 / #22 / #23 / #25 / #26 / #31 / #33 / #37）を、置換先のIssueまたはPRを明記したうえでクローズしました。実装済みのものは既存の PR（#19 / #28 / #35）に対応します。
- 放置されていた PR #13 / #15 / #17 も、代替のPRまたは作り直しのIssueを明記してクローズしました。

### Issue #38（検証記録の LaunchAgent 記述の訂正）の経緯

- Issue #37 のジョブ（15）は、MiniMax CLI の呼出し中にworkerが終了して `BUILDING` に残り、再開不能でした。`ado retry 15` は「中断したモデル呼出しの再生は禁止」で拒否されます。予算のある新しいジョブとして Issue #38 を作り直しました。
- #38 のジョブ（16）は、モデル生成 → 検査 → 実commit → 実branch → 実PR #40 → CI成功 → MiniMaxレビュー `APPROVE` まで進み、CodeRabbitの3件の指摘を反映する3回目の修復で MiniMax CLI の呼出しが失敗して `BLOCKED` になりました（修復予算3・呼出し予算4を使い切り）。
- PR #40 は閉じ、CodeRabbitの3件と LaunchAgent 記述の訂正は運用者が直接反映しました。

### 既知の限界（2026-10-25に追記）

- **既存の大きなファイルへの部分修正は、モデルに「置換後の全文」を返させる契約では安定しません。** #38 のジョブ（16）が返した `docs/verification.md`（約40KB）は、指定した1行の置換と末尾14行の追記に加えて、既存20行が書き換えられていました（合計41行の意図しない変更で、言い換え・誤字・句読点の混入を含みます）。同じ契約は新規ファイルにも表れます。#39 のジョブ（17）では3回目の修復で、それまでに反映済みの修正上限の説明が消失し、語句の混入が1件起こりました。新規ファイルの作成や小さな局所修正では同じ問題は起きていません。大きな既存ファイルを安全に直すには、差分だけを返す契約への変更が必要です。
- `ado dashboard` の既定ポート 8765 は他アプリと衝突し得ます。
### operator-runbook の実装（Issue #39 / PR #42）

- 予算を使い切ったジョブ（2）は作り直しになり、Issue #12 と同じ内容の Issue #39 を起票しました。
- ジョブ（17）は実MiniMaxが `docs/operator-runbook.md` を新規作成し（生成78秒、検証1回目で合格）、実branch `ado/issue-39` と実PR #42 を作成しました。CI `checks / unit` は成功し、製品自身のMiniMaxレビューは `APPROVE`（指摘0件、head SHAと変更digestに紐づく記録）でした。
- Codex と CodeRabbit が計6件の指摘を出しました（修正上限の説明、修正上限到達時の再試行の禁止、中断理由の文字列一致、dry-runの認証条件、語句の自然性、呼出し予算の明記）。ADO はそれらを次の修復入力に含む3回の修復ループで反映し、各修復のたびに CI とレビューをやり直しました。3回目の修復で呼出し予算（初回生成1回＋修復3回＝計4回）を使い切ったため、残った2件は運用者が直接反映しました。
- 人によるマージ操作で PR #42 を squash merge し、`docs/operator-runbook.md` は main に入りました。ADO のジョブ（17）は、外部での head 変更を検出して `BLOCKED`（PR identity/head changed externally）になったため、Issue #39 のクローズは運用者が行いました。

### 完了時点の状態（2026-10-25）

- バックログは0件です。OPEN の Issue と PR はなく、ADO が作った `ado/issue-*` のブランチも残っていません。
- ローカルのテストは172件成功し、`python -m compileall -q ado scripts tests` と `node --check ado/web/app.js` も成功します。
- 常駐は2本で、どちらも `launchctl list` に表示されています。pilot は `local.ado.worker`（`dry_run = true`、対象 `SMRI2170/animal-shadow-match`）、live は `local.ado.worker.live`（`dry_run = false`、対象 `SMRI2170/autonomous-dev-orchestrator`）です。
- ダッシュボードの既定ポート 8765 は他アプリと衝突し得るため、実運用では別ポートを指定して起動します。
