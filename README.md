# bsky-judge — レスバ判定ボット（Bluesky版）

スレッド内でボットをメンションすると、そこまでの議論を**論証構造の5軸**で採点し、スコアと勝者を1投稿でリプライします。ランニングコスト **0円**。

## 構成

| 役割 | 使うもの | 料金 |
|---|---|---|
| 定期トリガー | cron-job.org（外部cronサービス・2分間隔） | 無料 |
| 実行基盤 | GitHub Actions（workflow_dispatch） | 無料（publicリポジトリなら無制限） |
| SNS API | Bluesky AT Protocol | 無料・キー不要（アプリパスワードのみ） |
| 判定 | Gemini API `gemini-3.6-flash` | 無料枠 |
| 状態保存 | `state/seen.json` をリポジトリにコミット | 無料 |

## 採点軸（各20点・計100点）

| 軸 | 内容 |
|---|---|
| 根拠 | 具体的な根拠・出典・事例を添えているか |
| 論点 | 当初の論点を維持しているか（すり替え・ゴールポスト移動を減点） |
| 応答 | 相手の問いに正面から答えているか（無視・はぐらかしを減点） |
| 一貫 | 自分の発言間に矛盾がないか |
| 誤謬 | 藁人形論法・人身攻撃・レッテル貼りがないか |

思想や立場の是非、事実の真偽は**採点しません**。「根拠を示したか」という形式面のみを見ます。

## 判定結果の形式

1投稿（300文字以内）に論点・点数表・勝者をまとめて返します。

```
⚖️ レスバ判定
論点: 移民受け入れの是非
@nemu-az.bsky.social 91点
　根拠15/論点19/応答19/一貫19/誤謬19
@asd159bu.bsky.social 64点
　根拠13/論点15/応答12/一貫14/誤謬10
🏆 勝者: @nemu-az.bsky.social
```

## セットアップ

### 1. Bluesky のアプリパスワード発行
Bluesky → 設定 → プライバシーとセキュリティ → アプリパスワード → 追加。
`xxxx-xxxx-xxxx-xxxx` 形式の文字列が出るので控える。**本体のパスワードは使わないこと。**

### 2. Gemini APIキー取得
Google AI Studio でキーを発行（無料枠あり）。

### 3. リポジトリ作成
このディレクトリをGitHubにpush。**publicリポジトリ推奨**（Actionsの実行時間が無制限になるため）。

### 4. Secrets 登録
リポジトリ → Settings → Secrets and variables → Actions → New repository secret

- `BSKY_HANDLE` … `yourbot.bsky.social`
- `BSKY_APP_PASSWORD` … 手順1のパスワード
- `GEMINI_API_KEY` … 手順2のキー

### 5. cron-job.org で定期トリガーを設定
GitHub Actionsのcronは遅延が大きいため、外部サービスで2分ごとに`workflow_dispatch`を呼び出します。

1. [cron-job.org](https://cron-job.org) で無料アカウントを作成
2. 「CREATE CRONJOB」で以下を設定：

| 項目 | 値 |
|---|---|
| URL | `https://api.github.com/repos/<owner>/<repo>/actions/workflows/judge.yml/dispatches` |
| Request method | `POST` |
| Execution schedule | Every 2 minutes |

3. Headersに以下を追加：

| Header名 | 値 |
|---|---|
| `Authorization` | `Bearer <GitHubのPersonalAccessToken>` |
| `Content-Type` | `application/json` |
| `Accept` | `application/vnd.github.v3+json` |

4. Request bodyに入力：
```json
{"ref":"main"}
```

※ GitHub PATには `repo` と `workflow` スコープが必要です。

### 6. 動作確認
Actions タブ → bsky-judge → Run workflow → `dry_run` にチェック。
実際には投稿せず、判定結果がログに出ます。問題なければ本番運用へ。

### ローカル実行

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 値を埋める
export $(grep -v '^#' .env | xargs)
python -m src.main
```

## 環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `DRY_RUN` | 空 | `1` で投稿せずログのみ |
| `MAX_PER_RUN` | 3 | 1回の実行で処理する最大メンション数 |
| `MAX_AGE_MINUTES` | 90 | これより古いメンションは無視（初回の大量処理防止） |
| `MIN_POSTS` | 4 | これ未満の投稿数なら判定しない |
| `GEMINI_MODEL` | gemini-3.6-flash | 判定モデル |

## 設計上の判断メモ

- **召喚型（メンション起動）にしている理由**：キーワード監視で勝手にリプライを飛ばす方式は、Bluesky でもスパム扱いのリスクが高く、X に移植する際は規約上そもそも不可能です（Xは2026年2月から、元投稿の作者がメンションか引用をした場合しかAPI経由のリプライを許可していません）。最初から召喚型で作っておけば移植できます。
- **外部cronでworkflow_dispatchを叩く理由**：GitHub ActionsのcronはPublicリポジトリでは低優先度で処理され、指定間隔より10〜30分遅延することが多いです。`workflow_dispatch`をAPIで呼び出す方式にすると即時起動されます。
- **並列実行制御**：`concurrency: group: bsky-judge, cancel-in-progress: false` により、実行中のrunがある場合は新しいrunをキュー待機させます。Geminiの推論が2分を超えても処理は継続され、次のトリガーは待機してから起動するため取りこぼしは発生しません。
- **二重返信防止を2層にしている理由**：第1層として`state/seen.json`で処理済みURIを管理し、第2層としてBluesky上のスレッドにボットの返信が既にあるかをAPI経由で確認します。`seen.json`のgit pushが失敗した際のフォールバックとして第2層が機能します。
- **判定文に `@handle` を書くが、facet（リンク化）は付けていない**：敗者側に通知が飛んで晒し上げ・集団攻撃になるのを避けるためです。可読性は保ったまま通知だけ切っています。
- **エラーが出たメンションも processed に記録**：壊れたスレッドで毎回リトライして無限ループするのを防いでいます。

## 運用上の注意

- プロフィールに「自動判定ボットです」と明記してください。
- 個人攻撃の道具として使われた場合に備え、対象外にするハンドルの拒否リストを `handle_mention()` の冒頭に足すと安全です。
- cron-job.orgのジョブが停止していないか定期的に確認してください。レスポンスコードが `204` であれば正常です。
- Gemini APIの無料枠には1日あたりのリクエスト数上限があります。大量にメンションが来る場合は上限に達する可能性があります。

## X版への移植

`src/bluesky.py` を X API クライアントに差し替えるだけで、`judge.py` / `formatter.py` / `main.py` はほぼそのまま流用できます。ただし X は従量課金（読み取り $0.005/件、投稿 $0.015/件、リンク入り投稿 $0.20/件）で、AIリプライボットの運用には X からの事前承認が必要です。
