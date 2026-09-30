import sys
from pathlib import Path

# 允许在未安装到 site-packages 时（如本地 .deps 目录）直接运行 pytest。
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
