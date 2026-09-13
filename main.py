import sys
import math
import random
import time
import re
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSize, QTimer, QEvent
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPainterPath, QPixmap, QIntValidator
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QFrame, QButtonGroup, QStackedWidget, QSizePolicy,
    QGraphicsDropShadowEffect, QLineEdit, QCheckBox, QComboBox, QGridLayout, QScrollArea, QBoxLayout
)

APP_NAME = "5imp1e 5atebox"
APP_VERSION = "0.9.2"
APP_SETTINGS = {
    "language": "zh",
    "mark_back": False,
    "allow_reversed": True,
    "riffle_rounds": 3,
    "cut_groups": 2,
    # Custom draw keeps its own default behavior; all classic/other manual draws
    # default to a fan spread unless changed in Settings.
    "custom_manual_mode": "rain",
    "default_manual_mode": "fan",
}

def TXT(zh, en):
    return en if APP_SETTINGS.get("language") == "en" else zh
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
    __slots__ = ("card_id", "path", "face", "back", "reversed", "marked")

    def __init__(self, card_id, path, face, back=None):
        self.card_id = card_id
        self.path = path
        self.face = face
        self.back = back.copy() if back is not None and not back.isNull() else QPixmap()
        self.reversed = False
        self.marked = False


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
        self.cut_groups = 2
        self.shuffle_stop_requested = False
        # Multi-cut is animated as real persistent card packets.  Each packet keeps
        # the same PhysicalCard ids and internal order from split -> movement -> restack.
        self.cut_packets = []
        self.cut_packet_order = []
        self.cut_output = []
        self.cut_card_packet = {}
        self.cut_card_rank = {}
        self.cut_target_rank = {}
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
        self.cut_spread_duration = 0.82
        self.cut_packet_gap = 0.11
        self.cut_packet_move_duration = 0.46
        self.split_duration = 0.62
        self.release_gap = 0.018
        self.release_move_duration = 0.30
        self.square_duration = 0.48
        self.deal_duration = 0.78
        self.deal_gap = 0.08
        self.flip_duration = 0.52

        # Multiple cards may flip at the same time. Each slot keeps its own start time.
        self.flip_started_by_slot = {}
        self.flip_target_by_slot = {}
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

        # Manual spread physics. Fast cursor motion can brush cards aside.
        # Cards no longer return all the way to their original landing pose: each
        # disturbed card keeps a random 40%-80% of its furthest displacement.
        self.scatter_velocity = {}
        self.scatter_offset = {}
        self.scatter_rest_offset = {}
        self.scatter_peak_offset = {}
        self.scatter_retention = {}
        self._last_mouse_pos = None
        self._last_mouse_time = 0.0
        self._last_tick_time = time.perf_counter()

        # Presentation / manual-choice modes.
        self.language = APP_SETTINGS.get("language", "zh")
        self.mark_back_enabled = APP_SETTINGS.get("mark_back", False)
        self.manual_layout = "rain"
        self.showcase_enabled = False
        self.showcase_was_used = False
        self.showcase_front_duration = 1.15
        self.showcase_flip_card_duration = 0.34
        self.showcase_flip_gap = 0.010
        self.fan_insert_gap = 0.016
        self.fan_insert_duration = 0.28
        self.fan_total_duration = 0.0

        # Optional fixed spread geometry and free-layout interaction.
        self.spread_slots = None   # list of normalized (x,y,w,h,rotation) tuples
        self.spread_labels = []
        self.free_move_enabled = False
        self.free_positions = {}  # slot -> QRectF in widget coordinates
        self.drag_slot = -1
        self.drag_offset = QPointF(0.0, 0.0)
        self.drag_start_pos = QPointF(0.0, 0.0)
        self.drag_moved = False
        self.manual_drag_cid = -1
        self.manual_drag_rect = QRectF()
        self.manual_drag_rot = 0.0
        self.manual_drag_offset = QPointF(0.0, 0.0)

        # Round-to-round continuity.  Direct Draw / Choose actions do not reshuffle:
        # the current physical order is preserved and visible cards are collected
        # back to the top of the deck before the next action starts.
        self.deck_shuffled_since_last_action = False
        self.pending_action = None
        self.recollect_order_slots = []
        self.recollect_slot_rects = {}
        self.recollect_duration = 0.46
        self.recollect_gap = 0.075
        self.recollect_idle_gap = 0.012

        # Small top-right warning used when the player proceeds without reshuffling.
        self.warning_text = ""
        self.warning_until = 0.0

        # Card-rain opening: the physical deck visibly splits into several packets
        # before all packets release their cards together.
        self.rain_split_duration = 0.72
        self.rain_packets = []
        self.rain_card_group = {}
        self.rain_card_rank = {}

    def _t(self, zh, en):
        return en if self.language == "en" else zh

    def configure_runtime(self, language=None, mark_back=None):
        if language is not None:
            self.language = language
        if mark_back is not None:
            self.mark_back_enabled = bool(mark_back)

    def configure_spread(self, slots=None, labels=None):
        self.spread_slots = slots[:] if slots else None
        self.spread_labels = list(labels or [])
        self.free_positions.clear()

    def configure_free_move(self, enabled=False):
        self.free_move_enabled = bool(enabled)
        if not self.free_move_enabled:
            self.free_positions.clear()
        self.drag_slot = -1
        self.drag_moved = False
        self.manual_drag_cid = -1

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

    def _show_unshuffled_warning(self):
        self.warning_text = self._t("卡堆并未重新洗牌", "Deck has not been reshuffled")
        self.warning_until = time.perf_counter() + 2.8
        self._ensure_timer()
        self.update()

    def _clear_result_state(self):
        self.selected_indices = []
        self.selected_reversed = []
        self.revealed = []
        self.flip_started_by_slot = {}
        self.flip_target_by_slot = {}
        self.free_positions.clear()
        self.drag_slot = -1
        self.manual_drag_cid = -1
        self._finished_emitted = False

    def _execute_pending_action(self):
        pending = self.pending_action
        self.pending_action = None
        if not pending:
            return False
        kind, args = pending
        # A completed direct action consumes the current shuffle state.  The next
        # direct action will warn unless a physical shuffle occurs in between.
        self.deck_shuffled_since_last_action = False
        if kind == "draw":
            self.configure_free_move(False)
            self.draw_from_deck(*args)
        elif kind == "manual":
            count, allow_reversed, layout_mode, free_move = args
            self.configure_free_move(bool(free_move))
            self.start_manual(count, allow_reversed, layout_mode)
        elif kind == "shuffle":
            self.configure_free_move(False)
            self.start_shuffle(*args)
        return True

    def request_draw(self, count=3, major_only=False):
        return self._request_direct_action("draw", (count, major_only))

    def request_manual(self, count=3, allow_reversed=True, layout_mode="rain", free_move=False):
        return self._request_direct_action("manual", (count, allow_reversed, layout_mode, free_move))

    def request_shuffle(self, allow_reversed=True, major_only=False, riffle_rounds=3, cut_groups=2, showcase=False):
        if self.state not in ("idle", "done", "shuffled", "await_reveal"):
            return False
        self.pending_action = ("shuffle", (allow_reversed, major_only, riffle_rounds, cut_groups, showcase))
        if self.selected_indices and self.state in ("done", "await_reveal"):
            slots = self._result_slots()
            order = sorted(range(min(len(slots), len(self.selected_indices))), key=lambda i: slots[i].center().x())
            self.recollect_order_slots = order
            self.recollect_slot_rects = {i: QRectF(slots[i]) for i in order}
            self.state = "recollect_results"
            self.state_started = time.perf_counter()
            self.animationStatus.emit(self._t("正在收回桌面卡牌后重新洗牌", "Collecting table cards before reshuffling"))
            self._ensure_timer(); self.update(); return True
        return self._execute_pending_action()

    def _request_direct_action(self, kind, args):
        allowed = ("idle", "shuffled", "done", "await_reveal")
        if self.state not in allowed:
            return False
        if not self.deck_shuffled_since_last_action:
            self._show_unshuffled_warning()
        self.pending_action = (kind, args)

        # If cards from a previous reading are still on the table, collect them
        # left-to-right and place them on top of the surviving physical deck.
        if self.selected_indices and self.state in ("done", "await_reveal"):
            slots = self._result_slots()
            order = sorted(range(min(len(slots), len(self.selected_indices))),
                           key=lambda i: slots[i].center().x())
            self.recollect_order_slots = order
            self.recollect_slot_rects = {i: QRectF(slots[i]) for i in order}
            self.state = "recollect_results"
            self.state_started = time.perf_counter()
            self.animationStatus.emit(self._t("未重新洗牌 · 正从左到右收回桌面卡牌",
                                              "No reshuffle · collecting table cards left to right"))
            self._ensure_timer(); self.update()
            return True

        # The idle table shows the full deck fanned out.  Direct actions first
        # collect that visible fan into the central stack without altering order.
        if self.state == "idle" and self.deck_order:
            self.state = "recollect_idle"
            self.state_started = time.perf_counter()
            self.animationStatus.emit(self._t("未重新洗牌 · 正按当前顺序收回卡堆",
                                              "No reshuffle · collecting the deck in its current order"))
            self._ensure_timer(); self.update()
            return True

        return self._execute_pending_action()

    def _prepare_rain_packets(self, groups=6):
        order = list(self.scatter_order)
        if not order:
            self.rain_packets = []
            self.rain_card_group = {}
            self.rain_card_rank = {}
            return
        groups = max(3, min(int(groups), 8, len(order)))
        base = len(order) // groups
        rem = len(order) % groups
        packets=[]; pos=0
        for g in range(groups):
            size = base + (1 if g < rem else 0)
            packet = order[pos:pos+size]; pos += size
            packets.append(packet)
        self.rain_packets = packets
        self.rain_card_group = {}
        self.rain_card_rank = {}
        for g, packet in enumerate(packets):
            for rank, cid in enumerate(packet):
                self.rain_card_group[cid] = g
                self.rain_card_rank[cid] = rank

    def _rain_group_rect(self, group_index):
        count=max(1,len(self.rain_packets))
        cols=min(6,count)
        cw=min(62.0,max(42.0,self.width()*.052))
        ch=cw*1.58
        span=self.width()*.74
        left=(self.width()-span)/2
        step=span/max(1,cols-1)
        x=left + (group_index % cols)*step - cw/2
        y=max(18.0,self.height()*.10)
        if group_index>=cols:
            y += ch*.42
        return QRectF(x,y,cw,ch)

    def _apply_multi_cut(self, order, groups):
        """Cut the physical deck into N contiguous packets, then re-stack them.

        Internal packet order is preserved.  Only packet order changes, so this is a
        real cut rather than an extra random.shuffle().
        """
        order = list(order)
        n = len(order)
        groups = max(1, min(int(groups), n)) if n else 1
        if groups <= 1 or n <= 1:
            return order

        # Produce non-empty packet sizes around an even split, with small natural jitter.
        remaining = n
        sizes = []
        for i in range(groups - 1):
            left_groups = groups - i
            ideal = remaining / left_groups
            jitter = random.gauss(0.0, max(0.6, ideal * 0.12))
            size = int(round(ideal + jitter))
            min_size = 1
            max_size = remaining - (left_groups - 1)
            size = max(min_size, min(max_size, size))
            sizes.append(size)
            remaining -= size
        sizes.append(remaining)

        packets = []
        pos = 0
        for size in sizes:
            packets.append(order[pos:pos + size])
            pos += size

        # A cut normally relocates whole packets.  Use a random rotation and, for
        # 3+ groups, occasionally swap two neighboring packets for a hand-cut feel.
        shift = random.randrange(1, len(packets)) if len(packets) > 1 else 0
        packets = packets[shift:] + packets[:shift]
        if len(packets) >= 3 and random.random() < 0.45:
            i = random.randrange(0, len(packets) - 1)
            packets[i], packets[i + 1] = packets[i + 1], packets[i]

        out = []
        for packet in packets:
            out.extend(packet)
        return out

    def _prepare_multi_cut_plan(self, order, groups):
        """Build an explicit packet plan for the visible multi-cut animation.

        The deck is split into exactly ``groups`` contiguous physical packets.  We
        retain every PhysicalCard id inside its packet and preserve packet-internal
        order.  A packet-level rotation (plus the same occasional neighbor swap)
        determines the final stack order.
        """
        order = list(order)
        n = len(order)
        groups = max(1, min(int(groups), n)) if n else 1
        if not order:
            self.cut_packets = []
            self.cut_packet_order = []
            self.cut_output = []
            self.cut_card_packet = {}
            self.cut_card_rank = {}
            self.cut_target_rank = {}
            return

        remaining = n
        sizes = []
        for i in range(groups - 1):
            left_groups = groups - i
            ideal = remaining / left_groups
            jitter = random.gauss(0.0, max(0.6, ideal * 0.12))
            size = int(round(ideal + jitter))
            size = max(1, min(remaining - (left_groups - 1), size))
            sizes.append(size)
            remaining -= size
        sizes.append(remaining)

        packets = []
        pos = 0
        for size in sizes:
            packets.append(order[pos:pos + size])
            pos += size

        packet_order = list(range(len(packets)))
        if len(packet_order) > 1:
            shift = random.randrange(1, len(packet_order))
            packet_order = packet_order[shift:] + packet_order[:shift]
        if len(packet_order) >= 3 and random.random() < 0.45:
            i = random.randrange(0, len(packet_order) - 1)
            packet_order[i], packet_order[i + 1] = packet_order[i + 1], packet_order[i]

        output = []
        for pi in packet_order:
            output.extend(packets[pi])

        self.cut_packets = packets
        self.cut_packet_order = packet_order
        self.cut_output = output
        self.cut_card_packet = {}
        self.cut_card_rank = {}
        self.cut_target_rank = {}
        for pi, packet in enumerate(packets):
            for rank, cid in enumerate(packet):
                self.cut_card_packet[cid] = pi
                self.cut_card_rank[cid] = rank
        for rank, cid in enumerate(output):
            self.cut_target_rank[cid] = rank

    def _cut_group_rect(self, packet_index):
        """Visible table position of one cut packet; supports up to 20 groups."""
        count = max(1, len(self.cut_packets))
        cols = min(5, count)
        rows = int(math.ceil(count / cols))
        area = QRectF(self.width() * .08, self.height() * .18, self.width() * .84, self.height() * .48)
        cell_w = area.width() / cols
        cell_h = area.height() / rows
        row = packet_index // cols
        col = packet_index % cols
        # Keep 2:3 card proportion while leaving unmistakable gaps between groups.
        cw = min(82.0, max(42.0, cell_w * .48))
        ch = cw * 1.52
        if ch > cell_h * .72:
            ch = max(62.0, cell_h * .72)
            cw = ch / 1.52
        cx = area.left() + (col + .5) * cell_w
        cy = area.top() + (row + .5) * cell_h
        return QRectF(cx - cw/2, cy - ch/2, cw, ch)

    def _cut_restack_total_duration(self):
        return max(.46, (max(1, len(self.cut_packet_order)) - 1) * self.cut_packet_gap + self.cut_packet_move_duration)

    def request_stop_shuffle(self):
        """Ask the physical shuffle to stop at the nearest safe point."""
        shuffle_states = {"showcase_front", "showcase_flip", "gather", "cut_spread", "cut_restack", "split", "riffle", "square"}
        if self.state not in shuffle_states:
            return False

        # Before cards start interleaving there is no partial physical order to
        # preserve, so stopping can be immediate.
        if self.state in {"showcase_front", "showcase_flip", "gather"}:
            self.shuffle_stop_requested = False
            if not self.major_only:
                self.deck_order = self.active_order[:]
            self.state = "shuffled"
            self.deck_shuffled_since_last_action = True
            self.state_started = time.perf_counter()
            self.animationStatus.emit(self._t("洗牌已终止 · 当前牌序已保留", "Shuffle stopped · current deck order preserved"))
            self.shuffleFinished.emit()
            self.update()
            return True

        # During a visible cut/riffle, finish the current physical operation first.
        # This prevents bound cards from snapping or disappearing mid-movement.
        self.shuffle_stop_requested = True
        if self.state in {"cut_spread", "cut_restack"}:
            self.animationStatus.emit(self._t("终止已请求 · 将在当前 Cut 重新叠放完成后停止", "Stop requested · will stop after the current cut is restacked"))
        else:
            self.animationStatus.emit(self._t("终止已请求 · 将在本次交错整理完成后停止", "Stop requested · will stop after the current riffle is squared"))
        return True

    def start_shuffle(self, allow_reversed=True, major_only=False, riffle_rounds=3, cut_groups=2, showcase=False):
        if self.state not in ("idle", "done", "shuffled"):
            return
        if len(self.cards) < 22:
            self.animationStatus.emit(self._t("resource/cards/front 中没有完整牌组。", "No complete deck found in resource/cards/front."))
            return

        pool = self.major_indices[:] if major_only else list(range(len(self.cards)))
        if not pool:
            return
        self.manual_mode = False
        self.allow_reversed = allow_reversed
        self.major_only = major_only
        self.shuffle_rounds = max(1, min(50, int(riffle_rounds)))
        self.cut_groups = max(1, min(20, int(cut_groups)))
        self.shuffle_stop_requested = False
        self.deck_shuffled_since_last_action = False
        self.showcase_enabled = bool(showcase)
        self.showcase_was_used = bool(showcase)

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

        # Build, but do not instantly apply, the requested physical multi-cut.
        # The cut is committed only after the user has actually seen all N bound
        # packets separate and re-stack on screen.
        self._prepare_multi_cut_plan(self.active_order, self.cut_groups)

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
        self.state = "showcase_front" if self.showcase_enabled else "gather"
        self.state_started = time.perf_counter()
        if self.showcase_enabled:
            self.animationStatus.emit(self._t("卡牌展示 · 正面总览", "Card showcase · face overview"))
        else:
            self.animationStatus.emit(self._t(f"正在切牌 · {self.cut_groups} 组 / Riffle {self.shuffle_rounds} 次", f"Cutting · {self.cut_groups} groups / {self.shuffle_rounds} riffles"))
        self._ensure_timer()
        self.update()

    def start(self, count=3, allow_reversed=True, major_only=False):
        # Backward-compatible alias: start now performs shuffling only.
        self.start_shuffle(allow_reversed=allow_reversed, major_only=major_only, riffle_rounds=self.shuffle_rounds, cut_groups=self.cut_groups, showcase=False)

    def draw_from_deck(self, count=3, major_only=False):
        if self.state not in ("idle", "shuffled", "done"):
            return
        if not self.cards:
            return

        pool_set = set(self.major_indices) if major_only else set(range(len(self.cards)))
        order = [cid for cid in self.deck_order if cid in pool_set]
        if not order:
            self.animationStatus.emit(self._t("当前牌组中没有可抽取的牌。", "There are no drawable cards in the current deck."))
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
        self.animationStatus.emit(self._t(f"正在从牌顶抽取 1 / {self.draw_count}", f"Drawing from the top · 1 / {self.draw_count}"))
        self._ensure_timer()
        self.update()

    def start_manual(self, count=3, allow_reversed=True, layout_mode="rain"):
        if self.state not in ("idle", "done", "shuffled", "await_reveal"):
            return
        if len(self.cards) < 78:
            self.animationStatus.emit(self._t("自己选择模式需要完整的 78 张牌。", "Manual choice requires a complete 78-card deck."))
            return

        self.manual_mode = True
        self.manual_layout = layout_mode if layout_mode in ("rain", "fan") else "rain"
        self.draw_count = max(1, min(int(count), len(self.cards)))
        self.allow_reversed = allow_reversed
        self.major_only = False
        # Manual choice keeps the current physical deck order.  Missing cards are
        # only appended as an integrity fallback; normal round transitions return
        # table cards visibly before this method is entered.
        current = self.deck_order[:]
        seen = set(current)
        if len(current) < len(self.cards):
            current.extend(cid for cid in range(len(self.cards)) if cid not in seen)
        self.deck_order = current
        self.active_order = current[:]

        for cid in self.active_order:
            self.cards[cid].reversed = bool(random.getrandbits(1)) if allow_reversed else False

        self.selected_indices = []
        self.selected_reversed = []
        self.revealed = []
        self.flip_started_by_slot = {}
        self.flip_target_by_slot = {}
        self.manual_take_started = {}
        self.free_positions.clear()
        self.drag_slot = -1
        self.drag_moved = False
        self._finished_emitted = False
        self.deck_park_started = 0.0
        self.hovered_slot = -1
        self.hover_target = QPointF(0.0, 0.0)
        self.hover_current = QPointF(0.0, 0.0)

        self.scatter_order = self.active_order[:]
        if self.manual_layout == "fan":
            self._prepare_fan_layout()
            self.state = "fan_insert"
            self.state_started = time.perf_counter()
            self.animationStatus.emit(self._t("自己选择 · 卡牌正在逐张插入扇形", "Manual choice · cards are rapidly inserting into the fan"))
        else:
            self._prepare_rain_packets(6)
            self._prepare_scatter_layout()
            self.state = "rain_split"
            self.state_started = time.perf_counter()
            self.animationStatus.emit(self._t("牌雨 · 卡堆正在分成多组", "Card rain · splitting the deck into packets"))
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
        self.scatter_rest_offset = {}
        self.scatter_peak_offset = {}
        self.scatter_retention = {}
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

            # Every card starts from one of the visible packet stacks prepared by
            # _prepare_rain_packets().  All groups release together; only tiny
            # within-packet delays remain so the player can track real bound cards.
            g = self.rain_card_group.get(cid, i % max(1, len(self.rain_packets)))
            rank = self.rain_card_rank.get(cid, 0)
            group_rect = self._rain_group_rect(g)
            delay = rank * .014 + random.uniform(0.0, .028)
            duration = random.uniform(.92, 1.24)
            start_x = group_rect.left() + random.uniform(-5.0, 5.0)
            start_y = group_rect.top() + min(rank, 8) * .45
            start_rot = random.uniform(-7.0, 7.0)
            drift = random.uniform(-46.0, 46.0)
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
            self.scatter_rest_offset[cid] = QPointF(0.0, 0.0)
            self.scatter_peak_offset[cid] = QPointF(0.0, 0.0)
            self.scatter_retention[cid] = random.uniform(0.40, 0.80)
            max_end = max(max_end, delay + duration)
        self.scatter_drop_total = max_end + .12
        self._last_mouse_pos = None
        self._last_mouse_time = 0.0

    def _prepare_fan_layout(self):
        """Prepare a wide physical fan. Cards are inserted one-by-one in shuffled order."""
        self.scatter_pose = {}
        self.scatter_velocity = {}
        self.scatter_offset = {}
        self.scatter_rest_offset = {}
        self.scatter_peak_offset = {}
        self.scatter_retention = {}
        n = max(1, len(self.scatter_order))
        cw = max(56.0, min(72.0, self.width() * .055))
        ch = cw * 1.58
        left = max(34.0, self.width() * .045)
        right = min(self.width() - cw - 34.0, self.width() * .955 - cw)
        span = max(1.0, right - left)
        center_x = (left + right) * .5
        base_y = self.height() * .47
        for i, cid in enumerate(self.scatter_order):
            u = i / max(1, n - 1)
            x = left + span * u
            norm = (x - center_x) / max(1.0, span * .5)
            y = base_y + (norm * norm) * 88.0 - 20.0
            rot = norm * 22.0
            self.scatter_pose[cid] = {
                "rect": QRectF(x, y, cw, ch),
                "rot": rot,
                "delay": i * self.fan_insert_gap,
                "duration": self.fan_insert_duration,
                "start_x": self.width() * .50 - cw * .50 + random.uniform(-8.0, 8.0),
                "start_y": self.height() * .73,
                "start_rot": random.uniform(-5.0, 5.0),
                "drift": 0.0,
                "bounce": 0.0,
            }
            self.scatter_velocity[cid] = QPointF()
            self.scatter_offset[cid] = QPointF()
            self.scatter_rest_offset[cid] = QPointF()
            self.scatter_peak_offset[cid] = QPointF()
            self.scatter_retention[cid] = 0.0
        self.fan_total_duration = (n - 1) * self.fan_insert_gap + self.fan_insert_duration + .12

    def _fan_card_pose(self, cid, elapsed):
        d = self.scatter_pose[cid]
        target = d["rect"]
        q = (elapsed - d["delay"]) / max(.001, d["duration"])
        if q <= 0:
            return QRectF(d["start_x"], d["start_y"], target.width(), target.height()), d["start_rot"], 0.0
        if q >= 1:
            return QRectF(target), d["rot"], 1.0
        e = self._ease_in_out(q)
        x = self._lerp(d["start_x"], target.left(), e)
        y = self._lerp(d["start_y"], target.top(), e) - math.sin(e * math.pi) * 34.0
        rot = self._lerp(d["start_rot"], d["rot"], e)
        return QRectF(x, y, target.width(), target.height()), rot, q

    def _manual_hit(self, pos):
        return self._manual_card_hit(pos)

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

        # Cards settle toward a retained offset instead of returning to their
        # original landing point.  The retained offset follows 40%-80% of the
        # furthest displacement reached from the original pose.
        spring = 26.0
        damping = math.exp(-6.8 * dt)
        snap_distance = 0.22
        snap_speed = 5.0

        for cid in self.scatter_order:
            if cid in selected:
                continue
            off = self.scatter_offset.get(cid, QPointF())
            vel = self.scatter_velocity.get(cid, QPointF())
            rest = self.scatter_rest_offset.get(cid, QPointF())
            peak = self.scatter_peak_offset.get(cid, QPointF())
            retention = self.scatter_retention.get(cid, 0.60)

            # While the card is still travelling outward, keep updating the
            # furthest point and move its future resting place to 40%-80% of it.
            off_mag = math.hypot(off.x(), off.y())
            peak_mag = math.hypot(peak.x(), peak.y())
            if off_mag > peak_mag + 0.10:
                peak = QPointF(off)
                rest = QPointF(off.x() * retention, off.y() * retention)
                self.scatter_peak_offset[cid] = peak
                self.scatter_rest_offset[cid] = rest

            dx = off.x() - rest.x()
            dy = off.y() - rest.y()
            vx = vel.x() - dx * spring * dt
            vy = vel.y() - dy * spring * dt
            vx *= damping
            vy *= damping
            ox = off.x() + vx * dt
            oy = off.y() + vy * dt

            # Keep the spread readable even after repeated cursor brushes.
            mag = math.hypot(ox, oy)
            if mag > 52.0:
                scale = 52.0 / mag
                ox *= scale
                oy *= scale

            # Snap to the retained position, not to zero.
            if math.hypot(ox - rest.x(), oy - rest.y()) <= snap_distance and math.hypot(vx, vy) <= snap_speed:
                ox, oy = rest.x(), rest.y()
                vx = vy = 0.0
            else:
                active = True

            self.scatter_velocity[cid] = QPointF(vx, vy)
            self.scatter_offset[cid] = QPointF(ox, oy)

        return active

    def _repel_scatter_from_cursor(self, pos, cursor_speed):
        # Require a deliberate, fast mouse sweep before cards react.
        # Just crossing the threshold produces only a small nudge; faster motion
        # increases both the affected radius and the impulse strength.
        repel_threshold = 1500.0
        if cursor_speed < repel_threshold:
            return
        selected = set(self.selected_indices)
        excess_speed = cursor_speed - repel_threshold
        radius = min(190.0, 78.0 + excess_speed * .038)
        strength = min(1550.0, 90.0 + excess_speed * .92)
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
            # Each fresh brush leaves a slightly different amount of displacement.
            self.scatter_retention[cid] = random.uniform(0.40, 0.80)

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

        if self.state == "showcase_front":
            animation_active = True
            if elapsed >= self.showcase_front_duration:
                self.state = "showcase_flip"
                self.state_started = now
                self.animationStatus.emit(self._t("卡牌展示 · 正面正在翻转为背面", "Card showcase · flipping all cards face-down"))

        elif self.state == "showcase_flip":
            animation_active = True
            total = (max(0, len(self.active_order)-1) * self.showcase_flip_gap + self.showcase_flip_card_duration + .10)
            if elapsed >= total:
                self.state = "gather"
                self.state_started = now
                self.animationStatus.emit(self._t(f"展示完成 · 开始 Cut {self.cut_groups} 组", f"Showcase complete · starting {self.cut_groups}-group cut"))

        elif self.state == "gather":
            animation_active = True
            if elapsed >= self.gather_duration:
                if self.cut_groups > 1 and self.cut_packets:
                    self.state = "cut_spread"
                    self.state_started = now
                    self.animationStatus.emit(self._t(f"Cut · 正在展开 {len(self.cut_packets)} 组实体牌堆", f"Cut · spreading {len(self.cut_packets)} physical packets"))
                else:
                    self.active_order = self.cut_output[:] if self.cut_output else self.active_order
                    if not self.major_only:
                        self.deck_order = self.active_order[:]
                    self._prepare_riffle_round()
                    self.state = "split"
                    self.state_started = now
                    self.animationStatus.emit(self._t(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · Riffle 切半", f"Physical shuffle {self.shuffle_round + 1}/{self.shuffle_rounds} · riffle split"))

        elif self.state == "cut_spread":
            animation_active = True
            if elapsed >= self.cut_spread_duration:
                self.state = "cut_restack"
                self.state_started = now
                self.animationStatus.emit(self._t(f"Cut · {len(self.cut_packets)} 组按新顺序逐组叠回", f"Cut · restacking {len(self.cut_packets)} packets in the new order"))

        elif self.state == "cut_restack":
            animation_active = True
            if elapsed >= self._cut_restack_total_duration():
                self.active_order = self.cut_output[:] if self.cut_output else self.active_order
                if not self.major_only:
                    self.deck_order = self.active_order[:]
                if self.shuffle_stop_requested:
                    self.shuffle_stop_requested = False
                    self.state = "shuffled"
                    self.deck_shuffled_since_last_action = True
                    self.state_started = now
                    self.animationStatus.emit(self._t(f"洗牌已终止 · Cut {self.cut_groups} 组已完成并保留", f"Shuffle stopped · {self.cut_groups}-group cut completed and preserved"))
                    self.shuffleFinished.emit()
                else:
                    self._prepare_riffle_round()
                    self.state = "split"
                    self.state_started = now
                    self.animationStatus.emit(self._t(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · Riffle 切半", f"Physical shuffle {self.shuffle_round + 1}/{self.shuffle_rounds} · riffle split"))

        elif self.state == "split":
            animation_active = True
            if elapsed >= self.split_duration:
                self.state = "riffle"
                self.state_started = now
                self.animationStatus.emit(self._t(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · 交错落牌", f"Physical shuffle {self.shuffle_round + 1}/{self.shuffle_rounds} · interleaving"))

        elif self.state == "riffle":
            animation_active = True
            if elapsed >= self._riffle_total_duration():
                self.state = "square"
                self.state_started = now
                self.animationStatus.emit(self._t(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · 整理牌组", f"Physical shuffle {self.shuffle_round + 1}/{self.shuffle_rounds} · squaring the deck"))

        elif self.state == "square":
            animation_active = True
            if elapsed >= self.square_duration:
                # Commit the actual physical order only after every card has landed.
                self.active_order = self.round_output[:]
                if not self.major_only:
                    self.deck_order = self.active_order[:]
                self.shuffle_round += 1
                if self.shuffle_stop_requested:
                    self.shuffle_stop_requested = False
                    self.state = "shuffled"
                    self.deck_shuffled_since_last_action = True
                    self.state_started = now
                    self.animationStatus.emit(self._t(f"洗牌已终止 · 已完成 {self.shuffle_round} 次 Riffle", f"Shuffle stopped · {self.shuffle_round} riffles completed"))
                    self.shuffleFinished.emit()
                elif self.shuffle_round < self.shuffle_rounds:
                    self._prepare_riffle_round()
                    self.state = "split"
                    self.state_started = now
                    self.animationStatus.emit(self._t(f"真实洗牌 {self.shuffle_round + 1}/{self.shuffle_rounds} · 切牌", f"Physical shuffle {self.shuffle_round + 1}/{self.shuffle_rounds} · split"))
                else:
                    # Shuffling is now a complete, standalone action. Keep the
                    # squared physical deck on the table until the user presses Draw.
                    self.state = "shuffled"
                    self.deck_shuffled_since_last_action = True
                    self.state_started = now
                    self.animationStatus.emit(self._t("洗牌完成 · 可以抽取", "Shuffle complete · ready to draw"))
                    self.shuffleFinished.emit()

        elif self.state == "deal":
            animation_active = True
            each = self.deal_duration + self.deal_gap
            idx = int(elapsed // each)
            if idx >= self.draw_count:
                self.state = "await_reveal"
                self.state_started = now
                self.deck_park_started = now
                self.animationStatus.emit(self._t("抽取完成 · 点击任意牌翻开", "Draw complete · click any card to reveal it"))
            else:
                self.animationStatus.emit(self._t(f"正在从牌顶抽取 {idx + 1} / {self.draw_count}", f"Drawing from the top · {idx + 1} / {self.draw_count}"))

        elif self.state == "recollect_idle":
            animation_active = True
            total = max(self.recollect_duration, (max(0, len(self.deck_order)-1))*self.recollect_idle_gap + self.recollect_duration)
            if elapsed >= total:
                self.state = "idle"
                self.state_started = now
                self._execute_pending_action()

        elif self.state == "recollect_results":
            animation_active = True
            total = max(self.recollect_duration, (max(0, len(self.recollect_order_slots)-1))*self.recollect_gap + self.recollect_duration)
            if elapsed >= total:
                for slot in self.recollect_order_slots:
                    if slot < len(self.selected_indices):
                        cid = self.selected_indices[slot]
                        if cid not in self.deck_order:
                            self.deck_order.append(cid)
                self.active_order = self.deck_order[:]
                self._clear_result_state()
                self.state = "idle"
                self.state_started = now
                self._execute_pending_action()

        elif self.state == "rain_split":
            animation_active = True
            if elapsed >= self.rain_split_duration:
                self.state = "self_drop"
                self.state_started = now
                self.animationStatus.emit(self._t("牌雨 · 多组卡牌同时开始散落", "Card rain · all packets are scattering together"))

        elif self.state == "self_drop":
            animation_active = True
            if elapsed >= self.scatter_drop_total:
                self.state = "self_select"
                self.state_started = now
                self.animationStatus.emit(self._t(f"自己选择 · 请挑选 {self.draw_count} 张牌", f"Manual choice · choose {self.draw_count} cards"))

        elif self.state == "fan_insert":
            animation_active = True
            if elapsed >= self.fan_total_duration:
                self.state = "fan_select"
                self.state_started = now
                self.animationStatus.emit(self._t(f"扇形展开 · 请挑选 {self.draw_count} 张牌", f"Fan spread · choose {self.draw_count} cards"))

        elif self.state in ("self_select", "fan_select"):
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
                self.animationStatus.emit(self._t("选择完成 · 正在整理剩余牌组", "Selection complete · gathering the remaining deck"))
                animation_active = True

        elif self.state == "manual_gather_rest":
            animation_active = True
            if elapsed >= self.manual_rest_gather_duration:
                self.state = "await_reveal"
                self.state_started = now
                # Remaining cards are already visually at the parked target.
                self.deck_park_started = now - self.deck_park_duration
                self.animationStatus.emit(self._t("选择完成 · 点击任意已选卡牌翻开", "Selection complete · click any selected card to reveal it"))
                # Free-move readings must release the UI immediately after the cards
                # are placed; waiting for every card to be face-up previously left
                # the operation controls permanently disabled.
                if self.free_move_enabled:
                    self._emit_finished_once()

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
                self.revealed[idx] = self.flip_target_by_slot.pop(idx, True)
                self.flip_started_by_slot.pop(idx, None)

            if finished:
                if all(self.revealed) and not self.free_move_enabled:
                    self.state = "done"
                    self.animationStatus.emit(self._t("全部卡牌已翻开", "All cards revealed"))
                    self._emit_finished_once()
                elif self.free_move_enabled:
                    face_up = sum(1 for v in self.revealed if v)
                    self.animationStatus.emit(self._t(f"自由移动 · {face_up} 张正面朝上 · 可继续拖动或翻回背面", f"Free move · {face_up} face up · keep dragging or flip cards face-down"))
                    if not self._finished_emitted:
                        self._emit_finished_once()
                else:
                    left = sum(1 for v in self.revealed if not v)
                    active = len(self.flip_started_by_slot)
                    suffix = self._t(f" · {active} 张正在翻转", f" · {active} flipping") if active else ""
                    self.animationStatus.emit(self._t(f"已翻开 · 还有 {left} 张{suffix}", f"Revealed · {left} remaining{suffix}"))

        hx = self.hover_current.x()
        hy = self.hover_current.y()
        tx = self.hover_target.x() if self.hovered_slot >= 0 else 0.0
        ty = self.hover_target.y() if self.hovered_slot >= 0 else 0.0
        nx = hx + (tx - hx) * 0.24
        ny = hy + (ty - hy) * 0.24
        if abs(nx - tx) < 0.002:
            nx = tx
        if abs(ny - ty) < 0.002:
            ny = ty
        hover_active = abs(nx - hx) > 0.0005 or abs(ny - hy) > 0.0005
        self.hover_current = QPointF(nx, ny)

        self.update()
        warning_active = bool(self.warning_text and now < self.warning_until)
        if not animation_active and not hover_active and not self.flip_started_by_slot and not warning_active:
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
        if self.spread_slots:
            out = []
            for i, spec in enumerate(self.spread_slots[:n]):
                x, y, w, h, _rot = spec
                out.append(QRectF(self.width()*x - self.width()*w/2,
                                  self.height()*y - self.height()*h/2,
                                  self.width()*w, self.height()*h))
            if len(out) >= n:
                slots = out[:n]
            else:
                slots = out
        else:
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
            slots = [QRectF(x0 + i*(cw+gap), y, cw, ch) for i in range(n)]
        if self.free_move_enabled:
            merged=[]
            for i,r in enumerate(slots):
                if i not in self.free_positions:
                    self.free_positions[i]=QRectF(r)
                merged.append(QRectF(self.free_positions[i]))
            return merged
        return slots

    def _slot_rotation(self, index):
        if self.spread_slots and index < len(self.spread_slots):
            return self.spread_slots[index][4]
        return 0.0

    def _slot_contains(self, pos, rect, index, pad=5.0):
        """Hit-test a possibly rotated result card in its own local coordinates."""
        rot = self._slot_rotation(index)
        c = rect.center()
        if abs(rot) < 0.01:
            return rect.adjusted(-pad, -pad, pad, pad).contains(pos)
        a = math.radians(-rot)
        dx, dy = pos.x() - c.x(), pos.y() - c.y()
        rx = dx * math.cos(a) - dy * math.sin(a) + c.x()
        ry = dx * math.sin(a) + dy * math.cos(a) + c.y()
        return rect.adjusted(-pad, -pad, pad, pad).contains(QPointF(rx, ry))

    def _visual_card_bounds(self, rect, index):
        rot = math.radians(abs(self._slot_rotation(index)) % 180.0)
        c = abs(math.cos(rot)); s = abs(math.sin(rot))
        w = rect.width()*c + rect.height()*s
        h = rect.width()*s + rect.height()*c
        return QRectF(rect.center().x()-w/2, rect.center().y()-h/2, w, h)

    def _spread_label_rects(self, slots):
        """Keep every spread label close to the card it describes.

        The old fallback could throw a crowded label all the way to the edge of the
        stage, which made Celtic-Cross labels look detached from their cards.  This
        version always searches locally around the corresponding card and chooses
        the least-overlapping nearby position when no completely empty slot exists.
        """
        card_boxes = [self._visual_card_bounds(r, i).adjusted(-5,-5,5,5) for i,r in enumerate(slots)]
        used=[]; result=[]
        bounds = QRectF(10, 10, max(20, self.width()-20), max(20, self.height()-48))

        def overlap_area(a, b):
            inter = a.intersected(b)
            return max(0.0, inter.width()) * max(0.0, inter.height()) if not inter.isEmpty() else 0.0

        for i, r in enumerate(slots):
            vb = self._visual_card_bounds(r, i)
            label_w = max(72.0, min(136.0, vb.width()*1.22))
            label_h = 25.0
            gap = 8.0
            rot = abs(self._slot_rotation(i)) % 180.0

            # Crossing cards should read naturally: the horizontal card gets a side
            # label, while the vertical card prefers below/above.  Other cards keep
            # the conventional below-first ordering.
            is_crossing = any(j != i and abs(slots[j].center().x()-r.center().x()) < 3.0 and abs(slots[j].center().y()-r.center().y()) < 3.0 for j in range(len(slots)))
            if is_crossing and 55 <= rot <= 125:
                directions = ("right", "left", "above", "below")
            elif is_crossing:
                directions = ("below", "above", "left", "right")
            elif 55 <= rot <= 125:
                directions = ("right", "left", "below", "above")
            else:
                directions = ("below", "above", "right", "left")

            candidates=[]
            for step in (1.0, 1.45, 1.95, 2.55):
                for d in directions:
                    if d == "below":
                        cand=QRectF(vb.center().x()-label_w/2, vb.bottom()+gap*step, label_w, label_h)
                    elif d == "above":
                        cand=QRectF(vb.center().x()-label_w/2, vb.top()-gap*step-label_h, label_w, label_h)
                    elif d == "right":
                        cand=QRectF(vb.right()+gap*step, vb.center().y()-label_h/2, label_w, label_h)
                    else:
                        cand=QRectF(vb.left()-gap*step-label_w, vb.center().y()-label_h/2, label_w, label_h)
                    cand.moveLeft(max(bounds.left(), min(cand.left(), bounds.right()-cand.width())))
                    cand.moveTop(max(bounds.top(), min(cand.top(), bounds.bottom()-cand.height())))

                    card_penalty = sum(overlap_area(cand, b) for b in card_boxes)
                    label_penalty = sum(overlap_area(cand, u.adjusted(-4,-2,4,2)) for u in used)
                    distance = math.hypot(cand.center().x()-vb.center().x(), cand.center().y()-vb.center().y())
                    # Card overlap matters most, then label overlap. Distance keeps
                    # the label visually attached to its own card.
                    score = card_penalty*12.0 + label_penalty*20.0 + distance*0.10
                    candidates.append((score, cand))

            chosen=min(candidates, key=lambda item:item[0])[1] if candidates else QRectF(vb.center().x()-label_w/2, vb.bottom()+gap, label_w, label_h)
            used.append(chosen)
            result.append(chosen)
        return result

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
        if card_id is not None and 0 <= card_id < len(self.cards) and self.cards[card_id].marked:
            p.setBrush(QColor(150, 12, 12, 42))
            p.setPen(QPen(QColor("#c63232"), max(2.0, rect.width()*.028)))
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), 5, 5)
            if rect.width() > 38:
                p.setBrush(QColor("#c63232")); p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(r.right()-8, r.top()+8), 3.2, 3.2)
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

        # Keep the original, restrained card-tilt feel. Only genuinely small
        # free-move cards are enlarged on hover so they remain readable.
        hover_scale = 1.035
        if self.free_move_enabled and rect.width() < 88.0:
            target_width = 104.0
            hover_scale = min(1.72, max(1.18, target_width / max(1.0, rect.width())))

        p.translate(c)
        # Horizontal motion is restored to the original amplitude. Vertical
        # response is only slightly stronger than before so up/down hovering is
        # easier to perceive without making the card wobble excessively.
        p.translate(0.0, dy * 2.2)
        p.scale(hover_scale, hover_scale)
        p.rotate(dx * 2.8)
        p.shear(dx * 0.055, -dy * 0.068)
        p.translate(-c)

    def _paint_slot_card(self, p, slot, slot_index, face_up):
        p.save()
        rot = self._slot_rotation(slot_index)
        if rot:
            c = slot.center(); p.translate(c); p.rotate(rot); p.translate(-c)
        self._apply_hover_transform(p, slot, slot_index)
        if slot_index == self.hovered_slot and slot_index not in self.flip_started_by_slot:
            shadow = slot.translated(self.hover_current.x()*9 + 5, self.hover_current.y()*9 + 9)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 105))
            p.drawRoundedRect(shadow, 6, 6)
        cid = self.selected_indices[slot_index]
        if face_up:
            self._paint_front(p, slot, cid, self.cards[cid].reversed)
        else:
            self._paint_physical_back(p, cid, slot, 0, 255, True)
        p.restore()

    def _paint_flip(self, p, rect, card_idx, reversed_card, phase, rotation=0.0):
        p.save()
        if rotation:
            c0 = rect.center(); p.translate(c0); p.rotate(rotation); p.translate(-c0)
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
        p.restore()

    def _deal_geometry(self, index, local_progress, slot):
        deck = self._deck_rect()
        q = self._ease_in_out(max(0.0, min(1.0, local_progress)))
        x = self._lerp(deck.left(), slot.left(), q)
        y = self._lerp(deck.top(), slot.top(), q) - math.sin(q*math.pi)*34.0
        w = self._lerp(deck.width(), slot.width(), q)
        h = self._lerp(deck.height(), slot.height(), q)
        # Rotate continuously into the real spread orientation. In the Celtic
        # Cross this makes the Challenge card visibly travel and turn through 90°
        # instead of teleporting sideways after it lands.
        target_rot = self._slot_rotation(index)
        rot = self._lerp(-4.0, target_rot, q)
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

    def _idle_card_hit(self, pos):
        order = self.deck_order if self.deck_order else list(range(len(self.cards)))
        for i in range(len(order)-1, -1, -1):
            cid = order[i]
            x, y, cw, ch, rot = self._idle_geometry(i, len(order))
            rect = QRectF(x-cw/2, y-ch/2, cw, ch)
            c = rect.center(); a = math.radians(-rot)
            dx, dy = pos.x()-c.x(), pos.y()-c.y()
            rx = dx*math.cos(a)-dy*math.sin(a)+c.x()
            ry = dx*math.sin(a)+dy*math.cos(a)+c.y()
            if rect.adjusted(-3,-3,3,3).contains(QPointF(rx,ry)):
                return cid
        return -1

    def mouseMoveEvent(self, event):
        pos = event.position()
        slots = self._result_slots() if self.state in ("await_reveal", "done") else []
        hit = -1
        if self.free_move_enabled and self.manual_drag_cid >= 0 and self.state in ("self_select", "fan_select"):
            r = QRectF(self.manual_drag_rect)
            self.manual_drag_rect = QRectF(pos.x()-self.manual_drag_offset.x(), pos.y()-self.manual_drag_offset.y(), r.width(), r.height())
            self.setCursor(Qt.ClosedHandCursor); self.update(); event.accept(); return
        if self.free_move_enabled and self.drag_slot >= 0 and self.state in ("await_reveal", "done"):
            slots_now = self._result_slots()
            if self.drag_slot < len(slots_now):
                r = QRectF(slots_now[self.drag_slot])
                new_left = pos.x() - self.drag_offset.x()
                new_top = pos.y() - self.drag_offset.y()
                new_left = max(-r.width()*0.75, min(self.width()-r.width()*0.25, new_left))
                new_top = max(-r.height()*0.75, min(self.height()-r.height()*0.25, new_top))
                if math.hypot(pos.x()-self.drag_start_pos.x(), pos.y()-self.drag_start_pos.y()) > 5.0:
                    self.drag_moved = True
                self.free_positions[self.drag_slot] = QRectF(new_left, new_top, r.width(), r.height())
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                event.accept()
                return
        if self.state in ("self_select", "fan_select"):
            now = time.perf_counter()
            if self.state == "self_select" and self._last_mouse_pos is not None and self._last_mouse_time > 0:
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
        # Paint order places later spread cards on top. Hit-test in reverse so
        # crossing cards (notably Celtic Cross card 2) remain independently usable.
        for i in range(len(slots) - 1, -1, -1):
            r = slots[i]
            if self._slot_contains(pos, r, i, 5.0):
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
        if event.button() == Qt.RightButton and self.mark_back_enabled:
            cid = -1
            if self.state in ("self_select", "fan_select"):
                cid = self._manual_card_hit(event.position())
            elif self.state == "idle":
                cid = self._idle_card_hit(event.position())
            elif self.state == "shuffled":
                order = self.active_order if self.active_order else self.deck_order
                if order and self._deck_rect().adjusted(-8,-8,8,8).contains(event.position()):
                    cid = order[-1]
            elif self.state in ("await_reveal", "done"):
                _slots = self._result_slots()
                for i in range(len(_slots) - 1, -1, -1):
                    r = _slots[i]
                    if i < len(self.selected_indices) and self._slot_contains(event.position(), r, i, 5.0):
                        cid = self.selected_indices[i]
                        break
            if cid >= 0:
                self.cards[cid].marked = not self.cards[cid].marked
                state_text = self._t("红色标记", "red mark") if self.cards[cid].marked else self._t("取消标记", "mark removed")
                self.animationStatus.emit(self._t(f"卡背标记 · {state_text}", f"Card-back marking · {state_text}"))
                self.update()
                event.accept()
                return

        if event.button() == Qt.LeftButton and self.state in ("self_select", "fan_select"):
            if len(self.selected_indices) < self.draw_count:
                cid = self._manual_card_hit(event.position())
                if cid >= 0 and cid not in self.selected_indices:
                    if self.free_move_enabled:
                        d = self.scatter_pose[cid]
                        src = QRectF(d["rect"] if self.state == "fan_select" else self._effective_scatter_rect(cid))
                        self.manual_drag_cid = cid
                        self.manual_drag_rect = src
                        self.manual_drag_rot = d["rot"]
                        self.manual_drag_offset = QPointF(event.position().x()-src.left(), event.position().y()-src.top())
                        self.setCursor(Qt.ClosedHandCursor); self.update(); event.accept(); return
                    slot = len(self.selected_indices)
                    self.selected_indices.append(cid)
                    self.selected_reversed.append(self.cards[cid].reversed)
                    self.revealed.append(False)
                    self.manual_take_started[slot] = time.perf_counter()
                    self.animationStatus.emit(self._t(f"已选择 {slot + 1} / {self.draw_count} 张", f"Selected {slot + 1} / {self.draw_count}"))
                    self._ensure_timer()
                    self.update()
            super().mousePressEvent(event)
            return

        if event.button() == Qt.LeftButton and self.state in ("await_reveal", "done"):
            pos = event.position()
            _slots = self._result_slots()
            for i in range(len(_slots) - 1, -1, -1):
                r = _slots[i]
                if self._slot_contains(pos, r, i, 5.0):
                    if self.free_move_enabled:
                        self.drag_slot = i
                        self.drag_offset = QPointF(pos.x()-r.left(), pos.y()-r.top())
                        self.drag_start_pos = QPointF(pos)
                        self.drag_moved = False
                        self.setCursor(Qt.ClosedHandCursor)
                        event.accept()
                        return
                    if i not in self.flip_started_by_slot:
                        self.flip_target_by_slot[i] = not self.revealed[i]
                        self.flip_started_by_slot[i] = time.perf_counter()
                        if self.state == "done": self.state = "await_reveal"
                        active = len(self.flip_started_by_slot)
                        self.animationStatus.emit(self._t(f"翻转第 {i + 1} 张牌 · {active} 张正在翻转", f"Flipping card {i + 1} · {active} flipping"))
                        self._ensure_timer(); self.update()
                    break
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.free_move_enabled and self.manual_drag_cid >= 0 and self.state in ("self_select", "fan_select"):
            cid=self.manual_drag_cid; self.manual_drag_cid=-1
            if len(self.selected_indices) < self.draw_count and cid not in self.selected_indices:
                slot=len(self.selected_indices); self.selected_indices.append(cid); self.selected_reversed.append(self.cards[cid].reversed); self.revealed.append(False)
                # Keep exactly where the user dropped the physical card.
                r=QRectF(self.manual_drag_rect); self.free_positions[slot]=r
                self.animationStatus.emit(self._t(f"已选择 {slot+1} / {self.draw_count} 张 · 已自由放置", f"Selected {slot+1} / {self.draw_count} · placed freely"))
                self._ensure_timer()
            self.setCursor(Qt.PointingHandCursor); self.update(); event.accept(); return
        if event.button() == Qt.LeftButton and self.free_move_enabled and self.drag_slot >= 0 and self.state in ("await_reveal", "done"):
            slot = self.drag_slot
            moved = self.drag_moved
            self.drag_slot = -1
            self.drag_moved = False
            self.setCursor(Qt.PointingHandCursor)
            if not moved and slot < len(self.revealed) and slot not in self.flip_started_by_slot:
                self.flip_target_by_slot[slot] = not self.revealed[slot]
                self.flip_started_by_slot[slot] = time.perf_counter()
                self.state = "await_reveal"
                self.animationStatus.emit(self._t(f"翻转第 {slot + 1} 张牌", f"Flipping card {slot + 1}"))
                self._ensure_timer()
            self.update(); event.accept(); return
        super().mouseReleaseEvent(event)

    def _showcase_rects(self):
        n = max(1, len(self.active_order))
        cols = 13 if n >= 70 else max(6, int(math.ceil(math.sqrt(n * 2.0))))
        rows = int(math.ceil(n / cols))
        gap = 7.0
        max_w = self.width() - 70.0
        max_h = self.height() * .70
        cw = min(62.0, (max_w - gap*(cols-1)) / cols)
        ch = cw * 1.58
        if rows * ch + gap*(rows-1) > max_h:
            ch = (max_h - gap*(rows-1)) / rows
            cw = ch / 1.58
        total_w = cols*cw + (cols-1)*gap
        total_h = rows*ch + (rows-1)*gap
        ox = (self.width() - total_w) * .5
        oy = max(28.0, (self.height()* .72 - total_h)*.5)
        rects=[]
        for i in range(n):
            r=i//cols; c=i%cols
            rects.append(QRectF(ox+c*(cw+gap), oy+r*(ch+gap), cw, ch))
        return rects

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), QColor("#070707"))
        now = time.perf_counter()
        if self.warning_text and now < self.warning_until:
            box = QRectF(max(12.0, self.width()-278.0), 14.0, 260.0, 36.0)
            p.setPen(QPen(QColor("#553232"), 1))
            p.setBrush(QColor(32, 15, 15, 235))
            p.drawRoundedRect(box, 8, 8)
            p.setPen(QColor("#d69494"))
            p.setFont(QFont("Segoe UI", 9, QFont.DemiBold))
            p.drawText(box.adjusted(10,0,-10,0), Qt.AlignVCenter|Qt.AlignRight, self.warning_text)
        p.setPen(QPen(QColor(38, 38, 38, 150), 1))
        floor = QRectF(self.width()*.12, self.height()*.67, self.width()*.76, self.height()*.20)
        p.drawEllipse(floor)

        now = time.perf_counter()
        elapsed = now - self.state_started

        if self.state in ("showcase_front", "showcase_flip"):
            rects = self._showcase_rects()
            for i, cid in enumerate(self.active_order):
                if i >= len(rects):
                    break
                r = rects[i]
                if self.state == "showcase_front":
                    self._paint_front(p, r, cid, self.cards[cid].reversed)
                else:
                    local = (elapsed - i*self.showcase_flip_gap) / max(.001, self.showcase_flip_card_duration)
                    if local <= 0:
                        self._paint_front(p, r, cid, self.cards[cid].reversed)
                    elif local >= 1:
                        self._paint_physical_back(p, cid, r, 0.0, 255, False)
                    else:
                        e = self._ease_in_out(local)
                        if e < .5:
                            w = max(1.0, r.width()*(1.0-e/.5))
                            rr = QRectF(r.center().x()-w/2, r.top(), w, r.height())
                            self._paint_front(p, rr, cid, self.cards[cid].reversed)
                        else:
                            w = max(1.0, r.width()*((e-.5)/.5))
                            rr = QRectF(r.center().x()-w/2, r.top(), w, r.height())
                            self._paint_physical_back(p, cid, rr, 0.0, 255, False)
            label = self._t("展示所有卡牌正面" if self.state == "showcase_front" else "翻转为卡背",
                            "SHOW ALL CARD FACES" if self.state == "showcase_front" else "FLIP TO CARD BACKS")
            p.setPen(QColor("#8a8a8a")); p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRectF(0, self.height()-32, self.width(), 22), Qt.AlignCenter, label)
            return

        if self.state == "recollect_idle":
            order = self.deck_order[:] if self.deck_order else list(range(len(self.cards)))
            deck = self._deck_rect()
            for i, cid in enumerate(order):
                x,y,cw,ch,rot0 = self._idle_geometry(i, len(order))
                src=QRectF(x-cw/2,y-ch/2,cw,ch)
                dst,_=self._stack_pose(deck,i,len(order),0.0,.035)
                local=(elapsed-i*self.recollect_idle_gap)/max(.001,self.recollect_duration)
                if local <= 0:
                    self._paint_physical_back(p,cid,src,rot0,245,False)
                elif local >= 1:
                    self._paint_physical_back(p,cid,dst,0.0,255,False)
                else:
                    e=self._ease_in_out(local)
                    r=QRectF(self._lerp(src.left(),dst.left(),e),self._lerp(src.top(),dst.top(),e)-math.sin(e*math.pi)*18.0,self._lerp(src.width(),dst.width(),e),self._lerp(src.height(),dst.height(),e))
                    self._paint_physical_back(p,cid,r,self._lerp(rot0,0.0,e),255,False)
            return

        if self.state == "recollect_results":
            deck=self._deck_rect()
            # Remaining deck moves from the parked tabletop pile back to center.
            remain=self.deck_order[:]
            q0=self._ease_in_out(min(1.0,elapsed/max(.001,self.recollect_duration)))
            parked=self._parked_deck_rect()
            for rank,cid in enumerate(remain):
                depth=rank/max(1,len(remain)-1)
                src=parked.translated((depth-.5)*5.5,(depth-.5)*14.0)
                dst,_=self._stack_pose(deck,rank,len(remain)+len(self.recollect_order_slots),0.0,.035)
                r=QRectF(self._lerp(src.left(),dst.left(),q0),self._lerp(src.top(),dst.top(),q0),self._lerp(src.width(),dst.width(),q0),self._lerp(src.height(),dst.height(),q0))
                self._paint_physical_back(p,cid,r,self._lerp(-13.0,0.0,q0),255,False)
            # Table cards return left-to-right, one after another, to the top.
            for seq,slot in enumerate(self.recollect_order_slots):
                if slot>=len(self.selected_indices): continue
                cid=self.selected_indices[slot]; src=self.recollect_slot_rects.get(slot,self._result_slots()[slot])
                dst,_=self._stack_pose(deck,len(remain)+seq,len(remain)+len(self.recollect_order_slots),0.0,.035)
                local=(elapsed-seq*self.recollect_gap)/max(.001,self.recollect_duration)
                if local<=0:
                    if slot < len(self.revealed) and self.revealed[slot]: self._paint_front(p,src,cid,self.cards[cid].reversed)
                    else: self._paint_physical_back(p,cid,src,self._slot_rotation(slot),255,True)
                else:
                    e=self._ease_in_out(min(1.0,local))
                    r=QRectF(self._lerp(src.left(),dst.left(),e),self._lerp(src.top(),dst.top(),e)-math.sin(e*math.pi)*24.0,self._lerp(src.width(),dst.width(),e),self._lerp(src.height(),dst.height(),e))
                    self._paint_physical_back(p,cid,r,self._lerp(self._slot_rotation(slot),0.0,e),255,False)
            return

        if self.state == "rain_split":
            deck=self._deck_rect(); q=self._ease_in_out(min(1.0,elapsed/max(.001,self.rain_split_duration)))
            for g,packet in enumerate(self.rain_packets):
                target_base=self._rain_group_rect(g)
                for rank,cid in enumerate(packet):
                    source_rank=self.scatter_order.index(cid)
                    src,_=self._stack_pose(deck,source_rank,len(self.scatter_order),0.0,.035)
                    dst,rot_t=self._stack_pose(target_base,rank,len(packet),((g%3)-1)*2.0,.08)
                    r=QRectF(self._lerp(src.left(),dst.left(),q),self._lerp(src.top(),dst.top(),q)-math.sin(q*math.pi)*(10+g%3*3),self._lerp(src.width(),dst.width(),q),self._lerp(src.height(),dst.height(),q))
                    self._paint_physical_back(p,cid,r,self._lerp(0.0,rot_t,q),255,False)
            self._paint_progress(p,q,f"CARD RAIN · SPLIT {len(self.rain_packets)} GROUPS")
            return

        if self.state == "idle":
            order = self.deck_order if self.deck_order else list(range(len(self.cards)))
            for i, cid in enumerate(order):
                x, y, cw, ch, rot = self._idle_geometry(i, len(order))
                self._paint_physical_back(p, cid, QRectF(x-cw/2, y-ch/2, cw, ch), rot, 235, False)
            p.setPen(QColor("#757575"))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(QRectF(0, self.height()-36, self.width(), 24), Qt.AlignCenter,
                       self._t("先洗牌，再按“抽取”；也可以直接从当前牌序抽取", "Shuffle first, then press Draw; or draw directly from the current order"))
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
                       self._t("洗牌完成 · 可再次洗牌、自己选择，或按“抽取”", "Shuffle complete · shuffle again, choose manually, or press Draw"))
            return

        if self.state == "gather":
            q = self._ease_in_out(min(1.0, elapsed / self.gather_duration))
            order = self.active_order
            deck = self._deck_rect()
            for i, cid in enumerate(order):
                if self.showcase_was_used:
                    grid = self._showcase_rects()
                    start = grid[i] if i < len(grid) else self._deck_rect()
                    rot = 0.0
                else:
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

        if self.state == "cut_spread":
            q = self._ease_in_out(min(1.0, elapsed / self.cut_spread_duration))
            deck = self._deck_rect()
            # Every card remains permanently tied to its packet.  The exact number
            # entered by the user is therefore visible as that many separate stacks.
            for pi, packet in enumerate(self.cut_packets):
                target_base = self._cut_group_rect(pi)
                for rank, cid in enumerate(packet):
                    source_rank = self.active_order.index(cid)
                    source, _ = self._stack_pose(deck, source_rank, len(self.active_order), 0.0, .035)
                    target, _ = self._stack_pose(target_base, rank, len(packet), ((pi % 3) - 1) * 1.8, .10)
                    r = QRectF(
                        self._lerp(source.left(), target.left(), q),
                        self._lerp(source.top(), target.top(), q) - math.sin(q * math.pi) * (8.0 + (pi % 4) * 2.0),
                        self._lerp(source.width(), target.width(), q),
                        self._lerp(source.height(), target.height(), q),
                    )
                    rot = self._lerp(0.0, ((pi % 3) - 1) * 1.8, q)
                    self._paint_physical_back(p, cid, r, rot, 255, False)
            self._paint_progress(p, q, f"CUT · {len(self.cut_packets)} GROUPS")
            return

        if self.state == "cut_restack":
            deck = self._deck_rect()
            total = self._cut_restack_total_duration()
            landed_packets = []
            moving_packets = []
            waiting_packets = []
            for seq, pi in enumerate(self.cut_packet_order):
                start_t = seq * self.cut_packet_gap
                local = (elapsed - start_t) / self.cut_packet_move_duration
                if local >= 1.0:
                    landed_packets.append((seq, pi, 1.0))
                elif local > 0.0:
                    moving_packets.append((seq, pi, local))
                else:
                    waiting_packets.append((seq, pi, 0.0))

            # Waiting packets stay fully visible in their numbered cut positions.
            for seq, pi, _ in waiting_packets:
                packet = self.cut_packets[pi]
                base = self._cut_group_rect(pi)
                for rank, cid in enumerate(packet):
                    r, rot = self._stack_pose(base, rank, len(packet), ((pi % 3) - 1) * 1.8, .10)
                    self._paint_physical_back(p, cid, r, rot, 255, False)

            # Already landed packets are painted in final physical deck order.
            for seq, pi, _ in landed_packets:
                packet = self.cut_packets[pi]
                for cid in packet:
                    target_rank = self.cut_target_rank[cid]
                    r, rot = self._stack_pose(deck, target_rank, len(self.cut_output), 0.0, .035)
                    self._paint_physical_back(p, cid, r, rot, 255, False)

            # The currently moving packet(s) travel as intact bound stacks.
            for seq, pi, local in moving_packets:
                e = self._ease_in_out(local)
                packet = self.cut_packets[pi]
                base = self._cut_group_rect(pi)
                for rank, cid in enumerate(packet):
                    src, src_rot = self._stack_pose(base, rank, len(packet), ((pi % 3) - 1) * 1.8, .10)
                    target_rank = self.cut_target_rank[cid]
                    dst, _ = self._stack_pose(deck, target_rank, len(self.cut_output), 0.0, .035)
                    r = QRectF(
                        self._lerp(src.left(), dst.left(), e),
                        self._lerp(src.top(), dst.top(), e) - math.sin(e * math.pi) * 24.0,
                        self._lerp(src.width(), dst.width(), e),
                        self._lerp(src.height(), dst.height(), e),
                    )
                    rot = self._lerp(src_rot, 0.0, e)
                    self._paint_physical_back(p, cid, r, rot, 255, False)

            prog = min(1.0, elapsed / max(.001, total))
            self._paint_progress(p, prog, f"RESTACK · {len(self.cut_packets)} GROUPS")
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
            self._paint_progress(p, q, f"RIFFLE SPLIT · {self.shuffle_round + 1}/{self.shuffle_rounds}")
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

        if self.state in ("fan_insert", "fan_select"):
            slots = self._result_slots()
            selected = set(self.selected_indices)
            if self.state == "fan_insert":
                for cid in self.scatter_order:
                    r, rot, q = self._fan_card_pose(cid, elapsed)
                    if q > 0:
                        self._paint_physical_back(p, cid, r, rot, 255, False)
                prog = min(1.0, elapsed / max(.001, self.fan_total_duration))
                self._paint_progress(p, prog, "FAN INSERT · 78 CARDS")
                return
            for cid in self.scatter_order:
                if cid not in selected and cid != self.manual_drag_cid:
                    d = self.scatter_pose[cid]
                    self._paint_physical_back(p, cid, d["rect"], d["rot"], 255, False)
            now2 = time.perf_counter()
            for slot, cid in enumerate(self.selected_indices):
                d = self.scatter_pose[cid]
                if slot in self.manual_take_started:
                    q = min(1.0, max(0.0, (now2-self.manual_take_started[slot])/self.manual_take_duration))
                    e = self._ease_in_out(q)
                    src=d["rect"]; dst=slots[slot]
                    r=QRectF(self._lerp(src.left(),dst.left(),e), self._lerp(src.top(),dst.top(),e)-math.sin(e*math.pi)*46.0, self._lerp(src.width(),dst.width(),e), self._lerp(src.height(),dst.height(),e))
                    self._paint_physical_back(p,cid,r,self._lerp(d["rot"],self._slot_rotation(slot),e),255,True)
                else:
                    self._paint_physical_back(p,cid,slots[slot],self._slot_rotation(slot),255,True)
            if self.manual_drag_cid >= 0:
                self._paint_physical_back(p,self.manual_drag_cid,self.manual_drag_rect,0.0,255,True)
            p.setPen(QColor("#808080")); p.setFont(QFont("Segoe UI",10))
            p.drawText(QRectF(0,self.height()-30,self.width(),22),Qt.AlignCenter,self._t(f"从扇形牌中选择 · {len(self.selected_indices)} / {self.draw_count}",f"Choose from the fan · {len(self.selected_indices)} / {self.draw_count}"))
            return

        if self.state in ("self_drop", "self_select", "manual_gather_rest","recollect_idle","recollect_results","rain_split"):
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
                    if cid not in selected and cid != self.manual_drag_cid:
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
                        rot = self._lerp(d["rot"], self._slot_rotation(slot), e)
                        self._paint_physical_back(p, cid, r, rot, 255, True)
                    else:
                        self._paint_physical_back(p, cid, slots[slot], self._slot_rotation(slot), 255, True)

                if self.manual_drag_cid >= 0:
                    self._paint_physical_back(p,self.manual_drag_cid,self.manual_drag_rect,0.0,255,True)
                p.setPen(QColor("#808080"))
                p.setFont(QFont("Segoe UI", 10))
                p.drawText(QRectF(0, self.height()-30, self.width(), 22), Qt.AlignCenter,
                           self._t(f"从散落的牌中选择 · {len(self.selected_indices)} / {self.draw_count}", f"Choose from the scattered cards · {len(self.selected_indices)} / {self.draw_count}"))
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
                self._paint_physical_back(p, cid, slots[slot], self._slot_rotation(slot), 255, True)
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
                target_face = self.flip_target_by_slot.get(i, True)
                phase = q if target_face else (1.0 - q)
                self._paint_flip(p, slot, cid, self.cards[cid].reversed, phase, self._slot_rotation(i))
            else:
                self._paint_slot_card(p, slot, i, self.revealed[i])

        if self.spread_slots and self.spread_labels:
            p.save()
            p.setFont(QFont("Segoe UI", 8))
            label_rects = self._spread_label_rects(slots)
            for i, rr in enumerate(label_rects):
                if i < len(self.spread_labels):
                    txt = self.spread_labels[i]
                    p.setPen(Qt.NoPen)
                    p.setBrush(QColor(5, 5, 5, 230))
                    p.drawRoundedRect(rr.adjusted(-3, -1, 3, 1), 4, 4)
                    p.setPen(QColor("#949494"))
                    p.drawText(rr, Qt.AlignCenter | Qt.TextWordWrap, txt)
            p.restore()

        p.setPen(QColor("#777777"))
        p.setFont(QFont("Segoe UI", 10))
        if self.free_move_enabled:
            message = self._t("拖动卡牌自由摆放 · 单击翻转正反面", "Drag cards anywhere · click to flip either side")
        else:
            message = self._t("全部卡牌已翻开", "All cards revealed") if self.state == "done" else self._t("点击卡牌翻开 · 移动鼠标查看 3D 视差", "Click a card to reveal · move the mouse for 3D parallax")
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

