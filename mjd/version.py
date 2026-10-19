"""版本号的唯一出处。

改这里，标题栏、闪屏、exe 文件属性（右键 → 属性 → 详细信息）都会跟着变；
打包时 `tools/build_exe.py` 会读它生成 Windows 版本资源。
"""

VERSION = "1.0.0"
APP_NAME = "墨迹大侠 · 自动钓鱼"
REPO = "https://github.com/bigBoLang/inkangler"


def version_line() -> str:
    return "v" + VERSION
