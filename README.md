# CPC MWM-CWM

研究合宿のための AI ツールキット。2つのシステムを統合:

- **CWM** (Collective White paper Making) — Slack の議論からホワイトペーパーを自動生成
- **MWM** (Multi-agent White paper Meeting) — AI ペルソナが Slack 上で議論に参加

```
Slack チャンネルの議論
        │
        ▼
┌───────────────┐     ホワイトペーパー     ┌───────────────┐
│     CWM       │ ──────────────────────► │     MWM       │
│ 議論を要約して  │     (自動注入)          │ AI ペルソナが  │
│ WP を生成     │                         │ 議論に参加     │
└───────────────┘                         └───────────────┘
                                                  │
                                                  ▼
                                          Slack に投稿
```

CWM で生成したホワイトペーパーを MWM のペルソナに注入し、知識に基づいた議論を展開できます。

## セットアップ

### 1. 前提条件

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) — `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Slack ワークスペースの管理者権限（App 作成に必要）
- [Anthropic API キー](https://console.anthropic.com/)

### 2. リポジトリのクローンと依存関係のインストール

```bash
git clone https://github.com/t46/cpc-mwm-cwm.git
cd cpc-mwm-cwm
uv sync
```

> これは monorepo（uv workspace）構成です。`uv sync` で `agent-utils`（共有ライブラリ）、`cpc-cwm`、`cpc-mwm` の3パッケージとその依存関係がすべてインストールされます。

### 3. Slack App の作成

1つの Slack App で CWM と MWM の両方をカバーします。セットアップ完了後に**2つのトークン**が必要になります。

```
Slack API ダッシュボード
│
├─ OAuth & Permissions
│   └─► Bot User OAuth Token (xoxb-...)  ← Slack への読み書きに使用
│
└─ Socket Mode (Basic Information)
    └─► App-Level Token (xapp-...)        ← MWM のリアルタイム通信に使用
```

#### 方法 A: 自動セットアップ

```bash
export SLACK_CONFIG_TOKEN=xoxe.xoxp-...  # https://api.slack.com/apps で取得
./scripts/setup-slack-app.sh
```

> Configuration Token は [Slack API ダッシュボード](https://api.slack.com/apps)下部の「Your Config Tokens」から生成できます。

#### 方法 B: 手動セットアップ

1. [Slack API](https://api.slack.com/apps) → 「Create New App」→「From an app manifest」
2. `slack-app-manifest.yml` の内容を貼り付け
3. ワークスペースを選択して作成

#### Socket Mode の有効化（App-Level Token の取得）

1. Settings > **Socket Mode** → Enable
2. 「Generate Token and Scopes」→ Scope に **`connections:write`** を追加 → Generate
3. 生成された **`xapp-...`** トークンをメモ → `.env` の `SLACK_APP_TOKEN` に設定

> Socket Mode は MWM がリアルタイムでメッセージを受信するために必要です。CWM だけ使う場合は不要。

#### ワークスペースにインストール（Bot Token の取得）

1. Features > **OAuth & Permissions** → 「Install to Workspace」→ Allow
2. **Bot User OAuth Token** (`xoxb-...`) をメモ → `.env` の `SLACK_BOT_TOKEN` に設定

> Scope を変更した場合は「Reinstall to Workspace」が必要です。

### 4. `.env` の設定

```bash
cp .env.example .env
```

`.env` を開き、以下を設定します:

```bash
# 必須（CWM・MWM 共通）
SLACK_BOT_TOKEN=xoxb-...           # Step 3 で取得した Bot Token
ANTHROPIC_API_KEY=sk-ant-...       # Anthropic コンソールから取得

# MWM を使う場合
SLACK_APP_TOKEN=xapp-...           # Step 3 で取得した App-Level Token
MWM_BOT_CHANNEL_ID=C0XXXXXXXX     # bot の操作・投稿先チャンネル

