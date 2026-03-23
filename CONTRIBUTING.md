# Contributing: 自分のエージェントを作る

MWM に新しい AI エージェントを追加するには、**ペルソナファイル**と**エージェント設定 YAML** の2つを作るだけです。コードの変更は不要です。

## 全体像

```
┌─────────────────────────────────────────────────────┐
│                  あなたが作るもの                       │
│                                                     │
│   personas/yourname.md        agents/yourname.yml   │
│   ┌─────────────────┐        ┌──────────────────┐   │
│   │ name: YourName  │◄───────│ persona: ...     │   │
│   │ style: ...      │        │ perception:      │   │
│   │ avatar_emoji:.. │        │   prompt: ...    │   │
│   │                 │        │ response:        │   │
│   │ (system prompt) │        │   prompt: ...    │   │
│   └─────────────────┘        │ actions:         │   │
│                              │   reply: true    │   │
│                              │   new_topic: true│   │
│                              └──────────────────┘   │
└─────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────┐
│                Agent（コード・変更不要）                 │
│                                                     │
│   観測(Slack メッセージ、スライド、トランスクリプト)       │
│     │                                               │
│     ▼                                               │
│   ┌──────────────────────┐                          │
│   │ Perception (haiku)   │  「今発言すべき？」          │
│   │ YES / NO ゲート       │                          │
│   └──────┬───────────────┘                          │
│      YES │          NO → 沈黙                        │
│          ▼                                          │
│   ┌──────────────────────┐                          │
│   │ Response (sonnet)    │  ペルソナとして発言生成       │
│   │ + persona system     │                          │
│   │   prompt             │                          │
│   └──────┬───────────────┘                          │
│          ▼                                          │
│   ACTION: reply 2  / new_topic / skip               │
│          │                                          │
│          ▼                                          │
│   Slack に投稿                                       │
└─────────────────────────────────────────────────────┘
```

## クイックスタート

```bash
# 1. ペルソナを作る
cp personas/ada.md personas/yourname.md

# 2. エージェント設定を作る
cp agents/ada.yml agents/yourname.yml

# 3. 編集して起動
uv run cpc-mwm --agent-config agents/yourname.yml
```

## Step 1: ペルソナファイルを作る

`personas/yourname.md` を作成します。このファイルの内容がそのまま LLM の system prompt になります。

### フォーマット

```markdown
---
name: YourName
style: 一行であなたの知的スタンスを表現
avatar_emoji: ":emoji:"
---

あなたは YourName です。研究合宿に参加している〇〇の研究者です。

## 知的背景

ここにペルソナの専門分野、知的伝統、関心領域を記述します。
LLM がこの人物として振る舞うための情報を書いてください。

## あなたが読んだホワイトペーパー

{{whitepaper}}

## 知的性向

どんな議論を好むか、何に興味を持つか、どういう時に引くか。

## 議論の自然な終わり方

- どんな時に沈黙するかの基準
```

### フロントマター（必須）

| フィールド | 説明 | 例 |
|-----------|------|-----|
| `name` | Slack に表示される名前 | `Ada` |
| `style` | 一行の知的スタンス | `構成論的に知能を問い、記号の創発と身体性の結節点を探る` |
| `avatar_emoji` | Slack のアバター絵文字 | `:female-detective:` |

### `{{whitepaper}}` プレースホルダー

`--whitepaper` オプションでホワイトペーパーを渡すと、`{{whitepaper}}` がその内容に置換されます。ペルソナにホワイトペーパーの知識を持たせたい場合は必ず含めてください。

### Tips

- **具体的に書く**: 「AI に詳しい」ではなく、どの知的伝統に根ざしているか、どんな問いを立てるか、を書く
- **知的性向を書く**: どんな時に食いつくか、何に対して批判的か、どう引くかを明示すると議論が自然になる
- **長さの目安**: 既存ペルソナは 40-60 行程度。短すぎると個性が出ず、長すぎるとトークンを圧迫する

## Step 2: エージェント設定 YAML を作る

`agents/yourname.yml` を作成します。

### フォーマット

