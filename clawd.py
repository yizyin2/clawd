#!/usr/bin/env python3
"""Clawd 桌宠 —— 住在桌面底边的小 Clawd。

  python3 clawd.py                     把 Clawd 放到桌面上
  python3 clawd.py notify <事件> [文字]  给正在运行的 Clawd 发消息（busy / tool / done / attention / say / reload）
  python3 clawd.py --preview out.png   把所有动作渲染成一张图
  python3 clawd.py --icon icon.png     生成图标
"""

import html
import json
import math
import os
import random
import signal
import socket
import sys
import time

CONFIG_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "clawd-pet", "config.json"
)
SOCK_PATH = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"clawd-pet-{os.getuid()}.sock")
LOG_PATH = os.path.join(  # 重新加载后，新 Clawd 的输出和报错写在这里
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "clawd-pet", "clawd.log"
)


def send(msg):
    """把一条消息丢给正在运行的 Clawd；没在运行就返回 False。"""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.sendto(json.dumps(msg).encode(), SOCK_PATH)
        return True
    except OSError:
        return False
    finally:
        sock.close()


def notify(args):
    """命令行 / Claude Code hook 的入口。hook 会从 stdin 传一段 JSON，顺手读出工具名和通知内容。"""
    import select

    payload = {}
    if sys.stdin and not sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.2)[0]:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            pass
    if not isinstance(payload, dict):
        payload = {}
    send({
        "event": args[0] if args else "say",
        "text": " ".join(args[1:]),
        "tool": payload.get("tool_name") or "",
        "message": payload.get("message") or "",
    })
    return 0  # 不管 Clawd 在不在，都别打断 Claude Code


# hook 每次调用工具都会跑一遍，这条路径不加载 GTK，越快越好
if __name__ == "__main__" and sys.argv[1:2] == ["notify"]:
    sys.exit(notify(sys.argv[2:]))

# GNOME 的 Wayland 不让窗口自己定位、置顶；走 XWayland 就都可以
os.environ.setdefault("GDK_BACKEND", "x11")

import cairo  # noqa: E402
import gi  # noqa: E402

gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402, F401

# ---------------------------------------------------------------- 外观

ORANGE = (215 / 255, 119 / 255, 87 / 255)  # Claude Code 里 Clawd 的橙色
INK = (0.08, 0.08, 0.075)
CREAM = (0.98, 0.976, 0.96)
BLUSH = (0.96, 0.6, 0.6)
HEART = (0.91, 0.36, 0.42)
LAPTOP = (0.6, 0.6, 0.6)
LAPTOP_LIGHT = (0.74, 0.74, 0.74)
LAPTOP_DARK = (0.42, 0.42, 0.42)
STAR = (1.0, 0.84, 0.3)
DUST = (0.76, 0.72, 0.67)
CONFETTI = (ORANGE, STAR, HEART, (0.55, 0.75, 1.0), (0.6, 0.85, 0.55))


SCREEN_LIGHT = (0.72, 0.88, 1.0)  # 屏幕的光从盖子上沿漏出来
LID_LOGO = (0.93, 0.95, 0.97)
FONT = Pango.FontDescription("Sans 10")

# Clawd 住在一张 18 列、-2..10 行的像素网格里：身体占 3..14 列 × 0..7 行，
# 眼睛在第 5、12 列，胳膊挂在身体两侧的第 4、5 行，四条腿从第 8 行往下长，脚底是第 10 行的上沿。
GRID_W = 18
GRID_TOP = -2
GRID_ROWS = 13
FOOT = 10
BUBBLE_H = 96  # 头顶留给对话框的高度

WALK_SPEED = 7  # 格/秒
JUMP_SPEED = 95  # 格/秒
GRAVITY = 430  # 格/秒²
GRIP = 5  # 窗口减速时脚底能刹住多少（倍重力）：越大越不容易被横着甩出去
FLING_SPEED = 600  # 窗口急停、掉头时，Clawd 相对窗口冲出去超过这个速度（像素/秒）就被甩飞
LOOSE = 2  # 离开窗口上沿超过几格才算被甩开；慢慢往下拖时它只会浮起一点点
KNOCK_SPEED = 600  # 窗口扫过 Clawd 超过这个速度（像素/秒）就把它撞飞
SKID_FRICTION = 300  # 横着扔到地上后，脚底刹车有多猛（格/秒²）：越小滑得越远

# 右键菜单「调手感」里能改的参数：(常量名, 菜单里叫什么, 单位, 可选的值)
NEVER = 1e9  # 撞飞门槛设成这个就是永远不撞
TUNABLES = [
    ("GRIP", "抓地力", "倍重力", (1, 2.5, 5, 8, 15)),
    ("FLING_SPEED", "急停多快会被甩飞", "px/s", (300, 450, 600, 900, 1500)),
    ("LOOSE", "往下拉浮起几格就掉", "格", (1, 2, 4, 8)),
    ("KNOCK_SPEED", "窗口多快会把它撞飞", "px/s", (300, 600, 1000, 2000, NEVER)),
    ("GRAVITY", "重力", "格/秒²", (200, 300, 430, 600, 900)),
    ("JUMP_SPEED", "跳跃力度", "格/秒", (60, 95, 130)),
    ("WALK_SPEED", "走路速度", "格/秒", (4, 7, 12, 20)),
    ("SKID_FRICTION", "落地后刹车多猛（越小滑得越远）", "格/秒²", (100, 200, 300, 500, 900)),
]
CODE_VALUES = {name: globals()[name] for name, *_ in TUNABLES}  # 代码里写的值，「恢复」时用


def apply_tuning(tuning):
    """菜单里调过的值直接盖掉上面那些常量，Clawd 下一帧就按新的值动。"""
    for name, value in CODE_VALUES.items():
        globals()[name] = tuning.get(name, value)


def tuning_text(value, unit):
    return "永远不撞" if value >= NEVER else f"{value:g} {unit}"

# 窗口位置由 GNOME 扩展 gnome-extension/clawd-windows@clawd.pet 通过 D-Bus 提供
WINDOWS_BUS = "org.clawd.Windows"
WINDOWS_IFACE = "org.clawd.Windows"
WINDOWS_PATH = "/org/clawd/Windows"
WINDOWS_TYPE = "(a(tiiii))"  # (id, x, y, 宽, 高)，从下往上排

HEART_PIX = (".XX.XX.", "XOXXXXX", "XXXXXXX", ".XXXXX.", "..XXX..", "...X...")
STAR_PIX = ("..X..", ".XXX.", "XXOXX", ".XXX.", "..X..")
DUST_PIX = (  # 一团灰从蹭起来到散掉的四帧，底边贴着地
    (".XX.", "XOXX"),
    (".XX.", "XOXX", "XXXX"),
    (".XX.", "X..X", ".XX."),
    ("X..X", "....", "X..X"),
)
SPINNER = "·✢✳✶✻✽✻✶✳✢"


def eye_rects(kind, ex, ey, side):
    """一只眼睛的像素。side 是 -1（左眼）或 1（右眼），镜像对称的表情靠它朝外翻。"""
    out = ex + side
    if kind == "blink":
        return [(ex, ey + 1, 1, 1)]
    if kind == "closed":
        return [(min(ex, out), ey + 1, 2, 1)]
    if kind == "wide":
        return [(min(ex, out), ey, 2, 2)]
    if kind == "happy":  # ^ ^
        return [(ex - 1, ey + 1, 1, 1), (ex, ey, 1, 1), (ex + 1, ey + 1, 1, 1)]
    if kind == "squint":  # > <
        return [(out, ey, 1, 1), (ex, ey + 1, 1, 1), (out, ey + 2, 1, 1)]
    if kind == "down":
        return [(ex, ey + 1, 1, 2)]
    if kind == "up":
        return [(ex, ey - 1, 1, 2)]
    if kind == "dizzy":  # x x
        return [(ex - 1, ey, 1, 1), (ex + 1, ey, 1, 1), (ex, ey + 1, 1, 1), (ex - 1, ey + 2, 1, 1), (ex + 1, ey + 2, 1, 1)]
    return [(ex, ey, 1, 2)]


class Pose:
    """Clawd 的一帧。arms 是左右胳膊相对默认位置的上下偏移（负数是举起来），legs 是四条腿的长度。"""

    def __init__(self, eyes="open", look=0, arms=(0, 0), legs=(2, 2, 2, 2), shape="normal", blush=False, dx=0):
        self.eyes, self.look, self.arms, self.legs = eyes, look, arms, legs
        self.shape, self.blush, self.dx = shape, blush, dx

    def rects(self):
        left, right, top, bottom = 3, 14, 0, 7
        if self.shape == "squash":
            left, right, top = 2, 15, 2
        elif self.shape == "stretch":
            left, right, top = 4, 13, -1
        elif self.shape == "breathe":
            top = 1
        out = [(left, top, right - left + 1, bottom - top + 1, ORANGE)]
        for side, x, dy in ((-1, left - 1, self.arms[0]), (1, right + 1, self.arms[1])):
            if dy <= -3:  # 举高高：斜着往外上方伸，像 \o/
                out += [(x, top + 1, 1, 2, ORANGE), (x + side, top - 1, 1, 2, ORANGE)]
            else:
                out.append((min(x, x + side), top + 4 + dy, 2, 2, ORANGE))
        for x, n in zip((left + 1, left + 3, right - 3, right - 1), self.legs):
            out.append((x, bottom + 1, 1, n, ORANGE))
        ey = top + 2
        for side, ex in ((-1, left + 2 + self.look), (1, right - 2 + self.look)):
            out += [(x, y, w, h, INK) for x, y, w, h in eye_rects(self.eyes, ex, ey, side)]
        if self.blush:
            out += [(left + 1, ey + 2, 1, 1, BLUSH), (right - 1, ey + 2, 1, 1, BLUSH)]
        return [(x + self.dx, y, w, h, c) for x, y, w, h, c in out]


