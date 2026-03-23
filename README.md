# CPC MWM-CWM

研究合宿のための AI ツールキット。2つのシステムを統合:

- **CWM** (Collective White paper Making) — Slack の議論からホワイトペーパーを自動生成
- **MWM** (Multi-agent White paper Meeting) — AI ペルソナが Slack 上で議論に参加

CWM で生成したホワイトペーパーを MWM のペルソナに注入し、知識に基づいた議論を展開できます。

## セットアップ

### 1. 前提条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Slack ワークスペースの管理者権限
- Anthropic API キー

### 2. Slack App の作成

1つの Slack App で CWM と MWM の両方をカバーします。

#### 自動セットアップ

```bash
export SLACK_CONFIG_TOKEN=xoxe.xoxp-...  # https://api.slack.com/apps で取得
./scripts/setup-slack-app.sh
```

#### 手動セットアップ

1. [Slack API](https://api.slack.com/apps) → 「Create New App」→「From an app manifest」
2. `slack-app-manifest.yml` の内容を貼り付け
3. ワークスペースを選択して作成

#### Socket Mode の有効化

1. Settings > **Socket Mode** → Enable
2. 「Generate Token」→ Scope に **`connections:write`** を追加 → Generate
3. 生成された `xapp-...` トークンをメモ

#### ワークスペースにインストール

1. Features > **OAuth & Permissions** → 「Install to Workspace」→ Allow
2. **Bot User OAuth Token** (`xoxb-...`) をメモ

> Scope を変更した場合は「Reinstall to Workspace」が必要です。

### 3. インストールと設定

```bash
cd cpc-mwm-cwm
uv sync

cp .env.example .env
# .env を編集してトークン等を設定
```

### 4. bot をチャンネルに招待

```
/invite @cpc-camp-bot
```

- **CWM 用**: 読み取り対象チャンネル（`CWM_SOURCE_CHANNEL_IDS` で指定するチャンネル）
- **MWM 用**: bot チャンネル（`MWM_BOT_CHANNEL_ID`）+ セッションチャンネル

> bot を招待しないとそのチャンネルのメッセージを読み取れません。

## 使い方

### CWM — ホワイトペーパー生成

```bash
# ローカルに出力
uv run cpc-cwm --local

# チャンネル指定 + ローカル出力
uv run cpc-cwm --channel C0AAA C0BBB --local --local-path whitepapers/output.md

# GitHub に push
uv run cpc-cwm

# オプション
uv run cpc-cwm --channel C0AAA --limit 1000 --model claude-sonnet-4-20250514
```

### MWM — マルチエージェント議論 bot

```bash
# 起動（デフォルト: agents/ada.yml）
uv run cpc-mwm

# エージェント設定を指定して起動
uv run cpc-mwm --agent-config agents/karl.yml

# ホワイトペーパーを注入して起動
uv run cpc-mwm --whitepaper whitepapers/latest.md
```

bot チャンネルでのコマンド:

| コマンド | 説明 |
|---------|------|
| `!session start <名前> <チャンネルID>` | プレゼンモードでセッション開始 |
| `!session start-free <名前> <チャンネルID>` | 自律議論モードでセッション開始 |
| `!moltbook` | bot チャンネルで自律議論モード即開始 |
| `!session end` | セッション終了 |
| `!session status` | セッション状態表示 |

PDF スライドや VTT トランスクリプトを bot チャンネルにアップロードすると自動で読み込まれます。

### 統合実行 — CWM → MWM パイプライン

```bash
uv run python scripts/orchestrate.py
```

CWM でホワイトペーパーを生成し、その内容をペルソナに注入した状態で MWM を起動します。

## エージェント設定

各エージェントは `agents/` ディレクトリの YAML ファイルで定義します。YAML config は **perception**（発言判断）と **response**（応答生成）の2フェーズを制御します。

### プリセット

| ファイル | ペルソナ | スタイル |
|---------|---------|---------|
| `agents/ada.yml` | Ada | 記号創発とロボティクス、構成論的アプローチ |
| `agents/karl.yml` | Karl | 科学哲学、前提を問い直す |
| `agents/maya.yml` | Maya | メタサイエンス、分野間の架橋 |
| `agents/friston.yml` | Friston | 自由エネルギー原理、理論神経科学 |

### エージェント config の構造

```yaml
name: my_agent
persona: personas/ada.md          # ペルソナ定義ファイル
model: claude-sonnet-4-20250514   # デフォルトモデル

perception:
  model: claude-haiku-4-5-20251001  # 判断用の軽量モデル
  prompt: >
    以下に該当する場合 YES:
    - 発言すべき条件...

response:
  prompt: >
    応答スタイルの指示...

actions:
  reply:                          # スレッドへの返信
    enabled: true
  new_topic:                      # 新しいトピックを投稿
    enabled: true
```

- **perception**: haiku で「発言すべきか」を YES/NO で高速判定
- **response**: sonnet でペルソナに基づいた応答を生成
- **actions**: コード側で定義されたアクションの有効/無効を切り替え
- prompt フィールドは `.md` ファイルへの参照も可（例: `prompt: prompts/perception.md`）

### ペルソナファイル

`personas/` ディレクトリの MD ファイルでペルソナの人格を定義します。エージェント config の `persona` フィールドで参照します。

```markdown
---
name: YourBot
style: あなたのスタイルの説明
avatar_emoji: ":robot_face:"
---

あなたは YourBot です。研究合宿に参加している研究者です。

## あなたが読んだホワイトペーパー

{{whitepaper}}
```

`{{whitepaper}}` プレースホルダーは `--whitepaper` で指定したファイルの内容に置換されます。

### マルチエージェント

#### 同一プロセスで複数エージェント

```bash
AGENT_CONFIGS="agents/ada.yml,agents/karl.yml,agents/maya.yml" uv run cpc-mwm
```

#### 別プロセスで複数 bot

```bash
uv run --env-file .env.ada cpc-mwm --agent-config agents/ada.yml &
uv run --env-file .env.karl cpc-mwm --agent-config agents/karl.yml &
```

## 音声キャプチャ（BlackHole + faster-whisper）

Zoom の音声をリアルタイムで文字起こしする場合。

### BlackHole のインストール

```bash
brew install blackhole-2ch
```

> macOS の再起動が必要です。

### Audio MIDI Setup の設定

1. 「Audio MIDI Setup」アプリを開く
2. 左下の **「+」** → **「複数出力装置を作成」**
3. **MacBook Pro のスピーカー** と **BlackHole 2ch** の両方にチェック
4. macOS のサウンド出力を「複数出力装置」に変更

### 音声キャプチャ付きで起動

```bash
ENABLE_AUDIO=true AUDIO_DEVICE="BlackHole 2ch" uv run cpc-mwm
```

## 設定一覧

| 環境変数 | 対象 | 必須 | デフォルト | 説明 |
|---------|------|------|----------|------|
| `SLACK_BOT_TOKEN` | 共通 | Yes | - | Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | MWM | Yes* | - | App-Level Token (`xapp-...`) — MWM 使用時のみ |
| `ANTHROPIC_API_KEY` | 共通 | Yes | - | Anthropic API キー |
| `MODEL_NAME` | 共通 | No | `claude-sonnet-4-20250514` | Claude モデル名 |
| `CWM_SOURCE_CHANNEL_IDS` | CWM | Yes* | - | 読み取り対象チャンネル（カンマ区切り）— CWM 使用時 |
| `GITHUB_TOKEN` | CWM | No | - | GitHub Token（`--local` なら不要） |
| `GITHUB_REPO` | CWM | No | - | GitHub リポジトリ（`--local` なら不要） |
| `MWM_BOT_CHANNEL_ID` | MWM | Yes* | - | コマンド受付・応答投稿先チャンネル — MWM 使用時 |
| `AGENT_CONFIG` | MWM | No | `agents/ada.yml` | エージェント設定 YAML ファイル |
| `AGENT_CONFIGS` | MWM | No | - | 複数エージェント設定（カンマ区切り） |
| `WHITEPAPER_PATH` | MWM | No | - | ホワイトペーパーファイルパス |
| `RESPONSE_INTERVAL_SECONDS` | MWM | No | `120` | 応答間隔（秒） |
| `ENABLE_AUDIO` | MWM | No | `false` | 音声キャプチャ有効化 |
| `AUDIO_DEVICE` | MWM | No | デフォルトマイク | 音声デバイス名 |
| `WHISPER_MODEL` | MWM | No | `large-v3` | Whisper モデル名 |
| `WHISPER_LANGUAGE` | MWM | No | `ja` | 文字起こし言語 |

## チャンネルの意味

| 環境変数 | 使用元 | 意味 | アクセス |
|---------|--------|------|---------|
| `CWM_SOURCE_CHANNEL_IDS` | CWM | メッセージを一括取得する対象チャンネル | 読み取り専用 |
| `MWM_BOT_CHANNEL_ID` | MWM | コマンド受付 + bot の応答投稿先 | 読み書き |
| セッションチャンネル | MWM | `!session start` で動的に指定 | 読み取り専用 |

全チャンネルで bot の招待（`/invite`）が必要です。

## プロジェクト構成

```
cpc-mwm-cwm/
├── packages/
│   ├── agent-utils/    # 共有ライブラリ（Slack, Claude, Config, Persona）
│   ├── cpc-cwm/        # ホワイトペーパー生成
│   └── cpc-mwm/        # マルチエージェント議論 bot
├── agents/              # エージェント設定 YAML
├── personas/            # ペルソナ定義ファイル
├── whitepapers/         # 生成されたホワイトペーパー
└── scripts/             # セットアップ・オーケストレーション
```

## トラブルシューティング

### bot がメッセージを受信しない

- Slack App の Event Subscriptions で `message.channels` が設定されているか確認
- bot が対象チャンネルに `/invite` されているか確認
- Scope を変更した場合は「Reinstall to Workspace」を実行したか確認

### `onnxruntime` のインストールエラー

macOS で `onnxruntime` の wheel が見つからないエラーが出る場合、`pyproject.toml` で `onnxruntime` のバージョンを制約しています（`<1.24`）。通常は `uv sync` で自動解決されます。

### BlackHole が認識されない

`brew install blackhole-2ch` 後に macOS の再起動が必要です。
