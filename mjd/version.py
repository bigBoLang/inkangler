"""版本号的唯一出处。

改这里，标题栏、闪屏、exe 文件属性（右键 → 属性 → 详细信息）都会跟着变；
打包时 `tools/build_exe.py` 会读它生成 Windows 版本资源。
"""

VERSION = "1.0.2"
APP_NAME = "墨迹大侠 · 自动钓鱼"
AUTHOR = "大波浪"
REPO = "https://github.com/bigBoLang/inkangler"

# 界面上显示的使用声明
NOTICE = "仅供个人自用，禁止传播、转售或用于任何违法用途。"


def version_line() -> str:
    return "v" + VERSION