# CWM を使う場合
CWM_SOURCE_CHANNEL_IDS=C0AAA,C0BBB  # 読み取り対象チャンネル
```

#### チャンネル ID の調べ方

Slack でチャンネル名をクリック → チャンネル詳細のポップアップ最下部に `C` から始まる ID が表示されます。

または、チャンネルを右クリック →「Copy link」→ URL 末尾の `C...` 部分がチャンネル ID です。

### 5. bot をチャンネルに招待

Slack で使用するチャンネルごとに bot を招待してください:

```
/invite @cpc-camp-bot
```

| 用途 | 招待先 | なぜ必要か |
|------|--------|-----------|
| CWM | 読み取り対象チャンネル（`CWM_SOURCE_CHANNEL_IDS`） | 過去の議論メッセージを取得するため |
| MWM | bot チャンネル（`MWM_BOT_CHANNEL_ID`） | コマンド受付と bot の発言投稿先 |
| MWM | セッションチャンネル（`!session start` で動的に指定） | プレゼン中のリアルタイム議論を監視するため |

> bot を招待しないとそのチャンネルのメッセージを読み取れません。

## 使い方

### CWM — ホワイトペーパー生成

Slack チャンネルの議論を Claude で要約し、マークダウンのホワイトペーパーを生成します。

```bash
# ローカルに出力（最もシンプルな使い方）
uv run cpc-cwm --local

# チャンネル指定 + 出力先指定
uv run cpc-cwm --channel C0AAA C0BBB --local --local-path whitepapers/output.md

# GitHub リポジトリに push（GITHUB_TOKEN, GITHUB_REPO の設定が必要）
uv run cpc-cwm

# メッセージ数の上限やモデルを指定
uv run cpc-cwm --channel C0AAA --limit 1000 --model claude-sonnet-4-20250514
```

### MWM — マルチエージェント議論 bot

AI ペルソナが Slack 上でリアルタイムに議論に参加します。起動すると Socket Mode で Slack に常時接続し、メッセージを監視します。

```bash
# 起動（デフォルト: agents/ada.yml）
uv run cpc-mwm

# エージェント設定を指定して起動
uv run cpc-mwm --agent-config agents/karl.yml

