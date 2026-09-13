import sys
import math
import random
import time
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSize, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QFrame, QButtonGroup, QStackedWidget, QSizePolicy,
    QGraphicsDropShadowEffect
)

APP_NAME = "5imp1e 5atebox"
APP_VERSION = "0.6.2"
BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = BASE_DIR / "resource"


class AccentLine(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)

    def paintEvent(self, event):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#242424"))


class NavButton(QPushButton):
    def __init__(self, text, glyph, parent=None):
        super().__init__(f"{glyph}    {text}", parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(48)
        self.setObjectName("navButton")


class CountButton(QPushButton):
    def __init__(self, number, parent=None):
        super().__init__(str(number), parent)
        self.number = number
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(58, 58)
        self.setObjectName("countButton")


class MiniSwitch(QPushButton):
    def __init__(self, text, checked=False, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(36)
        self.setObjectName("chipButton")


class PhysicalCard:
    """A single physical card object.

    The face, back and orientation live on the same object for the lifetime of the
    program.  Shuffling therefore moves card objects instead of shuffling a list
    of filenames and choosing a result separately afterwards.
    """
    __slots__ = ("card_id", "path", "face", "back", "reversed")

    def __init__(self, card_id, path, face, back=None):
        self.card_id = card_id
        self.path = path
        self.face = face
        self.back = back.copy() if back is not None and not back.isNull() else QPixmap()
        self.reversed = False


class TarotStage(QWidget):
    animationFinished = Signal(list)
    shuffleFinished = Signal()
    animationStatus = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(470)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        front_dir = RESOURCE_DIR / "cards" / "front"
        front_files = []
        for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            front_files.extend(front_dir.glob(pattern))
        self.card_files = sorted(set(front_files), key=lambda p: p.name.lower())

        back_dir = RESOURCE_DIR / "cards" / "back"
        back_files = []
        for pattern in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            back_files.extend(back_dir.glob(pattern))
        back_pm = QPixmap(str(sorted(back_files, key=lambda p: p.name.lower())[0])) if back_files else QPixmap()

        self.cards = []
        for i, path in enumerate(self.card_files):
            self.cards.append(PhysicalCard(i, path, QPixmap(str(path)), back_pm))
        # Compatibility with the rest of the UI.
        self.card_pixmaps = [c.face for c in self.cards]
        self.major_indices = [i for i, p in enumerate(self.card_files) if self._is_major(p.stem)]

        self.state = "idle"
        self.state_started = time.perf_counter()
        self.draw_count = 3
        self.allow_reversed = True
        self.major_only = False

        # deck_order is bottom -> top. The top physical card is deck_order[-1].
        self.deck_order = list(range(len(self.cards)))
        self.active_order = self.deck_order[:]
        self.selected_indices = []
        self.selected_reversed = []
        self.revealed = []

        # Real shuffle state.
        self.shuffle_rounds = 3
        self.shuffle_round = 0
        self.round_input = []
        self.left_pile = []
        self.right_pile = []
        self.round_output = []
        self.release_index = {}
        self.release_side = {}
        self.release_source_rank = {}
        self.release_target_rank = {}

        # Timings deliberately favor visible physical motion over speed.
        self.gather_duration = 0.52
        self.split_duration = 0.62
        self.release_gap = 0.018
        self.release_move_duration = 0.30
        self.square_duration = 0.48
        self.deal_duration = 0.58
        self.deal_gap = 0.08
        self.flip_duration = 0.52

        # Multiple cards may flip at the same time. Each slot keeps its own start time.
        self.flip_started_by_slot = {}
        self._finished_emitted = False

        # After dealing, the remaining physical deck is never hidden. It glides to
        # the lower-left of the table and stays there in a flattened perspective pose.
        self.deck_park_started = 0.0
        self.deck_park_duration = 0.72

        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.hover_current = QPointF(0.0, 0.0)

        # Manual-choice mode: the full 78-card deck falls from above and scatters
        # across the table. Users then pick physical cards directly from the spread.
        self.manual_mode = False
        self.scatter_order = []
        self.scatter_pose = {}
        self.scatter_drop_total = 0.0
        self.manual_take_started = {}   # slot -> start time
        self.manual_take_duration = 0.52
        self.manual_rest_gather_duration = 0.72

        # Manual spread physics. The landed position remains the physical base pose;
        # transient offsets let a fast cursor brush nearby cards aside without
        # changing which card they actually are or permanently rearranging the deck.
        self.scatter_velocity = {}
        self.scatter_offset = {}
        self._last_mouse_pos = None
        self._last_mouse_time = 0.0
        self._last_tick_time = time.perf_counter()

    @staticmethod
    def _is_major(stem):
        name = stem.split("_", 1)[1] if "_" in stem else stem
        keys = {
            "Death", "Judgement", "Justice", "Strength", "Temperance", "Wheel_of_Fortune",
            "The_Chariot", "The_Devil", "The_Emperor", "The_Empress", "The_Fool",
            "The_Hanged_Man", "The_Hermit", "The_Hierophant", "The_High_Priestess",
            "The_Lovers", "The_Magician", "The_Moon", "The_Star", "The_Sun",
            "The_Tower", "The_World"
        }
        return name in keys

    @staticmethod
    def _ease(t):
        t = max(0.0, min(1.0, t))
        return 1 - pow(1 - t, 3)

    @staticmethod
    def _ease_in_out(t):
        t = max(0.0, min(1.0, t))
        return 4*t*t*t if t < .5 else 1 - pow(-2*t + 2, 3)/2

    @staticmethod
    def _lerp(a, b, t):
        return a + (b - a) * t

    def _ensure_timer(self):
        if not self._timer.isActive():
            self._timer.start()

    def start_shuffle(self, allow_reversed=True, major_only=False):
        if self.state not in ("idle", "done", "shuffled"):
            return
        if len(self.cards) < 22:
            self.animationStatus.emit("resource/cards/front 中没有完整牌组。")
            return

        pool = self.major_indices[:] if major_only else list(range(len(self.cards)))
        if not pool:
            return
        self.manual_mode = False
        self.allow_reversed = allow_reversed
        self.major_only = major_only

        # Start from the current physical order for a full deck. For a major-only
        # reading, retain the relative physical order of major cards.
        if major_only:
            current = [cid for cid in self.deck_order if cid in set(pool)]
            missing = [cid for cid in pool if cid not in current]
            self.active_order = current + missing
        else:
            # Make sure every physical card exists exactly once in the deck.
            seen = set(self.deck_order)
            self.active_order = self.deck_order[:] + [cid for cid in pool if cid not in seen]
            self.active_order = [cid for cid in self.active_order if cid in set(pool)]

        # Reversal is a property of the physical card, not of the final draw.
        # We rotate a random subset before shuffling, then that orientation travels
        # with the card through every split / interleave / deal animation.
        for cid in self.active_order:
            self.cards[cid].reversed = bool(random.getrandbits(1)) if allow_reversed else False

        self.selected_indices = []
        self.selected_reversed = []
        self.revealed = []
        self.flip_started_by_slot = {}
        self._finished_emitted = False
        self.deck_park_started = 0.0
        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.hover_current = QPointF(0.0, 0.0)

        self.shuffle_round = 0
        self.round_input = self.active_order[:]
        self.state = "gather"
        self.state_started = time.perf_counter()
        self.animationStatus.emit("正在收拢实体牌组")
        self._ensure_timer()
        self.update()

    def start(self, count=3, allow_reversed=True, major_only=False):
        # Backward-compatible alias: start now performs shuffling only.
        self.start_shuffle(allow_reversed=allow_reversed, major_only=major_only)

    def draw_from_deck(self, count=3, major_only=False):
        if self.state not in ("idle", "shuffled", "done"):
            return
        if not self.cards:
            return

        pool_set = set(self.major_indices) if major_only else set(range(len(self.cards)))
        order = [cid for cid in self.deck_order if cid in pool_set]
        if not order:
            self.animationStatus.emit("当前牌组中没有可抽取的牌。")
            return

        self.manual_mode = False
        self.major_only = major_only
        self.draw_count = max(1, min(int(count), len(order)))
        self.active_order = order[:]
        top = self.active_order[-self.draw_count:]
        self.selected_indices = list(reversed(top))
        self.selected_reversed = [self.cards[cid].reversed for cid in self.selected_indices]
        self.revealed = [False] * self.draw_count
        self.flip_started_by_slot = {}
        self._finished_emitted = False
        self.deck_park_started = 0.0
        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.hover_current = QPointF(0.0, 0.0)

        # Remove the drawn physical cards from the persistent deck immediately;
        # their own objects continue travelling through the deal animation.
        selected_set = set(self.selected_indices)
        self.deck_order = [cid for cid in self.deck_order if cid not in selected_set]

        self.state = "deal"
        self.state_started = time.perf_counter()
        self.animationStatus.emit(f"正在从牌顶抽取 1 / {self.draw_count}")
        self._ensure_timer()
        self.update()

    def start_manual(self, count=3, allow_reversed=True):
        if self.state not in ("idle", "done", "shuffled", "await_reveal"):
            return
        if len(self.cards) < 78:
            self.animationStatus.emit("自己选择模式需要完整的 78 张牌。")
            return

        self.manual_mode = True
        self.draw_count = max(1, min(int(count), 8))
        self.allow_reversed = allow_reversed
        self.major_only = False
        # Manual choice always represents a complete physical 78-card deck. If a
        # previous reading removed cards, return every card to the table first.
        current = self.deck_order[:]
        seen = set(current)
        current.extend(cid for cid in range(len(self.cards)) if cid not in seen)
        self.deck_order = current
        self.active_order = current[:]

        for cid in self.active_order:
            self.cards[cid].reversed = bool(random.getrandbits(1)) if allow_reversed else False

        self.selected_indices = []
        self.selected_reversed = []
        self.revealed = []
        self.flip_started_by_slot = {}
        self.manual_take_started = {}
        self._finished_emitted = False
        self.deck_park_started = 0.0
        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.hover_current = QPointF(0.0, 0.0)

        self.scatter_order = self.active_order[:]
        random.shuffle(self.scatter_order)
        self._prepare_scatter_layout()
        self.state = "self_drop"
        self.state_started = time.perf_counter()
        self.animationStatus.emit("自己选择 · 78 张牌正在落下")
        self._ensure_timer()
        self.update()

    def _prepare_scatter_layout(self):
        """Create a readable but still irregular 78-card tabletop spread.

        Instead of picking 78 unrelated coordinates (which can hide large parts of
        the deck), build a jittered grid, shuffle card-to-cell assignment, and then
        add overlap/rotation. Every physical card therefore has a visibly traceable
        landing place while the result still feels like a handful of cards thrown
        onto a table.
        """
        self.scatter_pose = {}
        self.scatter_velocity = {}
        self.scatter_offset = {}
        n = max(1, len(self.scatter_order))

        # Keep the cards small enough that nearly every one remains visually exposed.
        cw = max(34.0, min(47.0, self.width() * .038))
        ch = cw * 1.58
        left = max(18.0, self.width() * .025)
        right = max(left + cw + 10.0, self.width() - left - cw - 18.0)
        top = max(28.0, self.height() * .09)
        bottom = max(top + ch + 12.0, self.height() * .76 - ch)

        # 13 x 6 exactly fits 78 cards. For other deck sizes, derive a similar grid.
        cols = 13 if n >= 70 else max(5, int(math.ceil(math.sqrt(n * 2.1))))
        rows = int(math.ceil(n / cols))
        cell_w = (right - left) / max(1, cols - 1)
        cell_h = (bottom - top) / max(1, rows - 1)
        cells = []
        for row in range(rows):
            for col in range(cols):
                if len(cells) >= n:
                    break
                # Stagger alternate rows to avoid an obviously rectangular layout.
                stagger = (cell_w * .34) if row % 2 else 0.0
                x = left + col * cell_w + stagger
                if x > right:
                    x -= cell_w * .68
                y = top + row * cell_h
                cells.append((x, y))

        random.shuffle(cells)
        max_end = 0.0
        for i, cid in enumerate(self.scatter_order):
            bx, by = cells[i]
            x = bx + random.uniform(-cell_w * .28, cell_w * .28)
            y = by + random.uniform(-cell_h * .25, cell_h * .25)
            x = max(left - cw*.12, min(right, x))
            y = max(top, min(bottom, y))
            final_rot = random.uniform(-27.0, 27.0)

            # Drop in visibly separated waves. Cards originate well above the table
            # and fan across the width, so the user can follow where the deck lands.
            wave = i // max(1, cols)
            delay = wave * .105 + (i % cols) * .016 + random.uniform(0.0, .035)
            duration = random.uniform(.88, 1.28)
            start_x = x + random.uniform(-110.0, 110.0)
            start_y = -ch - random.uniform(70.0, 360.0)
            start_rot = final_rot + random.uniform(-150.0, 150.0)
            drift = random.uniform(-36.0, 36.0)
            bounce = random.uniform(10.0, 22.0)

            self.scatter_pose[cid] = {
                "rect": QRectF(x, y, cw, ch),
                "rot": final_rot,
                "delay": delay,
                "duration": duration,
                "start_x": start_x,
                "start_y": start_y,
                "start_rot": start_rot,
                "drift": drift,
                "bounce": bounce,
            }
            self.scatter_velocity[cid] = QPointF(0.0, 0.0)
            self.scatter_offset[cid] = QPointF(0.0, 0.0)
            max_end = max(max_end, delay + duration)
        self.scatter_drop_total = max_end + .12
        self._last_mouse_pos = None
        self._last_mouse_time = 0.0

    def _scatter_card_pose(self, cid, elapsed):
        d = self.scatter_pose[cid]
        target = d["rect"]
        q = (elapsed - d["delay"]) / max(.001, d["duration"])
        if q <= 0:
            return QRectF(d["start_x"], d["start_y"], target.width(), target.height()), d["start_rot"], 0.0
        if q >= 1:
            off = self.scatter_offset.get(cid, QPointF())
            return target.translated(off.x(), off.y()), d["rot"], 1.0

        # Accelerating fall, sideways air drift, then a damped tabletop bounce.
        fall = min(1.0, q / .80)
        gravity = fall * fall
        sx = d["start_x"]
        tx = target.left()
        x = self._lerp(sx, tx, self._ease_in_out(fall))
        x += math.sin(fall * math.pi) * d["drift"]
        y = self._lerp(d["start_y"], target.top(), gravity)
        if q > .80:
            b = (q - .80) / .20
            # One obvious impact hop followed by a tiny second settling oscillation.
            y = target.top() - math.sin(b * math.pi) * (1.0 - b) * d["bounce"]
            y += math.sin(b * math.pi * 2.0) * (1.0 - b) * 3.0
        rot = self._lerp(d["start_rot"], d["rot"], self._ease_in_out(q))
        return QRectF(x, y, target.width(), target.height()), rot, q

    def _effective_scatter_rect(self, cid):
        d = self.scatter_pose[cid]
        off = self.scatter_offset.get(cid, QPointF())
        return d["rect"].translated(off.x(), off.y())

    def _update_scatter_physics(self, dt):
        if not self.scatter_pose:
            return False
        active = False
        selected = set(self.selected_indices)
        # Gentle spring back + strong damping: cards are brushed away briefly rather
        # than drifting forever or losing their recognizable landing position.
        spring = 24.0
        damping = math.exp(-8.0 * dt)
        for cid in self.scatter_order:
            if cid in selected:
                continue
            off = self.scatter_offset.get(cid, QPointF())
            vel = self.scatter_velocity.get(cid, QPointF())
            vx = vel.x() - off.x() * spring * dt
            vy = vel.y() - off.y() * spring * dt
            vx *= damping
            vy *= damping
            ox = off.x() + vx * dt
            oy = off.y() + vy * dt
            # Limit displacement so cards still look like part of the spread.
            mag = math.hypot(ox, oy)
            if mag > 42.0:
                scale = 42.0 / mag
                ox *= scale; oy *= scale
            self.scatter_velocity[cid] = QPointF(vx, vy)
            self.scatter_offset[cid] = QPointF(ox, oy)
            if abs(vx) + abs(vy) > 2.0 or abs(ox) + abs(oy) > .7:
                active = True
        return active

    def _repel_scatter_from_cursor(self, pos, cursor_speed):
        if cursor_speed < 520.0:
            return
        selected = set(self.selected_indices)
        radius = min(170.0, 92.0 + cursor_speed * .035)
        strength = min(980.0, 280.0 + (cursor_speed - 520.0) * .48)
        for cid in self.scatter_order:
            if cid in selected:
                continue
            rect = self._effective_scatter_rect(cid)
            c = rect.center()
            dx = c.x() - pos.x()
            dy = c.y() - pos.y()
            dist = math.hypot(dx, dy)
            if dist <= 1.0 or dist >= radius:
                continue
            weight = (1.0 - dist / radius) ** 1.7
            nx, ny = dx / dist, dy / dist
            vel = self.scatter_velocity.get(cid, QPointF())
            self.scatter_velocity[cid] = QPointF(
                vel.x() + nx * strength * weight,
                vel.y() + ny * strength * weight,
            )

    def _manual_card_hit(self, pos):
        selected = set(self.selected_indices)
        # Reverse draw order = visually top-most card gets picked first.
        for cid in reversed(self.scatter_order):
            if cid in selected:
                continue
            d = self.scatter_pose.get(cid)
            if not d:
                continue
            rect = self._effective_scatter_rect(cid)
            c = rect.center()
            a = math.radians(-d["rot"])
            dx, dy = pos.x() - c.x(), pos.y() - c.y()
            rx = dx * math.cos(a) - dy * math.sin(a) + c.x()
            ry = dx * math.sin(a) + dy * math.cos(a) + c.y()
            if rect.adjusted(-3, -3, 3, 3).contains(QPointF(rx, ry)):
                return cid
        return -1

    def _prepare_riffle_round(self):
        order = self.active_order[:]
        n = len(order)
        if n < 2:
            self.left_pile = order[:]
            self.right_pile = []
            self.round_output = order[:]
            return

        # A real cut is rarely exactly 50/50.
        cut = int(round(n * 0.5 + random.gauss(0.0, max(1.0, n * 0.045))))
        cut = max(1, min(n - 1, cut))
        self.round_input = order[:]
        self.left_pile = order[:cut]
        self.right_pile = order[cut:]

        # Riffle in packets of 1-3 cards. The next side is weighted by how many
        # cards remain, avoiding a fake perfectly alternating pattern.
        li = ri = 0
        out = []
        release_side = {}
        release_source_rank = {}
        release_target_rank = {}
        side = random.choice(("L", "R"))
        while li < len(self.left_pile) or ri < len(self.right_pile):
            if li >= len(self.left_pile):
                side = "R"
            elif ri >= len(self.right_pile):
                side = "L"
            elif random.random() < 0.30:
                side = "R" if side == "L" else "L"

            remaining = (len(self.left_pile)-li) if side == "L" else (len(self.right_pile)-ri)
            packet = min(remaining, 1 + (1 if random.random() < .36 else 0) + (1 if random.random() < .10 else 0))
            for _ in range(packet):
                if side == "L":
                    cid = self.left_pile[li]
                    release_source_rank[cid] = li
                    li += 1
                else:
                    cid = self.right_pile[ri]
                    release_source_rank[cid] = ri
                    ri += 1
                release_side[cid] = side
                release_target_rank[cid] = len(out)
                out.append(cid)

            # Usually alternate packets, but occasionally the same hand releases twice.
            if random.random() < .82:
                side = "R" if side == "L" else "L"

        self.round_output = out
        self.release_index = {cid: i for i, cid in enumerate(out)}
        self.release_side = release_side
        self.release_source_rank = release_source_rank
        self.release_target_rank = release_target_rank

    def _riffle_total_duration(self):
        n = max(1, len(self.round_output))
        return (n - 1) * self.release_gap + self.release_move_duration

    def _tick(self):
        now = time.perf_counter()
        elapsed = now - self.state_started
        dt = max(0.0, min(.05, now - self._last_tick_time))
        self._last_tick_time = now
        animation_active = False
        if self.state == "self_select":
            if self._update_scatter_physics(dt):
                animation_active = True

        if self.state == "gather":
            animation_active = True
            if elapsed >= self.gather_duration:
                self._prepare_riffle_round()
                self.state = "split"
                self.state_started = now
                self.animationStatus.emit(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · 切牌")

        elif self.state == "split":
            animation_active = True
            if elapsed >= self.split_duration:
                self.state = "riffle"
                self.state_started = now
                self.animationStatus.emit(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · 交错落牌")

        elif self.state == "riffle":
            animation_active = True
            if elapsed >= self._riffle_total_duration():
                self.state = "square"
                self.state_started = now
                self.animationStatus.emit(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · 整理牌组")

        elif self.state == "square":
            animation_active = True
            if elapsed >= self.square_duration:
                # Commit the actual physical order only after every card has landed.
                self.active_order = self.round_output[:]
                if not self.major_only:
                    self.deck_order = self.active_order[:]
                self.shuffle_round += 1
                if self.shuffle_round < self.shuffle_rounds:
                    self._prepare_riffle_round()
                    self.state = "split"
                    self.state_started = now
                    self.animationStatus.emit(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · 切牌")
                else:
                    # Shuffling is now a complete, standalone action. Keep the
                    # squared physical deck on the table until the user presses Draw.
                    self.state = "shuffled"
                    self.state_started = now
                    self.animationStatus.emit("洗牌完成 · 可以抽取")
                    self.shuffleFinished.emit()

        elif self.state == "deal":
            animation_active = True
            each = self.deal_duration + self.deal_gap
            idx = int(elapsed // each)
            if idx >= self.draw_count:
                self.state = "await_reveal"
                self.state_started = now
                self.deck_park_started = now
                self.animationStatus.emit("抽取完成 · 点击任意牌翻开")
            else:
                self.animationStatus.emit(f"正在从牌顶抽取 {idx + 1} / {self.draw_count}")

        elif self.state == "self_drop":
            animation_active = True
            if elapsed >= self.scatter_drop_total:
                self.state = "self_select"
                self.state_started = now
                self.animationStatus.emit(f"自己选择 · 请挑选 {self.draw_count} 张牌")

        elif self.state == "self_select":
            if self.manual_take_started:
                animation_active = True
            finished_slots = []
            for slot, started in list(self.manual_take_started.items()):
                if now - started >= self.manual_take_duration:
                    finished_slots.append(slot)
            for slot in finished_slots:
                self.manual_take_started.pop(slot, None)
            if len(self.selected_indices) >= self.draw_count and not self.manual_take_started:
                # Commit the manual choices to the same physical deck model used by
                # shuffle/draw: picked cards leave the remaining deck immediately.
                selected_set = set(self.selected_indices)
                self.deck_order = [cid for cid in self.scatter_order if cid not in selected_set]
                self.active_order = self.deck_order[:]
                self.state = "manual_gather_rest"
                self.state_started = now
                self.animationStatus.emit("选择完成 · 正在整理剩余牌组")
                animation_active = True

        elif self.state == "manual_gather_rest":
            animation_active = True
            if elapsed >= self.manual_rest_gather_duration:
                self.state = "await_reveal"
                self.state_started = now
                # Remaining cards are already visually at the parked target.
                self.deck_park_started = now - self.deck_park_duration
                self.animationStatus.emit("选择完成 · 点击任意已选卡牌翻开")

        elif self.state in ("await_reveal", "done"):
            # Keep repainting while the remaining deck is gliding into its parked
            # tabletop pose, and while any number of independent flips are active.
            if self.deck_park_started > 0 and now - self.deck_park_started < self.deck_park_duration:
                animation_active = True

            finished = []
            for idx, started in list(self.flip_started_by_slot.items()):
                animation_active = True
                if (now - started) / self.flip_duration >= 1.0:
                    finished.append(idx)
            for idx in finished:
                self.revealed[idx] = True
                self.flip_started_by_slot.pop(idx, None)

            if finished:
                if all(self.revealed):
                    self.state = "done"
                    self.animationStatus.emit("全部卡牌已翻开")
                    self._emit_finished_once()
                else:
                    left = sum(1 for v in self.revealed if not v)
                    active = len(self.flip_started_by_slot)
                    suffix = f" · {active} 张正在翻转" if active else ""
                    self.animationStatus.emit(f"已翻开 · 还有 {left} 张{suffix}")

        hx = self.hover_current.x()
        hy = self.hover_current.y()
        tx = self.hover_target.x() if self.hovered_slot >= 0 else 0.0
        ty = self.hover_target.y() if self.hovered_slot >= 0 else 0.0
        nx = hx + (tx - hx) * 0.18
        ny = hy + (ty - hy) * 0.18
        if abs(nx - tx) < 0.002:
            nx = tx
        if abs(ny - ty) < 0.002:
            ny = ty
        hover_active = abs(nx - hx) > 0.0005 or abs(ny - hy) > 0.0005
        self.hover_current = QPointF(nx, ny)

        self.update()
        if not animation_active and not hover_active and not self.flip_started_by_slot:
            self._timer.stop()

    def _emit_finished_once(self):
        if self._finished_emitted:
            return
        self._finished_emitted = True
        names = [self._display_name(i) for i in self.selected_indices]
        self.animationFinished.emit(names)

    def _display_name(self, idx):
        stem = self.card_files[idx].stem
        if "_" in stem and stem[:2].isdigit():
            stem = stem[3:]
        return stem.replace("_", " ")

    def _deck_rect(self):
        cw = min(128.0, max(92.0, self.width() * .105))
        ch = cw * 1.58
        return QRectF(self.width() * .5 - cw/2, self.height() * .58 - ch/2, cw, ch)

    def _pile_rect(self, side):
        deck = self._deck_rect()
        shift = min(145.0, max(88.0, self.width() * .12))
        x = deck.left() - shift if side == "L" else deck.left() + shift
        return QRectF(x, deck.top(), deck.width(), deck.height())

    def _result_slots(self):
        n = max(1, self.draw_count)
        margin = 30
        max_w = 122.0
        gap = 16.0
        available = max(300.0, self.width() - margin*2)
        cw = min(max_w, (available - gap*(n-1)) / n)
        cw = max(66.0, cw)
        ch = cw * 1.58
        total = n*cw + (n-1)*gap
        x0 = (self.width() - total)/2
        y = max(34.0, self.height() * .14)
        return [QRectF(x0 + i*(cw+gap), y, cw, ch) for i in range(n)]

    def _idle_geometry(self, i, count=None):
        n = max(1, count if count is not None else len(self.active_order))
        t = i/(n-1) if n > 1 else .5
        spread = min(self.width()*.40, 390)
        a = math.radians(-42 + 84*t)
        cx, cy = self.width()*.5, self.height()*.58
        x = cx + math.sin(a)*spread
        y = cy - math.cos(a)*44 + abs(t-.5)*30
        cw = max(34.0, min(56.0, self.width()*.045))
        return x, y, cw, cw*1.58, math.degrees(a)*.68

    def _stack_pose(self, base_rect, rank, count, rotation=0.0, spread=0.10):
        # Tiny offsets make the thickness/order of the pile visible without faking
        # independent sprites. Higher ranks are physically higher in the stack.
        if count <= 1:
            depth = 0.0
        else:
            depth = rank / (count - 1)
        dx = (depth - .5) * base_rect.width() * spread
        dy = (depth - .5) * 7.0
        return base_rect.translated(dx, dy), rotation

    def _parked_deck_rect(self):
        # Lower-left position, visually resting on the ellipse/tabletop.
        cw = min(132.0, max(96.0, self.width() * .105))
        ch = cw * 1.58
        x = max(28.0, self.width() * .085)
        y = self.height() * .705
        return QRectF(x, y, cw, ch)

    def _park_progress(self, now=None):
        if self.deck_park_started <= 0:
            return 0.0
        now = time.perf_counter() if now is None else now
        return self._ease_in_out(min(1.0, max(0.0, (now - self.deck_park_started) / self.deck_park_duration)))

    def _remaining_deck_order(self):
        selected = set(self.selected_indices)
        return [cid for cid in self.active_order if cid not in selected]

    def _paint_parked_deck(self, p, now=None, progress=None):
        remaining = self._remaining_deck_order()
        if not remaining:
            return
        if progress is None:
            progress = self._park_progress(now)
        source = self._deck_rect()
        target = self._parked_deck_rect()
        e = max(0.0, min(1.0, progress))
        r = QRectF(
            self._lerp(source.left(), target.left(), e),
            self._lerp(source.top(), target.top(), e) - math.sin(e * math.pi) * 18.0,
            self._lerp(source.width(), target.width(), e),
            self._lerp(source.height(), target.height(), e),
        )

        # Transition from upright card geometry into a flattened tabletop pose.
        c = r.center()
        p.save()
        p.translate(c)
        p.rotate(self._lerp(0.0, -13.0, e))
        p.shear(self._lerp(0.0, -0.20, e), 0.0)
        p.scale(1.0, self._lerp(1.0, 0.42, e))
        p.translate(-c)

        # Paint enough physical layers to make the pile thickness readable. The
        # cards themselves remain individually bound objects; only hidden interior
        # layers are skipped for rendering efficiency.
        n = len(remaining)
        visible = remaining if n <= 26 else remaining[-26:]
        base_rank = max(0, n - len(visible))
        for j, cid in enumerate(visible):
            rank = base_rank + j
            depth = rank / max(1, n - 1)
            rr = r.translated((depth - .5) * 5.5, (depth - .5) * 14.0)
            rot = (j % 5 - 2) * .16
            self._paint_physical_back(p, cid, rr, rot, 255, False)
        p.restore()

    def _paint_back(self, p, rect, rotation=0, alpha=255, label=True, card_id=None):
        p.save()
        p.setOpacity(alpha/255)
        c = rect.center()
        p.translate(c)
        p.rotate(rotation)
        r = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())

        pm = QPixmap()
        if card_id is not None and 0 <= card_id < len(self.cards):
            pm = self.cards[card_id].back
        if not pm.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawPixmap(r.toRect(), pm)
            p.setPen(QPen(QColor("#4c4c4c"), max(1.0, rect.width()*.012)))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, 5, 5)
        else:
            p.setPen(QPen(QColor("#555555"), max(1.0, rect.width()*.015)))
            p.setBrush(QColor("#0c0c0c"))
            p.drawRoundedRect(r, 5, 5)
            inner = r.adjusted(5, 5, -5, -5)
            p.setPen(QPen(QColor("#262626"), 1))
            p.drawRoundedRect(inner, 4, 4)
            if label and rect.width() > 34:
                p.setPen(QColor("#888888"))
                f = QFont("Segoe UI", max(7, int(rect.width()*.14)))
                f.setWeight(QFont.DemiBold)
                p.setFont(f)
                p.drawText(r, Qt.AlignCenter, "515")
        p.restore()

    def _paint_front(self, p, rect, card_idx, reversed_card=False, alpha=255):
        if card_idx < 0 or card_idx >= len(self.cards):
            return
        pm = self.cards[card_idx].face
        p.save()
        p.setOpacity(alpha/255)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if reversed_card:
            c = rect.center()
            p.translate(c)
            p.rotate(180)
            target = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
        else:
            target = rect
        p.drawPixmap(target.toRect(), pm)
        p.setPen(QPen(QColor("#6a6a6a"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(target, 4, 4)
        p.restore()

    def _paint_physical_back(self, p, cid, rect, rotation=0.0, alpha=255, label=False):
        self._paint_back(p, rect, rotation, alpha, label, card_id=cid)

    def _apply_hover_transform(self, p, rect, slot_index):
        if slot_index != self.hovered_slot or slot_index in self.flip_started_by_slot:
            return
        dx = self.hover_current.x()
        dy = self.hover_current.y()
        c = rect.center()
        p.translate(c)
        p.scale(1.035, 1.035)
        p.rotate(dx * 2.8)
        p.shear(dx * 0.055, -dy * 0.045)
        p.translate(-c)

    def _paint_slot_card(self, p, slot, slot_index, face_up):
        p.save()
        self._apply_hover_transform(p, slot, slot_index)
        if slot_index == self.hovered_slot and slot_index not in self.flip_started_by_slot:
            shadow = slot.translated(self.hover_current.x()*4 + 4, self.hover_current.y()*4 + 7)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 105))
            p.drawRoundedRect(shadow, 6, 6)
        cid = self.selected_indices[slot_index]
        if face_up:
            self._paint_front(p, slot, cid, self.cards[cid].reversed)
        else:
            self._paint_physical_back(p, cid, slot, 0, 255, True)
        p.restore()

    def _paint_flip(self, p, rect, card_idx, reversed_card, phase):
        phase = max(0.0, min(1.0, phase))
        eased = self._ease_in_out(phase)
        if eased <= .5:
            q = eased / .5
            w = max(1.0, rect.width() * (1.0 - q))
            r = QRectF(rect.left(), rect.top(), w, rect.height())
            self._paint_physical_back(p, card_idx, r, 0, 255, False)
        else:
            q = (eased - .5) / .5
            w = max(1.0, rect.width() * q)
            r = QRectF(rect.left(), rect.top(), w, rect.height())
            self._paint_front(p, r, card_idx, reversed_card)
        p.setPen(QPen(QColor(190, 190, 190, 115), 1))
        p.drawLine(QPointF(rect.left(), rect.top()+2), QPointF(rect.left(), rect.bottom()-2))

    def _deal_geometry(self, index, local_progress, slot):
        deck = self._deck_rect()
        q = self._ease_in_out(max(0.0, min(1.0, local_progress)))
        x = self._lerp(deck.left(), slot.left(), q)
        y = self._lerp(deck.top(), slot.top(), q) - math.sin(q*math.pi)*34.0
        w = self._lerp(deck.width(), slot.width(), q)
        h = self._lerp(deck.height(), slot.height(), q)
        rot = self._lerp(-4.0, 0.0, q)
        return QRectF(x, y, w, h), rot

    def _riffle_pose(self, cid, elapsed):
        side = self.release_side[cid]
        src_pile = self.left_pile if side == "L" else self.right_pile
        src_rank = self.release_source_rank[cid]
        src_rect, src_rot = self._stack_pose(self._pile_rect(side), src_rank, len(src_pile), -6.0 if side == "L" else 6.0, .08)

        target_rank = self.release_target_rank[cid]
        target_rect, target_rot = self._stack_pose(self._deck_rect(), target_rank, len(self.round_output), 0.0, .06)
        target_rot += ((target_rank % 7) - 3) * 0.18

        start = self.release_index[cid] * self.release_gap
        q = (elapsed - start) / self.release_move_duration
        if q <= 0:
            return src_rect, src_rot, 0
        if q >= 1:
            return target_rect, target_rot, 2

        e = self._ease_in_out(q)
        x = self._lerp(src_rect.left(), target_rect.left(), e)
        y = self._lerp(src_rect.top(), target_rect.top(), e) - math.sin(e * math.pi) * 20.0
        w = self._lerp(src_rect.width(), target_rect.width(), e)
        h = self._lerp(src_rect.height(), target_rect.height(), e)
        rot = self._lerp(src_rot, target_rot, e)
        return QRectF(x, y, w, h), rot, 1

    def mouseMoveEvent(self, event):
        pos = event.position()
        slots = self._result_slots() if self.state in ("await_reveal", "done") else []
        hit = -1
        if self.state == "self_select":
            now = time.perf_counter()
            if self._last_mouse_pos is not None and self._last_mouse_time > 0:
                dt = max(.001, now - self._last_mouse_time)
                dxm = pos.x() - self._last_mouse_pos.x()
                dym = pos.y() - self._last_mouse_pos.y()
                speed = math.hypot(dxm, dym) / dt
                self._repel_scatter_from_cursor(pos, speed)
            self._last_mouse_pos = QPointF(pos)
            self._last_mouse_time = now

            cid = self._manual_card_hit(pos)
            self.setCursor(Qt.PointingHandCursor if cid >= 0 and len(self.selected_indices) < self.draw_count else Qt.ArrowCursor)
            self.hovered_slot = -1
            self.hover_target = QPointF(0.0, 0.0)
            self._ensure_timer()
            self.update()
            super().mouseMoveEvent(event)
            return
        for i, r in enumerate(slots):
            if r.adjusted(-5, -5, 5, 5).contains(pos):
                hit = i
                break
        if hit != self.hovered_slot:
            self.hovered_slot = hit
            self.hover_current = QPointF(0.0, 0.0)
        if hit >= 0:
            r = slots[hit]
            dx = (pos.x() - r.center().x()) / max(1.0, r.width()/2)
            dy = (pos.y() - r.center().y()) / max(1.0, r.height()/2)
            self.hover_target = QPointF(max(-1.0, min(1.0, dx)), max(-1.0, min(1.0, dy)))
            self.setCursor(Qt.PointingHandCursor)
        else:
            self.hover_target = QPointF(0.0, 0.0)
            self.setCursor(Qt.ArrowCursor)
        self._ensure_timer()
        self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.setCursor(Qt.ArrowCursor)
        self._ensure_timer()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.state == "self_select":
            if len(self.selected_indices) < self.draw_count:
                cid = self._manual_card_hit(event.position())
                if cid >= 0 and cid not in self.selected_indices:
                    slot = len(self.selected_indices)
                    self.selected_indices.append(cid)
                    self.selected_reversed.append(self.cards[cid].reversed)
                    self.revealed.append(False)
                    self.manual_take_started[slot] = time.perf_counter()
                    self.animationStatus.emit(f"已选择 {slot + 1} / {self.draw_count} 张")
                    self._ensure_timer()
                    self.update()
            super().mousePressEvent(event)
            return

        if event.button() == Qt.LeftButton and self.state in ("await_reveal", "done"):
            pos = event.position()
            for i, r in enumerate(self._result_slots()):
                if r.adjusted(-5, -5, 5, 5).contains(pos):
                    # Do not serialize flips. Rapid clicks can start several cards,
                    # each with an independent timeline and repaint animation.
                    if not self.revealed[i] and i not in self.flip_started_by_slot:
                        self.flip_started_by_slot[i] = time.perf_counter()
                        if self.state == "done":
                            self.state = "await_reveal"
                        active = len(self.flip_started_by_slot)
                        self.animationStatus.emit(f"翻开第 {i + 1} 张牌 · {active} 张正在翻转")
                        self._ensure_timer()
                        self.update()
                    break
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#070707"))
        p.setPen(QPen(QColor(38, 38, 38, 150), 1))
        floor = QRectF(self.width()*.12, self.height()*.67, self.width()*.76, self.height()*.20)
        p.drawEllipse(floor)

        now = time.perf_counter()
        elapsed = now - self.state_started

        if self.state == "idle":
            order = self.deck_order if self.deck_order else list(range(len(self.cards)))
            for i, cid in enumerate(order):
                x, y, cw, ch, rot = self._idle_geometry(i, len(order))
                self._paint_physical_back(p, cid, QRectF(x-cw/2, y-ch/2, cw, ch), rot, 235, False)
            p.setPen(QColor("#757575"))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRectF(0, self.height()-36, self.width(), 24), Qt.AlignCenter,
                       "先洗牌，再按“抽取”；也可以直接从当前牌序抽取")
            return

        if self.state == "shuffled":
            order = self.active_order if self.active_order else self.deck_order
            deck = self._deck_rect()
            for i, cid in enumerate(order):
                r, rot = self._stack_pose(deck, i, len(order), 0.0, .035)
                self._paint_physical_back(p, cid, r, rot, 255, False)
            p.setPen(QColor("#777777"))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRectF(0, self.height()-36, self.width(), 24), Qt.AlignCenter,
                       "洗牌完成 · 可再次洗牌、自己选择，或按“抽取”")
            return

        if self.state == "gather":
            q = self._ease_in_out(min(1.0, elapsed / self.gather_duration))
            order = self.active_order
            deck = self._deck_rect()
            for i, cid in enumerate(order):
                x, y, cw, ch, rot = self._idle_geometry(i, len(order))
                start = QRectF(x-cw/2, y-ch/2, cw, ch)
                target, _ = self._stack_pose(deck, i, len(order), 0.0, .06)
                r = QRectF(
                    self._lerp(start.left(), target.left(), q),
                    self._lerp(start.top(), target.top(), q),
                    self._lerp(start.width(), target.width(), q),
                    self._lerp(start.height(), target.height(), q),
                )
                rr = self._lerp(rot, 0.0, q)
                self._paint_physical_back(p, cid, r, rr, 255, False)
            self._paint_progress(p, q, "GATHER PHYSICAL DECK")
            return

        if self.state == "split":
            q = self._ease_in_out(min(1.0, elapsed / self.split_duration))
            deck = self._deck_rect()
            left_set = set(self.left_pile)
            for cid in self.round_input:
                if cid in left_set:
                    side = "L"; pile = self.left_pile; rank = pile.index(cid); rot_t = -6.0
                else:
                    side = "R"; pile = self.right_pile; rank = pile.index(cid); rot_t = 6.0
                source_rank = self.round_input.index(cid)
                source, _ = self._stack_pose(deck, source_rank, len(self.round_input), 0.0, .06)
                target, _ = self._stack_pose(self._pile_rect(side), rank, len(pile), rot_t, .08)
                r = QRectF(
                    self._lerp(source.left(), target.left(), q),
                    self._lerp(source.top(), target.top(), q) - math.sin(q*math.pi)*4.0,
                    self._lerp(source.width(), target.width(), q),
                    self._lerp(source.height(), target.height(), q),
                )
                rot = self._lerp(0.0, rot_t, q)
                self._paint_physical_back(p, cid, r, rot, 255, False)
            self._paint_progress(p, q, f"CUT · {self.shuffle_round + 1}/{self.shuffle_rounds}")
            return

        if self.state == "riffle":
            poses = {cid: self._riffle_pose(cid, elapsed) for cid in self.round_output}
            # Source piles first, in their own physical layer order.
            for pile in (self.left_pile, self.right_pile):
                for cid in pile:
                    r, rot, phase = poses[cid]
                    if phase == 0:
                        self._paint_physical_back(p, cid, r, rot, 255, False)
            # Moving cards next.
            for cid in self.round_output:
                r, rot, phase = poses[cid]
                if phase == 1:
                    self._paint_physical_back(p, cid, r, rot, 255, False)
            # Landed cards are painted in release order, so later drops visibly
            # interleave on top of earlier drops.
            for cid in self.round_output:
                r, rot, phase = poses[cid]
                if phase == 2:
                    self._paint_physical_back(p, cid, r, rot, 255, False)
            prog = min(1.0, elapsed / max(.001, self._riffle_total_duration()))
            self._paint_progress(p, prog, f"RIFFLE · {self.shuffle_round + 1}/{self.shuffle_rounds}")
            return

        if self.state == "square":
            q = self._ease_in_out(min(1.0, elapsed / self.square_duration))
            deck = self._deck_rect()
            # Start from the exact landed riffle positions, then remove all jitter.
            landed_time = self._riffle_total_duration() + .001
            for i, cid in enumerate(self.round_output):
                start, start_rot, _ = self._riffle_pose(cid, landed_time)
                target, _ = self._stack_pose(deck, i, len(self.round_output), 0.0, .035)
                r = QRectF(
                    self._lerp(start.left(), target.left(), q),
                    self._lerp(start.top(), target.top(), q),
                    self._lerp(start.width(), target.width(), q),
                    self._lerp(start.height(), target.height(), q),
                )
                rot = self._lerp(start_rot, 0.0, q)
                self._paint_physical_back(p, cid, r, rot, 255, False)
            self._paint_progress(p, q, f"SQUARE · {self.shuffle_round + 1}/{self.shuffle_rounds}")
            return

        if self.state in ("self_drop", "self_select", "manual_gather_rest"):
            slots = self._result_slots()
            selected = set(self.selected_indices)

            if self.state == "self_drop":
                for cid in self.scatter_order:
                    r, rot, q = self._scatter_card_pose(cid, elapsed)
                    if q > 0:
                        self._paint_physical_back(p, cid, r, rot, 255, False)
                self._paint_progress(p, min(1.0, elapsed / max(.001, self.scatter_drop_total)), "SCATTER 78 CARDS")
                return

            if self.state == "self_select":
                # Unpicked cards stay where they physically landed.
                for cid in self.scatter_order:
                    if cid not in selected:
                        d = self.scatter_pose[cid]
                        self._paint_physical_back(p, cid, self._effective_scatter_rect(cid), d["rot"], 250, False)

                # Picked cards travel independently to their result slots, allowing
                # rapid selection without serializing the pickup animations.
                now2 = time.perf_counter()
                for slot, cid in enumerate(self.selected_indices):
                    d = self.scatter_pose[cid]
                    if slot in self.manual_take_started:
                        q = min(1.0, max(0.0, (now2 - self.manual_take_started[slot]) / self.manual_take_duration))
                        e = self._ease_in_out(q)
                        src = self._effective_scatter_rect(cid); dst = slots[slot]
                        r = QRectF(
                            self._lerp(src.left(), dst.left(), e),
                            self._lerp(src.top(), dst.top(), e) - math.sin(e * math.pi) * 46.0,
                            self._lerp(src.width(), dst.width(), e),
                            self._lerp(src.height(), dst.height(), e),
                        )
                        rot = self._lerp(d["rot"], 0.0, e)
                        self._paint_physical_back(p, cid, r, rot, 255, True)
                    else:
                        self._paint_physical_back(p, cid, slots[slot], 0.0, 255, True)

                p.setPen(QColor("#808080"))
                p.setFont(QFont("Segoe UI", 10))
                p.drawText(QRectF(0, self.height()-30, self.width(), 22), Qt.AlignCenter,
                           f"从散落的牌中选择 · {len(self.selected_indices)} / {self.draw_count}")
                return

            # After the final choice, remaining cards physically gather from their
            # scattered poses into the same lower-left flattened deck used elsewhere.
            q = self._ease_in_out(min(1.0, elapsed / self.manual_rest_gather_duration))
            remaining = [cid for cid in self.scatter_order if cid not in selected]
            target = self._parked_deck_rect()
            for rank, cid in enumerate(remaining):
                d = self.scatter_pose[cid]
                depth = rank / max(1, len(remaining)-1)
                dst = target.translated((depth-.5)*5.5, (depth-.5)*14.0)
                src = self._effective_scatter_rect(cid)
                r = QRectF(
                    self._lerp(src.left(), dst.left(), q),
                    self._lerp(src.top(), dst.top(), q) - math.sin(q*math.pi)*20.0,
                    self._lerp(src.width(), dst.width(), q),
                    self._lerp(src.height(), dst.height(), q),
                )
                rot = self._lerp(d["rot"], -13.0 + (rank%5-2)*.16, q)
                # Approximate the tabletop flattening during convergence.
                c = r.center(); p.save(); p.translate(c); p.shear(-.20*q, 0.0); p.scale(1.0, 1.0-.58*q); p.translate(-c)
                self._paint_physical_back(p, cid, r, rot, 255, False)
                p.restore()
            for slot, cid in enumerate(self.selected_indices):
                self._paint_physical_back(p, cid, slots[slot], 0.0, 255, True)
            self._paint_progress(p, q, "GATHER REMAINING CARDS")
            return

        deck = self._deck_rect()
        # Paint the real shuffled deck, excluding cards that have already left it.
        if self.state == "deal":
            each = self.deal_duration + self.deal_gap
            completed = min(self.draw_count, int(elapsed // each))
            moving = completed if completed < self.draw_count else -1
            leaving = set(self.selected_indices[:completed + (1 if moving >= 0 else 0)])
            remaining_order = [cid for cid in self.active_order if cid not in leaving]
            for rank, cid in enumerate(remaining_order):
                r, rot = self._stack_pose(deck, rank, len(remaining_order), 0.0, .035)
                self._paint_physical_back(p, cid, r, rot, 255, False)

            slots = self._result_slots()
            for i in range(completed):
                self._paint_slot_card(p, slots[i], i, False)
            if completed < self.draw_count:
                local_elapsed = elapsed - completed*each
                local = max(0.0, min(1.0, local_elapsed/self.deal_duration))
                r, rot = self._deal_geometry(completed, local, slots[completed])
                cid = self.selected_indices[completed]
                self._paint_physical_back(p, cid, r, rot, 255, True)
            return

        # The remaining deck persists after the draw. It moves to the lower-left
        # and stays flattened on the tabletop while the selected cards are handled.
        self._paint_parked_deck(p, now)

        slots = self._result_slots()
        for i, slot in enumerate(slots):
            if i in self.flip_started_by_slot:
                q = min(1.0, max(0.0, (now - self.flip_started_by_slot[i]) / self.flip_duration))
                cid = self.selected_indices[i]
                self._paint_flip(p, slot, cid, self.cards[cid].reversed, q)
            else:
                self._paint_slot_card(p, slot, i, self.revealed[i])

        p.setPen(QColor("#777777"))
        p.setFont(QFont("Segoe UI", 10))
        message = "全部卡牌已翻开" if self.state == "done" else "点击卡牌翻开 · 移动鼠标查看 3D 视差"
        p.drawText(QRectF(0, self.height()-30, self.width(), 22), Qt.AlignCenter, message)

    def _paint_progress(self, p, progress, label):
        w = min(360.0, self.width()*.42)
        x = (self.width()-w)/2
        y = self.height()-32
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1d1d1d"))
        p.drawRoundedRect(QRectF(x, y, w, 2), 1, 1)
        p.setBrush(QColor("#9b9b9b"))
        p.drawRoundedRect(QRectF(x, y, w*progress, 2), 1, 1)
        p.setPen(QColor("#606060"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(x, y-18, w, 14), Qt.AlignCenter, label)


class MethodCard(QFrame):
    clicked = Signal(str)

    def __init__(self, title, subtitle, glyph, enabled=True, parent=None):
        super().__init__(parent)
        self.title = title
        self.enabled_method = enabled
        self.setObjectName("methodCard")
        self.setCursor(Qt.PointingHandCursor if enabled else Qt.ArrowCursor)
        self.setFixedHeight(78)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 11, 16, 11)
        row.setSpacing(14)

        icon = QLabel(glyph)
        icon.setObjectName("methodIcon")
        icon.setAlignment(Qt.AlignCenter)
        icon.setFixedSize(38, 38)
        row.addWidget(icon)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("methodTitle")
        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("methodSubtitle")
        text_col.addWidget(title_lbl)
        text_col.addWidget(sub_lbl)
        row.addLayout(text_col, 1)

        arrow = QLabel("›" if enabled else "—")
        arrow.setObjectName("methodArrow")
        row.addWidget(arrow)

        if not enabled:
            self.setProperty("disabledMethod", True)

    def mousePressEvent(self, event):
        if self.enabled_method and event.button() == Qt.LeftButton:
            self.clicked.emit(self.title)
        super().mousePressEvent(event)


class HomePage(QWidget):
    startTarot = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 38, 48, 34)
        outer.setSpacing(18)

        kicker = QLabel("5IMP1E 5ATEBOX / 515")
        kicker.setObjectName("kicker")
        outer.addWidget(kicker)

        title = QLabel("选择一种方式，看看随机性会给你什么。")
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        outer.addWidget(title)

        subtitle = QLabel("一个简洁的离线占卜工具。当前版本先完成塔罗牌界面，其他方式将逐步加入。")
        subtitle.setObjectName("heroSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)
        outer.addSpacing(8)

        grid = QHBoxLayout()
        grid.setSpacing(12)
        tarot = MethodCard("塔罗牌", "78 张牌 · 正位 / 逆位", "◇", True)
        dice = MethodCard("骰子", "开发中", "□", False)
        rune = MethodCard("符文", "开发中", "△", False)
        coin = MethodCard("硬币", "开发中", "○", False)
        tarot.clicked.connect(lambda _: self.startTarot.emit())
        for card in (tarot, dice, rune, coin):
            grid.addWidget(card, 1)
        outer.addLayout(grid)
        outer.addStretch(1)

        quote = QLabel("SIMPLE TO ASK  /  MORE TO DISCOVER")
        quote.setObjectName("bottomMotto")
        outer.addWidget(quote)


class TarotPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_count = 3

        outer = QVBoxLayout(self)
        outer.setContentsMargins(46, 30, 46, 24)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head_text = QVBoxLayout()
        head_text.setSpacing(4)
        eyebrow = QLabel("TAROT / PHYSICAL DECK")
        eyebrow.setObjectName("kicker")
        title = QLabel("塔罗牌抽取")
        title.setObjectName("pageTitle")
        desc = QLabel("洗牌与抽取现在彼此独立：先真实交错洗牌，再决定何时从牌顶抽取；也可以让 78 张牌散落后自行挑选。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        head_text.addWidget(eyebrow)
        head_text.addWidget(title)
        head_text.addWidget(desc)
        head.addLayout(head_text, 1)

        action_col = QHBoxLayout()
        action_col.setSpacing(8)
        self.manual_button = QPushButton("自己选择")
        self.manual_button.setObjectName("chipButton")
        self.manual_button.setCursor(Qt.PointingHandCursor)
        self.manual_button.setFixedSize(112, 44)
        action_col.addWidget(self.manual_button)

        self.shuffle_button = QPushButton("洗牌")
        self.shuffle_button.setObjectName("chipButton")
        self.shuffle_button.setCursor(Qt.PointingHandCursor)
        self.shuffle_button.setFixedSize(92, 44)
        action_col.addWidget(self.shuffle_button)

        self.draw_button = QPushButton("抽取")
        self.draw_button.setObjectName("primaryButton")
        self.draw_button.setCursor(Qt.PointingHandCursor)
        self.draw_button.setFixedSize(96, 44)
        action_col.addWidget(self.draw_button)
        head.addLayout(action_col)
        outer.addLayout(head)

        config = QFrame()
        config.setObjectName("configPanel")
        cfg = QHBoxLayout(config)
        cfg.setContentsMargins(18, 12, 18, 12)
        cfg.setSpacing(12)

        label_col = QVBoxLayout()
        label_col.setSpacing(1)
        label = QLabel("抽牌数量")
        label.setObjectName("settingTitle")
        note = QLabel("1–8 张")
        note.setObjectName("settingNote")
        label_col.addWidget(label)
        label_col.addWidget(note)
        cfg.addLayout(label_col)
        cfg.addSpacing(8)

        self.count_group = QButtonGroup(self)
        self.count_group.setExclusive(True)
        for n in range(1, 9):
            b = CountButton(n)
            b.setChecked(n == self.selected_count)
            self.count_group.addButton(b, n)
            cfg.addWidget(b)
        self.count_group.idClicked.connect(self._set_count)

        cfg.addStretch(1)
        self.reverse_chip = MiniSwitch("允许逆位", True)
        self.major_chip = MiniSwitch("仅大阿卡纳", False)
        cfg.addWidget(self.reverse_chip)
        cfg.addWidget(self.major_chip)
        outer.addWidget(config)

        self.stage = TarotStage()
        outer.addWidget(self.stage, 1)

        status_row = QHBoxLayout()
        self.status = QLabel("准备就绪 · 78 张牌已载入")
        self.status.setObjectName("statusText")
        status_row.addWidget(self.status)
        status_row.addStretch(1)
        self.card_count_label = QLabel(f"RESOURCE / {len(self.stage.card_files)} CARDS")
        self.card_count_label.setObjectName("muted")
        status_row.addWidget(self.card_count_label)
        outer.addLayout(status_row)

        self.shuffle_button.clicked.connect(self._shuffle_clicked)
        self.draw_button.clicked.connect(self._draw_clicked)
        self.manual_button.clicked.connect(self._manual_clicked)
        self.stage.animationStatus.connect(self.status.setText)
        self.stage.shuffleFinished.connect(self._shuffle_finished)
        self.stage.animationFinished.connect(self._finished)

    def _set_count(self, n):
        self.selected_count = n

    def _set_controls_enabled(self, enabled):
        self.shuffle_button.setEnabled(enabled)
        self.draw_button.setEnabled(enabled)
        self.manual_button.setEnabled(enabled)
        for b in self.count_group.buttons():
            b.setEnabled(enabled)
        self.reverse_chip.setEnabled(enabled)
        self.major_chip.setEnabled(enabled)

    def _shuffle_clicked(self):
        if self.stage.state not in ("idle", "done", "shuffled"):
            return
        self._set_controls_enabled(False)
        self.stage.start_shuffle(
            allow_reversed=self.reverse_chip.isChecked(),
            major_only=self.major_chip.isChecked(),
        )

    def _shuffle_finished(self):
        self._set_controls_enabled(True)
        self.status.setText("洗牌完成 · 可以调整抽牌数量后按“抽取”")

    def _draw_clicked(self):
        if self.stage.state not in ("idle", "done", "shuffled"):
            return
        self._set_controls_enabled(False)
        self.stage.draw_from_deck(
            count=self.selected_count,
            major_only=self.major_chip.isChecked(),
        )

    def _manual_clicked(self):
        if self.stage.state not in ("idle", "done", "shuffled"):
            return
        previous_state = self.stage.state
        self._set_controls_enabled(False)
        self.stage.start_manual(
            count=self.selected_count,
            allow_reversed=self.reverse_chip.isChecked(),
        )
        # Defensive recovery: if manual mode could not start for any reason,
        # never leave the whole control strip disabled.
        if self.stage.state == previous_state:
            self._set_controls_enabled(True)
            self.status.setText("自己选择未能启动，请重试。")

    def _finished(self, names):
        self._set_controls_enabled(True)
        short = " · ".join(names[:4])
        if len(names) > 4:
            short += f" · +{len(names)-4}"
        self.status.setText(f"抽取完成 · {short}")


class PlaceholderPage(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 38, 48, 38)
        kicker = QLabel("5IMP1E 5ATEBOX")
        kicker.setObjectName("kicker")
        title_lbl = QLabel(title)
        title_lbl.setObjectName("pageTitle")
        desc = QLabel("此模块尚未启用。当前开发重点为塔罗牌界面与伪 3D 交互。")
        desc.setObjectName("pageDesc")
        layout.addWidget(kicker)
        layout.addWidget(title_lbl)
        layout.addWidget(desc)
        layout.addStretch(1)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  ·  {APP_VERSION}")
        self.resize(1440, 900)
        self.setMinimumSize(1180, 760)
        self._build_ui()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        main = QHBoxLayout(root)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # navigation rail
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(240)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(18, 22, 18, 18)
        sl.setSpacing(8)

        brand = QLabel("5imp1e 5atebox")
        brand.setObjectName("brand")
        brand_code = QLabel("5 1 5")
        brand_code.setObjectName("brandCode")
        sl.addWidget(brand)
        sl.addWidget(brand_code)
        sl.addSpacing(18)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        nav_specs = [
            ("首页", "⌂"),
            ("塔罗牌", "◇"),
            ("骰子", "□"),
            ("符文", "△"),
            ("硬币", "○"),
            ("抽签", "│"),
        ]
        self.nav_buttons = []
        for idx, (text, glyph) in enumerate(nav_specs):
            b = NavButton(text, glyph)
            b.setChecked(idx == 0)
            self.nav_group.addButton(b, idx)
            self.nav_buttons.append(b)
            sl.addWidget(b)

        sl.addSpacing(10)
        sl.addWidget(AccentLine())
        sl.addSpacing(10)

        settings = NavButton("设置", "⚙")
        about = NavButton("关于", "·")
        self.nav_group.addButton(settings, 6)
        self.nav_group.addButton(about, 7)
        self.nav_buttons.extend([settings, about])
        sl.addWidget(settings)
        sl.addWidget(about)
        sl.addStretch(1)

        small = QLabel("OFFLINE DESKTOP BUILD\nPYTHON / PYSIDE6")
        small.setObjectName("sideMeta")
        sl.addWidget(small)

        # content shell with top bar
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(64)
        tl = QHBoxLayout(topbar)
        tl.setContentsMargins(24, 0, 24, 0)
        tl.setSpacing(10)
        self.section_title = QLabel("首页")
        self.section_title.setObjectName("sectionTitle")
        tl.addWidget(self.section_title)
        tl.addStretch(1)
        dot = QLabel("●")
        dot.setObjectName("readyDot")
        ready = QLabel("LOCAL")
        ready.setObjectName("topMeta")
        version = QLabel(f"v{APP_VERSION}")
        version.setObjectName("topMeta")
        tl.addWidget(dot)
        tl.addWidget(ready)
        tl.addSpacing(8)
        tl.addWidget(version)
        content_layout.addWidget(topbar)

        self.stack = QStackedWidget()
        self.stack.setObjectName("stack")
        self.home = HomePage()
        self.tarot = TarotPage()
        self.home.startTarot.connect(lambda: self._navigate(1))
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.tarot)
        for title in ["骰子", "符文", "硬币", "抽签", "设置", "关于"]:
            self.stack.addWidget(PlaceholderPage(title))
        content_layout.addWidget(self.stack, 1)

        main.addWidget(side)
        main.addWidget(content, 1)

        self.nav_group.idClicked.connect(self._navigate)
        self.setStyleSheet(STYLE)

    def _navigate(self, idx):
        self.stack.setCurrentIndex(idx)
        names = ["首页", "塔罗牌", "骰子", "符文", "硬币", "抽签", "设置", "关于"]
        self.section_title.setText(names[idx])
        btn = self.nav_group.button(idx)
        if btn:
            btn.setChecked(True)


STYLE = r"""
* { outline: none; }
QMainWindow, QWidget#root, QStackedWidget#stack, QWidget {
    background: #070707;
    color: #d8d8d8;
    font-family: "Segoe UI", "Microsoft YaHei UI", Arial, sans-serif;
    font-size: 13px;
}
QFrame#sidebar {
    background: #090909;
    border-right: 1px solid #202020;
}
QFrame#topbar {
    background: #090909;
    border-bottom: 1px solid #202020;
}
QLabel#brand {
    color: #f0f0f0;
    font-family: Georgia, "Times New Roman";
    font-size: 22px;
    letter-spacing: 1px;
}
QLabel#brandCode {
    color: #777;
    font-size: 11px;
    letter-spacing: 8px;
}
QLabel#sideMeta {
    color: #565656;
    font-size: 9px;
    letter-spacing: 2px;
    line-height: 1.6;
}
QPushButton#navButton {
    color: #909090;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    text-align: left;
    padding: 0 14px;
    font-size: 14px;
}
QPushButton#navButton:hover {
    color: #e7e7e7;
    background: #111111;
}
QPushButton#navButton:checked {
    color: #f2f2f2;
    background: #151515;
    border: 1px solid #282828;
}
QLabel#sectionTitle {
    color: #cfcfcf;
    font-size: 14px;
    font-weight: 600;
}
QLabel#topMeta {
    color: #666;
    font-size: 10px;
    letter-spacing: 1px;
}
QLabel#readyDot {
    color: #9b9b9b;
    font-size: 9px;
}
QLabel#kicker {
    color: #666;
    font-size: 10px;
    letter-spacing: 4px;
}
QLabel#heroTitle {
    color: #f0f0f0;
    font-size: 34px;
    font-weight: 600;
    padding-top: 6px;
}
QLabel#heroSubtitle, QLabel#pageDesc {
    color: #858585;
    font-size: 14px;
    line-height: 1.5;
}
QLabel#bottomMotto {
    color: #555;
    font-family: Georgia, "Times New Roman";
    font-size: 10px;
    letter-spacing: 4px;
}
QFrame#methodCard {
    background: #0d0d0d;
    border: 1px solid #252525;
    border-radius: 13px;
}
QFrame#methodCard:hover {
    background: #111111;
    border-color: #383838;
}
QFrame#methodCard[disabledMethod="true"] {
    background: #0a0a0a;
    border-color: #1d1d1d;
}
QLabel#methodIcon {
    color: #d9d9d9;
    background: #151515;
    border: 1px solid #303030;
    border-radius: 9px;
    font-size: 18px;
}
QLabel#methodTitle {
    color: #e0e0e0;
    font-size: 14px;
    font-weight: 600;
}
QLabel#methodSubtitle, QLabel#methodArrow {
    color: #686868;
    font-size: 11px;
}
QLabel#pageTitle {
    color: #f2f2f2;
    font-size: 30px;
    font-weight: 600;
}
QFrame#configPanel {
    background: #0d0d0d;
    border: 1px solid #242424;
    border-radius: 14px;
}
QLabel#settingTitle {
    color: #d6d6d6;
    font-size: 13px;
    font-weight: 600;
}
QLabel#settingNote, QLabel#muted, QLabel#statusText {
    color: #6e6e6e;
    font-size: 11px;
}
QPushButton#countButton {
    color: #8d8d8d;
    background: #111111;
    border: 1px solid #2b2b2b;
    border-radius: 11px;
    font-size: 16px;
    font-weight: 600;
}
QPushButton#countButton:hover {
    color: #ededed;
    border-color: #444;
}
QPushButton#countButton:checked {
    color: #080808;
    background: #e3e3e3;
    border-color: #e3e3e3;
}
QPushButton#chipButton {
    color: #8b8b8b;
    background: #111111;
    border: 1px solid #282828;
    border-radius: 9px;
    padding: 0 12px;
}
QPushButton#chipButton:hover { color: #ededed; border-color: #404040; }
QPushButton#chipButton:checked {
    color: #ededed;
    background: #1a1a1a;
    border-color: #4b4b4b;
}
QPushButton#primaryButton {
    color: #080808;
    background: #e8e8e8;
    border: none;
    border-radius: 10px;
    font-size: 14px;
    font-weight: 600;
}
QPushButton#primaryButton:hover { background: #ffffff; }
QPushButton#primaryButton:pressed { background: #cfcfcf; }
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