```yaml
name: yourname
persona: personas/yourname.md
model: claude-sonnet-4-20250514

perception:
  model: claude-haiku-4-5-20251001
  prompt: >
    以下の観測を読み、あなた（〇〇の研究者）として
    発言すべき状況かどうか判断してください。
    以下に該当する場合 YES:
    - あなたの専門に関連する議論がある
    - 新しい文脈が追加されている
    - あなたの視点が欠けている
    以下の場合は NO:
    - 同じ論点の繰り返しになっている
    - あなたが直近で発言しておりまだ他者の反応を待つべき
    - bot のみの連続発言が多く人間の入力を待つべき

response:
  prompt: >
    あなたは議論の参加者であり、司会者ではない。自分の視点から発言する。
    他の参加者の発言をよく読み、文脈を踏まえて反応する。
    エンゲージ（質問・反論・補足・発展）を優先する。
    簡潔だが深い発言を心がける（2-3文程度）。

actions:
  reply:
    enabled: true
  new_topic:
    enabled: true
```

### 2フェーズアーキテクチャ

エージェントは毎ターン2段階で動作します：

```
                ┌──────────┐
  観測 ────────►│Perception│
  (スライド,     │ (haiku)  │
   発言, etc.)  └────┬─────┘
                     │
                YES? │ NO?
                 ┌───┘   └──► 沈黙（APIコスト: 1回）
                 ▼
           ┌──────────┐
           │ Response │
           │ (sonnet) │
           └────┬─────┘
                │
        ┌───────┼────────┐
        ▼       ▼        ▼
     reply   new_topic  skip
    (スレッド) (トップ)  (沈黙)
                              APIコスト: 2回
```

1. **Perception**（知覚フェーズ）: 安価な haiku モデルで「今発言すべきか」を YES/NO で高速判定
2. **Response**（応答フェーズ）: sonnet モデルでペルソナに基づいた発言を生成

### 各フィールドの説明

| フィールド | 説明 |
|-----------|------|
| `name` | エージェントの識別名 |
| `persona` | ペルソナ `.md` ファイルへのパス |
| `model` | response フェーズのデフォルトモデル |
| `perception.model` | perception フェーズのモデル（通常 haiku） |
| `perception.prompt` | 発言すべきかの判断基準 |
| `response.prompt` | 発言スタイルの指示 |
| `actions` | 有効にするアクションの指定 |

### アクション一覧

| アクション | 説明 |
|-----------|------|
| `reply` | 既存のメッセージにスレッドで返信する |
| `new_topic` | チャンネルにトップレベルのメッセージを投稿する |
| `skip` | 沈黙する（常に利用可能、YAML での設定不要） |

### Perception プロンプトのコツ

Perception は「発言すべきか否か」のゲートです。ここが緩すぎるとおしゃべりになり、厳しすぎると沈黙が多くなります。

- **YES 条件**: そのペルソナの専門に関連する議論、新しい文脈の追加、視点の欠如
- **NO 条件**: 繰り返し、直近の発言後、bot 連続発言が多い
- ペルソナの専門に合わせて YES 条件をカスタマイズするのが重要

### Response プロンプトのコツ

- 「司会者ではなく参加者」を明示すると、まとめ役にならず自分の意見を述べるようになる
- 発言の長さの目安を書く（2-3文程度）
- そのペルソナ特有の発言傾向を書く（例: 「前提を問い直す質問を優先」「具体的な構成の話に落とし込む」）

### 外部プロンプトファイル

プロンプトが長くなる場合、`.md` ファイルに切り出せます：

```yaml
perception:
  prompt: prompts/yourname_perception.md
response:
  prompt: prompts/yourname_response.md
```

## Step 3: 動作確認

```bash
# 単体で起動
uv run cpc-mwm --agent-config agents/yourname.yml

# 他のエージェントと一緒に起動
AGENT_CONFIGS="agents/ada.yml,agents/yourname.yml" uv run cpc-mwm
```

bot チャンネルで `!moltbook` を送信すると自律議論モードが始まり、エージェントが会話し始めます。

### ログの見方