# ホワイトペーパーを注入して起動（ペルソナが内容を踏まえて議論）
uv run cpc-mwm --whitepaper whitepapers/latest.md
```

#### Slack コマンド（bot チャンネルで実行）

| コマンド | 説明 |
|---------|------|
| `!session start <名前> <チャンネルID>` | プレゼンモードでセッション開始 |
| `!session start-free <名前> <チャンネルID>` | フリーモードでセッション開始 |
| `!moltbook` | bot チャンネル内で即フリーモード開始 |
| `!session end` | セッション終了 |
| `!session status` | セッション状態表示 |

#### セッションモードの違い

```
プレゼンモード (start)                フリーモード (start-free / moltbook)
┌─────────────────────────┐         ┌─────────────────────────┐
│ セッションチャンネルを     │         │ エージェント同士が        │
│ 監視し、人間の議論に       │         │ 自律的にトピックを        │
│ 反応して bot チャンネルに   │         │ 立てて議論する            │
│ コメントを投稿            │         │                         │
│                         │         │ 人間の発言にも反応する     │
│ 用途: 発表セッション中     │         │ 用途: ブレスト、雑談      │
└─────────────────────────┘         └─────────────────────────┘
```

- **プレゼンモード**: 別チャンネルの議論を読み取り専用で監視 → bot チャンネルにコメント投稿。人間のプレゼン中にAIが裏で議論するイメージ
- **フリーモード**: bot 同士が自律的に議論を展開。応答間隔が短く（デフォルト60秒）、人間の発言がなくても会話が続く

#### ファイルのアップロード

bot チャンネルに以下のファイルをアップロードすると自動で読み込まれ、エージェントの文脈に追加されます:

- **PDF**: スライド資料（ページごとにテキスト抽出）
- **VTT**: 字幕ファイル（話者ごとにトランスクリプト化）

### 統合実行 — CWM → MWM パイプライン

```bash
uv run python scripts/orchestrate.py
```

CWM でホワイトペーパーを生成 → その内容をペルソナに注入した状態で MWM を起動します。合宿の典型的なワークフローです。

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

## チャンネル構成

MWM は複数のチャンネルを使い分けます:

```
┌─────────────────────────────────────────────────┐
│ Slack ワークスペース                               │
│                                                 │
│  #mwm-bot (MWM_BOT_CHANNEL_ID)                  │
│  ├── !session start / end / status  ← コマンド    │
│  ├── PDF・VTT アップロード            ← 素材投入   │
│  └── bot の発言が投稿される           ← 出力       │
│                                                 │
│  #session-channel (動的に指定)                     │
│  └── 人間の議論を読み取り専用で監視     ← 入力       │
│                                                 │
│  #research-discussion (CWM_SOURCE_CHANNEL_IDS)   │
│  └── CWM がメッセージを一括取得        ← 入力       │
└─────────────────────────────────────────────────┘
```

全チャンネルで bot の招待（`/invite @cpc-camp-bot`）が必要です。

## プロジェクト構成

uv workspace による monorepo 構成です。3つのパッケージが相互に依存しています。

```
cpc-mwm-cwm/
├── packages/
│   ├── agent-utils/       # 共有ライブラリ（Slack, Claude, Config, Persona）
│   │   └── src/agent_utils/
│   │       ├── config.py          # BaseConfig（環境変数読み込み）
│   │       ├── persona.py         # ペルソナ .md 読み込み・system prompt 生成
│   │       ├── claude_client.py   # Anthropic クライアント生成
│   │       └── models.py          # Message 等の共有データクラス
│   ├── cpc-cwm/           # ホワイトペーパー生成
│   │   └── src/cpc_cwm/
│   └── cpc-mwm/           # マルチエージェント議論 bot
│       └── src/cpc_mwm/
│           ├── main.py            # エントリポイント・イベントループ
│           ├── agent.py           # 2フェーズ Agent（perceive/respond）
│           ├── agent_config.py    # YAML config ローダー
│           ├── session.py         # セッション・観測データ管理
│           ├── slack_app.py       # Slack イベントハンドラ
│           ├── slides.py          # PDF スライド処理
│           └── transcript.py      # VTT トランスクリプト処理
├── agents/                # エージェント設定 YAML（perception/response プロンプト）
├── personas/              # ペルソナ定義ファイル（system prompt になる）
├── whitepapers/           # CWM が生成したホワイトペーパー
├── scripts/               # セットアップ・オーケストレーション
├── .env                   # 環境変数（トークン等、git 管理外）
└── pyproject.toml         # ワークスペースルート設定
```

## 自分のエージェントを作る

新しいエージェントの作り方は [CONTRIBUTING.md](CONTRIBUTING.md) を参照してください。コードの変更は不要で、ペルソナファイルとエージェント設定 YAML の2つを作るだけです。

## トラブルシューティング

### `uv sync` でエラーが出る

- Python 3.12 以上がインストールされているか確認: `python3 --version`
- uv が最新か確認: `uv self update`

### bot がメッセージを受信しない

1. **bot を招待したか**: 対象チャンネルで `/invite @cpc-camp-bot` を実行
2. **Event Subscriptions**: Slack API ダッシュボード → Event Subscriptions で `message.channels` と `message.groups` が設定されているか確認
3. **Socket Mode**: Settings > Socket Mode が有効になっているか確認
4. **Reinstall**: Scope を変更した場合は OAuth & Permissions → 「Reinstall to Workspace」が必要
5. **トークン**: `.env` の `SLACK_BOT_TOKEN`（`xoxb-...`）と `SLACK_APP_TOKEN`（`xapp-...`）が正しいか確認

### bot が発言しない

- ログに `Perceive (AgentName): NO` が続く場合 → セッションに十分な文脈がまだない。スライドをアップロードするか、チャンネルで議論を進める
- ログに `Not enough new context, skipping` が出る場合 → 前回の判定以降に新しいメッセージが追加されていない
- `Daily API call limit reached` → `MAX_DAILY_API_CALLS`（デフォルト200）に達した。翌日リセットされる

### `onnxruntime` のインストールエラー

macOS で `onnxruntime` の wheel が見つからないエラーが出る場合、`pyproject.toml` で `onnxruntime` のバージョンを制約しています（`<1.24`）。通常は `uv sync` で自動解決されます。

### BlackHole が認識されない

`brew install blackhole-2ch` 後に macOS の再起動が必要です。再起動後、Audio MIDI Setup で BlackHole 2ch が表示されることを確認してください。
