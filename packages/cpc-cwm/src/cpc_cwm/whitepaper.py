"""Generate a whitepaper from Slack discussion using Claude."""

import logging

import anthropic

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
あなたは学術的な議論を分析し、ホワイトペーパーとしてまとめる専門家です。

Slackチャネルから抽出された議論データを受け取ります。
この議論の中から「AI agentの役割・設計・インタラクション」に
関連する内容を特定し、構造化されたホワイトペーパーとして
markdownフォーマットでまとめてください。

## 出力フォーマット

以下の構造に従ってください:

```
# [タイトル]

## Abstract
（議論全体の要約。3-5文）

## 1. Introduction
（背景と問題意識）

## 2. Key Themes
（議論から抽出された主要テーマごとにサブセクション）

### 2.1 [テーマ名]
- 議論の要点
- 参加者の主要な主張（名前付きで引用）

### 2.2 [テーマ名]
...

## 3. Points of Convergence
（参加者間で合意が見られた点）

## 4. Points of Divergence
（意見が分かれた点、未解決の論点）

## 5. Open Questions
（議論から浮上した未回答の問い）

## 6. Implications and Future Directions
（議論が示唆する今後の方向性）

## Appendix: Discussion Participants
（参加者リスト）
```

## ルール
- 議論の中でAI agentに関連しない部分は省略してよい
- 参加者の発言を正確に反映する（意見を歪めない）
- 特定の立場を支持しない。中立的にまとめる
- 議論にない主張を追加しない
- 日本語の議論は日本語でまとめる。英語は英語で。
"""


def generate_whitepaper(
    client: anthropic.Anthropic,
    discussion_text: str,
    model: str = "claude-sonnet-4-20250514",
) -> str:
    """Generate a whitepaper markdown from formatted discussion text."""
    logger.info("Generating whitepaper with Claude...")

    response = client.messages.create(
        model=model,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    "以下のSlackチャネルの議論をホワイトペーパーとして"
                    "まとめてください。\n\n"
                    "---\n\n"
                    f"{discussion_text}"
                ),
            }
        ],
    )

    whitepaper = response.content[0].text
    logger.info(f"Whitepaper generated: {len(whitepaper)} chars")
    return whitepaper
