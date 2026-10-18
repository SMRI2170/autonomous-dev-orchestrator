# セットアップと最初のライブ運用

## 前提

- Mac mini M2 16GB、macOS、Python 3.11以上、GitHub CLI。Intel/macOSやLinuxでのライブAI実行は対象外。
- MiniMax Codeの既存契約・利用枠。Codexは必要時の手動補助のみ。
- 対象は公開GitHubリポジトリ。標準ホストRunnerを使うCIと、CodeRabbit GitHub Appを事前に接続する。
- 専用のmacOS一般ユーザーで運用することを推奨。対象コードの実行環境と認証を分ける。

このリポジトリは既存プロジェクトへファイルを配布・変更しません。以下の対象側の作業は運用者がレビューして手動実施します。

## 1. ローカル導入

READMEのvenv作成・テストを実施し、`examples/config.toml`をコピーします。複数対象は`[[repositories]]`の追加だけです。登録順に探索し、アクティブなジョブがある間は他のIssueを開始しません。BlockedとDoneは自動再実行されません。大量のIssueがある先頭リポジトリが他を待たせる可能性があります（ラウンドロビンは未実装）。

Mac標準のPython 3.9では動きません。プロジェクトで管理したPython 3.11以上を使用してください。

## 2. 最小権限GitHub認証

ライブworkerは環境変数`GH_TOKEN`が必須です。GitHub CLIの広い個人トークンを自動流用しません。**fine-grained PATを対象リポジトリだけに限定**し、次の権限を付与します。

| Repository permission | 権限 | 用途 |
|---|---|---|
| Metadata | Read | リポジトリ情報 |
| Contents | Read/Write | git objects、専用ブランチ、マージ |
| Issues | Read/Write | Issue取得・完了 |
| Pull requests | Read/Write | PR作成、レビューとスレッドの読取り、マージ |
| Checks | Read、選択できる場合 | check-runsの取得。公開リソースの読取りはこの権限なしでも利用できるため、UIに項目がなければ取得を実測して確認 |
| Administration | Read、任意 | 自動マージ時の保護ブランチ読取りのみ |

Actions Write、Workflows Write、Projects Write、organization全体の権限、管理書込みは不要です。ルールのバイパス権限を付けないでください。Branch protectionを設定する操作はこのツールに実装していません。Contents WriteはGitHub側では専用ブランチだけに限定できないため、default branchを保護してください。

一時的な手動実行ではシェルの非表示入力等でGH_TOKENを渡し、終了後に解除します。トークンを設定TOML、コマンドの履歴、plist、リポジトリに書かないでください。常駐用は[Keychainの手順](launchd.md)を利用します。macOSでは`python scripts/setup_token.py --config config.toml`で非表示入力し、`github_login`（任意）と設定に登録した全repoの存在・公開状態を確認してログインKeychainへ保存できます。リポジトリのPAT選択範囲とContents/Issues/PRの書込み権限は読み取りAPIでは証明できないため、GitHubの権限設定UIで確認し、制御したpilotで実測します。既存のADO項目は上書きしません。新規保存後の読戻しに失敗した場合は、その項目をロールバックします。トークンを引数に渡さず、Keychain操作のstdinで受け渡します。

## 3. 対象リポジトリの準備（手動）

