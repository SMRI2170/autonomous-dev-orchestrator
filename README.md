# Autonomous Dev Orchestrator (ADO)

Mac mini M2 / 16GBの1台で動かす、複数GitHubプロジェクト用の自律開発オーケストレーターMVPです。Python 3.11以上・標準ライブラリのみ。初期設定は **dry-run、自動マージ無効、1ジョブ**。n8n、追加AI API、クラウドDB、Webhookサーバーは不要です。

**実装済みの制御ループと、実アカウントで未検証のAI連携を区別しています。** MiniMax Codeが未導入の環境ではAI実装を開始できません。CLI出力形式・macOS sandboxとの互換性も、利用するバージョンで受入確認が必要です。課金APIへの代替経路はありません。

## フロー

```mermaid
flowchart TD
  A[公開リポジトリをポーリング] --> B[信頼する作成者 + ado:ready]
  B --> C[受入条件・依存Issue確認]
  C --> D[許可されたテキストを取得]
  D --> E[MiniMax Code: toolsなしで変更JSONを提案]
  E --> F[パス・内容・機密・変更量検査]
  F --> G[専用ブランチ・PR作成]
  G --> H[同一head SHAのCI・CodeRabbit判定]
  H -->|要修正: 最大3回| E
  H -->|合格| I{マージ許可と安全条件}
  I -->|既定| J[人間のマージ待ち]
  I -->|docs限定・保護ブランチ| K[SHA指定squash merge]
  J --> L[マージ済み確認・Issue完了]
  K --> L
  L --> A
  C -->|不備| X[Blocked]
  F -->|違反| X
  H -->|上限・期限・API失敗| X
```

処理状態はSQLite、監査イベントはマスキングしたJSONLに記録します。対象リポジトリのclone・ローカルテスト・ビルドは行わず、Git Data APIで変更を作成します。対象コードはGitHubの標準ホストRunnerで検証します。公開リポジトリのself-hosted Runnerは使用しません。

## すぐ試す

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install --no-deps -e .
cp examples/config.toml config.toml
.venv/bin/python -m ado --config config.toml doctor
.venv/bin/python -m ado --config config.toml run --once
.venv/bin/python -m unittest discover -v
.venv/bin/python -m ado --config config.toml status
```

`config.toml`のリポジトリ一覧を確認してください。サンプルはSMRI2170/animal-shadow-matchを**参照するだけ**で、設定ファイルを対象側に追加しません。dry-runはIssueの取得・仕様と依存関係の検査だけを実施し、モデル呼び出し・GitHub書き込み・ライブジョブの予約を行いません。既存`gh auth login`による公開リポジトリ読み取りが使えます。

ライブ運用の手順は[セットアップ](docs/setup.md)、常駐方法は[launchd](docs/launchd.md)、安全条件は[セキュリティ設計](docs/security.md)、動作確認と未実装事項は[検証結果](docs/verification.md)を参照してください。

## 実装範囲

| 機能 | MVPの状態 |
|---|---|
| 複数対象のTOML登録、Issue opt-in、仕様・依存チェック | 実装済み。単一リポジトリ内の`Depends-On: #12, #13`のみ |
| MiniMax実装・修正 | `mcode exec --mode lightweight`の変更JSONアダプター実装。実ログイン未検証 |
| 分離 | toolsなし、環境変数を限定、macOS Seatbelt、対象cloneなし。危険コマンド実行経路なし |
| ブランチ・PR・CI・レビュー取得 | GitHub REST/GraphQL実装。全ページを上限付きで取得、欠落時は不合格 |
| 修正上限・冪等性・再起動 | 初回+最大3回。予算を呼び出し前に保存。publish意図・commitを永続化 |
| 自動マージ | 既定OFF。docs内Markdownのみ。SHA一致、CI/CodeRabbit承認、未解決指摘ゼロ、保護ブランチ必須 |
| Issue完了・次Issue | 実際のマージ済み状態を確認後に完了。Blockedは自動再試行しない |
| 可視化 | `ado status`、JSONL、PR。GitHub Projectsは手動/組込みauto-addの利用を提案 |
| GitHub Projects書込み、n8n、Codex自動呼出し | **未実装**。比較と拡張案は[設計判断](docs/architecture.md) |

`run --once`は1回の状態遷移です。通常の`run`は60秒間隔で継続します。CI/レビュー待ち・人間のマージ待ちも1つのアクティブジョブとして扱います。安全な小規模変更向けMVPであり、任意規模のFlutter/Unity開発を自律完遂するものではありません。

## コストの境界

MiniMaxは既存契約でログインした専用プロファイルを使います。OpenAI/MiniMaxの従量課金APIキーは受け付けず、枠超過・ログイン失敗はBlockedです。ただし、CLI側で選択したプロバイダーや契約内容は運用者による確認が必要で、本ツールがアカウントの請求額を保証する仕組みはありません。新規有料サービスへの登録や自動アップグレードはしません。

CI対象は公開リポジトリのみ、標準`ubuntu-latest`のみをサンプルで使用します。private repoと有料Runnerは対象外。CodeRabbitは公開向け無料枠を運用者が接続します。無料枠で現在SHAへの`APPROVED`が提供されない場合も基準を下げず、人間の対応待ちから期限切れBlockedになります。

## ライセンス

MIT。GitHub ActionsはSHAで固定しています。再利用時はレビュー済みのコミットSHAを指定してください。
