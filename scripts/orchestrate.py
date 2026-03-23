"""cwm でホワイトペーパー生成 → mwm でペルソナに注入して議論開始."""

import subprocess
import sys
from pathlib import Path


def main() -> None:
    wp_path = Path("whitepapers/latest.md")

    # Step 1: ホワイトペーパー生成
    print("=== Step 1: ホワイトペーパー生成 ===")
    subprocess.run(
        [sys.executable, "-m", "cpc_cwm.main", "--local", "--local-path", str(wp_path)],
        check=True,
    )
    print(f"ホワイトペーパーを {wp_path} に保存しました。")

    # Step 2: MWM 起動（WP 注入付き）
    print("\n=== Step 2: MWM 起動（ホワイトペーパー注入） ===")
    subprocess.run(
        [sys.executable, "-m", "cpc_mwm.main", "--whitepaper", str(wp_path)],
        check=True,
    )


if __name__ == "__main__":
    main()
