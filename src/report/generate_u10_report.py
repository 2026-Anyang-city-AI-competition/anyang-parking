"""U10 오류 리포트 생성 경로를 U11 CSV 검증·자동 판정으로 대체한다."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.report.u11_report import generate

if __name__ == "__main__":
    generate()
