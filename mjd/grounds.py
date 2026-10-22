"""切换渔场。

游戏里怎么切
------------
主界面顶部「紫霞渔场」标题右边有个圆形箭头图标（552x991 坐标 (364,147)），
点开是「切换渔场」面板：

    上面  预览图 + 鱼种
    左下  当前选中的渔场名（紫色/青色那条）
    右下  橙色「切换」/ 绿色「升级钓具」
    底部  一排渔场卡片（缩略图 + 等级，可以左右拖）

点一张卡片 → 名字变了 → 点「切换」→ 顶部标题就换了。面板不会自己关，
切完点右上角红叉关掉。

卡片上没有名字
--------------
卡片只有缩略图和等级，名字只在左下信息条里。所以这里给每个渔场存了一张
信息条模板（`mjd/ground_templates/*.png`，只截名字那一块），点一张卡片、
截一下信息条、比一下就知道是哪张卡。实测：同名字差 0.0，最接近的异类
≥7.2，阈值取 4 很安全。

面板刚打开时信息条显示的就是「当前渔场」（不用点任何卡片）—— 用它读当前
渔场，也用它切完以后核对。

锁没锁
------
能切的是橙色「切换」，锁着的是绿色「升级钓具」。按按钮底色区分：
橙 R≈150 而 G≈125（R−G>10），绿 G≈120 而 R≈55（G−R>20）。
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np

TEMPLATES = Path(__file__).resolve().parent / "ground_templates"

# 界面上的顺序（按游戏里的卡片顺序）
GROUNDS = ("桃源渔场", "侠客渔场", "云梦渔场", "逍遥渔场", "紫霞渔场",
           "寒天渔场", "绿洲渔场", "斜阳渔场", "龙渊渔场")
SLUG = {"桃源渔场": "taoyuan", "侠客渔场": "xiake", "云梦渔场": "yunmeng",
        "逍遥渔场": "xiaoyao", "紫霞渔场": "zixia", "寒天渔场": "hantian",
        "绿洲渔场": "lvzhou", "斜阳渔场": "xieyang", "龙渊渔场": "longyuan"}

# 面板上的位置（552x991）
BAR = (55, 596, 470, 662)      # 左下信息条（名字 + 说明 + 按钮）
NAME_BOX = (0, 0, 300, 42)     # 信息条里名字那一块（相对坐标）
BTN_BOX = (330, 28, 410, 52)   # 信息条里按钮那一块（相对坐标）
ICON = (364, 147)              # 顶部圆形箭头
BTN = (409, 653)               # 橙色「切换」
CLOSE = (489, 213)             # 面板右上角红叉
CARDS_X = (100, 231, 362, 493)  # 底部四张卡片中心
CARD_Y = 745

MAX_DIST = 4.0                 # 名字模板匹配阈值（同类 0.0，异类 ≥7.2）
HDR = (150, 118, 350, 178)     # 顶部大标题那一块
MAX_HDR = 6.0                  # 标题模板阈值（同一渔场 0.0，换个渔场 ≥22）
OPEN_WAIT = 0.85
CLICK_WAIT = 0.42
SWITCH_WAIT = 2.0

_cache: dict[str, np.ndarray] | None = None
_hdr_cache: dict[str, np.ndarray] | None = None


def _templates() -> dict[str, np.ndarray]:
    global _cache
    if _cache is None:
        _cache = {}
        for name, slug in SLUG.items():
            p = TEMPLATES / (slug + ".png")
            if p.exists():
                _cache[name] = np.asarray(_pil(p)).astype(np.int16)
    return _cache


def _pil(p):
    from PIL import Image
    return Image.open(p).convert("RGB")


def _headers() -> dict[str, np.ndarray]:
    global _hdr_cache
    if _hdr_cache is None:
        _hdr_cache = {}
        for name, slug in SLUG.items():
            p = TEMPLATES / ("hdr_" + slug + ".png")
            if p.exists():
                _hdr_cache[name] = np.asarray(_pil(p)).astype(np.int16)
    return _hdr_cache


def _arr(img) -> np.ndarray:
    if isinstance(img, np.ndarray):
        return img if img.ndim == 3 else np.stack([img] * 3, -1)
    return np.asarray(img.convert("RGB"))


def _bar(a: np.ndarray) -> np.ndarray | None:
    y0, y1, x0, x1 = BAR[1], BAR[3], BAR[0], BAR[2]
    if a.shape[0] < y1 or a.shape[1] < x1:
        return None
    return a[y0:y1, x0:x1]


def identify(img) -> tuple[str | None, float]:
    """看信息条认渔场。返回 (名字 or None, 最小差值)。"""
    a = _arr(img).astype(np.int16)
    bar = _bar(a)
    if bar is None:
        return None, 99.0
    probe = bar[NAME_BOX[1]:NAME_BOX[3], NAME_BOX[0]:NAME_BOX[2]]
    best, bd = None, 1e9
    for name, t in _templates().items():
        if t.shape != probe.shape:
            continue
        d = float(np.abs(t - probe).mean())
        if d < bd:
            best, bd = name, d
    return (best if bd <= MAX_DIST else None), bd


def is_unlocked(img) -> bool:
    """信息条右下按钮是橙色的「切换」= 已经解锁。"""
    a = _arr(img).astype(np.int16)
    bar = _bar(a)
    if bar is None:
        return False
    b = bar[BTN_BOX[1]:BTN_BOX[3], BTN_BOX[0]:BTN_BOX[2]]
    r, g = float(b[..., 0].mean()), float(b[..., 1].mean())
    return r > 110 and r - g > 10


def panel_open(img) -> bool:
    return identify(img)[0] is not None


def read_header(img) -> tuple[str | None, float]:
    """看顶部大标题认当前渔场（面板关着才行，面板开着会被压暗）。"""
    a = _arr(img).astype(np.int16)
    if a.shape[0] < HDR[3] or a.shape[1] < HDR[2]:
        return None, 99.0
    probe = a[HDR[1]:HDR[3], HDR[0]:HDR[2]]
    best, bd = None, 1e9
    for name, t in _headers().items():
        if t.shape != probe.shape:
            continue
        d = float(np.abs(t - probe).mean())
        if d < bd:
            best, bd = name, d
    return (best if bd <= MAX_HDR else None), bd


def current(img) -> str | None:
    """当前渔场（看顶部标题，不开面板）。认不出来返回 None。"""
    return read_header(img)[0]


def open_panel(pi, grab) -> bool:
    """打开面板；已经开着就不动它。"""
    if panel_open(grab()):
        return True
    pi.click(*ICON)
    time.sleep(OPEN_WAIT)
    return panel_open(grab())


def close_panel(pi) -> None:
    pi.click(*CLOSE)
    time.sleep(0.5)


def current_panel(pi, grab) -> str | None:
    """兜底：顶部标题认不出来时，开面板看信息条（面板刚打开时显示的是
    上一次预览的卡片，不一定等于当前渔场，所以只当参考）。"""
    if not open_panel(pi, grab):
        return None
    name = identify(grab())[0]
    close_panel(pi)
    return name


def switch(pi, grab, target: str, log=print, max_pages: int = 8) -> bool:
    """切到 target。成功返回 True。

    做法：先看顶部标题，已经在目标渔场就直接返回；否则开面板，从最左边
    一张张点，名字对上就按「切换」，关掉面板后再看标题核对。
    """
    if target not in SLUG:
        log("不认识的渔场：%s" % target)
        return False
    if current(grab()) == target:
        return True
    if not open_panel(pi, grab):
        log("打不开「切换渔场」面板（可能不在钓鱼主界面）")
        return False

    # 拖回最左边，从头找
    for _ in range(6):
        pi.drag(80, CARD_Y, 470, CARD_Y, steps=20, pause=0.012)
        time.sleep(0.25)

    seen: set[str] = set()
    for page in range(max_pages):
        before = len(seen)
        for x in CARDS_X:
            pi.click(x, CARD_Y)
            time.sleep(CLICK_WAIT)
            img = grab()
            name = identify(img)[0]
            if not name:
                continue
            seen.add(name)
            if name != target:
                continue
            if not is_unlocked(img):
                log("%s 还没解锁（要先升级钓具）" % target)
                close_panel(pi)
                return False
            pi.click(*BTN)
            time.sleep(SWITCH_WAIT)
            # 面板还开着：信息条这时候显示的就是「切换后」的渔场，先按它核对
            bar_now = identify(grab())[0]
            close_panel(pi)
            time.sleep(0.8)
            now = current(grab())          # 顶部标题能认就以标题为准
            if now is not None:
                if now == target:
                    return True
                log("按了「切换」，但当前渔场是 %s" % now)
                return False
            if bar_now == target:
                return True
            log("按了「切换」，但看起来没成功（%s）" % (bar_now or "?"))
            return False
        if len(seen) == before:
            break        # 这一页没有新渔场，到头了
        pi.drag(500, CARD_Y, 240, CARD_Y, steps=22, pause=0.012)
        time.sleep(0.55)
    close_panel(pi)
    log("没找到 %s（这一轮认出来的：%s）" % (target, "、".join(sorted(seen)) or "无"))
    return False
