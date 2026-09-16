# -*- coding: utf-8 -*-
"""
重跑 eda 目录下三个 notebook（原地执行，刷新输出到最新数据库）。

用法（在 eda 目录下）:
    python tools/rerun_notebooks.py

说明：
- 对数据库只读（common.py 使用 sqlite mode=ro）；
- Windows 下 jupyter_client 默认会给连接文件设 DACL（SetFileSecurity），
  在某些受限环境会报“拒绝访问”，这里将其置为 no-op 并改用工作区内的 runtime 目录。
"""
import os
import sys

# 先 patch，再导入 jupyter 组件
import jupyter_core.paths as _paths

def _noop_restrict(fname):
    pass

_paths.win32_restrict_file_to_user = _noop_restrict

# runtime/数据目录放工作区，避免 %TEMP% 权限问题
os.environ.setdefault("JUPYTER_RUNTIME_DIR",
                      os.path.abspath(os.path.join(os.path.dirname(__file__), ".jupyter_runtime")))
os.environ.setdefault("JUPYTER_DATA_DIR",
                      os.path.abspath(os.path.join(os.path.dirname(__file__), ".jupyter_data")))

from nbconvert.nbconvertapp import main  # noqa: E402

if __name__ == "__main__":
    sys.argv = [
        "jupyter-nbconvert",
        "--to", "notebook",
        "--execute", "--inplace",
        "00_data_overview.ipynb",
        "01_buy_timing.ipynb",
        "02_structure_volatility.ipynb",
        "--ExecutePreprocessor.timeout=600",
    ]
    raise SystemExit(main())
