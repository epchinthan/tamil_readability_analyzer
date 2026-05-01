import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from tamil_readability_analyzer.app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
