#!/usr/bin/env python3
"""parking_access_rules.csv 검증 CLI."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import ACCESS_RULES_CSV, PARKING_DB
from src.serve.access_rules import format_result, validate_access_rules


def main():
    parser = argparse.ArgumentParser(description="주차장 출입·과금 조사 CSV를 검증합니다")
    parser.add_argument("path", nargs="?", type=Path, default=ACCESS_RULES_CSV)
    parser.add_argument("--parking-db", type=Path, default=PARKING_DB)
    args = parser.parse_args()
    result = validate_access_rules(args.path, args.parking_db)
    print(format_result(result))
    return 0 if result.valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
