"""순위 민감도는 U11의 A/B/C 공통 정렬·OOF 예측으로 재계산한다."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.models.u11_evaluate import rerank

if __name__ == "__main__":
    rerank()
