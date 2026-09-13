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
APP_VERSION = "0.4.0"
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


class TarotStage(QWidget):
    animationFinished = Signal(list)
    animationStatus = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(470)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self.card_files = sorted((RESOURCE_DIR / "cards" / "front").glob("*.jpg"))
        self.card_pixmaps = [QPixmap(str(p)) for p in self.card_files]
        self.major_indices = [i for i, p in enumerate(self.card_files) if self._is_major(p.stem)]

        self.state = "idle"
        self.state_started = time.perf_counter()
        self.selected_indices = []
        self.selected_reversed = []
        self.revealed = []
        self.draw_count = 3

        # Motion tuning. All phase boundaries share the exact same geometry so
        # a card never disappears for one frame between animation states.
        self.shuffle_duration = 4.2
        self.collapse_duration = 0.78
        self.deal_duration = 0.58
        self.deal_gap = 0.08
        self.flip_duration = 0.52

        self.flipping_index = -1
        self.flip_started = 0.0
        self._finished_emitted = False

        # Hover / pseudo-3D state. Values are normalized to [-1, 1].
        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.hover_current = QPointF(0.0, 0.0)

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

    def start(self, count=3, allow_reversed=True, major_only=False):
        if self.state not in ("idle", "done", "await_reveal"):
            return
        if len(self.card_files) < 22:
            self.animationStatus.emit("resource/cards/front 中没有完整牌组。")
            return

        pool = self.major_indices if major_only else list(range(len(self.card_files)))
        count = max(1, min(count, len(pool)))
        self.draw_count = count
        self.selected_indices = random.sample(pool, count)
        self.selected_reversed = [bool(random.getrandbits(1)) if allow_reversed else False for _ in range(count)]
        self.revealed = [False] * count
        self.flipping_index = -1
        self._finished_emitted = False
        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.hover_current = QPointF(0.0, 0.0)

        self.state = "shuffle"
        self.state_started = time.perf_counter()
        self.animationStatus.emit("洗牌中 · 牌组正在进行两圈圆周运动")
        self._ensure_timer()
        self.update()

    def _tick(self):
        now = time.perf_counter()
        elapsed = now - self.state_started
        animation_active = False

        if self.state == "shuffle":
            animation_active = True
            if elapsed >= self.shuffle_duration:
                # Collapse starts from the exact final orbit geometry.
                self.state = "collapse"
                self.state_started = now
                self.animationStatus.emit("洗牌完成 · 正在收拢牌组")

        elif self.state == "collapse":
            animation_active = True
            if elapsed >= self.collapse_duration:
                self.state = "deal"
                self.state_started = now
                self.animationStatus.emit(f"正在抽取 1 / {self.draw_count}")

        elif self.state == "deal":
            animation_active = True
            each = self.deal_duration + self.deal_gap
            idx = int(elapsed // each)
            if idx >= self.draw_count:
                self.state = "await_reveal"
                self.state_started = now
                self.animationStatus.emit("抽取完成 · 点击任意牌翻开")
            else:
                self.animationStatus.emit(f"正在抽取 {idx + 1} / {self.draw_count}")

        elif self.state == "await_reveal" and self.flipping_index >= 0:
            animation_active = True
            q = (now - self.flip_started) / self.flip_duration
            if q >= 1.0:
                idx = self.flipping_index
                self.revealed[idx] = True
                self.flipping_index = -1
                if all(self.revealed):
                    self.state = "done"
                    self.animationStatus.emit("全部卡牌已翻开")
                    self._emit_finished_once()
                else:
                    left = sum(1 for v in self.revealed if not v)
                    self.animationStatus.emit(f"已翻开 · 还有 {left} 张")

        # Smooth spring-like approach for hover tilt, avoiding sudden jumps.
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
        if not animation_active and not hover_active and self.flipping_index < 0:
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

    def _orbit_geometry(self, i, progress):
        n = max(1, len(self.card_files))
        base = (2*math.pi*i/n) - math.pi/2
        theta = base + 4*math.pi*self._ease_in_out(progress)
        r = min(self.width(), self.height()) * .31
        cx, cy = self.width()*.5, self.height()*.54
        x = cx + math.cos(theta)*r
        y = cy + math.sin(theta)*r
        cw = max(30.0, min(48.0, self.width()*.038))
        ch = cw*1.58
        rot = math.degrees(theta) + 90
        return x, y, cw, ch, rot

    def _idle_geometry(self, i):
        n = max(1, len(self.card_files))
        t = i/(n-1) if n > 1 else .5
        spread = min(self.width()*.40, 390)
        a = math.radians(-42 + 84*t)
        cx, cy = self.width()*.5, self.height()*.58
        x = cx + math.sin(a)*spread
        y = cy - math.cos(a)*44 + abs(t-.5)*30
        cw = max(34.0, min(56.0, self.width()*.045))
        return x, y, cw, cw*1.58, math.degrees(a)*.68

    def _paint_back(self, p, rect, rotation=0, alpha=255, label=True):
        p.save()
        p.setOpacity(alpha/255)
        c = rect.center()
        p.translate(c)
        p.rotate(rotation)
        r = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
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
        if card_idx < 0 or card_idx >= len(self.card_pixmaps):
            return
        pm = self.card_pixmaps[card_idx]
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

    def _apply_hover_transform(self, p, rect, slot_index):
        if slot_index != self.hovered_slot or self.flipping_index == slot_index:
            return
        dx = self.hover_current.x()
        dy = self.hover_current.y()
        c = rect.center()
        # A centered affine/perspective illusion: small scale, shear and rotation.
        # The center remains the pivot, so the card follows the mouse naturally.
        p.translate(c)
        p.scale(1.035, 1.035)
        p.rotate(dx * 2.8)
        p.shear(dx * 0.055, -dy * 0.045)
        p.translate(-c)

    def _paint_slot_card(self, p, slot, slot_index, face_up):
        p.save()
        self._apply_hover_transform(p, slot, slot_index)

        if slot_index == self.hovered_slot and self.flipping_index != slot_index:
            shadow = slot.translated(self.hover_current.x()*4 + 4, self.hover_current.y()*4 + 7)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 105))
            p.drawRoundedRect(shadow, 6, 6)

        if face_up:
            self._paint_front(p, slot, self.selected_indices[slot_index], self.selected_reversed[slot_index])
        else:
            self._paint_back(p, slot, 0, 255, label=True)
        p.restore()

    def _paint_flip(self, p, rect, card_idx, reversed_card, phase):
        # The LEFT edge is the hinge for the full animation. No frame is blank:
        # at 90 degrees a one-pixel edge is still drawn, then the front expands.
        phase = max(0.0, min(1.0, phase))
        eased = self._ease_in_out(phase)
        if eased <= .5:
            q = eased / .5
            w = max(1.0, rect.width() * (1.0 - q))
            r = QRectF(rect.left(), rect.top(), w, rect.height())
            self._paint_back(p, r, 0, 255, label=False)
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
        # A shallow rising arc, but the card remains face-down throughout.
        x = self._lerp(deck.left(), slot.left(), q)
        y = self._lerp(deck.top(), slot.top(), q) - math.sin(q*math.pi)*34.0
        w = self._lerp(deck.width(), slot.width(), q)
        h = self._lerp(deck.height(), slot.height(), q)
        rot = self._lerp(-4.0, 0.0, q)
        return QRectF(x, y, w, h), rot

    def mouseMoveEvent(self, event):
        pos = event.position()
        slots = self._result_slots() if self.state in ("await_reveal", "done") else []
        hit = -1
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
        if event.button() == Qt.LeftButton and self.state in ("await_reveal", "done"):
            pos = event.position()
            for i, r in enumerate(self._result_slots()):
                if r.adjusted(-5, -5, 5, 5).contains(pos):
                    if not self.revealed[i] and self.flipping_index < 0:
                        self.flipping_index = i
                        self.flip_started = time.perf_counter()
                        # Keep state as await_reveal so other cards remain stable.
                        if self.state == "done":
                            self.state = "await_reveal"
                        self.animationStatus.emit(f"翻开第 {i + 1} 张牌")
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
            geos = [self._idle_geometry(i) for i in range(len(self.card_files))]
            for i, (x, y, cw, ch, rot) in enumerate(geos):
                self._paint_back(p, QRectF(x-cw/2, y-ch/2, cw, ch), rot, 235, label=False)
            p.setPen(QColor("#757575"))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRectF(0, self.height()-36, self.width(), 24), Qt.AlignCenter,
                       "设置抽牌数量，然后按“洗牌并抽取”")
            return

        if self.state == "shuffle":
            prog = min(1.0, elapsed/self.shuffle_duration)
            geos = [self._orbit_geometry(i, prog) for i in range(len(self.card_files))]
            order = sorted(range(len(geos)), key=lambda j: geos[j][1])
            for i in order:
                x, y, cw, ch, rot = geos[i]
                self._paint_back(p, QRectF(x-cw/2, y-ch/2, cw, ch), rot, 245, label=False)
            self._paint_progress(p, prog, "SHUFFLE · 2 TURNS")
            return

        deck = self._deck_rect()
        if self.state == "collapse":
            prog = self._ease_in_out(min(1.0, elapsed/self.collapse_duration))
            final_geos = [self._orbit_geometry(i, 1.0) for i in range(len(self.card_files))]
            order = sorted(range(len(final_geos)), key=lambda j: final_geos[j][1])
            for i in order:
                x, y, cw, ch, rot = final_geos[i]
                tx, ty = deck.center().x(), deck.center().y()
                nx = self._lerp(x, tx, prog)
                ny = self._lerp(y, ty, prog)
                nw = self._lerp(cw, deck.width(), prog)
                nh = self._lerp(ch, deck.height(), prog)
                nr = self._lerp(rot, 0, prog)
                self._paint_back(p, QRectF(nx-nw/2, ny-nh/2, nw, nh), nr, 230, label=False)
            self._paint_progress(p, prog, "STACK")
            return

        # From this point on, the deck and every dealt card are always painted.
        # This persistence eliminates the one-frame disappearance seen before.
        for d in range(4, -1, -1):
            self._paint_back(p, deck.translated(d*1.8, d*1.2), 0, 255, label=(d == 0))

        slots = self._result_slots()

        if self.state == "deal":
            each = self.deal_duration + self.deal_gap
            completed = min(self.draw_count, int(elapsed // each))

            # Completed cards remain face-down in their final slots.
            for i in range(completed):
                self._paint_slot_card(p, slots[i], i, False)

            # Current card moves continuously from deck to slot, still face-down.
            if completed < self.draw_count:
                local_elapsed = elapsed - completed*each
                local = max(0.0, min(1.0, local_elapsed/self.deal_duration))
                r, rot = self._deal_geometry(completed, local, slots[completed])
                self._paint_back(p, r, rot, 255, label=True)
            return

        # Await reveal / done: every selected card is laid out back-up first.
        for i, slot in enumerate(slots):
            if i == self.flipping_index:
                q = min(1.0, max(0.0, (now - self.flip_started)/self.flip_duration))
                self._paint_flip(p, slot, self.selected_indices[i], self.selected_reversed[i], q)
            else:
                self._paint_slot_card(p, slot, i, self.revealed[i])

        p.setPen(QColor("#777777"))
        p.setFont(QFont("Segoe UI", 10))
        if self.state == "done":
            message = "全部卡牌已翻开"
        else:
            message = "点击卡牌翻开 · 移动鼠标查看 3D 视差"
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
        eyebrow = QLabel("TAROT / SHUFFLE & DRAW")
        eyebrow.setObjectName("kicker")
        title = QLabel("塔罗牌抽取")
        title.setObjectName("pageTitle")
        desc = QLabel("洗牌后整副牌完成两圈圆周运动；抽出的牌背朝上平铺，点击卡牌后再从左侧翻开。")
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        head_text.addWidget(eyebrow)
        head_text.addWidget(title)
        head_text.addWidget(desc)
        head.addLayout(head_text, 1)

        self.draw_button = QPushButton("洗牌并抽取")
        self.draw_button.setObjectName("primaryButton")
        self.draw_button.setCursor(Qt.PointingHandCursor)
        self.draw_button.setFixedSize(146, 44)
        head.addWidget(self.draw_button, 0, Qt.AlignBottom)
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

        self.draw_button.clicked.connect(self._draw_clicked)
        self.stage.animationStatus.connect(self.status.setText)
        self.stage.animationFinished.connect(self._finished)

    def _set_count(self, n):
        self.selected_count = n

    def _set_controls_enabled(self, enabled):
        self.draw_button.setEnabled(enabled)
        for b in self.count_group.buttons():
            b.setEnabled(enabled)
        self.reverse_chip.setEnabled(enabled)
        self.major_chip.setEnabled(enabled)

    def _draw_clicked(self):
        if self.stage.state not in ("idle", "done"):
            return
        self._set_controls_enabled(False)
        self.stage.start(
            count=self.selected_count,
            allow_reversed=self.reverse_chip.isChecked(),
            major_only=self.major_chip.isChecked(),
        )

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
