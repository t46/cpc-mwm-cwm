#!/usr/bin/env bash
set -euo pipefail

# Slack App のセットアップスクリプト（CWM + MWM 統合版）
# 前提: SLACK_CONFIG_TOKEN 環境変数にConfiguration Tokenが設定されていること
#
# Configuration Token の取得方法（ブラウザで1回だけ必要）:
#   1. https://api.slack.com/apps にアクセス
#   2. 任意のアプリ → "Your Config Tokens" → Generate Token
#   3. 取得したトークンを SLACK_CONFIG_TOKEN に設定

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFEST_FILE="${SCRIPT_DIR}/../slack-app-manifest.yml"
ENV_FILE="${1:-.env}"

if [ -z "${SLACK_CONFIG_TOKEN:-}" ]; then
    echo "エラー: SLACK_CONFIG_TOKEN が設定されていません。"
    echo ""
    echo "Configuration Token を取得してください:"
    echo "  1. https://api.slack.com/apps にアクセス"
    echo "  2. ページ下部の 'Your Config Tokens' → Generate Token"
    echo "  3. export SLACK_CONFIG_TOKEN=xoxe.xoxp-..."
    exit 1
fi

if [ ! -f "$MANIFEST_FILE" ]; then
    echo "エラー: マニフェストファイルが見つかりません: $MANIFEST_FILE"
    exit 1
fi

# yq が必要（YAML → JSON 変換）
if ! command -v yq &> /dev/null; then
    echo "yq が必要です。インストール: brew install yq"
    exit 1
fi

MANIFEST_JSON=$(yq -o=json "$MANIFEST_FILE")

echo "=== Slack App を作成中 ==="

RESPONSE=$(curl -s -X POST "https://slack.com/api/apps.manifest.create" \
    -H "Authorization: Bearer ${SLACK_CONFIG_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{\"manifest\": ${MANIFEST_JSON}}")

OK=$(echo "$RESPONSE" | yq -p=json -r '.ok')
if [ "$OK" != "true" ]; then
    ERROR=$(echo "$RESPONSE" | yq -p=json -r '.error // "unknown error"')
    echo "エラー: App の作成に失敗しました: $ERROR"
    echo "$RESPONSE" | yq -p=json -P
    exit 1
fi

APP_ID=$(echo "$RESPONSE" | yq -p=json -r '.app_id')
echo "App 作成完了: APP_ID=$APP_ID"

# App-Level Token (Socket Mode 用) は API では生成できないため手動で取得
APP_TOKEN=""
echo ""
echo "=== App-Level Token（手動生成が必要）==="
echo "Slack API では App-Level Token の自動生成ができません。"
echo "以下の手順で手動生成してください:"
echo "  1. https://api.slack.com/apps/${APP_ID}/general にアクセス"
echo "  2. 'App-Level Tokens' セクションで 'Generate Token and Scopes' をクリック"
echo "  3. Token Name に任意の名前（例: socket-mode）を入力"
echo "  4. Scope に 'connections:write' を追加"
echo "  5. 'Generate' をクリックして xapp-... トークンをコピー"

# .env ファイルに書き出し
echo ""
echo "=== ${ENV_FILE} に書き出し中 ==="

if [ -f "$ENV_FILE" ]; then
    echo "${ENV_FILE} は既に存在します。上書きしますか？ [y/N]"
    read -r confirm
    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        echo "中止しました。以下を手動で設定してください:"
        echo "  SLACK_APP_TOKEN=<手動で取得した xapp-... トークンを設定>"
        echo ""
        echo "Bot Token はワークスペースへのインストール後に取得できます:"
        echo "  https://api.slack.com/apps/${APP_ID}/oauth"
        exit 0
    fi
fi

cat > "$ENV_FILE" << EOF
# === Slack App（共通）===
SLACK_BOT_TOKEN=<ワークスペースにインストール後に取得: https://api.slack.com/apps/${APP_ID}/oauth>
SLACK_APP_TOKEN=<手動で取得した xapp-... トークンを設定>

# === 共通 ===
ANTHROPIC_API_KEY=<Anthropic API キーを設定>
MODEL_NAME=claude-sonnet-4-20250514

# === CWM（ホワイトペーパー生成）===
CWM_SOURCE_CHANNEL_IDS=<読み取り対象チャンネル ID をカンマ区切りで設定>
GITHUB_TOKEN=<GitHub Token を設定（--local モードなら不要）>
GITHUB_REPO=<owner/repo を設定（--local モードなら不要）>

# === MWM（マルチエージェント議論 bot）===
MWM_BOT_CHANNEL_ID=<bot チャンネルの ID を設定>
PERSONA_FILES=personas/ada.md,personas/karl.md,personas/maya.md
WHITEPAPER_PATH=
RESPONSE_INTERVAL_SECONDS=120
ENABLE_AUDIO=false
EOF

echo "${ENV_FILE} を作成しました。"

echo ""
echo "=== 残りの手動ステップ ==="
echo "1. App-Level Token を生成（上記の手順参照）して ${ENV_FILE} の SLACK_APP_TOKEN に設定"
echo "2. ワークスペースにインストール（ブラウザで認可が必要）:"
echo "   https://api.slack.com/apps/${APP_ID}/oauth"
echo "3. Bot User OAuth Token (xoxb-...) を ${ENV_FILE} の SLACK_BOT_TOKEN に設定"
echo "4. ANTHROPIC_API_KEY、CWM_SOURCE_CHANNEL_IDS、MWM_BOT_CHANNEL_ID を設定"
echo "5. bot をチャンネルに招待: /invite @cpc-camp-bot"
echo "   - CWM 用: 読み取り対象チャンネル"
echo "   - MWM 用: bot チャンネル + セッションチャンネル"
