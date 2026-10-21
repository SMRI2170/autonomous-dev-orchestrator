# macOS launchd常駐

ログインユーザーのLaunchAgentで動かします。root常駐や公開self-hosted Actions Runnerは使用しません。Macのスリープ中・ログアウト中は処理されません。24時間動かす場合はmacOS設定で電源接続時の自動スリープを調整してください。初期作成時は未登録でした。2026-10-18にDeveloper配下の導入では、dry-runに限って登録・起動を確認しています。

## 1. 起動前チェック

venvとconfig.tomlを絶対パスで固定し、`doctor`、テスト、dry-runを完了します。ライブ化はセットアップ手順の後で行います。Keychain Accessで一般パスワード項目を作成し、サービス名`ado.github-token`、アカウント`ado`に対象限定fine-grained PATを保存します。トークンの値をplistや環境設定へ記載しません。手動登録の代わりに`python scripts/setup_token.py`を実行すると、非表示入力・対象アカウント確認・既存項目の保護・読戻し確認を行えます。

`dry_run = true`ではKeychainを読みません。ライブ時だけ`scripts/launch.py`が`security find-generic-password`で取得してworkerに渡します。最初のKeychainアクセス確認・ロック解除は対話環境で行ってください。取得できなければ起動を止めます。

## 2. plistを生成し確認

リポジトリのルートで実行します。

```sh
.venv/bin/python scripts/render_launchd.py \
  --config "$PWD/config.toml" \
  --output "$PWD/.state/local.ado.worker.plist"
plutil -lint .state/local.ado.worker.plist
plutil -p .state/local.ado.worker.plist
```

plistは絶対パス、最小PATH、ログ、失敗時60秒の再起動間隔、umask077を設定します。`render_launchd.py`は生成のみで、OSには登録しません。

## 3. 手動で登録・起動

```sh
mkdir -p "$HOME/Library/LaunchAgents"
cp .state/local.ado.worker.plist "$HOME/Library/LaunchAgents/local.ado.worker.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.ado.worker.plist"
launchctl print "gui/$(id -u)/local.ado.worker"
```

手動workerと同時起動しないでください。同じstate_dirのロックが二重処理を防ぎます。ループ全体のクラッシュはlaunchdが再起動しますが、DBのBlockedジョブは再試行しません。

## 4. 確認・停止

```sh
.venv/bin/python -m ado --config config.toml status
launchctl bootout "gui/$(id -u)/local.ado.worker"
```

標準出力/エラーログはリポジトリ配下`.state/launchd`、状態と監査イベントはTOMLのstate_dirに保存します。ログサイズの上限/ローテーションは未実装なので定期的に確認してください。リポジトリを移動したらplistを再生成し再登録します。