class Typing:
    """帮 Claude 敲代码：坐在一台小笔记本后面，我们看到的是盖子背面（中间有个小标志，上沿漏出屏幕的光）。
    两只胳膊在盖子两边一上一下地敲键盘，眼睛往下盯着屏幕，跟着读的代码左右移。
    隔几秒停下来抬眼想一想，再接着敲。opened 是开始干活多久了：最开始盖子一格一格掀起来。"""

    KEYS = "LRLLRLRRLRLRRLLR"  # 左右手轮流敲的顺序，不整齐才像在打字
    LID = 4  # 盖子全开时有几行高

    def __init__(self, t, opened, blink=False):
        self.lid = min(self.LID, int(opened / 0.07))
        ready = self.lid == self.LID
        self.thinking = ready and t % 5.3 > 4.3  # 每 5 秒多停下来想一秒
        working = ready and not self.thinking
        self.key = self.KEYS[int(t / 0.09) % len(self.KEYS)] if working else ""
        self.look = (-1 if int(t / 5.3) % 2 else 1) if self.thinking else (0, 1, 0, -1)[int(t / 0.7) % 4] if working else 0
        self.blink = blink

    def rects(self):
        ready = self.lid == self.LID
        out = [(3, 0, 12, 8, ORANGE)]  # 身体；腿藏在笔记本后面
        for side, ex in ((-1, 5), (1, 12)):
            kind = "blink" if self.blink else "up" if self.thinking else "down"
            out += [(x + self.look, y, w, h, INK) for x, y, w, h in eye_rects(kind, ex, 2, side)]
        for x, key in ((1, "L"), (15, "R")):  # 敲的那只往下按，另一只微微抬起
            dy = 2 if self.key == key else 1 if self.key else 0
            out.append((x, 4 + dy, 2, 2, ORANGE))
        out.append((3, 9, 12, 1, LAPTOP_DARK))  # 键盘那一截
        if self.lid:
            top = 9 - self.lid
            out += [(4, top, 10, 9 - top, LAPTOP), (4, top, 10, 1, SCREEN_LIGHT if ready else LAPTOP_LIGHT)]
            if ready:
                out.append((8, 7, 2, 1, LID_LOGO))
        return out


def draw_clawd(cr, pose, s, ox, oy):
    """(ox, oy) 是网格第 0 列第 0 行在画布上的位置，s 是一格多少像素。"""
    cr.save()
    cr.set_antialias(cairo.ANTIALIAS_NONE)
    for x, y, w, h, color in pose.rects():
        cr.set_source_rgb(*color)
        cr.rectangle(ox + x * s, oy + y * s, w * s, h * s)
        cr.fill()
    cr.restore()


