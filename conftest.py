"""让 `pytest` 在未安装 deepsee 的环境(如系统 python 的 pre-push hook)也能导入源码树。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