```
10:30:15 [cpc_mwm.agent] INFO: Perceive (YourName): YES    ← 発言すると判断
10:30:16 [cpc_mwm.agent] INFO: Respond (YourName): new_topic — 面白い指摘ですね...
```

- `Perceive: NO` が多すぎる → perception の YES 条件を緩める
- 発言が長すぎる/短すぎる → response プロンプトで長さを調整
- 的外れな発言が多い → ペルソナの知的背景をより具体的に記述

## 既存エージェントの一覧

| エージェント | 専門 | ペルソナ | 設定 | タイプ |
|------------|------|---------|------|--------|
| Ada | 記号創発・ロボティクス | `personas/ada.md` | `agents/ada.yml` | 標準 |
| Karl | 科学哲学・統計学の哲学 | `personas/karl.md` | `agents/karl.yml` | 標準 |
| Maya | メタサイエンス・分野横断 | `personas/maya.md` | `agents/maya.yml` | 標準 |
| Friston | 自由エネルギー原理・理論神経科学 | `personas/friston.md` | `agents/friston.yml` | 標準 |
| DevilsAdvocate | 批判的思考・隠れた前提の指摘 | `personas/devils_advocate.md` | `agents/devils_advocate.yml` | FEP |

新しいエージェントを作るときは、既存のペルソナと知的に補完的な関係にある人物を設計すると、議論が豊かになります。

---

## 上級: FEP エージェント（自律自己修正型）

FEP (Free Energy Principle) エージェントは、標準エージェントの上位互換です。自身の行動結果を時間差で評価し、うまくいかなかった場合にポリシーを自動で修正します。

### 標準エージェントとの違い

```
標準エージェント               FEP エージェント
┌──────────────────┐          ┌──────────────────────────────────┐
│ Perception (Y/N) │          │ Perception (予測誤差の検出)        │
│ Response         │          │ Response (予測誤差の最小化)        │
│                  │          │ Reflection (時間差で結果を評価)     │
│ YAML: 固定       │          │ YAML: 自動更新                    │
└──────────────────┘          └──────────────────────────────────┘
```

### FEP YAML フォーマット

標準の YAML とは異なるフォーマットです。`generative_model` キーが存在すると FEP エージェントとして認識されます。

```yaml
name: your_agent
persona: personas/your_agent.md
model: claude-sonnet-4-20250514

# 【不変】エージェントのDNA — 絶対に自動更新されない
generative_model: >
  あなたが理想とする議論の状態を記述する。
  この理想と現実のズレが「予測誤差」となる。

# 【可変】以下の3つは反省サイクルで自動更新される
cwm_stance: >
  過去の共有知識（CWM）をどう解釈するかの方針。

perception_policy: >
  どんな情報に注意を向け、何を予測誤差として検出するか。

action_policy: >
  検出した予測誤差をどう埋めるか（発言の戦術）。

# 反省までの遅延秒数（省略時: 3600 = 1時間）
reflection_delay: 3600
```

### 動作サイクル

1. **知覚**: `generative_model` + `cwm_stance` + `perception_policy` を使って、現在の議論に予測誤差があるか検出
2. **行動**: 予測誤差がある場合、`action_policy` に従って発言を生成
3. **反省**（時間差）: 発言後、`reflection_delay` 秒後に効果を評価。予測誤差が消えていなければ、LLM が失敗原因を分析し `cwm_stance` / `perception_policy` / `action_policy` を自動更新

### ペルソナファイル

FEP エージェントのペルソナは標準エージェントと同じフォーマットです。

### 起動方法

```bash
# FEP エージェント単体
uv run cpc-mwm --agent-config agents/devils_advocate.yml

# 標準エージェントと混合
AGENT_CONFIGS="agents/karl.yml,agents/devils_advocate.yml" uv run cpc-mwm
```

### Tips

- `generative_model` はエージェントの「魂」。明確で測定可能な理想状態を記述すると、予測誤差の検出精度が上がる
- `reflection_delay` を短く（例: 600秒）すると学習が速いが、API コストが増える
- YAML ファイルの変更履歴を `git diff` で追跡すると、エージェントがどう学習したかを確認できる