def draw_shadow(cr, s, ox, oy):
    cr.set_source_rgba(*INK, 0.2)
    cr.rectangle(ox + 3 * s, oy + FOOT * s, 12 * s, max(2, s // 2))
    cr.fill()


def draw_heart(cr, x, y, u, alpha):
    x, y = int(x - 3.5 * u), int(y)
    for j, row in enumerate(HEART_PIX):
        for i, ch in enumerate(row):
            if ch != ".":
                cr.set_source_rgba(*(CREAM if ch == "O" else HEART), alpha)
                cr.rectangle(x + i * u, y + j * u, u, u)
                cr.fill()


def draw_dust(cr, x, y, u, frame):
    """一团灰，(x, y) 是它贴着地的底边中点。"""
    rows = DUST_PIX[frame]
    x, y = int(x - 2 * u), int(y - len(rows) * u)
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch != ".":
                cr.set_source_rgb(*(CREAM if ch == "O" else DUST))
                cr.rectangle(x + i * u, y + j * u, u, u)
                cr.fill()


def draw_star(cr, x, y, u, alpha):
    x, y = int(x - 2.5 * u), int(y - 2.5 * u)
    for j, row in enumerate(STAR_PIX):
        for i, ch in enumerate(row):
            if ch != ".":
                cr.set_source_rgba(*(CREAM if ch == "O" else STAR), alpha)
                cr.rectangle(x + i * u, y + j * u, u, u)
                cr.fill()


def draw_dizzy_stars(cr, cx, cy, s, t):
    """三颗小星星绕着头顶转。转到前面（下半圈）的大一点、亮一点，转到后面的小一点、暗一点。"""
    for k in range(3):
        a = t * 4.0 + k * 2 * math.pi / 3
        front = math.sin(a) > 0
        draw_star(cr, cx + 5.5 * s * math.cos(a), cy + 1.2 * s * math.sin(a),
                  max(2, s // 2) if front else max(2, s // 3), 1.0 if front else 0.65)


def draw_outlined(cr, text, x, y, size, alpha):
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(f"Sans Bold {size:.1f}"))
    layout.set_text(text, -1)
    cr.push_group()  # 描边和填充先画实，再整体淡出，叠在一起才不会发虚
    cr.move_to(x, y)
    PangoCairo.layout_path(cr, layout)
    cr.set_line_width(3)
    cr.set_source_rgb(*CREAM)
    cr.stroke_preserve()
    cr.set_source_rgb(*INK)
    cr.fill()
    cr.pop_group_to_source()
    cr.paint_with_alpha(alpha)


def draw_bubble(cr, markup, cx, bottom, width):
    """像素风对话框。(cx, bottom) 是尖角的位置，width 是画布宽度，对话框不会出界。"""
    u = 2
    pad = 4 * u
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(FONT)
    layout.set_width(min(200, width - 2 * pad) * Pango.SCALE)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_height(-3)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_markup(markup, -1)
    _, ext = layout.get_pixel_extents()
    bw, bh = ext.width + 2 * pad, ext.height + 2 * pad - 2 * u
    x = int(min(max(cx - bw / 2, 0), width - bw))
    y = int(bottom - 3 * u - bh)
    # 外框和底色都是两块矩形叠出来的，四个角各缺一格，看起来像像素画
    cr.set_source_rgb(*INK)
    cr.rectangle(x + u, y, bw - 2 * u, bh)
    cr.rectangle(x, y + u, bw, bh - 2 * u)
    cr.fill()
    cr.set_source_rgb(*CREAM)
    cr.rectangle(x + 2 * u, y + u, bw - 4 * u, bh - 2 * u)
    cr.rectangle(x + u, y + 2 * u, bw - 2 * u, bh - 4 * u)
    cr.fill()
    # 三层台阶的小尖角
    tx, by = int(min(max(cx, x + 6 * u), x + bw - 6 * u)), y + bh
    cr.rectangle(tx - 2 * u, by - u, 4 * u, u)
    cr.rectangle(tx - 2 * u, by, 4 * u, u)
    cr.rectangle(tx - u, by + u, 2 * u, u)
    cr.fill()
    cr.set_source_rgb(*INK)
    for px, py, pw in ((-3, 0, 1), (2, 0, 1), (-2, 1, 1), (1, 1, 1), (-1, 2, 2)):
        cr.rectangle(tx + px * u, by + py * u, pw * u, u)
    cr.fill()
    cr.move_to(x + pad - ext.x, y + pad - u - ext.y)
    PangoCairo.show_layout(cr, layout)


def spinner_markup(t, verb):
    glyph = SPINNER[int(t / 0.12) % len(SPINNER)]
    return f'<span foreground="#D77757" weight="bold">{glyph}</span> {html.escape(verb)}…'


# ---------------------------------------------------------------- 台词

CHATTER = [
    "今天也要加油鸭",
    "写累了就看看我",
    "要不要喝口水？",
    "坐太久啦，站起来走走",
    "记得 git commit 哦",
    "我不是螃蟹！……大概吧",
    "有 bug 吗？我帮你盯着",
    "✻ 思考中……其实在发呆",
    "伸个懒腰吧～",
    "你今天写的代码很棒",
]
CLICK_LINES = ["嘿嘿", "干嘛呀～", "痒！", "在呢在呢", "♪", "再来一下嘛"]
DRAG_LINES = ["放我下来！", "飞起来啦～", "哇——"]
OUCH_LINES = ["哎哟！", "屁股好痛……", "我没事！"]
FLING_LINES = ["哇啊——", "飞起来啦！", "慢点慢点！", "呜哇！"]
DIZZY_LINES = ["眼冒金星……", "天在转……", "@_@", "晕晕的……"]
PEEK_LINES = ["好高……", "下面是什么？", "跳不跳呢……"]
WAKE_LINES = ["唔……醒啦", "刚刚梦到在写代码", "我没睡！真的！"]
POKE_LINES = ["别戳啦 >_<", "要被戳扁了！", "再戳我要生气了"]
PET_LINES = ["嘿嘿，好舒服～", "再摸摸～", "♥"]
DONE_LINES = ["搞定啦 ✓", "做完咯，快来看看", "完成！✓"]
BUSY_VERBS = ["Thinking", "Clauding", "Pondering", "Noodling", "Brewing", "Percolating"]
TOOL_VERBS = {
    "Read": "翻代码", "Grep": "找线索", "Glob": "找文件",
    "Edit": "改代码", "MultiEdit": "改代码", "Write": "写代码", "NotebookEdit": "改笔记本",
    "Bash": "敲命令", "WebFetch": "上网查", "WebSearch": "上网搜",
    "Task": "摇人帮忙", "Agent": "摇人帮忙", "TodoWrite": "列计划",
}


def chatter_line():
    hour = time.localtime().tm_hour
    if hour >= 23 or hour < 5:
        extra = ["这么晚了，早点睡吧", "夜猫子你好"]
    elif 11 <= hour < 13:
        extra = ["午饭吃了吗？"]
    elif 17 <= hour < 19:
        extra = ["快下班了吧？"]
    else:
        extra = []
    return random.choice(CHATTER + extra * 3)


def greeting():
    hour = time.localtime().tm_hour
    if 5 <= hour < 11:
        return "早上好！"
    if 11 <= hour < 14:
        return "中午好～"
    if 14 <= hour < 18:
        return "下午好！"
    if 18 <= hour < 23:
        return "晚上好～"
    return "夜深了，还在忙呀"


def subtract(segments, a, b):
    """从一组 [x0, x1) 线段里挖掉 [a, b)。"""
    out = []
    for x0, x1 in segments:
        if b <= x0 or a >= x1:
            out.append((x0, x1))
            continue
        if x0 < a:
            out.append((x0, a))
        if b < x1:
            out.append((b, x1))
    return out


def hits(box, x, y, w, h):
    """box 是 (左, 上, 右, 下)，看它和窗口 (x, y, w, h) 有没有重叠。"""
    return box[0] < x + w and x < box[2] and box[1] < y + h and y < box[3]


def attention_text(message):
    low = message.lower()
    if "permission" in low:
        return "Claude 需要你批准一下～"
    if "waiting" in low:
        return "等你回复哦～"
    return message[:60] or "需要你来看一下～"


# ---------------------------------------------------------------- 设置

DEFAULTS = {"scale": 6, "walk": True, "windows": True, "quiet": False, "met": False, "tuning": {}}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError, TypeError):
        pass
    if cfg["scale"] not in (4, 6, 8):
        cfg["scale"] = 6
    tuning = cfg["tuning"] if isinstance(cfg["tuning"], dict) else {}
    cfg["tuning"] = {k: v for k, v in tuning.items() if k in CODE_VALUES and isinstance(v, (int, float))}
    return cfg


def save_cfg(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 桌宠本体

# 这些状态下 Clawd 站在地上、没被人拎着，外部事件可以直接打断
CALM = ("idle", "walk", "look", "sleep", "wave", "pet", "annoyed", "surprised", "crouch",
        "dizzy", "yawn", "stretchup", "peek", "skid")
DAZE_STATES = ("dizzy", "air", "drag", "skid")  # 晕着的时候会待的状态，切到别的就算缓过来了
DIZZY_SPEED = 230  # 落地时超过这个速度（格/秒）就摔晕，头上冒星星
DIZZY_TIME = 2.6  # 弹完落地后晕多久（秒），最后 0.35 秒是甩头回神


class Pet:
    def __init__(self, cfg):
        self.cfg = cfg
        apply_tuning(cfg["tuning"])
        self.t = 0.0
        self.clock = time.monotonic()
        self.state, self.state_t, self.idle_for = "idle", 0.0, 2.0
        self.mood, self.vx, self.vy = "fall", 0.0, 0.0
        self.walk_to = self.sleep_for = self.spawn_t = 0.0
        self.leap_v = (0.0, 0.0)
        self.after_land = self.after_walk = None
        self.heavy = False  # 这一跤有没有砸懵（弹起来时就晕，落地后接着晕）
        self.dizzy_end = 0.0  # 晕到什么时候。中途被拎起来、扔出去也照样晕，到点才缓过来
        self.skid_v = 0.0  # 落地后往前滑的速度（站在窗口上时是相对窗口的）
        self.peek_dir = 0
        self.busy_since = 0.0
        self.next_blink, self.blink_until = 1.5, 0.0
        self.bubble, self.bubble_until = "", 0.0
        self.particles = []
        self.busy, self.busy_verb, self.busy_seen = False, "", 0.0
        self.hover = self.press = None
        self.samples, self.pokes = [], []
        self.stroke_x, self.stroke_dir, self.strokes, self.stroke_t = None, 0, 0, 0.0
        self.last_touch = self.last_chat = 0.0
        self.win_pos = self.menu = None
        self.windows, self.platforms = [], []  # 别的窗口 / 它们露在外面、能站人的上沿
        self.perch = None  # 正站在哪个窗口上；None 是地面
        self.motion = {}  # 窗口 id -> [最后一次挪动的时间, x, y, 宽, 高, vx, vy]
        self.air_tops = {}  # 在空中时，上一帧各窗口上沿的高度（窗口可能正迎上来）
        # 站在窗口上时：相对窗口左边的位置、相对窗口的横向滑动速度、上次见到的窗口 x、Clawd 自己的横向速度
        self.rel_x = self.slip = self.carry_x = self.carry_vx = 0.0
        self.ride_clock = 0.0
        self.ride_t = -1.0  # 上一次被脚下的窗口带着走是什么时候
        self.linked = False  # GNOME 扩展在不在

        self.win = Gtk.Window(type=Gtk.WindowType.POPUP)  # 不归窗口管理器管：永远在最上层、不抢焦点、不进任务栏
        self.win.set_title("Clawd")
        self.win.set_app_paintable(True)
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.win.connect("draw", self.on_draw)
        self.win.connect("button-press-event", self.on_press)
        self.win.connect("button-release-event", self.on_release)
        self.win.connect("motion-notify-event", self.on_motion)
        self.win.connect("leave-notify-event", self.on_leave)
        self.set_scale(cfg["scale"], save=False)

        # 灰另画在一个透明小窗口上，留在屏幕上原地。画在 Clawd 自己的窗口里的话，
        # 它一弹起来、跳走，地上的灰就被窗口带走、裁掉了
        self.fx = Gtk.Window(type=Gtk.WindowType.POPUP)
        self.fx.set_app_paintable(True)
        if visual:
            self.fx.set_visual(visual)
        self.fx.input_shape_combine_region(cairo.Region())
        self.fx.connect("draw", self.on_draw_fx)
        self.fx_rect = None

        display = Gdk.Display.get_default()
        wa = (display.get_primary_monitor() or display.get_monitor(0)).get_workarea()
        self.x = wa.x + wa.width * random.uniform(0.3, 0.7)
        self.y = wa.y + 13 * self.s
        self.drop("intro")  # 从屏幕顶上掉下来登场（这一跤不算，不会摔晕）
        self.after_land = self.greet
        self.place()
        self.win.show()

        self.open_socket()
        self.watch_windows()
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.shutdown)
        GLib.timeout_add(33, self.tick)

    # ---- 尺寸与位置

    def set_scale(self, s, save=True):
        self.s = s
        self.W = max(240, (GRID_W + 4) * s)
        self.H = BUBBLE_H + GRID_ROWS * s
        self.gx = (self.W - GRID_W * s) // 2
        self.gy = BUBBLE_H - GRID_TOP * s  # 网格第 0 行在窗口里的 y
        self.foot = self.gy + FOOT * s
        self.win.set_size_request(self.W, self.H)
        self.win.resize(self.W, self.H)
        # 只有身体能点，其余全让鼠标穿过去。X11 会话里靠这个就够了；
        # GNOME 的 Wayland 会冲掉它，那边由扩展照着同一块区域来管（track_pet）
        self.win.input_shape_combine_region(cairo.Region(cairo.RectangleInt(*self.hitbox())))
        self.track_pet()
        self.win_pos = None
        self.update_platforms()
        if save:
            self.cfg["scale"] = s
            save_cfg(self.cfg)

    def hitbox(self):
        """窗口里能点的那块：身体和腿（第 3..14 列、第 0..9 行）。两边的小手、跳起来举的手、
        敲代码时的笔记本、对话框都不挡。固定不变，不跟着姿势走。"""
        s = self.s
        return self.gx + 3 * s, self.gy, 12 * s, 10 * s

    def bounds(self):
        """当前显示器上 Clawd 能待的范围：(左, 右, 脚最高能到哪, 地面)，都是脚底中心的屏幕坐标。"""
        display = Gdk.Display.get_default()
        wa = display.get_monitor_at_point(int(self.x), int(self.y) - self.s).get_workarea()
        half = 8 * self.s
        return wa.x + half, wa.x + wa.width - half, wa.y + (FOOT - GRID_TOP) * self.s, wa.y + wa.height - self.s

    def place(self):
        pos = (int(round(self.x - self.W / 2)), int(round(self.y - self.foot)))
        if pos != self.win_pos:
            self.win.move(*pos)
            self.win_pos = pos

    def on_ground(self):
        """站稳了就返回 True；脚下空了（窗口挪走了、走到了窗口边上、换显示器）就开始往下掉。"""
        if self.perch is not None:
            return self.ride()
        left, right, _, ground = self.bounds()
        self.x = min(max(self.x, left), right)
        self.walk_to = min(max(self.walk_to, left), right)
        if self.y < ground - 1:
            self.drop("fall")
            return False
        self.y = ground
        return True

    def ride(self):
        """站在窗口上时的物理。横向：窗口加速时它抓得住、直接跟着走；窗口减速、急停、掉头时，
        它凭惯性往前冲，脚底只能慢慢刹住，冲得太快就被甩飞。
        竖直方向只有重力把它压在窗口上：窗口往下猛拉、往上推完急停，它都会被甩开。"""
        now = time.monotonic()
        dt = min(now - self.ride_clock, 0.05)
        self.ride_clock = now
        m = self.motion.get(self.perch)
        if m is None:  # 窗口关了、最小化了
            self.detach()
            return False
        vx, vy = self.window_velocity(self.perch, now)
        g = GRAVITY * self.s
        cv = self.carry_vx  # Clawd 自己横向的速度
        if vx * cv >= 0 and abs(vx) >= abs(cv) or abs(cv - vx) < 150:
            cv = vx  # 窗口在加速（或者速度只是有点抖）：抓得住，跟着走
        else:
            brake = GRIP * g * dt
            cv = vx if abs(cv - vx) <= brake else cv - math.copysign(brake, cv - vx)
        self.carry_vx, self.slip = cv, cv - vx
        if abs(self.slip) > FLING_SPEED:  # 窗口一下子停住或者掉头，它被甩出去
            self.say(random.choice(FLING_LINES), 1.5)
            self.drop("thrown", cv, min(self.vy, 0.0) - min(0.35 * abs(self.slip), 700.0))
            return False
        self.rel_x += self.slip * dt
        self.walk_to += m[1] - self.carry_x
        self.carry_x = m[1]
        self.x = m[1] + self.rel_x
        self.y += (self.vy + g * dt / 2) * dt
        self.vy += g * dt
        if self.y >= m[2]:  # 踩在窗口上就被它托着走；只跟九成的速度，免得窗口速度有点抖就被弹起来
            self.y, self.vy = m[2], vy * 0.9
        if m[2] - self.y > LOOSE * self.s or not self.platform_under(self.perch):
            self.detach()
            return False
        if abs(vx) + abs(vy) + abs(self.slip) > 60:
            self.ride_t = self.t
        return True

    def detach(self):
        """离开脚下的窗口，带着当时的速度出去：被甩飞、滑出边、或者自己走出边。"""
        vx, vy = self.carry_vx, self.vy
        if self.state == "skid":
            vx += self.skid_v
        if self.state == "walk":
            vx += WALK_SPEED * self.s * (1 if self.walk_to > self.x else -1)
            if random.random() < 0.3:
                self.say("嘿咻～", 1.2)
        flung = math.hypot(vx, vy) > 600
        if flung:
            self.say(random.choice(FLING_LINES), 1.5)
        self.drop("thrown" if flung else "fall", vx, vy)

    def perch_on(self, wid):
        m = self.motion.get(wid)
        if m is None:
            return
        self.perch = wid
        self.rel_x, self.slip, self.vy = self.x - m[1], 0.0, 0.0
        self.carry_x = m[1]
        self.ride_clock = time.monotonic()
        self.carry_vx = self.window_velocity(wid, self.ride_clock)[0]

    def track_motion(self, windows, now):
        """记下每个窗口的位置和速度：算惯性、判断是不是被窗口撞了都要用。
        消息到达的时间会抖，所以速度按最近 0.1 秒的轨迹算，不按相邻两次算。"""
        motion = {}
        for wid, x, y, w, h in windows:
            m = self.motion.get(wid)
            if m is None or m[3:5] != [w, h]:  # 新窗口，或者改了大小（最大化之类）：不算在动
                motion[wid] = [now, x, y, w, h, 0.0, 0.0, []]
            elif m[1:3] != [x, y]:
                if now - m[0] > 0.1:  # 停了一阵刚开始动：当它是这一帧才动起来的
                    trail = [(now - 1 / 60, m[1], m[2]), (now, x, y)]
                else:
                    trail = [p for p in m[7] if now - p[0] < 0.1] + [(now, x, y)]
                t0, x0, y0 = trail[0]
                span = max(now - t0, 1 / 60)
                motion[wid] = [now, x, y, w, h, (x - x0) / span, (y - y0) / span, trail]
            else:
                motion[wid] = m
        self.motion = motion

    def window_velocity(self, wid, now):
        m = self.motion.get(wid)
        if m is None or now - m[0] > 0.06:  # 一会儿没动就是停下了
            return 0.0, 0.0
        return m[5], m[6]

    def knock(self, before):
        """窗口快速扫过 Clawd 就把它撞飞。慢慢挪过去、最大化之类的不算。"""
        if self.state == "drag" or not self.cfg["windows"]:
            return
        s = self.s
        box = (self.x - 7 * s, self.y - 10 * s, self.x + 7 * s, self.y)
        for wid, m in self.motion.items():
            o = before.get(wid)
            if wid == self.perch or o is None or o[3:5] != m[3:5] or o[1:3] == m[1:3]:
                continue
            if math.hypot(m[5], m[6]) < KNOCK_SPEED or not hits(box, *m[1:5]) or hits(box, *o[1:5]):
                continue
            away = 1 if self.x > m[1] + m[3] / 2 else -1
            self.say(random.choice(FLING_LINES), 1.5)
            self.drop("thrown", m[5] * 1.1 + away * 150, min(m[6] * 1.1, 0.0) - 450)
            self.last_touch = self.t
            return

    def platform_under(self, wid):
        for p in self.platforms:
            if p[0] == wid and p[2] <= self.x <= p[3]:
                return p
        return None

    def update_platforms(self):
        """窗口的上沿就是 Clawd 能站的地方。被更上层的窗口盖住的那段、
        头顶放不下 Clawd 和一行对话框的（比如最大化的窗口）都不算。"""
        s, display = self.s, Gdk.Display.get_default()
        windows = self.windows if self.cfg["windows"] else []
        platforms = []
        for i, (wid, x, y, w, h) in enumerate(windows):
            wa = display.get_monitor_at_point(x + w // 2, y).get_workarea()
            if y - (FOOT - GRID_TOP) * s - 40 < wa.y or y >= wa.y + wa.height - s:
                continue
            segments = [(x, x + w)]
            for _, ox, oy, ow, oh in windows[i + 1:]:
                if oy <= y <= oy + oh:
                    segments = subtract(segments, ox, ox + ow)
            for a, b in segments:
                a, b = max(a, wa.x + 8 * s), min(b, wa.x + wa.width - 8 * s)
                if b - a >= 16 * s:
                    platforms.append((wid, y, a, b))
        self.platforms = platforms

    def landing(self, y0, y1):
        """从 y0 落到 y1 的途中第一个踩到的窗口上沿。窗口自己也可能正往上迎过来，所以拿它上一帧的高度比。"""
        best = None
        for p in self.platforms:
            if p[2] <= self.x <= p[3] and y0 <= self.air_tops.get(p[0], p[1]) and p[1] <= y1 \
                    and (best is None or p[1] < best[1]):
                best = p
        return best

    # ---- 主循环

    def tick(self):
        now = time.monotonic()
        dt = min(now - self.clock, 0.1)
        self.clock = now
        self.t += dt
        self.state_t += dt
        if self.state in CALM or (self.state == "land" and self.perch is not None):
            self.on_ground()  # 脚下空了的话它会把状态切成 air
        getattr(self, "step_" + self.state)(dt)
        if self.t >= self.next_blink:
            self.blink_until = self.t + 0.12
            self.next_blink = self.t + (0.3 if random.random() < 0.2 else random.uniform(2, 6))
        for p in self.particles:
            p["age"] += dt
            p["vy"] += p.get("g", 0.0) * dt
            if "drag" in p:
                f = max(0.0, 1 - p["drag"] * dt)
                p["vx"] *= f
                p["vy"] *= f
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
        self.particles = [p for p in self.particles if p["age"] < p["life"]]
        self.place()
        self.win.queue_draw()
        if self.fx_rect:
            if any(p["kind"] == "dust" for p in self.particles):
                self.fx.queue_draw()
            else:
                self.fx.hide()
                self.fx_rect = None
        return True

    def go(self, state):
        self.state, self.state_t = state, 0.0
        if state not in DAZE_STATES:  # 被别的事（比如右键让它睡觉）打断，就不晕了
            self.dizzy_end = 0.0

    def dazed(self):
        """晕着：从砸下去那一下到缓过来（最后那下甩头不算）。"""
        return self.t < self.dizzy_end - 0.35

    def free(self):
        """闲着、能被 Claude 那边的事叫走。晕着、正滑着的时候不算，得等它停下来、缓过来。"""
        return self.state in CALM and self.state not in ("dizzy", "skid")

    def idle(self, dur=None):
        self.go("idle")
        self.idle_for = dur if dur is not None else random.uniform(2, 6)
        self.after_walk = None  # 走到一半被叫停（比如 Claude 开始干活），原来打算干的事就算了

    def step_idle(self, dt):
        if self.busy:
            if self.t - self.busy_seen > 600:  # 十分钟没动静，大概是漏了 done
                self.busy = False
            return
        if self.state_t >= self.idle_for:
            self.decide()

    def decide(self):
        r = random.random()
        if self.t - self.last_touch > 300 and r < 0.3:
            self.sleep()
        elif self.cfg["walk"] and r < 0.5:
            if r < 0.1 and self.leap():
                pass
            elif r < 0.2 and self.perch is not None:
                self.walk_off()
            else:
                self.wander()
        elif r < 0.65:
            self.go("look")
        elif r < 0.75 and not self.cfg["quiet"] and self.t - self.last_chat > 90:
            self.last_chat = self.t
            self.say(chatter_line())
            self.idle()
        else:
            self.idle()

    def wander(self):
        left, right, _, _ = self.bounds()
        p = self.perch is not None and self.platform_under(self.perch)
        if p:  # 在窗口上就别走出边
            left, right = p[2] + 4 * self.s, p[3] - 4 * self.s
        to = min(max(self.x + random.choice((-1, 1)) * random.uniform(80, 420), left), right)
        if abs(to - self.x) < 20:
            self.idle()
            return
        self.walk_to = to
        self.go("walk")

    def walk_off(self):
        """朝近的那一边走到窗口边上，探头往下看一眼，再一脚踩空跳下去。"""
        p = self.platform_under(self.perch)
        if not p:
            self.wander()
            return
        self.peek_dir = -1 if self.x - p[2] < p[3] - self.x else 1
        self.walk_to = p[2] + 2 * self.s if self.peek_dir < 0 else p[3] - 2 * self.s
        self.after_walk = self.peek
        self.go("walk")

    def peek(self):
        self.go("peek")
        if random.random() < 0.4:
            self.say(random.choice(PEEK_LINES), 1.2)

    def step_peek(self, dt):
        if self.state_t > 1.1:
            self.walk_to = self.x + self.peek_dir * 8 * self.s  # 走出边，on_ground 发现脚下空了就会掉下去
            self.go("walk")

    def leap(self):
        """附近有够得着的窗口就蹦上去（往下的也行）。算好抛物线，正好落在窗口上沿。"""
        s, g = self.s, GRAVITY * self.s
        ceiling = self.bounds()[2]
        options = []
        for p in self.platforms:
            dy = self.y - p[1]  # 目标比现在高多少
            lx = min(max(self.x + random.uniform(-120, 120), p[2] + 6 * s), p[3] - 6 * s)
            apex = max(dy, 0) + 6 * s
            if p[0] != self.perch and 4 * s < abs(dy) and -400 < dy < 800 and abs(lx - self.x) < 500 \
                    and self.y - apex > ceiling:
                options.append((lx, dy, apex))
        if not options:
            return False
        lx, dy, apex = random.choice(options)
        up = math.sqrt(2 * g * apex)
        flight = (up + math.sqrt(up * up - 2 * g * dy)) / g
        self.leap_v = ((lx - self.x) / flight, -up)
        self.go("crouch")  # 先蹲一下蓄力
        return True

    def step_crouch(self, dt):
        if self.state_t > 0.35:
            self.drop("leap", *self.leap_v)
            if random.random() < 0.4:
                self.say("嘿！", 1)

    def step_walk(self, dt):
        if self.busy:
            self.idle()
            return
        step = WALK_SPEED * self.s * dt
        d = self.walk_to - self.x
        if abs(d) <= step:
            self.shift(d)
            then, self.after_walk = self.after_walk, None
            (then or self.idle)()
        else:
            self.shift(step if d > 0 else -step)

    def shift(self, dx):
        self.x += dx
        if self.perch is not None:
            self.rel_x += dx

    def step_look(self, dt):
        if self.state_t > 2.2:
            self.idle()

    def sleep(self, dur=None):
        """先伸长身子打个哈欠，再睡。"""
        self.go("yawn")
        self.sleep_for = dur or random.uniform(90, 300)
        if self.t >= self.bubble_until:
            self.say("哈啊——", 1.3)

    def step_yawn(self, dt):
        if self.state_t > 1.6:
            self.go("sleep")
            self.spawn_t = -1.0

    def step_sleep(self, dt):
        if self.state_t - self.spawn_t > 1.8:
            self.spawn_t = self.state_t
            self.add_z()
        if self.state_t > self.sleep_for:
            self.wake()

    def wake(self):
        self.go("stretchup")  # 醒来先伸个懒腰
        self.say(random.choice(WAKE_LINES))

    def step_stretchup(self, dt):
        if self.state_t > 0.8:
            self.idle()

    def step_dizzy(self, dt):
        if self.t >= self.dizzy_end:
            self.idle()

    def drop(self, mood, vx=0.0, vy=0.0):
        self.mood, self.vx, self.vy = mood, vx, vy
        self.perch = None
        self.heavy = False
        self.air_tops = {p[0]: p[1] for p in self.platforms}
        self.go("air")

    def jump(self):
        riding = self.perch is not None  # 窗口正在动的话，起跳时带着窗口的速度
        vx, vy = (self.carry_vx, min(self.vy, 0.0)) if riding else (0.0, 0.0)
        self.drop("happy", vx, vy - JUMP_SPEED * self.s)
        self.add_heart()
        self.add_heart()

    def step_air(self, dt):
        left, right, ceiling, ground = self.bounds()
        y0, g = self.y, GRAVITY * self.s
        if self.mood != "leap":  # 蹦窗口是算好的抛物线，不加空气阻力
            self.vx *= max(0.0, 1 - 0.6 * dt)
        self.x += self.vx * dt
        self.y += (self.vy + g * dt / 2) * dt  # 按精确的抛物线走，不然每跳一次都比算的矮一截
        self.vy += g * dt
        if self.x < left:
            self.x, self.vx = left, abs(self.vx) * 0.5
        elif self.x > right:
            self.x, self.vx = right, -abs(self.vx) * 0.5
        if self.y < ceiling:
            self.y, self.vy = ceiling, abs(self.vy) * 0.3
        floor, perch = ground, None
        p = self.vy > 0 and self.landing(y0, self.y)
        if p:
            floor, perch = p[1], p[0]
        if self.y >= floor:
            self.y = floor
            s = self.s
            if self.vy > 70 * s:  # 落地扬灰，摔得越重灰越多
                self.add_dust(min(1.0, (self.vy - 70 * s) / (250 * s)))
            if self.vy > DIZZY_SPEED * s and self.mood not in ("happy", "leap", "intro") and not self.heavy:
                self.heavy = True  # 砸懵了：照样弹，但弹起来时就已经眼冒金星
                # 从这一下就开始算晕，弹到一半被拎走也照样晕；弹完落地时再按落地的时间重新算一次
                self.dizzy_end = self.t + 1.0 + DIZZY_TIME
                self.say(random.choice(DIZZY_LINES), 2.6)
            if self.heavy:
                # 砸晕了身子是软的，像皮球一样一下比一下矮地接着弹，弹不动了才停。第一下和平时一样高，
                # 之后落得越慢、弹回来的比例越大，后面那几下小的才不会一下子就没了
                # 着地那一刻的速度按能量算、不按帧算：按帧算的话，矮的弹跳每次都会被重力多补一点劲，永远停不下来
                v_in = math.sqrt(max(0.0, (self.vy - g * dt) ** 2 + 2 * g * (floor - y0)))
                rebound = v_in * (0.7 - 0.4 * min(1.0, v_in / (300 * s)))
                bounce = rebound > 25 * s  # 再矮就只是原地抖一下，不像弹了
            else:
                rebound, bounce = self.vy * 0.3, self.vy > 150 * s  # 摔得重就弹一下
            # 着地那一下脚底的摩擦吃掉一点横向速度：砸得越实吃得越多，轻轻一点地几乎不减速，
            # 所以横着扔出去会在地上一下下往前跳，最后落地接着往前滑
            rub = 0.1 * (self.vy + (rebound if bounce else 0.0))
            self.vx = math.copysign(max(0.0, abs(self.vx) - rub), self.vx)
            if bounce:
                if self.mood == "thrown":
                    if not self.heavy:
                        self.say(random.choice(OUCH_LINES))
                    self.mood = "fall"
                self.vy = -rebound
            else:
                vx, self.vx, self.vy = self.vx, 0.0, 0.0
                if perch is not None:
                    self.perch_on(perch)
                    vx -= self.carry_vx  # 在窗口上滑是相对窗口滑
                if abs(vx) > 20 * s:  # 横着扔下来的，落地后带着横向的速度往前滑一段
                    self.skid_v = vx
                    self.go("skid")
                    self.spawn_t = -1.0
                else:
                    self.settle()
        self.air_tops = {p[0]: p[1] for p in self.platforms}

    def settle(self):
        """落稳了：砸晕了的、还没晕完的接着晕，不然落地缓一下。"""
        if self.heavy:
            self.dizzy_end = self.t + DIZZY_TIME
        if self.t < self.dizzy_end:  # 弹完了接着晕；晕着被拎起来又放下的话，也接着晕完
            self.after_land = None
            self.go("dizzy")
        else:
            self.go("land")

    def step_skid(self, dt):
        """脚底刹着往前滑，越滑越慢；撞到屏幕边弹回来一点，滑出窗口边就掉下去（ride 会管）。"""
        v, brake = self.skid_v, SKID_FRICTION * self.s * dt
        if abs(v) <= brake:
            self.skid_v = 0.0
            self.settle()
            return
        self.skid_v = v - math.copysign(brake, v)
        self.shift((v + self.skid_v) / 2 * dt)
        if self.perch is None:
            left, right = self.bounds()[:2]
            if not left <= self.x <= right:
                self.x = min(max(self.x, left), right)
                self.skid_v *= -0.3
        if abs(self.skid_v) > 40 * self.s and self.state_t - self.spawn_t > 0.08:
            self.spawn_t = self.state_t
            self.add_skid_dust()

    def step_land(self, dt):
        if self.state_t > 0.18:
            then, self.after_land = self.after_land, None
            (then or self.idle)()

    def step_drag(self, dt):
        pass

    def step_wave(self, dt):
        if self.state_t > 2.4:
            self.idle()

    def step_pet(self, dt):
        if self.state_t - self.spawn_t > 0.35:
            self.spawn_t = self.state_t
            self.add_heart()
        if self.state_t > 1.6:
            self.idle()

    def step_annoyed(self, dt):
        if self.state_t > 0.9:
            self.idle()

    def step_surprised(self, dt):
        if self.state_t > 0.7:
            self.wake()

    def greet(self):
        self.idle()
        if not self.cfg["met"]:
            self.cfg["met"] = True
            save_cfg(self.cfg)
            self.say("嗨！我是 Clawd～\n右键我有菜单哦", 6)
        else:
            self.say(greeting())

    def say(self, text, dur=None):
        self.bubble = text
        self.bubble_until = self.t + (dur or min(6.0, 2 + len(text) * 0.15))

    def add_heart(self):
        self.particles.append(dict(
            kind="heart", x=self.W / 2 + random.uniform(-6, 6) * self.s, y=BUBBLE_H + random.uniform(0, 2) * self.s,
            vx=random.uniform(-12, 12), vy=-random.uniform(35, 55), age=0.0, life=1.3,
        ))

    def add_z(self):
        self.particles.append(dict(kind="z", x=self.W / 2 + 4 * self.s, y=self.gy, vx=14.0, vy=-20.0, age=0.0, life=2.6))

    # 灰尘和彩纸记的是屏幕坐标：扬起来就留在原地，不跟着 Clawd 走

    def add_dust(self, strength):
        """落地时脚边扬起的灰：贴着地往两边蹭出去，越来越慢，散掉。strength 是 0..1，摔得越重灰越多、蹭得越远。"""
        s = self.s
        for side in (-1, 1):
            for _ in range(1 + int(strength * 2.5)):
                self.particles.append(dict(
                    kind="dust", screen=True, x=self.x + side * random.uniform(4, 6) * s, y=self.y - random.uniform(0, 0.5) * s,
                    vx=side * random.uniform(30, 60 + 140 * strength), vy=-random.uniform(5, 15 + 35 * strength), drag=4.0,
                    age=0.0, life=random.uniform(0.3, 0.45) + 0.15 * strength,
                ))
        self.place_fx()

    def add_skid_dust(self):
        """滑的时候脚底下一路蹭起来的灰，留在身后。"""
        d = 1 if self.skid_v > 0 else -1
        self.particles.append(dict(
            kind="dust", screen=True, x=self.x - d * random.uniform(3, 5) * self.s, y=self.y,
            vx=-d * random.uniform(10, 30), vy=-random.uniform(5, 15), drag=4.0,
            age=0.0, life=random.uniform(0.3, 0.45),
        ))
        self.place_fx()

    def add_puff(self):
        """噗：笔记本收起来时冒的一小团灰。"""
        s = self.s
        for k in range(8):
            a = k * math.pi / 4
            self.particles.append(dict(
                kind="dust", screen=True, x=self.x, y=self.y - 2 * s, vx=math.cos(a) * 60, vy=math.sin(a) * 40 - 10,
                age=0.0, life=0.4,
            ))
        self.place_fx()

    def place_fx(self):
        """把画灰的窗口挪过去，盖住所有灰从现在到散掉要飘过的地方。"""
        boxes, u = [], max(2, self.s // 2)
        for p in self.particles:
            if p["kind"] == "dust":
                k, left = p.get("drag", 0.0), p["life"] - p["age"]
                reach = (1 - math.exp(-k * left)) / k if k else left  # 有阻力的话越飘越慢，飘不了 v * t 那么远
                for x, y in ((p["x"], p["y"]), (p["x"] + p["vx"] * reach, p["y"] + p["vy"] * reach)):
                    boxes.append((x - 2 * u - 1, y - 3 * u - 1, x + 2 * u + 1, y + 1))
        x0, y0 = int(min(b[0] for b in boxes)), int(min(b[1] for b in boxes))
        w, h = int(max(b[2] for b in boxes)) + 1 - x0, int(max(b[3] for b in boxes)) + 1 - y0
        shown = self.fx_rect is not None
        self.fx_rect = (x0, y0, w, h)
        self.fx.set_size_request(w, h)
        self.fx.resize(w, h)
        self.fx.move(x0, y0)
        if not shown:
            self.fx.show()
            win = self.win.get_window()
            if win:
                win.raise_()  # 灰在 Clawd 身后
        self.fx.queue_draw()

    def add_confetti(self):
        """撒花：彩色纸屑往上喷，再飘下来。"""
        s = self.s
        for _ in range(16):
            self.particles.append(dict(
                kind="confetti", screen=True, x=self.x + random.uniform(-3, 3) * s, y=self.y - 9 * s,
                vx=random.uniform(-80, 80), vy=-random.uniform(180, 280), g=420.0,  # 比 Clawd 跳得还快，喷到它头顶上
                age=0.0, life=random.uniform(0.9, 1.4), color=random.choice(CONFETTI),
            ))

    # ---- 画面

    def pose(self):
        st, k, t = self.state, self.state_t, self.t
        eyes = "blink" if t < self.blink_until else "open"

        def flip(period):
            return int(t / period) % 2 == 1

        if st == "idle":
            if self.busy:  # 躲在小笔记本后面敲代码
                return Typing(t, t - self.busy_since, blink=t < self.blink_until)
            if t - self.ride_t < 0.3:  # 坐着窗口兜风，张开手保持平衡
                return Pose("wide", arms=(-1, -1))
            return Pose(eyes, self.hover_look())
        if st == "walk":
            f = flip(0.16)
            return Pose(eyes, 1 if self.walk_to > self.x else -1,
                        arms=(1, 0) if f else (0, 1), legs=(1, 2, 1, 2) if f else (2, 1, 2, 1))
        if st == "look":
            return Pose(eyes, -1 if 0.3 <= k < 1.0 else 1 if 1.2 <= k < 1.9 else 0)
        if st == "sleep":
            return Pose("closed", shape="breathe" if int(k / 1.4) % 2 else "normal")
        if st in ("air", "drag") and self.dazed():  # 懵着，手脚软软地耷拉着，和落地后晕着的样子一样
            return Pose("dizzy", arms=(1, 1), legs=(3, 3, 3, 3) if st == "drag" else (2, 2, 2, 2))
        if st == "air" and self.mood in ("happy", "leap"):
            return Pose("happy", arms=(-3, -3), shape="stretch" if self.vy < 0 else "normal",
                        blush=self.mood == "happy")
        if st in ("air", "drag"):
            fast = st == "drag" and math.hypot(*self.fling()) > 1200
            return Pose("squint" if fast else "wide", arms=(-3, -3),
                        legs=(3, 2, 3, 2) if flip(0.1) else (2, 3, 2, 3))
        if st == "land":
            happy = self.mood == "happy"
            return Pose("happy" if happy else eyes, shape="squash", blush=happy)
        if st == "crouch":
            return Pose("down", arms=(1, 1), shape="squash")
        if st == "wave":
            return Pose("happy", arms=(0, -3 if flip(0.22) else -1))
        if st == "pet":
            return Pose("happy", blush=True, shape="breathe" if int(k / 0.3) % 2 else "normal")
        if st == "annoyed":
            return Pose("squint", dx=1 if int(k / 0.06) % 2 else -1)
        if st == "skid":
            d = 1 if self.skid_v > 0 else -1
            if self.dazed():  # 晕着就软软地滑，不刹
                return Pose("dizzy", arms=(1, 1))
            return Pose("squint", d, arms=(-2, 0) if d > 0 else (0, -2),  # 眯着眼往前看，后面那只手甩起来，脚底乱蹬着刹车
                        legs=(2, 1, 2, 1) if flip(0.06) else (1, 2, 1, 2))
        if st == "dizzy":
            if not self.dazed():  # 甩甩头，回过神来
                return Pose("squint", dx=1 if int(k / 0.05) % 2 else -1)
            return Pose("dizzy", arms=(1, 1), dx=round(math.sin(k * 5)))  # 晕乎乎地左右晃
        if st == "yawn":
            if 0.3 <= k < 1.1:  # 伸长身子，举手，眯眼
                return Pose("squint", arms=(-3, -3), shape="stretch")
            return Pose("closed", shape="breathe" if k >= 1.1 else "normal")
        if st == "stretchup":
            return Pose("happy", arms=(-3, -3), shape="stretch") if k < 0.5 else Pose("happy")
        if st == "peek":  # 探出身子往下看，靠边那只手微微抬起来保持平衡
            d = self.peek_dir
            return Pose("down", look=d, dx=d, arms=(-1, 0) if d < 0 else (0, -1))
        return Pose("wide", arms=(-1, -1))  # surprised

    def hover_look(self):
        if self.hover is None:
            return 0
        d = self.hover - self.W / 2
        return -1 if d < -2 * self.s else 1 if d > 2 * self.s else 0

    def bubble_markup(self):
        if self.t < self.bubble_until:
            return html.escape(self.bubble, quote=False)
        if self.busy and self.state != "sleep":
            return spinner_markup(self.t, self.busy_verb)
        return None

    def on_draw(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        s = self.s
        if self.state not in ("air", "drag"):
            draw_shadow(cr, s, self.gx, self.gy)
        draw_clawd(cr, self.pose(), s, self.gx, self.gy)
        ox, oy = self.win_pos or (0, 0)
        u = max(2, s // 2)
        for p in self.particles:
            fade = min(1.0, 3 * (1 - p["age"] / p["life"]))
            grow = p["age"] / p["life"]
            x, y = (p["x"] - ox, p["y"] - oy) if p.get("screen") else (p["x"], p["y"])
            if p["kind"] == "heart":
                draw_heart(cr, x, y, u, fade)
            elif p["kind"] == "z":
                draw_outlined(cr, "Z" if grow > 0.4 else "z", x, y, 7 + 6 * grow, fade)
            elif p["kind"] == "confetti":  # 彩纸，翻来翻去；灰画在 on_draw_fx 里
                w, h = (u, max(1, u // 2)) if int(p["age"] * 12) % 2 else (max(1, u // 2), u)
                cr.set_source_rgba(*p["color"], fade)
                cr.rectangle(int(x), int(y), w, h)
                cr.fill()
        if self.state in DAZE_STATES and self.dazed():
            sway = round(math.sin(self.state_t * 5)) if self.state == "dizzy" else 0
            draw_dizzy_stars(cr, self.W / 2 + sway * s, self.gy - 1.5 * s, s, self.t)
        markup = self.bubble_markup()
        if markup:
            draw_bubble(cr, markup, self.W / 2, BUBBLE_H, self.W)
        return True

    def on_draw_fx(self, _w, cr):
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)
        if self.fx_rect:
            ox, oy = self.fx_rect[:2]
            u = max(2, self.s // 2)
            for p in self.particles:
                if p["kind"] == "dust":
                    frame = min(len(DUST_PIX) - 1, int(len(DUST_PIX) * p["age"] / p["life"]))
                    draw_dust(cr, p["x"] - ox, p["y"] - oy, u, frame)
        return True

    # ---- 鼠标

    def on_press(self, _w, ev):
        if ev.type != Gdk.EventType.BUTTON_PRESS:  # 双击三击会额外再来一遍，忽略
            return True
        self.last_touch = self.t
        if ev.button == 3:
            self.show_menu(ev)
        elif ev.button == 1:
            self.press = (ev.x_root, ev.y_root, self.x, self.y)
            self.samples = [(time.monotonic(), ev.x_root, ev.y_root)]
        return True

    def on_motion(self, _w, ev):
        if self.press:
            x0, y0, px, py = self.press
            if self.state != "drag" and abs(ev.x_root - x0) + abs(ev.y_root - y0) > 6:
                self.go("drag")
                self.perch = None
                if random.random() < 0.4 and not self.dazed():
                    self.say(random.choice(DRAG_LINES))
            if self.state == "drag":
                self.x, self.y = px + ev.x_root - x0, py + ev.y_root - y0
                self.samples = self.samples[-5:] + [(time.monotonic(), ev.x_root, ev.y_root)]
                self.place()
        else:
            self.stroke(ev.x)
            self.hover = ev.x
        return True

    def on_release(self, _w, ev):
        if ev.button != 1 or not self.press:
            return True
        self.press = None
        if self.state == "drag":
            self.drop("thrown", *self.fling())
        else:
            self.click()
        return True

    def on_leave(self, _w, _ev):
        self.hover = self.stroke_x = None
        return True

    def fling(self):
        """最近 0.1 秒的拖拽速度，松手时就按这个速度把 Clawd 扔出去。"""
        now = time.monotonic()
        pts = [p for p in self.samples if now - p[0] < 0.1]
        if len(pts) < 2 or pts[-1][0] - pts[0][0] < 0.01:
            return 0.0, 0.0
        span = pts[-1][0] - pts[0][0]

        def clamp(v):
            return max(-2500.0, min(2500.0, v))

        return clamp((pts[-1][1] - pts[0][1]) / span), clamp((pts[-1][2] - pts[0][2]) / span)

    def click(self):
        if self.state == "sleep":
            self.go("surprised")
            self.say("！？", 0.8)
            return
        self.pokes = [p for p in self.pokes if self.t - p < 2.5] + [self.t]
        if self.state in ("air", "land", "drag", "dizzy", "skid"):  # 半空中的、晕着的、正滑着的也算数，停下来再生气
            return
        if len(self.pokes) >= 5:
            self.pokes = []
            self.go("annoyed")
            self.say(random.choice(POKE_LINES))
            return
        self.jump()
        if random.random() < 0.5:
            self.say(random.choice(CLICK_LINES))

    def stroke(self, x):
        """鼠标在 Clawd 身上来回蹭五下就算摸摸。"""
        if self.stroke_x is None:
            self.stroke_x = x
            return
        dx = x - self.stroke_x
        if abs(dx) < 2 * self.s:
            return
        self.stroke_x = x
        d = 1 if dx > 0 else -1
        if d == self.stroke_dir:
            return
        self.stroke_dir = d
        self.strokes = self.strokes + 1 if self.t - self.stroke_t < 0.7 else 1
        self.stroke_t = self.t
        if self.strokes >= 5 and self.state in ("idle", "look", "walk"):
            self.strokes = 0
            self.last_touch = self.t
            self.go("pet")
            self.spawn_t = -1.0
            self.say(random.choice(PET_LINES))

    def show_menu(self, ev):
        menu = Gtk.Menu()

        def item(label, callback):
            it = Gtk.MenuItem(label=label)
            it.connect("activate", lambda *_: callback())
            menu.append(it)

        def check(label, key):
            it = Gtk.CheckMenuItem(label=label)
            it.set_active(self.cfg[key])
            it.connect("toggled", lambda w: self.set_option(key, w.get_active()))
            menu.append(it)

        item("说句话", lambda: self.say(chatter_line()) if self.state != "sleep" else self.wake())
        item("叫醒" if self.state == "sleep" else "去睡觉", self.toggle_sleep)
        menu.append(Gtk.SeparatorMenuItem())
        check("自由散步", "walk")
        it = Gtk.CheckMenuItem(label="在窗口上走" if self.linked else "在窗口上走（要先装 GNOME 扩展）")
        it.set_active(self.cfg["windows"])
        it.set_sensitive(self.linked)
        it.connect("toggled", lambda w: self.set_option("windows", w.get_active()) or self.update_platforms())
        menu.append(it)
        check("安静模式（不主动说话）", "quiet")
        sizes, group = Gtk.Menu(), None
        for label, s in (("小", 4), ("中", 6), ("大", 8)):
            it = Gtk.RadioMenuItem.new_with_label_from_widget(group, label)
            group = it
            it.set_active(self.s == s)
            it.connect("toggled", lambda w, s=s: w.get_active() and self.set_scale(s))
            sizes.append(it)
        size_item = Gtk.MenuItem(label="大小")
        size_item.set_submenu(sizes)
        menu.append(size_item)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(self.tuning_menu())
        item("重新加载 clawd.py", self.reload)
        item("拜拜（退出）", self.quit)
        menu.show_all()
        self.menu = menu  # 留个引用，免得菜单还开着就被回收
        menu.popup_at_pointer(ev)

    def tuning_menu(self):
        """「调手感」：每个参数一个子菜单，选哪档马上生效。调过的标 *，代码里写的那档也标出来。"""
        tune = Gtk.Menu()
        for name, label, unit, presets in TUNABLES:
            current = globals()[name]
            values, group = Gtk.Menu(), None
            for value in sorted(set(presets) | {current, CODE_VALUES[name]}):
                text = tuning_text(value, unit) + ("（代码里的值）" if value == CODE_VALUES[name] else "")
                it = Gtk.RadioMenuItem.new_with_label_from_widget(group, text)
                group = it
                it.set_active(value == current)
                it.connect("toggled", lambda w, n=name, v=value: w.get_active() and self.tune(n, v))
                values.append(it)
            mark = "  *" if current != CODE_VALUES[name] else ""
            it = Gtk.MenuItem(label=f"{label}：{tuning_text(current, unit)}{mark}")
            it.set_submenu(values)
            tune.append(it)
        tune.append(Gtk.SeparatorMenuItem())
        reset = Gtk.MenuItem(label="全部恢复成代码里的值")
        reset.set_sensitive(bool(self.cfg["tuning"]))
        reset.connect("activate", lambda *_: self.tune(None, None))
        tune.append(reset)
        it = Gtk.MenuItem(label="调手感")
        it.set_submenu(tune)
        return it

    def tune(self, name, value):
        """name 为 None 就是全部恢复。调过的值存进设置，重启后还在。"""
        tuning = self.cfg["tuning"]
        if name is None:
            tuning.clear()
            self.say("手感恢复成代码里的了", 2)
        else:
            if value == CODE_VALUES[name]:
                tuning.pop(name, None)
            else:
                tuning[name] = value
            label, unit = next((t[1], t[2]) for t in TUNABLES if t[0] == name)
            self.say(f"{label} → {tuning_text(value, unit)}", 2)
        apply_tuning(tuning)
        save_cfg(self.cfg)

    def set_option(self, key, value):
        self.cfg[key] = value
        save_cfg(self.cfg)

    def toggle_sleep(self):
        if self.state == "sleep":
            self.wake()
        elif self.state in CALM:
            self.say("晚安～", 1.5)
            self.sleep(dur=1e9)  # 自己点的睡觉就一直睡到被叫醒

    def quit(self):
        if self.state in CALM:
            self.go("wave")
        self.say("拜拜～", 2)
        GLib.timeout_add(900, self.shutdown)

    def shutdown(self, *_):
        save_cfg(self.cfg)
        try:
            os.unlink(SOCK_PATH)
        except OSError:
            pass
        Gtk.main_quit()
        return False

    def reload(self):
        """改完 clawd.py 换成新的自己。新代码有语法错误、或者一启动就崩，就不换，旧的接着用。"""
        import subprocess

        path = os.path.abspath(__file__)
        try:
            with open(path, encoding="utf-8") as f:
                compile(f.read(), path, "exec")
        except SyntaxError as e:
            self.say(f"第 {e.lineno} 行有语法错误，先不换", 5)
            return
        except (OSError, ValueError) as e:
            self.say(f"读不了 clawd.py：{e}", 5)
            return
        save_cfg(self.cfg)
        self.close_socket()  # 让出 socket，新的才不会以为已经有 Clawd 在了
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "w") as log:
            self.heir = subprocess.Popen([sys.executable, path], stdout=log, stderr=log, start_new_session=True)
        self.heir_deadline = time.monotonic() + 8
        self.win.hide()
        self.fx.hide()
        GLib.timeout_add(200, self.await_heir)

    def await_heir(self):
        """新的 Clawd 开好了自己的 socket 就算接班成功，旧的退出；它崩了或者一直起不来，旧的就回来。"""
        if self.heir.poll() is None:
            if send({"event": "ping"}):
                Gtk.main_quit()
                return False
            if time.monotonic() < self.heir_deadline:
                return True
            self.heir.kill()
            self.heir.wait()
        self.open_socket()
        self.win.show()
        self.say("新代码跑不起来，报错在 " + LOG_PATH.replace(os.path.expanduser("~"), "~", 1), 8)
        return False

    # ---- 别的窗口在哪（GNOME 扩展通过 D-Bus 告诉我们）

    def watch_windows(self):
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        self.bus.signal_subscribe(WINDOWS_BUS, WINDOWS_IFACE, "Changed", WINDOWS_PATH, None,
                                  Gio.DBusSignalFlags.NONE, self.on_windows_changed)
        Gio.bus_watch_name_on_connection(self.bus, WINDOWS_BUS, Gio.BusNameWatcherFlags.NONE,
                                         self.on_link_up, self.on_link_down)

    def on_link_up(self, bus, _name, _owner):
        self.linked = True
        bus.call(WINDOWS_BUS, WINDOWS_PATH, WINDOWS_IFACE, "GetWindows", None, GLib.VariantType(WINDOWS_TYPE),
                 Gio.DBusCallFlags.NONE, 2000, None, self.on_windows_reply)
        self.track_pet()

    def track_pet(self):
        """告诉扩展哪个窗口是 Clawd、身体在哪：鼠标不在身体上时，它让整个窗口不挡鼠标。
        旧版扩展没有这个方法，调用失败也没关系，只是周围透明的地方还会挡。"""
        if not self.linked:
            return
        self.win.realize()  # 要拿 X 窗口编号，窗口得先建出来
        xid = self.win.get_window().get_xid()
        self.bus.call(WINDOWS_BUS, WINDOWS_PATH, WINDOWS_IFACE, "TrackPet",
                      GLib.Variant("(tuiiii)", (xid, os.getpid(), *self.hitbox())),
                      None, Gio.DBusCallFlags.NONE, 2000, None, None)

    def on_windows_reply(self, bus, result):
        try:
            self.set_windows(bus.call_finish(result).unpack()[0])
        except GLib.Error:
            pass

    def on_windows_changed(self, _bus, _sender, _path, _iface, _signal, params):
        if params.get_type_string() == WINDOWS_TYPE:
            self.set_windows(params.unpack()[0])

    def on_link_down(self, _bus, _name):
        self.linked = False
        self.set_windows([])

    def set_windows(self, windows):
        """窗口一有变化扩展就发过来，拖动时每帧一次。收到就马上算、马上挪，不等下一帧，不然会拖在后面。"""
        before = self.motion
        self.track_motion(windows, time.monotonic())
        self.windows = list(windows)
        self.update_platforms()
        self.knock(before)
        if self.perch is not None and (self.state in CALM or self.state == "land"):
            self.ride()
        self.place()
        self.win.get_display().flush()

    # ---- 外部消息（Claude Code hook 等）

    def open_socket(self):
        try:
            os.unlink(SOCK_PATH)
        except FileNotFoundError:
            pass
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(SOCK_PATH)
        self.sock.setblocking(False)
        self.sock_watch = GLib.io_add_watch(self.sock.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self.on_socket)

    def close_socket(self):
        GLib.source_remove(self.sock_watch)
        self.sock.close()
        try:
            os.unlink(SOCK_PATH)
        except OSError:
            pass

    def on_socket(self, *_):
        while True:
            try:
                data = self.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                return True
            except OSError:  # 刚处理的消息是 reload，socket 已经让出去了
                return False
            try:
                msg = json.loads(data)
            except ValueError:
                continue
            if isinstance(msg, dict):
                self.handle(msg)

    def handle(self, msg):
        event, text = msg.get("event"), str(msg.get("text") or "").strip()
        if event == "ping":  # 重新加载时，旧的 Clawd 用它确认新的已经起来了
            return
        self.last_touch = self.t
        if event in ("busy", "tool"):
            tool = str(msg.get("tool") or "")
            verb = "调用工具" if tool.startswith("mcp__") else TOOL_VERBS.get(tool)
            if event == "busy" or not self.busy:
                self.busy_verb = random.choice(BUSY_VERBS)
            if not self.busy:
                self.busy_since = self.t  # 笔记本从这时候开始掀盖子
            self.busy_verb = text or verb or self.busy_verb
            self.busy, self.busy_seen = True, self.t
            if self.free() and self.state != "idle":
                self.idle()
        elif event == "done":
            if self.busy and self.state == "idle":
                self.add_puff()  # 笔记本噗地收起来
            self.busy = False
            if self.free():
                self.jump()
                self.add_confetti()
            self.say(text or random.choice(DONE_LINES))
        elif event == "attention":
            self.busy = False
            if self.free():
                self.go("wave")
            self.say(text or attention_text(str(msg.get("message") or "")), 5)
        elif event == "say":
            if self.state == "sleep":
                self.idle()
            self.say(text or chatter_line())
        elif event == "reload":
            self.reload()


# ---------------------------------------------------------------- 预览图 / 图标

def render_preview(path):
    s = 7
    say = html.escape
    demos = [
        ("待机", Pose(), None, None),
        ("眨眼", Pose("blink"), None, None),
        ("东张西望", Pose(look=-1), None, None),
        ("散步", Pose(look=1, arms=(1, 0), legs=(1, 2, 1, 2)), None, None),
        ("点一下：开心跳", Pose("happy", arms=(-3, -3), shape="stretch", blush=True), say("嘿嘿"), "heart"),
        ("落地", Pose(shape="squash"), None, None),
        ("被拎起来", Pose("squint", arms=(-3, -3), legs=(3, 2, 3, 2)), say("放我下来！"), None),
        ("睡觉", Pose("closed", shape="breathe"), None, "z"),
        ("Claude 开工：掀开笔记本", Typing(0.0, 0.15), None, None),
        ("Claude 在干活", Typing(0.0, 9.0), spinner_markup(0.5, "改代码"), None),
        ("敲着敲着想一想", Typing(4.6, 9.0), spinner_markup(1.0, "Pondering"), None),
        ("Claude 干完：撒花", Pose("happy", arms=(-3, -3), shape="stretch", blush=True), say("搞定啦 ✓"), "confetti"),
        ("Claude 等你确认", Pose("happy", arms=(0, -3)), say("Claude 需要你批准一下～"), None),
        ("被摸摸", Pose("happy", blush=True), say("嘿嘿，好舒服～"), "heart"),
        ("戳太多下", Pose("squint", dx=1), say("别戳啦 >_<"), None),
        ("重摔：落地扬灰", Pose("dizzy", arms=(1, 1), shape="squash"), say("@_@"), "dust"),
        ("然后眼冒金星", Pose("dizzy", arms=(1, 1), dx=1), say("眼冒金星……"), "stars"),
        ("睡前打哈欠", Pose("squint", arms=(-3, -3), shape="stretch"), say("哈啊——"), None),
        ("窗口边探头往下看", Pose("down", look=1, dx=1, arms=(0, -1)), say("好高……"), None),
    ]
    cols, cw, ch = 4, 240, BUBBLE_H + GRID_ROWS * s + 30
    rows = (len(demos) + cols - 1) // cols
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, cols * cw, rows * ch)
    cr = cairo.Context(surface)
    cr.set_source_rgb(0.94, 0.93, 0.9)
    cr.paint()
    for i, (label, pose, bubble, extra) in enumerate(demos):
        cr.save()
        cr.translate((i % cols) * cw, (i // cols) * ch)
        gx, gy = (cw - GRID_W * s) // 2, BUBBLE_H - GRID_TOP * s
        if getattr(pose, "arms", None) != (-3, -3):  # 双手举高的都在半空，不画影子
            draw_shadow(cr, s, gx, gy)
        draw_clawd(cr, pose, s, gx, gy)
        if extra == "heart":
            draw_heart(cr, cw / 2 - 5 * s, BUBBLE_H + 4, 3, 1)
            draw_heart(cr, cw / 2 + 6 * s, BUBBLE_H - 14, 3, 0.8)
        elif extra == "z":
            draw_outlined(cr, "z", cw / 2 + 5 * s, gy - 4, 8, 1)
            draw_outlined(cr, "Z", cw / 2 + 7 * s, gy - 30, 11, 0.8)
        elif extra == "stars":
            draw_dizzy_stars(cr, cw / 2 + s, gy - 1.5 * s, s, 0.35)
        elif extra == "dust":
            for dx, dy, frame in ((-8.5, 10, 1), (-11.5, 9.6, 2), (8.5, 10, 1), (11.8, 9.5, 2)):
                draw_dust(cr, cw / 2 + dx * s, gy + dy * s, max(2, s // 2), frame)
        elif extra == "confetti":
            rng = random.Random(4)
            for _ in range(14):
                cr.set_source_rgb(*rng.choice(CONFETTI))
                w, h = rng.choice(((4, 2), (2, 4)))
                cr.rectangle(int(cw / 2 + rng.uniform(-9, 9) * s), int(gy + rng.uniform(-4, 3) * s), w, h)
                cr.fill()
        if bubble:
            draw_bubble(cr, bubble, cw / 2, BUBBLE_H, cw)
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription("Sans 10"))
        layout.set_text(label, -1)
        _, ext = layout.get_pixel_extents()
        cr.set_source_rgba(*INK, 0.7)
        cr.move_to((cw - ext.width) / 2, gy + (FOOT + 1) * s + 6)
        PangoCairo.show_layout(cr, layout)
        cr.restore()
    surface.write_to_png(path)


def render_icon(path, size=256):
    s = size // 20
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    draw_clawd(cr, Pose(), s, (size - GRID_W * s) // 2, (size - FOOT * s) // 2)
    surface.write_to_png(path)


def main():
    args = sys.argv[1:]
    if args[:1] == ["--preview"]:
        render_preview(args[1] if len(args) > 1 else "preview.png")
        return
    if args[:1] == ["--icon"]:
        render_icon(args[1] if len(args) > 1 else "icon.png")
        return
    if send({"event": "say", "text": "我在这儿呢！"}):
        print("Clawd 已经在桌面上啦～")
        return
    if Gdk.Display.get_default() is None:
        sys.exit("打不开图形界面，Clawd 需要在桌面环境里运行")
    Pet(load_cfg())
    Gtk.main()


if __name__ == "__main__":
    main()