class SpreadPreview(QWidget):
    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        self.setFixedSize(84, 58)

    def _mini_card(self, p, cx, cy, w=12.0, h=18.0, rot=0.0):
        p.save()
        p.translate(cx, cy)
        p.rotate(rot)
        rect = QRectF(-w/2, -h/2, w, h)
        p.setPen(QPen(QColor('#858585'), 1.0))
        p.setBrush(QColor(16, 16, 16, 220))
        p.drawRoundedRect(rect, 2.2, 2.2)
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), Qt.transparent)
        box = QRectF(0.5, 0.5, self.width()-1.0, self.height()-1.0)
        p.setPen(QPen(QColor('#303030'), 1.0))
        p.setBrush(QColor('#121212'))
        p.drawRoundedRect(box, 10, 10)
        key = self.key

        if key == 'single':
            self._mini_card(p, 42, 29, 15, 23, 0)
        elif key == 'three':
            for x in (22, 42, 62):
                self._mini_card(p, x, 29, 12, 20, 0)
        elif key == 'horseshoe':
            pts = [(14,42,-12),(25,27,-8),(37,17,-4),(49,15,0),(61,17,4),(73,27,8),(84-14,42,12)]
            for x,y,r in pts:
                self._mini_card(p, x, y, 8.8, 13.2, r)
        elif key == 'celtic':
            self._mini_card(p, 28, 30, 10, 16, 0)
            self._mini_card(p, 28, 30, 10, 16, 90)
            self._mini_card(p, 28, 47, 10, 16, 0)
            self._mini_card(p, 14, 30, 10, 16, 0)
            self._mini_card(p, 28, 13, 10, 16, 0)
            self._mini_card(p, 42, 30, 10, 16, 0)
            for i,y in enumerate((46,34,22,10)):
                self._mini_card(p, 68, y, 10, 16, 0)
        elif key == 'relationship':
            for y in (14, 29, 44):
                self._mini_card(p, 20, y, 9.5, 14.5, 0)
                self._mini_card(p, 64, y, 9.5, 14.5, 0)
            self._mini_card(p, 42, 29, 10.5, 15.5, 0)
        elif key == 'choice':
            self._mini_card(p, 42, 32, 10, 15, 0)
            for x,y,r in ((28,23,-8),(18,15,-12),(10,8,-15),(56,23,8),(66,15,12),(74,8,15)):
                self._mini_card(p, x, y, 8.5, 12.8, r)
        elif key == 'timeline':
            for x in (12, 28, 42, 56, 72):
                self._mini_card(p, x, 29, 9.5, 15, 0)
        elif key == 'annual':
            self._mini_card(p, 42, 29, 10, 15, 0)
            for i in range(12):
                a = 2*math.pi*i/12.0 - math.pi/2
                x = 42 + math.cos(a)*22
                y = 29 + math.sin(a)*18
                self._mini_card(p, x, y, 6.5, 10, 0)
        elif key == 'tree':
            pts = [(42,10),(55,18),(29,18),(29,30),(55,30),(42,36),(29,43),(55,43),(42,50),(42,58)]
            for x,y in pts:
                if 0 <= y <= 58:
                    self._mini_card(p, x, y, 8.0, 11.8, 0)
        else:
            self._mini_card(p, 42, 29, 14, 22, 0)


class SpreadChoiceCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key, title, subtitle, parent=None):
        super().__init__(parent)
        self.key = key
        self.setObjectName('spreadChoiceCard')
        self.setProperty('checked', False)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(104)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 12, 12, 12)
        row.setSpacing(12)

        preview = SpreadPreview(key)
        row.addWidget(preview)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        title_lbl = QLabel(title)
        title_lbl.setObjectName('spreadChoiceTitle')
        title_lbl.setWordWrap(True)
        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName('spreadChoiceSubtitle')
        sub_lbl.setWordWrap(True)
        sub_lbl.setMinimumHeight(30)
        text_col.addWidget(title_lbl)
        text_col.addWidget(sub_lbl)
        row.addLayout(text_col, 1)

        arrow = QLabel('›')
        arrow.setObjectName('spreadChoiceArrow')
        row.addWidget(arrow)

    def setSelected(self, selected):
        self.setProperty('checked', bool(selected))
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
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

        title = QLabel(TXT("选择一种方式，看看随机性会给你什么。", "Choose a method and see what chance gives you."))
        title.setObjectName("heroTitle")
        title.setWordWrap(True)
        outer.addWidget(title)

        subtitle = QLabel(TXT("一个简洁的离线占卜工具。当前版本先完成塔罗牌界面，其他方式将逐步加入。", "A minimalist offline divination tool. Tarot is the current focus; more methods will be added over time."))
        subtitle.setObjectName("heroSubtitle")
        subtitle.setWordWrap(True)
        outer.addWidget(subtitle)
        outer.addSpacing(8)

        grid = QHBoxLayout()
        grid.setSpacing(12)
        tarot = MethodCard(TXT("塔罗牌", "Tarot"), TXT("78 张牌 · 正位 / 逆位", "78 cards · upright / reversed"), "◇", True)
        dice = MethodCard(TXT("骰子", "Dice"), TXT("开发中", "In development"), "□", False)
        rune = MethodCard(TXT("符文", "Runes"), TXT("开发中", "In development"), "△", False)
        coin = MethodCard(TXT("硬币", "Coin"), TXT("开发中", "In development"), "○", False)
        tarot.clicked.connect(lambda _: self.startTarot.emit())
        for card in (tarot, dice, rune, coin):
            grid.addWidget(card, 1)
        outer.addLayout(grid)
        outer.addStretch(1)

        quote = QLabel("SIMPLE TO ASK  /  MORE TO DISCOVER")
        quote.setObjectName("bottomMotto")
        outer.addWidget(quote)


class CustomTarotPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.selected_count = 3

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 20, 34, 20)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head_text = QVBoxLayout(); head_text.setSpacing(3)
        eyebrow = QLabel("TAROT / PHYSICAL DECK"); eyebrow.setObjectName("kicker")
        title = QLabel(TXT("自定义塔罗牌抽取", "Custom Tarot Draw")); title.setObjectName("pageTitle")
        desc = QLabel(TXT(
            f"当前版本 v{APP_VERSION} · 已加载卡牌：Rider–Waite–Smith（RWS）官方卡面",
            f"Current version v{APP_VERSION} · Loaded deck: Rider–Waite–Smith (RWS) official artwork"
        ))
        desc.setObjectName("pageDesc"); desc.setWordWrap(True)
        head_text.addWidget(eyebrow); head_text.addWidget(title); head_text.addWidget(desc)
        head.addLayout(head_text, 1)

        actions = QHBoxLayout(); actions.setSpacing(8)
        self.manual_button = QPushButton(TXT("自己选择", "Choose Cards")); self.manual_button.setObjectName("chipButton"); self.manual_button.setFixedSize(112, 42)
        self.shuffle_button = QPushButton(TXT("洗牌", "Shuffle")); self.shuffle_button.setObjectName("chipButton"); self.shuffle_button.setFixedSize(88, 42)
        self.stop_shuffle_button = QPushButton(TXT("终止洗牌", "Stop Shuffle")); self.stop_shuffle_button.setObjectName("dangerChipButton"); self.stop_shuffle_button.setFixedSize(104, 42); self.stop_shuffle_button.setEnabled(False)
        self.draw_button = QPushButton(TXT("抽取", "Draw")); self.draw_button.setObjectName("primaryButton"); self.draw_button.setFixedSize(92, 42)
        for b in (self.manual_button, self.shuffle_button, self.stop_shuffle_button, self.draw_button): b.setCursor(Qt.PointingHandCursor)
        actions.addWidget(self.manual_button); actions.addWidget(self.shuffle_button); actions.addWidget(self.stop_shuffle_button); actions.addWidget(self.draw_button)
        head.addLayout(actions); outer.addLayout(head)

        config = QFrame(); config.setObjectName("configPanel")
        panel = QVBoxLayout(config); panel.setContentsMargins(16, 10, 16, 10); panel.setSpacing(8)
        top = QHBoxLayout(); top.setSpacing(8)
        label_col = QVBoxLayout(); label_col.setSpacing(0)
        label = QLabel(TXT("抽牌数量", "Draw Count")); label.setObjectName("settingTitle")
        note = QLabel(TXT("1–8 张", "1–8 cards")); note.setObjectName("settingNote")
        label_col.addWidget(label); label_col.addWidget(note); top.addLayout(label_col); top.addSpacing(4)

        self.count_group = QButtonGroup(self); self.count_group.setExclusive(True)
        for n in range(1, 9):
            b = CountButton(n); b.setChecked(n == self.selected_count); self.count_group.addButton(b, n); top.addWidget(b)
        self.count_group.idClicked.connect(self._set_count)
        top.addStretch(1); panel.addLayout(top)

        options = QHBoxLayout(); options.setSpacing(9)
        settings_note = QLabel(TXT("洗牌 / 逆位 / 自己选择方式可在“设置”中调整", "Shuffle / reversed / manual-choice behavior is configured in Settings"))
        settings_note.setObjectName("settingNote"); options.addWidget(settings_note)
        self.showcase_check = QCheckBox(TXT("卡牌展示", "Card Showcase")); self.showcase_check.setObjectName("optionCheck"); self.showcase_check.setToolTip(TXT("洗牌前展示所有卡牌正面，再动画翻为背面。", "Show every card face before shuffling, then animate them face-down.")); options.addWidget(self.showcase_check)
        self.free_move_check = QCheckBox(TXT("自由移动", "Free Move")); self.free_move_check.setObjectName("optionCheck"); self.free_move_check.setToolTip(TXT("自己选择后可拖动已抽出的牌到任意位置；单击牌面可反复翻转正反面。", "After manual selection, drag drawn cards anywhere; click a card to flip freely between face and back.")); options.addWidget(self.free_move_check)
        options.addStretch(1)
        self.major_chip = MiniSwitch(TXT("仅大阿卡纳", "Major Arcana Only"), False)
        options.addWidget(self.major_chip); panel.addLayout(options)
        outer.addWidget(config)

        self.stage = TarotStage(); self.stage.configure_runtime(APP_SETTINGS.get("language","zh"), APP_SETTINGS.get("mark_back",False)); outer.addWidget(self.stage, 1)
        status_row = QHBoxLayout()
        self.status = QLabel(TXT("准备就绪 · 78 张牌已载入", "Ready · 78 cards loaded")); self.status.setObjectName("statusText")
        status_row.addWidget(self.status); status_row.addStretch(1)
        self.card_count_label = QLabel(f"RESOURCE / {len(self.stage.card_files)} CARDS"); self.card_count_label.setObjectName("muted"); status_row.addWidget(self.card_count_label)
        outer.addLayout(status_row)

        self.shuffle_button.clicked.connect(self._shuffle_clicked)
        self.stop_shuffle_button.clicked.connect(self._stop_shuffle_clicked)
        self.draw_button.clicked.connect(self._draw_clicked)
        self.manual_button.clicked.connect(self._manual_clicked)
        self.stage.animationStatus.connect(self.status.setText)
        self.stage.shuffleFinished.connect(self._shuffle_finished)
        self.stage.animationFinished.connect(self._finished)

    def _set_count(self, n): self.selected_count = n

    def _set_controls_enabled(self, enabled):
        for b in (self.shuffle_button,self.draw_button,self.manual_button): b.setEnabled(enabled)
        for b in self.count_group.buttons(): b.setEnabled(enabled)
        for w in (self.major_chip,self.showcase_check,self.free_move_check): w.setEnabled(enabled)
        running = self.stage.state in ("showcase_front","showcase_flip","gather","cut_spread","cut_restack","split","riffle","square")
        self.stop_shuffle_button.setEnabled(running)

    def _shuffle_clicked(self):
        if self.stage.state not in ("idle","done","shuffled","await_reveal"): return
        riffles = max(1, min(50, int(APP_SETTINGS.get("riffle_rounds", 3))))
        cuts = max(1, min(20, int(APP_SETTINGS.get("cut_groups", 2))))
        allow_reversed = bool(APP_SETTINGS.get("allow_reversed", True))
        self._set_controls_enabled(False)
        self.stage.configure_spread(None)
        if not self.stage.request_shuffle(allow_reversed, self.major_chip.isChecked(), riffles, cuts, self.showcase_check.isChecked()):
            self._set_controls_enabled(True)
        self.stop_shuffle_button.setEnabled(self.stage.state in ("showcase_front","showcase_flip","gather","cut_spread","cut_restack","split","riffle","square"))

    def _stop_shuffle_clicked(self):
        if self.stage.request_stop_shuffle(): self.stop_shuffle_button.setEnabled(False)

    def _shuffle_finished(self):
        self._set_controls_enabled(True)
        self.status.setText(TXT("洗牌完成 · 可以调整抽牌数量后按“抽取”", "Shuffle complete · adjust the draw count, then press Draw"))

    def _draw_clicked(self):
        if self.stage.state not in ("idle","done","shuffled","await_reveal"): return
        self._set_controls_enabled(False); self.stage.configure_spread(None)
        if not self.stage.request_draw(self.selected_count, self.major_chip.isChecked()):
            self._set_controls_enabled(True)

    def _manual_clicked(self):
        if self.stage.state not in ("idle","done","shuffled","await_reveal"): return
        self._set_controls_enabled(False)
        self.stage.configure_spread(None)
        mode = APP_SETTINGS.get("custom_manual_mode", "rain")
        if mode not in ("rain", "fan"): mode = "rain"
        if not self.stage.request_manual(self.selected_count,bool(APP_SETTINGS.get("allow_reversed", True)),mode, self.free_move_check.isChecked()):
            self._set_controls_enabled(True); self.status.setText(TXT("自己选择未能启动，请重试。", "Manual choice could not start. Please try again."))

    def _finished(self,names):
        self._set_controls_enabled(True); short=" · ".join(names[:4]); short += f" · +{len(names)-4}" if len(names)>4 else ""
        self.status.setText(TXT(f"抽取完成 · {short}",f"Draw complete · {short}"))


