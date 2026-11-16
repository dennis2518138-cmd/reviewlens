import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.rag import index_reviews     


def load_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    ap = argparse.ArgumentParser(description="把评论写入向量库")
    ap.add_argument("--input", default=str(ROOT / "data" / "reviews_raw.jsonl"))
    ap.add_argument("--keep", action="store_true", help="增量模式：不删旧集合")
    args = ap.parse_args()

    rows = load_rows(args.input)
    stats = index_reviews(rows, reset=not args.keep)
    print(json.dumps(stats, ensure_ascii=False))
    if stats["indexed"] == 0:
        raise SystemExit("没有可入库的评论，检查输入文件")


if __name__ == "__main__":
    main()