1. opt-inラベル`ado:ready`を作成する。自動マージを将来検討する場合だけ`ado:low-risk`も用意する。
2. `trusted_authors`をIssue作成者の明示的な許可リストにする。公開の第三者Issueは実行しない。
3. 既存CIを利用し、PRで必ず走る標準ホストRunnerのcheck名を確認する。Python用・Flutter用の再利用workflowとcaller例をexamplesに用意している。Flutterは対象に合うSDKの正確なバージョンを入力する。`required_checks`に**APIが返す正確なname**と`app = "github-actions"`を設定する。再利用workflowでは表示名が`checks / unit`等になる場合があるため、`unit`と決め打ちしない。
4. 標準の`review_provider = "coderabbit"`ならCodeRabbitを[公開リポジトリ向け無料プラン](https://www.coderabbit.ai/pricing)で接続する。従量課金の追加レビューはOffにし、課金機能は有効化しない。bot loginは実アカウントのAPI結果と照合する。正式なAPPROVEDを返すため、`examples/coderabbit.yaml`の`reviews.request_changes_workflow = true`相当の設定を対象repoに手動導入する。作者自身の承認コマンドは`allow_author_approval: false`で無効にする。既定のCOMMENTEDだけではworkerは合格にしない。
   既存契約のMiniMaxをレビュー主体にする場合は、対象リポジトリに`review_provider = "minimax"`を設定する。CI成功・PRのhead一致・変更ファイルのSHA検証後だけ、ツールなしの新規MiniMax CLI実行でレビューし、判定と差分ダイジェストをSQLiteに保存する。実装とレビューは別呼び出しだが同じ基盤モデルであり、独立モデルによる承認とは異なる。CodeRabbitは任意。MiniMax方式では自動マージを実施しない。レビュー呼び出し中に終了したジョブは自動再実行不可。既存の進行中ジョブに対してレビュー方式を変更すると、policy hash検査によりBlockedとなるので、新しいIssueから移行すること。
5. `examples/issue.md`を参考に、`## Acceptance criteria`を含む小さなIssueを作る。依存がなければ`Depends-On`行を削除。依存Issueは同一リポジトリ内で`completed`として閉じられている必要がある。
6. `ado run --once`で読み取りだけの計画を確認する。

CIはtrusted設定です。既存workflowが有料Runner・他の有料サービスを呼ぶかまでは本ツールでは監査できません。公開+標準Runnerの無料構成を確認した対象だけ登録してください。iOS/macOSやUnityビルドはこのMVPのサンプル対象外です。

## 4. MiniMax Codeを既存契約で用意

[公式インストール手順](https://github.com/MiniMax-AI/minimax-code#quick-start)でバージョンを固定して導入し、専用プロファイルを作成します。自動インストールやAPIキー発行は行いません。

```sh
MINIMAX_DATA_DIR=/absolute/path/to/ado-minimax-profile mcode login --region global
MINIMAX_DATA_DIR=/absolute/path/to/ado-minimax-profile mcode
```

Globalでないアカウントは公式の該当地域用ログインを使います。対話UIの`/status`と`/provider`で、既存契約を使う公式アカウントであり、従量課金のcustom providerやAPIキーを使っていないことを確認します。利用枠超過時の挙動も契約側で確認してください。本ツールは請求情報APIを使いません。

設定するパスは`node`実行体、CLIの`cli.js`（npm版はpackage直下、source buildでは`dist/cli.js`）、依存パッケージを含む`runtime_root`、専用`data_dir`です。シェルの`mcode`ランチャーは直接起動しません。ランチャー経由の任意コマンドや別CLIへのフォールバックは拒否します。

アダプターのコマンド契約:

```text
node /reviewed/runtime/dist/cli.js exec --mode lightweight --input - --output-format json
```

npm配布版0.6.12ではentryは`node_modules/@minimax-ai/code/cli.js`です。CLIはバージョンを固定し、既存インストールとは別の専用ディレクトリに置いてください。`mcode provider use token-plan`で既存契約の認証元を明示します。

stdinで指示と許可済みファイルのスナップショットを渡します。標準出力は`{"status":"succeeded","output":"…"}`、outputはさらに`{"files":{"docs/example.md":"全文"}}`というJSON文字列である必要があります。Markdownフェンス、追加キー、重複キー、空変更は不合格。ファイル削除、rename、バイナリ、実行権限変更は未対応です。

[公式のlightweightモード](https://github.com/MiniMax-AI/minimax-code#lightweight-conversations)はtoolsを渡さず、workspace instructions・Skills・memoryを省きます。本ツールはさらにOS側で実行とファイルアクセスを制限します。既存のCLI設定・hook・認証データの信頼性は専用プロファイル側で管理してください。CLI自身のセッション記録は本ツールのログマスキング対象外です。

この契約を利用バージョンで小さなタスクにより確認し、`subscription_confirmed = true`にします。認証更新のためのネットワークと専用data dirの読み書きは許可されます。起動に不足するOSアクセスがあると失敗しBlockedになります。HOME全体の許可やsandbox無効化で回避しないでください。

## 5. MiniMaxレビューの単発受入テスト（GitHub書込みなし）

Macで最新版をpullし、`python -m unittest discover -s tests -v`がすべて通ってから実行してください。これにより既存MiniMax契約で本物のレビューCLI応答を一度検証します（モデルの利用枠を消費します）。本番用のIssue・PR・GitHub APIは呼びません。

```sh
.venv/bin/python - <<'PY'
from ado.config import load
from ado.agent import MiniMax
from ado.minimax_review import MiniMaxReviewer

cfg = load("config.toml")
result = MiniMaxReviewer(MiniMax(cfg["minimax"])).review(
    {"title": "Explain PR", "body": "## Acceptance criteria\\nExplain what PR means."},
    {"docs/example.md": "PR means private repository."},
    {"docs/example.md": "PR means pull request."},
    "a" * 40,
)
print("MiniMax reviewer:", result["verdict"], "findings:", len(result["findings"]))
PY
```

`APPROVE`または`REQUEST_CHANGES`のどちらも正常な構造化応答です。CLI起動・ログイン・モデル契約のエラーは安全停止します。トークン・プロファイル内容は共有しないでください。現在の`config.toml`に`review_provider`が未指定なら、旧CodeRabbit方式が維持されます。レビュー方式変更時は進行中ジョブの扱いを確認し、先に手動で整理してください。

## 6. ライブ実行（自動マージは無効のまま）

`dry_run = false`のみ変更します。`auto_merge = false`は維持します。1つの小さなdocs Issueで開始してください。

```sh
.venv/bin/python -m ado --config config.toml run --once
.venv/bin/python -m ado --config config.toml status
# 内容と状態を確認した後、継続ポーリング
.venv/bin/python -m ado --config config.toml run
```

`run --once`を複数回実行すると、変更生成→PR公開→レビュー確認と進みます。PR本文の編集・コメントはworkerの設定変更やマージ許可には使われません。PRに`Closes #…`を付けず、workerがマージ済みを確認してからIssueを完了します。

## 6. 停止・復旧

Ctrl-Cまたはlaunchd停止でポーリングを止めます。BUILDING中断は予算を消費済みとして保存し、自動リプレイしません。Blockedの原因を確認し、再開可能なAPI送信段階のみ明示して復旧できます。人間への引き継ぎ後にPRが `behind` / `dirty` へ変化した場合は、安全のためジョブをBlockedにします。headの更新は保存済みSHAの保証を無効化するため、このジョブを再開せず、PRの更新・マージ・Issueのクローズを人間が確認して行ってください。

```sh
.venv/bin/python -m ado --config config.toml retry 1
```

この操作は修正回数をリセットしません。BUILDING中断と3回の修正上限到達は再開不可です。人間がPRを確認・閉鎖するなど整理し、新しいIssueで依頼してください。期限切れの既存reviewを再試行しても期限は延長しません。レビュー待ち開始時刻は新しい修正commitの公開時だけ更新します。

publishのHTTP結果が失われた場合、保存した親SHA・内容・日時で同じcommitを作り、既存refとPRを照合します。別のプロセスが作ったブランチやPRと衝突するとBlockedです。`.state`やSQLiteを削除して再実行する運用は冪等性を失うため避けてください。

## 将来の自動マージ設定

初期導入では変更しません。検証後、globalと対象repo両方の`auto_merge`、Issueの`ado:low-risk`、`auto_merge_paths`を明示し、さらに以下をすべて満たす必要があります。

- 全変更が`docs/`配下の`.md`、許可されたパス、上限以内。コードは常に人間へ。
- 作成時からbase branchのSHAが変わっていない。
- head SHAに対応した必須CI全成功、CodeRabbitのBotレビューが`APPROVED`。
- 最新の変更要求レビューなし、未解決スレッドなし、draftでなくmergeableがclean。
- ブランチ保護にstrict CI、GitHub Actions App IDに紐づく必須check、古い承認の破棄、1名以上の承認、管理者にも適用、force push/deletion禁止。
- PR identity、remoteファイル集合、Git blob SHAが検査済み変更と一致。

最後のmerge APIもhead SHAを指定します。base更新との競合はサーバー側strict保護で防ぎます。ルール変更との完全な原子性まではGitHub APIが提供しないため、保護設定を変更できる管理者は信頼境界内です。必要な権限・保護がなければBlockedとなり、勝手に保護ルールを変更しません。