class ClassicTarotPage(QWidget):
    """Classic spreads use a two-step flow: choose a spread, then open its table."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_spread_key = "single"
        self.spread_cards = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(34, 20, 34, 20)
        outer.setSpacing(10)

        self.view_stack = QStackedWidget()
        outer.addWidget(self.view_stack, 1)

        # ---------- page 1: spread selection only ----------
        choose_page = QWidget()
        choose_outer = QVBoxLayout(choose_page)
        choose_outer.setContentsMargins(0, 0, 0, 0)
        choose_outer.setSpacing(14)

        kicker = QLabel("TAROT / CLASSIC SPREADS")
        kicker.setObjectName("kicker")
        choose_title = QLabel(TXT("选择经典牌阵", "Choose a Classic Spread"))
        choose_title.setObjectName("pageTitle")
        choose_desc = QLabel(TXT(
            "先选择牌阵。选择后才会进入独立牌桌页面；每个模块会直观展示牌阵的结构或特色。",
            "Choose a spread first. The reading table opens on a separate page only after selection; each module previews the spread's structure or signature."
        ))
        choose_desc.setObjectName("pageDesc")
        choose_desc.setWordWrap(True)
        choose_outer.addWidget(kicker)
        choose_outer.addWidget(choose_title)
        choose_outer.addWidget(choose_desc)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("spreadScroll")
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 6, 8, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)

        spread_info = [
            ("single", TXT("单张牌", "Single Card"), TXT("1 张 · 聚焦核心问题", "1 card · focus the core question")),
            ("three", TXT("三张牌阵", "Three-Card Spread"), TXT("过去 · 现在 · 未来", "Past · Present · Future")),
            ("horseshoe", TXT("马蹄铁牌阵", "Horseshoe"), TXT("7 张弧形 · 观察整体趋势", "7-card arc · see the overall flow")),
            ("celtic", TXT("凯尔特十字", "Celtic Cross"), TXT("10 张经典结构 · 复杂问题", "10-card classic layout · complex readings")),
            ("relationship", TXT("关系牌阵", "Relationship Spread"), TXT("双方状态 + 关系核心", "two people + relationship core")),
            ("choice", TXT("选择牌阵", "Choice / Two Paths"), TXT("A / B 两条路径对比", "compare Path A and Path B")),
            ("timeline", TXT("五张时间线", "Five-Card Timeline"), TXT("过去 → 现在 → 未来", "past → present → future")),
            ("annual", TXT("年度十三张", "13-Card Year Spread"), TXT("中央主题 + 12 个月", "year theme + 12 months")),
            ("tree", TXT("生命之树", "Tree of Life"), TXT("卡巴拉十个质点位置", "ten sephirot positions")),
        ]
        for i, (key, title_txt, sub_txt) in enumerate(spread_info):
            card = SpreadChoiceCard(key, title_txt, sub_txt)
            card.clicked.connect(self._enter_spread)
            self.spread_cards[key] = card
            grid.addWidget(card, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        scroll.setWidget(grid_host)
        choose_outer.addWidget(scroll, 1)
        self.view_stack.addWidget(choose_page)

        # ---------- page 2: reading table only ----------
        table_page = QWidget()
        table_outer = QVBoxLayout(table_page)
        table_outer.setContentsMargins(0, 0, 0, 0)
        table_outer.setSpacing(10)

        head = QHBoxLayout()
        text_col = QVBoxLayout(); text_col.setSpacing(3)
        top_line = QHBoxLayout(); top_line.setSpacing(8)
        self.back_button = QPushButton(TXT("‹ 选择牌阵", "‹ Choose Spread"))
        self.back_button.setObjectName("chipButton")
        self.back_button.setCursor(Qt.PointingHandCursor)
        self.back_button.setMinimumHeight(34)
        top_line.addWidget(self.back_button, 0, Qt.AlignLeft)
        top_line.addStretch(1)
        text_col.addLayout(top_line)
        reading_kicker = QLabel("TAROT / CLASSIC SPREAD / TABLE")
        reading_kicker.setObjectName("kicker")
        self.reading_title = QLabel()
        self.reading_title.setObjectName("pageTitle")
        self.reading_desc = QLabel()
        self.reading_desc.setObjectName("pageDesc")
        self.reading_desc.setWordWrap(True)
        text_col.addWidget(reading_kicker)
        text_col.addWidget(self.reading_title)
        text_col.addWidget(self.reading_desc)
        head.addLayout(text_col, 1)

        actions = QHBoxLayout(); actions.setSpacing(8)
        self.manual_button = QPushButton(TXT("自己选择", "Choose Cards")); self.manual_button.setObjectName("chipButton"); self.manual_button.setMinimumSize(112,42)
        self.shuffle_button = QPushButton(TXT("洗牌", "Shuffle")); self.shuffle_button.setObjectName("chipButton"); self.shuffle_button.setMinimumSize(88,42)
        self.stop_button = QPushButton(TXT("终止洗牌", "Stop Shuffle")); self.stop_button.setObjectName("dangerChipButton"); self.stop_button.setMinimumSize(104,42); self.stop_button.setEnabled(False)
        self.draw_button = QPushButton(TXT("抽取", "Draw")); self.draw_button.setObjectName("primaryButton"); self.draw_button.setMinimumSize(92,42)
        for b in (self.manual_button,self.shuffle_button,self.stop_button,self.draw_button):
            b.setCursor(Qt.PointingHandCursor)
            actions.addWidget(b)
        head.addLayout(actions)
        table_outer.addLayout(head)

        config = QFrame(); config.setObjectName("configPanel")
        panel = QVBoxLayout(config); panel.setContentsMargins(16,10,16,10); panel.setSpacing(8)
        row1 = QHBoxLayout(); row1.setSpacing(9)
        self.count_text = QLabel(); self.count_text.setObjectName("settingTitle"); row1.addWidget(self.count_text)
        settings_note = QLabel(TXT("经典牌阵的自己选择默认使用扇形展开；可在“设置”中修改", "Classic manual choice defaults to Fan Spread; change it in Settings"))
        settings_note.setObjectName("settingNote"); row1.addWidget(settings_note)
        row1.addStretch(1)
        self.showcase=QCheckBox(TXT("卡牌展示","Card Showcase")); self.showcase.setObjectName("optionCheck"); row1.addWidget(self.showcase)
        self.major=MiniSwitch(TXT("仅大阿卡纳","Major Arcana Only"),False); row1.addWidget(self.major)
        panel.addLayout(row1)
        table_outer.addWidget(config)

        self.stage=TarotStage()
        self.stage.configure_runtime(APP_SETTINGS.get("language","zh"), APP_SETTINGS.get("mark_back",False))
        table_outer.addWidget(self.stage,1)
        stat=QHBoxLayout()
        self.status=QLabel(TXT("牌阵已就绪。","Spread ready.")); self.status.setObjectName("statusText"); self.status.setWordWrap(True)
        stat.addWidget(self.status, 1)
        self.card_count=QLabel(f"RESOURCE / {len(self.stage.card_files)} CARDS"); self.card_count.setObjectName("muted"); stat.addWidget(self.card_count)
        table_outer.addLayout(stat)
        self.view_stack.addWidget(table_page)

        self.back_button.clicked.connect(self._back_to_selection)
        self.shuffle_button.clicked.connect(self._shuffle)
        self.stop_button.clicked.connect(self._stop)
        self.draw_button.clicked.connect(self._draw)
        self.manual_button.clicked.connect(self._manual)
        self.stage.animationStatus.connect(self.status.setText)
        self.stage.shuffleFinished.connect(self._shuffle_finished)
        self.stage.animationFinished.connect(self._finished)
        self.view_stack.setCurrentIndex(0)

    def _definition(self, key):
        # Positions follow conventional physical layouts. The Celtic Cross second
        # card shares the center but is rotated 90 degrees as the crossing card.
        defs = {
            "single": dict(count=1, title=TXT("单张牌", "Single Card"), desc=TXT("一张牌用于聚焦当前问题、每日提示或核心主题。","One card focuses the reading on the present question, daily message, or core theme."),
                slots=[(.50,.48,.105,.34,0)], labels=[TXT("核心","Core")]),
            "three": dict(count=3, title=TXT("三张牌阵", "Three-Card Spread"), desc=TXT("最常见的三张牌阵：过去－现在－未来。","The classic three-card line: Past – Present – Future."),
                slots=[(.30,.48,.09,.30,0),(.50,.48,.09,.30,0),(.70,.48,.09,.30,0)], labels=[TXT("过去","Past"),TXT("现在","Present"),TXT("未来","Future")]),
            "horseshoe": dict(count=7, title=TXT("马蹄铁牌阵", "Horseshoe"), desc=TXT("七张马蹄铁由左至右形成弧形，用于观察过去、现在、隐性影响、阻碍、外界、建议与结果。","Seven cards form a horseshoe arc for past, present, hidden influence, obstacles, environment, advice, and outcome."),
                slots=[(.18,.66,.067,.225,-11),(.29,.48,.067,.225,-7),(.40,.34,.067,.225,-3),(.50,.29,.067,.225,0),(.60,.34,.067,.225,3),(.71,.48,.067,.225,7),(.82,.66,.067,.225,11)],
                labels=[TXT("过去","Past"),TXT("现在","Present"),TXT("隐性影响","Hidden"),TXT("阻碍","Obstacle"),TXT("外界","Environment"),TXT("建议","Advice"),TXT("结果","Outcome")]),
            "celtic": dict(count=10, title=TXT("凯尔特十字", "Celtic Cross"), desc=TXT("经典凯尔特十字：中央十字描述问题本身，右侧四张牌形成纵列。","Traditional Celtic Cross: the central cross describes the situation and a four-card staff stands on the right."),
                slots=[(.40,.48,.062,.215,0),(.40,.48,.062,.215,90),(.40,.75,.062,.215,0),(.25,.48,.062,.215,0),(.40,.20,.062,.215,0),(.55,.48,.062,.215,0),(.78,.79,.062,.215,0),(.78,.59,.062,.215,0),(.78,.39,.062,.215,0),(.78,.19,.062,.215,0)],
                labels=[TXT("现状","Present"),TXT("挑战","Challenge"),TXT("根基","Foundation"),TXT("过去","Past"),TXT("可能","Possibility"),TXT("近期未来","Near Future"),TXT("自我","Self"),TXT("环境","Environment"),TXT("希望/恐惧","Hopes/Fears"),TXT("结果","Outcome")]),
            "relationship": dict(count=7, title=TXT("关系牌阵", "Relationship Spread"), desc=TXT("七张关系牌阵：左右两列分别代表双方，中央牌代表关系核心。","Seven-card relationship spread: two columns represent each person and the center card represents the relationship itself."),
                slots=[(.30,.24,.072,.24,0),(.30,.50,.072,.24,0),(.30,.76,.072,.24,0),(.50,.50,.072,.24,0),(.70,.24,.072,.24,0),(.70,.50,.072,.24,0),(.70,.76,.072,.24,0)],
                labels=[TXT("你·想法","You·Mind"),TXT("你·感受","You·Heart"),TXT("你·行动","You·Action"),TXT("关系核心","Bond"),TXT("对方·想法","Other·Mind"),TXT("对方·感受","Other·Heart"),TXT("对方·行动","Other·Action")]),
            "choice": dict(count=7, title=TXT("选择牌阵", "Choice / Two Paths"), desc=TXT("选择牌阵从中央现状牌分成左右两条路径，每条路径连续三张。","A two-path choice spread: one present card branches into three cards for Path A and three for Path B."),
                slots=[(.50,.50,.072,.24,0),(.38,.39,.072,.24,-6),(.27,.29,.072,.24,-9),(.17,.20,.072,.24,-12),(.62,.39,.072,.24,6),(.73,.29,.072,.24,9),(.83,.20,.072,.24,12)],
                labels=[TXT("当前","Present"),TXT("A·第一步","A·Step 1"),TXT("A·发展","A·Development"),TXT("A·结果","A·Outcome"),TXT("B·第一步","B·Step 1"),TXT("B·发展","B·Development"),TXT("B·结果","B·Outcome")]),
            "timeline": dict(count=5, title=TXT("五张时间线", "Five-Card Timeline"), desc=TXT("五张牌由左到右构成时间线，从远过去延伸到未来趋势。","Five cards form a left-to-right timeline from the deeper past to the future trend."),
                slots=[(.16,.50,.075,.25,0),(.33,.50,.075,.25,0),(.50,.50,.075,.25,0),(.67,.50,.075,.25,0),(.84,.50,.075,.25,0)], labels=[TXT("远过去","Distant Past"),TXT("近期过去","Recent Past"),TXT("现在","Present"),TXT("近期未来","Near Future"),TXT("趋势","Trend")]),
            "annual": dict(count=13, title=TXT("年度十三张", "13-Card Year Spread"), desc=TXT("十二张牌环绕中央年度主题牌；外圈按顺时针方向对应十二个月。","Twelve month cards circle a central yearly-theme card, arranged clockwise."),
                slots=[(.50,.50,.052,.18,0)] + [(.50+math.sin(2*math.pi*i/12)*.34,.50-math.cos(2*math.pi*i/12)*.34,.048,.165,0) for i in range(12)],
                labels=[TXT("年度主题","Year Theme")]+[TXT(f"{i}月",f"Month {i}") for i in range(1,13)]),
            "tree": dict(count=10, title=TXT("生命之树", "Tree of Life"), desc=TXT("十张牌按照卡巴拉生命之树的十个质点位置排列。","Ten cards follow the ten sephirot of the Kabbalistic Tree of Life."),
                slots=[(.50,.12,.058,.20,0),(.66,.25,.058,.20,0),(.34,.25,.058,.20,0),(.34,.43,.058,.20,0),(.66,.43,.058,.20,0),(.50,.54,.058,.20,0),(.34,.67,.058,.20,0),(.66,.67,.058,.20,0),(.50,.79,.058,.20,0),(.50,.93,.058,.20,0)],
                labels=["Kether","Chokmah","Binah","Chesed","Geburah","Tiphareth","Netzach","Hod","Yesod","Malkuth"]),
        }
        return defs[key]

    def _enter_spread(self, key):
        if key not in self.spread_cards:
            key = "single"
        self.current_spread_key = key
        d = self._definition(key)
        for spread_key, card in self.spread_cards.items():
            card.setSelected(spread_key == key)
        self.reading_title.setText(d["title"])
        self.reading_desc.setText(d["desc"])
        self.count_text.setText(TXT(f'{d["count"]} 张固定位置', f'{d["count"]} fixed positions'))
        self.stage.configure_spread(d["slots"], d["labels"])
        self.stage.configure_free_move(False)
        self.stage.draw_count = d["count"]
        # A new spread starts a fresh full physical deck but preserves marks bound
        # to PhysicalCard objects.
        self.stage.deck_order = list(range(len(self.stage.cards)))
        self.stage.active_order = self.stage.deck_order[:]
        self.stage.selected_indices = []
        self.stage.selected_reversed = []
        self.stage.revealed = []
        self.stage.flip_started_by_slot = {}
        self.stage.flip_target_by_slot = {}
        self.stage.state = "idle"
        self.stage.update()
        self.status.setText(TXT("牌阵已就绪 · 可洗牌、自动抽取或自己选择。","Spread ready · shuffle, draw automatically, or choose cards yourself."))
        self.view_stack.setCurrentIndex(1)

    def _back_to_selection(self):
        if self.stage.state in ("showcase_front","showcase_flip","gather","cut_spread","cut_restack","split","riffle","square","deal","self_drop","fan_insert","manual_gather_rest","recollect_idle","recollect_results","rain_split"):
            return
        self.view_stack.setCurrentIndex(0)

    def _set_controls(self, enabled):
        for w in (self.back_button,self.shuffle_button,self.draw_button,self.manual_button,self.showcase,self.major):
            w.setEnabled(enabled)
        running=self.stage.state in ("showcase_front","showcase_flip","gather","cut_spread","cut_restack","split","riffle","square")
        self.stop_button.setEnabled(running)

    def _shuffle(self):
        if self.stage.state not in ("idle","done","shuffled","await_reveal"): return
        d=self._definition(self.current_spread_key); self.stage.configure_spread(d["slots"],d["labels"])
        r=max(1,min(50,int(APP_SETTINGS.get("riffle_rounds",3))))
        c=max(1,min(20,int(APP_SETTINGS.get("cut_groups",2))))
        allow_reversed=bool(APP_SETTINGS.get("allow_reversed",True))
        self._set_controls(False)
        if not self.stage.request_shuffle(allow_reversed,self.major.isChecked(),r,c,self.showcase.isChecked()): self._set_controls(True)
        self.stop_button.setEnabled(self.stage.state in ("showcase_front","showcase_flip","gather","cut_spread","cut_restack","split","riffle","square"))

    def _stop(self):
        if self.stage.request_stop_shuffle(): self.stop_button.setEnabled(False)

    def _shuffle_finished(self):
        self._set_controls(True); self.status.setText(TXT("洗牌完成 · 可按“抽取”或“自己选择”。","Shuffle complete · press Draw or Choose Cards."))

    def _draw(self):
        if self.stage.state not in ("idle","done","shuffled","await_reveal"): return
        d=self._definition(self.current_spread_key); self.stage.configure_spread(d["slots"],d["labels"]); self.stage.configure_free_move(False); self._set_controls(False)
        if not self.stage.request_draw(d["count"],self.major.isChecked()): self._set_controls(True)

    def _manual(self):
        if self.stage.state not in ("idle","done","shuffled","await_reveal"): return
        d=self._definition(self.current_spread_key); self.stage.configure_spread(d["slots"],d["labels"]); self.stage.configure_free_move(False); self._set_controls(False)
        mode=APP_SETTINGS.get("default_manual_mode","fan")
        if mode not in ("rain","fan"): mode="fan"
        if not self.stage.request_manual(d["count"],bool(APP_SETTINGS.get("allow_reversed",True)),mode, False): self._set_controls(True)

    def _finished(self,names):
        self._set_controls(True); self.status.setText(TXT("牌阵抽取完成 · 可逐张翻牌。","Spread complete · reveal cards one by one."))


class TarotPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 18, 28, 18)
        outer.setSpacing(10)
        tabs = QHBoxLayout(); tabs.setSpacing(8)
        self.classic_btn = QPushButton(TXT("经典牌阵", "Classic Spreads"))
        self.custom_btn = QPushButton(TXT("自定义塔罗牌抽取", "Custom Tarot Draw"))
        self.classic_btn.setCheckable(True); self.custom_btn.setCheckable(True)
        self.classic_btn.setObjectName("tarotTab"); self.custom_btn.setObjectName("tarotTab")
        self.tab_group = QButtonGroup(self); self.tab_group.setExclusive(True)
        self.tab_group.addButton(self.classic_btn, 0); self.tab_group.addButton(self.custom_btn, 1)
        self.classic_btn.setChecked(True)
        tabs.addWidget(self.classic_btn); tabs.addWidget(self.custom_btn); tabs.addStretch(1)
        outer.addLayout(tabs)
        self.pages = QStackedWidget()
        self.classic = ClassicTarotPage()
        self.custom = CustomTarotPage()
        self.pages.addWidget(self.classic); self.pages.addWidget(self.custom)
        outer.addWidget(self.pages, 1)
        self.tab_group.idClicked.connect(self.pages.setCurrentIndex)



class ResponsiveSettingRow(QWidget):
    """A setting row that changes between horizontal and vertical layout.

    This avoids relying on fixed pixel heights. Text is allowed to wrap naturally,
    and controls move underneath the description when the available width becomes
    narrow.
    """
    def __init__(self, title_text, note_text, control, parent=None):
        super().__init__(parent)
        self.control = control
        self._vertical_mode = None

        self.row = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.row.setContentsMargins(6, 10, 6, 10)
        self.row.setSpacing(20)

        self.text_box = QWidget(self)
        self.text_layout = QVBoxLayout(self.text_box)
        self.text_layout.setContentsMargins(0, 0, 0, 0)
        self.text_layout.setSpacing(5)

        self.title_label = QLabel(title_text)
        self.title_label.setObjectName("settingTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.note_label = QLabel(note_text)
        self.note_label.setObjectName("settingNote")
        self.note_label.setWordWrap(True)
        self.note_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.text_layout.addWidget(self.title_label)
        self.text_layout.addWidget(self.note_label)

        self.control.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)

        self.row.addWidget(self.text_box, 1)
        self.row.addWidget(self.control, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._update_direction()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_direction()

    def _update_direction(self):
        # Use the row's actual live width, rather than a hard-coded window size.
        vertical = self.width() < 760
        if vertical == self._vertical_mode:
            return
        self._vertical_mode = vertical
        if vertical:
            self.row.setDirection(QBoxLayout.Direction.TopToBottom)
            self.row.setAlignment(self.control, Qt.AlignLeft | Qt.AlignTop)
        else:
            self.row.setDirection(QBoxLayout.Direction.LeftToRight)
            self.row.setAlignment(self.control, Qt.AlignRight | Qt.AlignVCenter)
        self.updateGeometry()


class SettingsPage(QWidget):
    settingsChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # The settings page is deliberately scrollable. Its content therefore keeps
        # its natural height at every resolution instead of being compressed until
        # labels overlap each other.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(scroll)

        content = QWidget()
        content.setObjectName("settingsContent")
        content.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(48, 38, 48, 42)
        layout.setSpacing(16)

        kicker = QLabel("5IMP1E 5ATEBOX / SETTINGS")
        kicker.setObjectName("kicker")
        title = QLabel(TXT("设置", "Settings"))
        title.setObjectName("pageTitle")
        desc = QLabel(TXT(
            "界面、塔罗洗牌、自己选择与实体卡牌设置。",
            "Interface, tarot shuffle, manual-choice, and physical-card settings."
        ))
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        desc.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout.addWidget(kicker)
        layout.addWidget(title)
        layout.addWidget(desc)

        def category(zh, en):
            lab = QLabel(TXT(zh, en))
            lab.setObjectName("settingCategory")
            lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            return lab

        def make_panel():
            panel = QFrame()
            panel.setObjectName("configPanel")
            panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            form = QVBoxLayout(panel)
            form.setContentsMargins(22, 10, 22, 10)
            form.setSpacing(0)
            return panel, form

        def divider(form):
            form.addWidget(AccentLine())

        # ---- Interface ----
        layout.addSpacing(4)
        layout.addWidget(category("界面", "INTERFACE"))
        interface_panel, interface_form = make_panel()

        self.language_combo = QComboBox()
        self.language_combo.setObjectName("settingCombo")
        self.language_combo.setMinimumWidth(220)
        self.language_combo.addItem(TXT("简体中文", "Chinese (Simplified)"), "zh")
        self.language_combo.addItem("English", "en")
        idx = self.language_combo.findData(APP_SETTINGS.get("language", "zh"))
        self.language_combo.setCurrentIndex(max(0, idx))

        interface_form.addWidget(ResponsiveSettingRow(
            TXT("语言", "Language"),
            TXT("切换整个程序的界面语言。", "Change the interface language for the entire app."),
            self.language_combo
        ))
        layout.addWidget(interface_panel)

        # ---- Tarot & Shuffle ----
        layout.addSpacing(4)
        layout.addWidget(category("塔罗牌与洗牌", "TAROT & SHUFFLE"))
        tarot_panel, tarot_form = make_panel()

        self.reverse_check = QCheckBox(TXT("启用逆位", "Enable reversed cards"))
        self.reverse_check.setChecked(bool(APP_SETTINGS.get("allow_reversed", True)))

        tarot_form.addWidget(ResponsiveSettingRow(
            TXT("允许逆位", "Allow Reversed Cards"),
            TXT(
                "默认开启。关闭后所有新一轮洗牌与自己选择都只使用正位。",
                "Enabled by default. When disabled, newly started shuffles and manual selections use upright cards only."
            ),
            self.reverse_check
        ))
        divider(tarot_form)

        shuffle_controls = QWidget()
        shuffle_controls.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        sc = QGridLayout(shuffle_controls)
        sc.setContentsMargins(0, 0, 0, 0)
        sc.setHorizontalSpacing(9)
        sc.setVerticalSpacing(7)

        riffle_label = QLabel(TXT("Riffle 次数", "Riffle Count"))
        riffle_label.setObjectName("settingNote")
        self.riffle_input = QLineEdit(str(APP_SETTINGS.get("riffle_rounds", 3)))
        self.riffle_input.setObjectName("numberInput")
        self.riffle_input.setValidator(QIntValidator(1, 50, self))
        self.riffle_input.setAlignment(Qt.AlignCenter)
        self.riffle_input.setFixedWidth(60)

        cut_label = QLabel(TXT("Cut 组数", "Cut Groups"))
        cut_label.setObjectName("settingNote")
        self.cut_input = QLineEdit(str(APP_SETTINGS.get("cut_groups", 2)))
        self.cut_input.setObjectName("numberInput")
        self.cut_input.setValidator(QIntValidator(1, 20, self))
        self.cut_input.setAlignment(Qt.AlignCenter)
        self.cut_input.setFixedWidth(60)

        sc.addWidget(riffle_label, 0, 0)
        sc.addWidget(self.riffle_input, 0, 1)
        sc.addWidget(cut_label, 1, 0)
        sc.addWidget(self.cut_input, 1, 1)

        tarot_form.addWidget(ResponsiveSettingRow(
            TXT("洗牌参数", "Shuffle Parameters"),
            TXT(
                "Riffle 可设置 1–50 次；Cut 可设置 1–20 组。所有塔罗牌页面共同使用这些数值。",
                "Riffle can be set from 1–50 rounds and Cut from 1–20 groups. These values are shared by all Tarot pages."
            ),
            shuffle_controls
        ))
        layout.addWidget(tarot_panel)

        # ---- Manual Choice ----
        layout.addSpacing(4)
        layout.addWidget(category("自己选择", "MANUAL CHOICE"))
        manual_panel, manual_form = make_panel()

        self.custom_mode_combo = QComboBox()
        self.custom_mode_combo.setObjectName("settingCombo")
        self.custom_mode_combo.setMinimumWidth(220)
        self.custom_mode_combo.addItem(TXT("牌雨", "Card Rain"), "rain")
        self.custom_mode_combo.addItem(TXT("扇形展开", "Fan Spread"), "fan")
        idx = self.custom_mode_combo.findData(APP_SETTINGS.get("custom_manual_mode", "rain"))
        self.custom_mode_combo.setCurrentIndex(max(0, idx))

        manual_form.addWidget(ResponsiveSettingRow(
            TXT("自定义塔罗牌抽取", "Custom Tarot Draw"),
            TXT(
                "仅控制“自定义塔罗牌抽取”页面中的自己选择展示方式。",
                "Controls the manual-choice presentation used only on the Custom Tarot Draw page."
            ),
            self.custom_mode_combo
        ))
        divider(manual_form)

        self.default_mode_combo = QComboBox()
        self.default_mode_combo.setObjectName("settingCombo")
        self.default_mode_combo.setMinimumWidth(220)
        self.default_mode_combo.addItem(TXT("扇形展开", "Fan Spread"), "fan")
        self.default_mode_combo.addItem(TXT("牌雨", "Card Rain"), "rain")
        idx = self.default_mode_combo.findData(APP_SETTINGS.get("default_manual_mode", "fan"))
        self.default_mode_combo.setCurrentIndex(max(0, idx))

        manual_form.addWidget(ResponsiveSettingRow(
            TXT("经典牌阵及其他模式", "Classic Spreads & Other Modes"),
            TXT(
                "默认使用扇形展开。以后新增的非自定义“自己选择”模式也使用此选项。",
                "Defaults to Fan Spread. Future non-custom manual-choice modes also use this option."
            ),
            self.default_mode_combo
        ))
        layout.addWidget(manual_panel)

        # ---- Physical Cards ----
        layout.addSpacing(4)
        layout.addWidget(category("实体卡牌", "PHYSICAL CARDS"))
        physical_panel, physical_form = make_panel()

        self.mark_check = QCheckBox(TXT(
            "启用卡背红色标记",
            "Enable red card-back marks"
        ))
        self.mark_check.setChecked(bool(APP_SETTINGS.get("mark_back", False)))

        physical_form.addWidget(ResponsiveSettingRow(
            TXT("卡背标记", "Card-back Marking"),
            TXT(
                "开启后右键可见卡牌，将该实体卡背标记为红色；再次右键取消。标记会跟随该实体牌通过 Cut、Riffle、抽取与翻牌。",
                "Right-click a visible card to mark that physical card's back red; right-click again to remove it. The mark follows the card through cuts, riffles, draws, and reveals."
            ),
            self.mark_check
        ))
        layout.addWidget(physical_panel)
        layout.addStretch(1)

        self.language_combo.currentIndexChanged.connect(self._changed)
        self.mark_check.toggled.connect(self._changed)
        self.reverse_check.toggled.connect(self._changed)
        self.custom_mode_combo.currentIndexChanged.connect(self._changed)
        self.default_mode_combo.currentIndexChanged.connect(self._changed)
        self.riffle_input.editingFinished.connect(self._changed)
        self.cut_input.editingFinished.connect(self._changed)

    def _changed(self, *args):
        APP_SETTINGS["language"] = self.language_combo.currentData()
        APP_SETTINGS["mark_back"] = self.mark_check.isChecked()
        APP_SETTINGS["allow_reversed"] = self.reverse_check.isChecked()
        APP_SETTINGS["custom_manual_mode"] = self.custom_mode_combo.currentData() or "rain"
        APP_SETTINGS["default_manual_mode"] = self.default_mode_combo.currentData() or "fan"

        try:
            riffles = int(self.riffle_input.text() or APP_SETTINGS.get("riffle_rounds", 3))
        except ValueError:
            riffles = 3
        try:
            cuts = int(self.cut_input.text() or APP_SETTINGS.get("cut_groups", 2))
        except ValueError:
            cuts = 2

        riffles = max(1, min(50, riffles))
        cuts = max(1, min(20, cuts))
        APP_SETTINGS["riffle_rounds"] = riffles
        APP_SETTINGS["cut_groups"] = cuts
        self.riffle_input.setText(str(riffles))
        self.cut_input.setText(str(cuts))
        self.settingsChanged.emit()


class PlaceholderPage(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 38, 48, 38)
        kicker = QLabel("5IMP1E 5ATEBOX")
        kicker.setObjectName("kicker")
        title_lbl = QLabel(title)
        title_lbl.setObjectName("pageTitle")
        desc = QLabel(TXT("此模块尚未启用。当前开发重点为塔罗牌界面与伪 3D 交互。", "This module is not enabled yet. Current development focuses on Tarot and pseudo-3D interaction."))
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
        self._base_window_min = QSize(1180, 760)
        self.setMinimumSize(self._base_window_min)
        self._language = APP_SETTINGS.get("language", "zh")
        self.ui_scale = 1.0
        self._build_ui()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

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
            (TXT("首页", "Home"), "⌂"),
            (TXT("塔罗牌", "Tarot"), "◇"),
            (TXT("骰子", "Dice"), "□"),
            (TXT("符文", "Runes"), "△"),
            (TXT("硬币", "Coin"), "○"),
            (TXT("抽签", "Lots"), "│"),
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

        card_games = NavButton(TXT("卡牌游戏", "Card Games"), "▤")
        settings = NavButton(TXT("设置", "Settings"), "⚙")
        about = NavButton(TXT("关于", "About"), "·")
        self.nav_group.addButton(card_games, 6)
        self.nav_group.addButton(settings, 7)
        self.nav_group.addButton(about, 8)
        self.nav_buttons.extend([card_games, settings, about])
        sl.addWidget(card_games)
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
        self.section_title = QLabel(TXT("首页", "Home"))
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
        self.stack.addWidget(PlaceholderPage(TXT("骰子", "Dice")))
        self.stack.addWidget(PlaceholderPage(TXT("符文", "Runes")))
        self.stack.addWidget(PlaceholderPage(TXT("硬币", "Coin")))
        self.stack.addWidget(PlaceholderPage(TXT("抽签", "Lots")))
        self.stack.addWidget(PlaceholderPage(TXT("卡牌游戏", "Card Games")))
        self.settings_page = SettingsPage()
        self.settings_page.settingsChanged.connect(self._settings_changed)
        self.stack.addWidget(self.settings_page)
        self.stack.addWidget(PlaceholderPage(TXT("关于", "About")))
        content_layout.addWidget(self.stack, 1)

        main.addWidget(side)
        main.addWidget(content, 1)

        self.nav_group.idClicked.connect(self._navigate)
        self._capture_zoom_baselines()
        self._apply_zoom()

    def _capture_zoom_baselines(self):
        """Remember the unscaled geometry of fixed/minimum-sized widgets."""
        for w in self.findChildren(QWidget):
            w._zoom_base_min = QSize(w.minimumSize())
            w._zoom_base_max = QSize(w.maximumSize())

    @staticmethod
    def _scaled_size(base, scale, is_max=False):
        w, h = base.width(), base.height()
        # Qt's default maximum is effectively infinite and should stay that way.
        if is_max:
            sw = w if w >= 1000000 else max(0, int(round(w * scale)))
            sh = h if h >= 1000000 else max(0, int(round(h * scale)))
        else:
            sw = max(0, int(round(w * scale)))
            sh = max(0, int(round(h * scale)))
        return QSize(sw, sh)

    def _apply_zoom(self):
        scale = max(.70, min(1.60, float(self.ui_scale)))
        self.ui_scale = scale
        self.setStyleSheet(_scaled_stylesheet(STYLE, scale))
        for w in self.findChildren(QWidget):
            # Interface zoom must never resize the physical cards/table renderer.
            # TarotStage keeps its original geometry; only surrounding UI scales.
            if isinstance(w, TarotStage):
                continue
            bmin = getattr(w, "_zoom_base_min", None)
            bmax = getattr(w, "_zoom_base_max", None)
            if bmin is not None:
                w.setMinimumSize(self._scaled_size(bmin, scale, False))
            if bmax is not None:
                w.setMaximumSize(self._scaled_size(bmax, scale, True))
        self.setMinimumSize(self._base_window_min)
        self.updateGeometry()
        self.update()

    def _set_zoom(self, value):
        value = round(max(.70, min(1.60, value)) * 10.0) / 10.0
        if abs(value - self.ui_scale) < .001:
            return
        self.ui_scale = value
        self._apply_zoom()

    def eventFilter(self, obj, event):
        # Ctrl + wheel zooms regardless of which child widget is under the cursor.
        if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
            delta = event.angleDelta().y()
            if delta:
                self._set_zoom(self.ui_scale + (.10 if delta > 0 else -.10))
                return True
        if event.type() == QEvent.KeyPress:
            key = event.key()
            ctrl = bool(event.modifiers() & Qt.ControlModifier)
            focus = QApplication.focusWidget()
            # Plus/minus also work directly when the user is not typing in an input;
            # Ctrl+Plus/Minus always work. Ctrl+0 resets to 100%.
            allowed = ctrl or not isinstance(focus, QLineEdit)
            if allowed and key in (Qt.Key_Plus, Qt.Key_Equal):
                self._set_zoom(self.ui_scale + .10)
                return True
            if allowed and key in (Qt.Key_Minus, Qt.Key_Underscore):
                self._set_zoom(self.ui_scale - .10)
                return True
            if ctrl and key == Qt.Key_0:
                self._set_zoom(1.0)
                return True
        return super().eventFilter(obj, event)

    def _navigate(self, idx):
        self.stack.setCurrentIndex(idx)
        names = [TXT("首页", "Home"), TXT("塔罗牌", "Tarot"), TXT("骰子", "Dice"), TXT("符文", "Runes"), TXT("硬币", "Coin"), TXT("抽签", "Lots"), TXT("卡牌游戏", "Card Games"), TXT("设置", "Settings"), TXT("关于", "About")]
        self.section_title.setText(names[idx])
        btn = self.nav_group.button(idx)
        if btn:
            btn.setChecked(True)

    def _settings_changed(self):
        idx = self.stack.currentIndex() if hasattr(self, "stack") else 7
        new_lang = APP_SETTINGS.get("language", "zh")
        if new_lang == self._language:
            try:
                mark_back = APP_SETTINGS.get("mark_back", False)
                self.tarot.custom.stage.configure_runtime(mark_back=mark_back)
                self.tarot.classic.stage.configure_runtime(mark_back=mark_back)
            except Exception:
                pass
            return
        self._language = new_lang
        old_group = getattr(self, "nav_group", None)
        if old_group is not None:
            old_group.deleteLater()
        self._build_ui()
        self._navigate(min(idx, self.stack.count()-1))


STYLE = r"""
* { outline: none; }
QMainWindow, QWidget#root, QStackedWidget#stack {
    background: #070707;
}
QWidget {
    background: transparent;
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
QScrollArea#spreadScroll, QScrollArea#spreadScroll > QWidget > QWidget {
    background: transparent;
    border: none;
}
QLabel {
    background: transparent;
}
QFrame#spreadChoiceCard {
    background: #0d0d0d;
    border: 1px solid #252525;
    border-radius: 13px;
}
QFrame#spreadChoiceCard:hover {
    background: #111111;
    border-color: #383838;
}
QFrame#spreadChoiceCard[checked="true"] {
    background: #171717;
    border-color: #474747;
}
QLabel#spreadChoiceTitle {
    color: #e4e4e4;
    font-size: 13px;
    font-weight: 600;
}
QLabel#spreadChoiceSubtitle, QLabel#spreadChoiceArrow {
    color: #707070;
    font-size: 11px;
}
QLabel#pageTitle {
    color: #f2f2f2;
    font-size: 30px;
    font-weight: 600;
}
QScrollArea#settingsScroll { background: transparent; border: none; }
QWidget#settingsContent { background: #070707; }
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
QLabel#settingCategory {
    color: #7f7f7f;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 2px;
    padding-top: 4px;
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
QPushButton#dangerChipButton {
    background: #121010;
    color: #a9a1a1;
    border: 1px solid #342828;
    border-radius: 10px;
    padding: 0 14px;
}
QPushButton#dangerChipButton:hover { color: #ededed; border-color: #5a3b3b; }
QPushButton#dangerChipButton:disabled { color: #4b4545; border-color: #242020; background: #0d0c0c; }
QLineEdit#numberInput {
    background: #0c0c0c;
    color: #e2e2e2;
    border: 1px solid #303030;
    border-radius: 8px;
    padding: 2px 6px;
    selection-background-color: #303030;
}
QLineEdit#numberInput:focus { border-color: #666666; }
QLineEdit#numberInput:disabled { color: #555555; border-color: #202020; }

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

QPushButton#tarotTab {
    color: #777; background: #0d0d0d; border: 1px solid #222; border-radius: 9px;
    padding: 8px 16px; min-height: 24px;
}
QPushButton#tarotTab:hover { color: #ddd; background: #121212; }
QPushButton#tarotTab:checked { color: #f0f0f0; background: #171717; border-color: #3a3a3a; }
QCheckBox#optionCheck { color: #aaa; spacing: 7px; }
QCheckBox#optionCheck::indicator, QCheckBox::indicator { width: 16px; height: 16px; }
QComboBox#settingCombo { background: #111; color: #ddd; border: 1px solid #303030; border-radius: 8px; padding: 7px 12px; min-width: 150px; }
"""


def _scaled_stylesheet(style, scale):
    """Scale pixel dimensions in the Qt stylesheet while preserving the design."""
    def repl(match):
        value = float(match.group(1))
        scaled = max(1, int(round(value * scale))) if value > 0 else 0
        return f"{scaled}px"
    return re.sub(r"(?<![\w.])(\d+(?:\.\d+)?)px", repl, style)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
