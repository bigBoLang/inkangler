"""把界面打包成单文件 exe，直接放到桌面。

用法（每次改完代码跑一遍就行）：
    python tools/build_exe.py              # 正式版：带管理员清单，双击会弹 UAC
    python tools/build_exe.py --no-admin   # 测试版：不提权，只用来检查能不能启动

为什么要管理员：微信如果是以管理员身份运行的，普通权限的程序既抓不到它的
画面（PrintWindow 返回 0），也发不进模拟点击（UIPI 拦截）。带 --uac-admin
清单以后，双击 exe 就是管理员权限，和微信同级，就不会再出现「点了没反应」。
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mjd.version import APP_NAME, AUTHOR, NOTICE, VERSION  # noqa: E402

NAME = "墨迹大侠自动钓鱼"


def write_version_file(name: str) -> Path:
    """生成 Windows 版本资源，右键 → 属性 → 详细信息 里就能看到版本号。"""
    nums = tuple(([int(x) for x in re.findall(r"\d+", VERSION)] + [0, 0, 0, 0])[:4])
    text = """# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=%s, prodvers=%s,
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', %r),
        StringStruct('FileDescription', %r),
        StringStruct('FileVersion', %r),
        StringStruct('InternalName', 'inkangler'),
        StringStruct('LegalCopyright', %r),
        StringStruct('Comments', %r),
        StringStruct('OriginalFilename', %r),
        StringStruct('ProductName', 'inkangler'),
        StringStruct('ProductVersion', %r)
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
""" % (nums, nums, AUTHOR, APP_NAME, VERSION, "© " + AUTHOR, NOTICE,
       name + ".exe", VERSION)
    p = ROOT / "build" / "version_info.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def desktop() -> Path:
    for cand in (Path.home() / "Desktop", Path.home() / "OneDrive" / "Desktop",
                 Path.home() / "桌面"):
        if cand.is_dir():
            return cand
    return Path.home() / "Desktop"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-admin", action="store_true", help="不要求管理员权限（测试用）")
    ap.add_argument("--name", default=NAME)
    a = ap.parse_args()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--splash", str(ROOT / "assets" / "splash.png"),
        "--version-file", str(write_version_file(a.name)),
        "--name", a.name,
        "--paths", str(ROOT),
        "--add-data", "%s;mjd" % (ROOT / "mjd" / "digit_bank.npz"),
        "--add-data", "%s;mjd/templates" % (ROOT / "mjd" / "templates"),
        "--hidden-import", "PIL._tkinter_finder",
        "--hidden-import", "mjd._pubkey",
        "--hidden-import", "cryptography.hazmat.primitives.asymmetric.ed25519",
        "--hidden-import", "cryptography.hazmat.primitives.serialization",
        "--hidden-import", "cryptography.exceptions",
    ]
    if not a.no_admin:
        args.append("--uac-admin")
    args.append(str(ROOT / "tools" / "fishui.py"))

    print("正在打包 …（第一次要一两分钟）")
    subprocess.run(args, cwd=str(ROOT), check=True)

    src = ROOT / "dist" / (a.name + ".exe")
    if not src.exists():
        print("打包失败：没生成 %s" % src)
        return 1
    dst = desktop() / (a.name + ".exe")
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        print("桌面上的 %s 正在运行，文件被占用，覆盖不了。" % dst.name)
        print("先把那个窗口关掉，再跑一次 python tools/build_exe.py")
        return 1
    print("已生成：%s  (%.1f MB)" % (dst, dst.stat().st_size / 1024 / 1024))

    # 本机授权也一起放好（只有本机的 license.key 才会通过校验）
    lic_src = ROOT / "license.key"
    lic_dst = dst.parent / "license.key"
    if lic_src.exists() and not lic_dst.exists():
        shutil.copy2(lic_src, lic_dst)
        print("已放好本机授权：%s" % lic_dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
