"""U10의 잘못된 마스크/CQR 구현은 U11 공통 평가로 대체됐다."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.models.u11_evaluate import run

if __name__ == "__main__":
    run()
