import sys
import math
import random
import time
import re
import os
import io
import json
import shutil
import zipfile
import wave
import hashlib
from pathlib import Path

from PySide6.QtCore import Qt, QRectF, QPointF, Signal, QSize, QTimer, QEvent, QUrl, QSettings
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont, QPainterPath, QPixmap, QIntValidator, QMatrix4x4, QVector3D, QQuaternion, QImage, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QPushButton, QFrame, QButtonGroup, QStackedWidget, QSizePolicy,
    QGraphicsDropShadowEffect, QLineEdit, QCheckBox, QComboBox, QGridLayout, QScrollArea, QBoxLayout, QProgressBar, QSlider, QFontComboBox, QSpinBox, QColorDialog
)

from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

try:
    from mutagen import File as MutagenFile
    MUTAGEN_AVAILABLE = True
except Exception:
    MutagenFile = None
    MUTAGEN_AVAILABLE = False

try:
    import numpy as np
    import pyqtgraph.opengl as gl
    from pyqtgraph.opengl.GLGraphicsItem import GLGraphicsItem
    from OpenGL import GL
    OPENGL_3D_AVAILABLE = True
    OPENGL_3D_ERROR = ""
except Exception as exc:
    np = None
    gl = None
    GLGraphicsItem = object
    GL = None
    OPENGL_3D_AVAILABLE = False
    OPENGL_3D_ERROR = str(exc)

try:
    import culverin
    JOLT_AVAILABLE = True
    JOLT_ERROR = ""
except Exception as exc:
    culverin = None
    JOLT_AVAILABLE = False
    JOLT_ERROR = str(exc)

APP_NAME = "5imp1e 5atebox"
APP_VERSION = "0.16.4"
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
    "hover_preview_hotkey": "V",
}

def TXT(zh, en):
    return en if APP_SETTINGS.get("language") == "en" else zh

HELD_KEYS = set()

def qt_key_name(key):
    if Qt.Key_A <= key <= Qt.Key_Z:
        return chr(ord("A") + int(key - Qt.Key_A))
    if Qt.Key_0 <= key <= Qt.Key_9:
        return chr(ord("0") + int(key - Qt.Key_0))
    special = {
        Qt.Key_Space: "SPACE",
        Qt.Key_Tab: "TAB",
        Qt.Key_Backspace: "BACKSPACE",
        Qt.Key_Return: "ENTER",
        Qt.Key_Enter: "ENTER",
        Qt.Key_Escape: "ESC",
        Qt.Key_Left: "LEFT",
        Qt.Key_Right: "RIGHT",
        Qt.Key_Up: "UP",
        Qt.Key_Down: "DOWN",
    }
    return special.get(key, "")

BASE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = BASE_DIR / "resource"
# NOTE: when an EXE build is introduced, this path abstraction is the only place
# that needs to change for the music library location.
MUSIC_LIBRARY_DIR = RESOURCE_DIR / "music_lyrics"


if OPENGL_3D_AVAILABLE:
    class TexturedFaceSetItem(GLGraphicsItem):
        def __init__(self, face_entries, parentItem=None):
            super().__init__(parentItem=parentItem)
            self.face_entries = face_entries
            self.texture_ids = []
            self._textures_ready = False
            self.setGLOptions("translucent")

        def _to_bytes(self, image):
            ptr = image.bits()
            size = image.sizeInBytes()
            try:
                ptr.setsize(size)
                return bytes(ptr)
            except Exception:
                try:
                    return bytes(ptr[:size])
                except Exception:
                    return ptr.asstring(size)

        def _ensure_textures(self):
            if self._textures_ready:
                return
            self.texture_ids = []
            for entry in self.face_entries:
                img = entry["image"].convertToFormat(QImage.Format_RGBA8888)
                tex_id = GL.glGenTextures(1)
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
                GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
                GL.glTexImage2D(
                    GL.GL_TEXTURE_2D, 0, GL.GL_RGBA,
                    img.width(), img.height(), 0,
                    GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, self._to_bytes(img)
                )
                self.texture_ids.append(tex_id)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            self._textures_ready = True

        def paint(self):
            self._ensure_textures()
            self.setupGLState()
            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
            GL.glEnable(GL.GL_TEXTURE_2D)
            GL.glDisable(GL.GL_CULL_FACE)
            for tex_id, entry in zip(self.texture_ids, self.face_entries):
                GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
                verts = entry["verts"]
                uvs = entry["uvs"]
                GL.glBegin(GL.GL_TRIANGLE_FAN)
                for (u, v), (x, y, z) in zip(uvs, verts):
                    GL.glTexCoord2f(float(u), float(v))
                    GL.glVertex3f(float(x), float(y), float(z))
                GL.glEnd()
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            GL.glDisable(GL.GL_TEXTURE_2D)
            GL.glEnable(GL.GL_CULL_FACE)


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
        # Small-card magnification is deliberate: it appears only while the
        # configured preview hotkey is held over the card.
        self.hover_preview_slot = -1
        self.hover_preview_started = 0.0
        self.hover_preview_active = False

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

        # Small-card preview activates immediately while the configured hotkey is held.
        preview_waiting = False
        preview_key = str(APP_SETTINGS.get("hover_preview_hotkey", "V")).upper()
        preview_key_down = preview_key in HELD_KEYS
        valid_preview = False
        if self.free_move_enabled and self.hovered_slot >= 0 and self.state in ("await_reveal", "done"):
            slots = self._result_slots()
            if self.hovered_slot < len(slots) and slots[self.hovered_slot].width() < 88.0:
                valid_preview = True

        if valid_preview and preview_key_down:
            self.hover_preview_slot = self.hovered_slot
            self.hover_preview_started = now
            self.hover_preview_active = True
        else:
            self.hover_preview_slot = -1
            self.hover_preview_started = 0.0
            self.hover_preview_active = False

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
        if (self.free_move_enabled and rect.width() < 88.0
                and self.hover_preview_active and slot_index == self.hover_preview_slot):
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
    startDice = Signal()

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
        dice = MethodCard(TXT("骰子", "Dice"), TXT("3D 骰子 · 弹跳动画", "3D die · bounce animation"), "□", True)
        rune = MethodCard(TXT("符文", "Runes"), TXT("开发中", "In development"), "△", False)
        coin = MethodCard(TXT("硬币", "Coin"), TXT("开发中", "In development"), "○", False)
        tarot.clicked.connect(lambda _: self.startTarot.emit())
        dice.clicked.connect(lambda _: self.startDice.emit())
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
            "选择一个牌阵开始占卜。",
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



class HotkeyCaptureButton(QPushButton):
    hotkeyChanged = Signal(str)

    def __init__(self, key_name="V", parent=None):
        super().__init__(parent)
        self.key_name = (key_name or "V").upper()
        self.recording = False
        self.setObjectName("chipButton")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(190)
        self.setMinimumHeight(38)
        self._refresh_text()
        self.clicked.connect(self._begin_capture)

    def _refresh_text(self):
        if self.recording:
            self.setText(TXT("请按下新的按键…", "Press a new key…"))
        else:
            self.setText(TXT(f"当前：{self.key_name} · 点击重新绑定",
                             f"Current: {self.key_name} · Click to rebind"))

    def _begin_capture(self):
        self.recording = True
        self._refresh_text()
        self.setFocus(Qt.OtherFocusReason)

    def keyPressEvent(self, event):
        if not self.recording:
            return super().keyPressEvent(event)
        if event.isAutoRepeat():
            event.accept()
            return
        name = qt_key_name(event.key())
        if not name:
            self.recording = False
            self._refresh_text()
            event.accept()
            return
        self.key_name = name
        self.recording = False
        self._refresh_text()
        self.hotkeyChanged.emit(name)
        event.accept()


REFERENCE_DICE_MODELS = {'12': {'v': [[0.101398, -0.23705, -0.153374], [-0.101399, 0.237049, 0.153382], [-0.011875, 0.149964, -0.23962], [0.011873, -0.149964, 0.239628], [-0.238861, -0.074595, -0.03484], [0.221797, 0.09649, 0.055271], [0.215966, 0.012957, -0.181193], [0.230571, -0.147803, 0.070422], [-0.068248, -0.247781, 0.022879], [-0.177113, 0.025867, 0.230024], [-0.191723, 0.209934, -0.064128], [0.068209, 0.079548, 0.233807], [-0.112078, -0.075447, -0.233724], [0.055209, 0.247871, -0.051671], [0.109626, 0.243344, 0.135447], [-0.157419, -0.139922, 0.143427], [-0.229745, 0.080005, 0.060715], [-0.079437, -0.23275, -0.156394], [-0.207247, 0.055065, -0.152758], [0.08709, -0.246619, 0.106003], [0.068247, -0.009013, -0.273681], [0.162547, -0.056239, 0.188114], [0.219151, 0.149884, -0.095646], [0.262423, -0.035732, -0.035363], [-0.273231, -0.054527, 0.109619], [0.111873, 0.111307, -0.190978], [0.196632, 0.073193, 0.187647], [0.175957, -0.127862, -0.136168], [-0.055201, 0.246973, 0.021622], [-0.007944, -0.029623, 0.289833], [-0.16976, -0.186256, -0.072482], [-0.029798, 0.246897, -0.151986], [0.273433, -0.027111, 0.097173], [-0.285133, 0.043597, -0.044955], [0.074395, -0.120038, -0.219944], [-0.078615, 0.133023, 0.213721], [0.166165, -0.239728, 0.014709], [-0.041723, 0.039709, -0.286308], [-0.019419, -0.241524, 0.163054], [0.171292, 0.234194, -0.027551], [-0.165643, 0.199948, 0.063073], [-0.118898, 0.171567, -0.149087], [-0.19144, -0.21193, 0.058128], [-0.085418, -0.09114, 0.220487], [0.007049, 0.200254, 0.176762], [0.041428, -0.247202, 0.003765], [0.277765, 0.054487, -0.097573], [0.161826, -0.057724, -0.245652], [-0.183827, 0.099272, 0.154853], [0.214108, -0.128323, -0.034837], [0.095646, 0.202442, -0.145815], [0.105724, -0.011667, 0.2614], [-0.158931, -0.148079, -0.180152], [-0.204337, -0.04918, -0.201914], [0.021921, 0.246946, 0.084203], [-0.02334, -0.115861, -0.21798], [0.139018, 0.147889, 0.196076], [-0.129261, 0.081996, -0.198472], [-0.003369, -0.246745, -0.102467], [-0.228543, 0.137459, -0.016358], [0.162312, -0.139858, 0.134543], [0.17975, 0.177193, 0.053908], [0.096344, 0.247628, 0.028145], [-0.058668, 0.247354, -0.068673], [-0.095654, -0.20244, 0.145823], [0.165645, -0.199949, -0.063056], [0.264155, 0.038639, 0.011101], [0.189083, 0.095366, -0.149666], [-0.191271, -0.039285, 0.182396], [-0.047777, 0.035664, 0.260464], [-0.224542, -0.139955, 0.037259], [-0.23263, -0.037861, -0.125872], [-0.205317, 0.138023, -0.100938], [-0.007045, -0.200256, -0.17675], [-0.036087, -0.043363, -0.253046], [-0.131171, -0.247015, -0.037678], [0.232625, 0.037859, 0.125886], [0.107104, -0.110897, 0.197706], [-0.006148, 0.098095, 0.228025], [-0.131467, 0.246501, -0.034437], [0.151409, 0.048674, 0.244236], [0.269838, -0.071921, 0.035306], [0.049723, -0.074309, 0.266911], [-0.117589, 0.071228, 0.245629], [0.027258, 0.209282, -0.186249], [0.159798, 0.209517, -0.098986], [0.024133, 0.074716, -0.270309], [0.170519, -0.2077, 0.083773], [0.04658, -0.208871, 0.182862], [-0.281869, -0.012373, 0.006253], [0.188313, 0.128487, 0.11495], [-0.105698, 0.01166, -0.261404], [-0.22046, 0.035359, 0.146774], [0.139507, -0.146852, -0.197328], [-0.12744, -0.031677, 0.228933], [-0.161796, 0.162044, 0.125852], [-0.046127, 0.246373, 0.099832], [-0.186832, -0.129009, -0.119238], [-0.100567, 0.233247, -0.122977], [-0.143303, 0.146412, 0.194092], [0.096062, -0.246569, -0.037012], [-0.135673, -0.247339, 0.073209], [-0.132357, -0.23325, -0.114091], [0.028969, -0.074673, -0.240479], [-0.063313, 0.162213, -0.199706], [-0.022795, -0.247804, 0.070961], [-0.266362, 0.048348, -0.107966], [-0.139244, -0.093285, 0.183936], [0.045436, 0.123912, -0.226691], [-0.196287, 0.146859, 0.056187], [-0.16136, -0.031635, -0.241215], [-0.083195, -0.123891, -0.21124], [-0.032392, -0.247251, -0.049559], [0.09185, 0.03911, -0.247844], [-0.074221, -0.24752, 0.113437], [-0.081058, -0.01272, 0.270022], [0.074254, 0.24752, -0.113408], [-0.219155, -0.149867, 0.095677], [0.15215, 0.233694, 0.043357], [-0.262426, 0.035731, 0.03537], [0.220459, -0.035359, -0.146766], [0.231851, -0.010486, 0.160555], [-0.129869, 0.245564, 0.049745], [0.110878, -0.2013, 0.136308], [0.224544, 0.13995, -0.037248], [-0.182633, 0.022691, -0.193302], [-0.256058, -0.070184, 0.019495], [0.005031, 0.245801, 0.138559], [-0.002855, 0.247505, -0.000616], [-0.156129, -0.179657, -0.133266], [-0.167747, -0.085999, -0.222905], [0.205241, -0.108839, -0.085665], [-0.233896, 0.006218, -0.158809], [0.086098, 0.207694, 0.169368], [-0.209745, -0.10962, -0.073897], [0.238582, 0.075112, -0.12911], [0.228543, -0.137455, 0.016371], [-0.172088, 0.09499, -0.155578], [0.149617, 0.074387, -0.188538], [-0.045292, -0.08137, 0.252542], [-0.022822, 0.211578, -0.199147], [-0.163895, 0.083572, 0.22606], [0.135692, 0.24735, -0.073157], [-0.190104, 0.207217, -0.01247], [0.000857, -0.087157, 0.278114], [0.250261, -0.072415, 0.106581], [0.175605, 0.010025, 0.219767], [-0.15981, -0.209512, 0.098996], [-0.082997, -0.246883, -0.106741], [0.241192, -0.086627, -0.020901], [0.154741, -0.008202, -0.23499], [-0.025696, 0.087012, -0.276962], [0.144555, -0.245788, 0.060284], [-0.26944, 0.085459, -0.073452], [0.029844, -0.246884, 0.151994], [0.198302, -0.209776, 0.040164], [-0.125072, -0.210646, -0.158999], [-0.161412, -0.241566, 0.029831], [0.007901, 0.029613, -0.289838], [-0.002071, -0.211715, 0.200301], [0.14383, 0.208255, 0.123196], [-0.193845, -0.159118, -0.037377], [0.116347, 0.086835, 0.227915], [0.026957, -0.247485, 0.049772], [0.071141, 0.03632, 0.254981], [-0.193761, -0.108776, 0.138407], [-0.077647, -0.247598, -0.024293], [0.054871, -0.139458, 0.221217], [-0.032749, -0.13995, 0.22525], [-0.232862, 0.147857, -0.062955], [0.140463, -0.239655, -0.053709], [-0.005606, -0.239924, -0.149846], [-0.220969, -0.002113, 0.176691], [0.282627, 0.001566, -0.012997], [-0.134171, 0.20671, 0.13603], [0.075518, -0.246008, -0.114061], [0.13417, -0.206721, -0.136005], [-0.072417, 0.20683, 0.17667], [0.072424, -0.206823, -0.176662], [-0.271305, 0.009236, -0.074443], [0.144072, -0.109363, 0.169054], [0.084881, -0.24691, 0.019937], [0.179328, -0.071119, -0.204973], [0.117582, -0.07122, -0.24563], [0.124549, -0.067761, 0.211364], [0.037823, -0.033391, 0.28754], [0.19045, 0.212094, -0.062978], [0.015765, 0.245213, -0.157617], [-0.138814, 0.029956, 0.255064], [-0.155188, 0.235924, -0.074073]], 'f': [[89, 24, 33], [90, 160, 26], [124, 46, 186], [121, 26, 80], [32, 26, 121], [142, 62, 14], [186, 85, 142], [142, 85, 187], [29, 80, 164], [1, 141, 83], [83, 177, 1], [83, 35, 177], [80, 26, 56], [56, 160, 14], [56, 26, 160], [14, 1, 133], [1, 177, 133], [133, 56, 14], [1, 143, 174], [81, 46, 173], [173, 32, 81], [32, 7, 81], [90, 26, 76], [76, 26, 32], [39, 124, 186], [39, 142, 14], [186, 142, 39], [61, 160, 90], [124, 39, 61], [87, 88, 19], [80, 51, 146], [146, 121, 80], [179, 89, 33], [33, 106, 179], [110, 130, 53], [52, 71, 53], [53, 130, 52], [71, 179, 53], [53, 179, 106], [70, 42, 24], [8, 163, 105], [37, 151, 158], [158, 130, 37], [37, 130, 110], [186, 46, 22], [22, 85, 186], [62, 142, 13], [29, 144, 185], [185, 80, 29], [185, 51, 80], [159, 147, 38], [159, 3, 144], [88, 3, 159], [114, 8, 105], [101, 42, 157], [101, 147, 42], [157, 75, 101], [38, 147, 101], [101, 114, 38], [8, 114, 101], [24, 9, 172], [172, 9, 141], [172, 92, 24], [141, 92, 172], [24, 42, 117], [42, 147, 117], [68, 9, 24], [24, 117, 68], [33, 24, 119], [24, 92, 119], [115, 144, 29], [139, 144, 115], [141, 9, 188], [188, 83, 141], [9, 115, 188], [29, 83, 188], [188, 115, 29], [35, 83, 69], [29, 164, 69], [69, 83, 29], [56, 133, 162], [162, 164, 80], [80, 56, 162], [78, 69, 164], [35, 69, 78], [99, 141, 1], [1, 174, 99], [6, 46, 47], [178, 47, 0], [66, 76, 32], [46, 124, 66], [66, 32, 173], [173, 46, 66], [160, 61, 118], [118, 61, 39], [14, 160, 118], [118, 39, 14], [154, 19, 88], [154, 159, 38], [88, 159, 154], [38, 114, 154], [154, 114, 105], [105, 163, 154], [163, 19, 154], [123, 88, 87], [21, 180, 145], [145, 146, 21], [121, 146, 145], [87, 7, 145], [32, 121, 145], [145, 7, 32], [82, 146, 51], [82, 3, 88], [51, 185, 82], [144, 3, 82], [82, 185, 144], [10, 169, 143], [153, 106, 33], [143, 169, 153], [153, 10, 106], [169, 10, 153], [110, 53, 132], [132, 53, 106], [132, 125, 110], [106, 125, 132], [89, 179, 126], [24, 89, 126], [126, 70, 24], [42, 70, 30], [17, 178, 0], [86, 47, 158], [158, 151, 86], [151, 2, 86], [187, 85, 84], [84, 86, 2], [63, 13, 31], [140, 2, 151], [140, 31, 187], [187, 84, 140], [140, 84, 2], [110, 125, 91], [91, 37, 110], [151, 37, 91], [106, 10, 72], [151, 91, 57], [57, 91, 125], [85, 22, 135], [135, 46, 6], [135, 22, 46], [62, 13, 128], [128, 13, 63], [14, 62, 127], [127, 1, 14], [127, 96, 1], [1, 96, 122], [122, 143, 1], [189, 10, 122], [122, 10, 143], [147, 159, 64], [33, 119, 59], [59, 153, 33], [143, 153, 59], [9, 68, 94], [94, 115, 9], [139, 115, 94], [11, 162, 133], [164, 162, 11], [11, 78, 164], [166, 101, 75], [8, 101, 166], [163, 8, 166], [181, 19, 163], [182, 47, 46], [47, 178, 183], [183, 178, 34], [90, 76, 5], [76, 66, 5], [5, 66, 124], [5, 61, 90], [124, 61, 5], [170, 175, 0], [155, 7, 87], [136, 81, 155], [155, 81, 7], [155, 65, 136], [36, 170, 155], [60, 123, 87], [180, 123, 60], [87, 145, 60], [60, 145, 180], [184, 180, 21], [21, 146, 184], [146, 82, 184], [167, 82, 88], [88, 123, 167], [167, 123, 180], [167, 184, 82], [70, 126, 4], [4, 179, 71], [4, 126, 179], [129, 30, 97], [97, 71, 52], [52, 129, 97], [102, 75, 157], [102, 129, 52], [157, 42, 102], [42, 30, 102], [102, 30, 129], [148, 166, 75], [75, 102, 148], [148, 102, 17], [6, 47, 150], [47, 86, 150], [150, 135, 6], [116, 13, 142], [116, 31, 13], [116, 142, 187], [187, 31, 116], [104, 140, 151], [151, 57, 104], [104, 57, 41], [41, 72, 98], [98, 72, 10], [98, 104, 41], [140, 104, 98], [98, 10, 189], [189, 31, 98], [31, 140, 98], [18, 57, 125], [18, 125, 106], [106, 72, 18], [86, 84, 108], [62, 128, 54], [54, 128, 96], [54, 127, 62], [96, 127, 54], [28, 128, 63], [96, 128, 28], [147, 64, 15], [44, 11, 133], [78, 11, 44], [44, 133, 177], [177, 35, 44], [35, 78, 44], [163, 166, 45], [45, 181, 163], [19, 181, 152], [87, 19, 152], [152, 155, 87], [36, 155, 152], [175, 170, 152], [152, 170, 36], [46, 81, 23], [23, 81, 136], [176, 170, 0], [65, 155, 176], [176, 155, 170], [158, 47, 20], [47, 183, 20], [180, 184, 77], [77, 167, 180], [184, 167, 77], [161, 30, 70], [156, 102, 52], [17, 102, 156], [52, 130, 156], [130, 17, 156], [58, 148, 175], [166, 148, 58], [0, 175, 171], [175, 148, 171], [171, 17, 0], [171, 148, 17], [178, 17, 73], [34, 178, 73], [73, 55, 34], [103, 183, 34], [34, 55, 103], [12, 17, 130], [12, 130, 158], [137, 18, 72], [57, 18, 137], [137, 72, 41], [41, 57, 137], [79, 122, 96], [96, 28, 79], [79, 28, 63], [63, 31, 79], [79, 31, 189], [189, 122, 79], [165, 117, 147], [147, 15, 165], [165, 68, 117], [139, 94, 43], [95, 109, 48], [48, 92, 141], [141, 99, 48], [48, 99, 174], [174, 95, 48], [40, 95, 174], [40, 109, 95], [40, 174, 143], [143, 59, 40], [59, 109, 40], [120, 182, 46], [46, 23, 120], [131, 120, 23], [47, 182, 93], [182, 176, 93], [0, 47, 93], [93, 176, 0], [134, 4, 71], [71, 97, 134], [70, 4, 134], [134, 161, 70], [134, 97, 30], [30, 161, 134], [100, 58, 175], [45, 58, 100], [181, 45, 100], [175, 152, 100], [100, 152, 181], [112, 45, 166], [166, 58, 112], [112, 58, 45], [111, 73, 17], [17, 12, 111], [55, 73, 111], [111, 12, 55], [74, 103, 55], [55, 12, 74], [74, 12, 158], [158, 20, 74], [74, 20, 183], [183, 103, 74], [135, 150, 138], [113, 150, 86], [86, 108, 113], [108, 25, 113], [113, 138, 150], [25, 138, 113], [50, 84, 85], [50, 108, 84], [50, 25, 108], [139, 43, 168], [168, 64, 159], [168, 144, 139], [168, 159, 144], [107, 15, 64], [64, 168, 107], [107, 168, 43], [68, 165, 107], [107, 165, 15], [107, 94, 68], [107, 43, 94], [92, 48, 16], [16, 48, 109], [16, 119, 92], [16, 59, 119], [16, 109, 59], [136, 65, 49], [49, 131, 136], [65, 131, 49], [149, 23, 136], [136, 131, 149], [149, 131, 23], [65, 176, 27], [27, 131, 65], [27, 176, 182], [182, 120, 27], [120, 131, 27], [135, 138, 67], [67, 138, 25], [85, 135, 67], [67, 50, 85], [25, 50, 67]], 'logical': [{'n': [0.860082, 0.450565, 0.23927], 'c': [0.197762, 0.101058, 0.051473], 's': 0.097342}, {'n': [0.839345, -0.441105, -0.317689], 'c': [0.186093, -0.097725, -0.070184], 's': 0.332887}, {'n': [0.038846, 0.442624, 0.895865], 'c': [0.01055, 0.103211, 0.207392], 's': 0.33759}, {'n': [0.560899, -0.447914, 0.696251], 'c': [0.130415, -0.105657, 0.163458], 's': 0.341956}, {'n': [0.489539, 0.453817, -0.744581], 'c': [0.119, 0.108251, -0.178149], 's': 0.343946}, {'n': [-0.860143, -0.450461, -0.239245], 'c': [-0.193514, -0.101256, -0.052629], 's': 0.331506}, {'n': [-0.489559, -0.453834, 0.744558], 'c': [-0.112346, -0.108404, 0.172905], 's': 0.336426}, {'n': [-0.839655, 0.440872, 0.317193], 'c': [-0.203901, 0.105784, 0.075965], 's': 0.072501}, {'n': [-0.561228, 0.446876, -0.696652], 'c': [-0.129115, 0.098624, -0.160451], 's': 0.334052}, {'n': [0.005448, -0.999973, -0.004932], 'c': [0.004956, -0.211446, 0.003426], 's': 0.325068}, {'n': [-0.006724, 0.999965, 0.004928], 'c': [-0.007368, 0.231856, -3.8e-05], 's': 0.336653}, {'n': [-0.038903, -0.441577, -0.896379], 'c': [-0.005494, -0.099401, -0.211749], 's': 0.088328}]}, '8': {'v': [[0.238258, -0.180447, -0.025584], [-0.238266, 0.18046, 0.025759], [-0.101012, -0.167489, 0.225987], [-0.149273, -0.17149, -0.193808], [0.151169, 0.170061, 0.193778], [0.102909, 0.16606, -0.226017], [0.005633, 0.18452, -0.002304], [-0.175113, -0.055479, 0.018644], [0.071338, -0.064862, -0.158432], [0.110389, -0.059366, 0.137146], [-0.061969, 0.059783, 0.163561], [-0.099238, 0.072686, -0.137959], [0.010953, -0.185509, 0.022997], [0.184473, 0.039018, 0.009177], [0.139237, 0.167113, -0.017491], [-0.124453, -0.179151, 0.01607], [0.043874, -0.185583, -0.108667], [0.18112, -0.001474, -0.123042], [-0.024038, -0.00278, -0.217403], [-0.175714, 0.006675, 0.130372], [-0.047252, 0.177802, 0.118366], [-0.198316, 0.008198, -0.091777], [0.030998, -0.00249, 0.21659], [-0.066813, 0.182923, -0.099472], [0.075988, -0.173478, 0.110078], [0.19727, -0.011109, 0.094578], [-0.145525, 0.189207, 0.01503], [-0.220901, 0.088321, 0.024624], [0.065675, 0.1329, 0.186811], [0.066204, 0.180256, -0.141225], [-0.135183, -0.080247, -0.179158], [-0.135708, -0.12761, 0.148888], [-0.019621, -0.13045, 0.199109], [0.092473, 0.075287, -0.206774], [0.167303, 0.1288, 0.111582], [-0.094452, -0.18397, -0.119258], [0.190876, -0.138486, 0.043024], [0.176439, -0.139703, -0.082425], [-0.190415, 0.138179, -0.042908], [-0.176011, 0.13935, 0.082561], [-0.064524, -0.181481, 0.141188], [-0.165649, -0.130122, -0.111643], [-0.090805, -0.076609, 0.206825], [0.021343, 0.129224, -0.199167], [0.221337, -0.088659, -0.024526], [0.145979, -0.189531, -0.014877], [0.137352, 0.126329, -0.148861], [-0.063995, -0.134123, -0.186793], [0.096187, 0.182738, 0.119301], [0.136836, 0.078974, 0.179119], [0.178184, 0.031546, -0.063935], [0.079823, 0.18167, -0.01081], [0.05886, -0.121942, -0.139891], [0.133307, -0.032871, -0.144962], [-0.013598, 0.028866, 0.196239], [-0.151223, -0.127587, 0.018677], [-0.084908, 0.132454, -0.121488], [-0.193047, -0.021587, -0.042337], [-0.024113, 0.186065, 0.065909], [-0.072701, 0.037893, -0.1722], [-0.069881, -0.18318, 0.009862], [-0.184356, 0.185007, 0.053478], [-0.192022, 0.184408, -0.013509], [-0.224601, 0.131138, 0.058571], [-0.23227, 0.130543, -0.008422], [-0.114222, -0.123474, -0.205593], [-0.112239, -0.173828, 0.166768], [0.114078, 0.172441, -0.166792], [0.116112, 0.122094, 0.205563], [-0.168592, -0.121284, -0.165491], [-0.126373, -0.117796, 0.201836], [0.052072, 0.174076, -0.193657], [-0.146709, -0.176745, -0.1335], [0.148664, 0.175271, 0.133464], [0.06621, 0.117919, -0.228651], [-0.050384, -0.175272, 0.193619], [-0.064247, -0.119392, 0.228613], [-0.092568, -0.178767, -0.173626], [0.128021, 0.116491, -0.201767], [0.224801, -0.131357, -0.058462], [0.232528, -0.130682, 0.008512], [0.184571, -0.185212, -0.053278], [0.192296, -0.184534, 0.013703], [0.170216, 0.119965, 0.165434], [0.094271, 0.177585, 0.173628], [0.063748, -0.030101, 0.183654], [-0.11028, 0.043291, 0.149811], [-0.17835, -0.020354, 0.085571], [0.015266, -0.030126, -0.196205], [0.155683, 0.117604, -0.018928], [0.039906, -0.185624, 0.062059], [-0.055431, 0.134887, 0.135231], [-0.038833, 0.18485, -0.061997], [-0.161756, 0.029615, -0.111666], [0.025219, -0.186846, -0.065807], [0.162813, -0.030406, 0.111701], [0.086008, -0.133222, 0.121516], [0.005031, -0.185892, -0.019261], [0.194098, 0.020747, 0.042351], [-0.163895, -0.088974, 0.019763], [0.050208, 0.182808, -0.007415], [-0.06356, 0.086926, 0.151717], [0.050097, -0.054946, -0.171423], [-0.010197, 0.184638, -0.023555], [0.098419, -0.082114, 0.135618], [-0.09737, 0.050007, -0.149238], [-0.057802, 0.025218, -0.188372], [-0.132252, 0.032024, 0.14506], [0.179392, 0.01954, -0.085514], [-0.17571, -0.04532, 0.040879]], 'f': [[1, 26, 62], [30, 21, 93], [81, 37, 0], [68, 76, 22], [23, 62, 26], [71, 62, 23], [69, 30, 3], [21, 30, 69], [69, 64, 21], [11, 30, 93], [65, 47, 3], [3, 30, 65], [3, 47, 77], [37, 81, 77], [77, 47, 37], [4, 68, 49], [49, 68, 22], [26, 48, 58], [76, 68, 42], [38, 62, 71], [1, 62, 38], [38, 64, 1], [38, 11, 93], [56, 11, 38], [93, 21, 38], [21, 64, 38], [43, 11, 56], [43, 38, 71], [56, 38, 43], [73, 48, 4], [73, 67, 48], [74, 43, 5], [5, 43, 71], [5, 33, 74], [78, 33, 5], [48, 67, 29], [71, 23, 29], [29, 5, 71], [67, 5, 29], [22, 76, 32], [32, 49, 22], [36, 24, 82], [36, 80, 25], [36, 82, 0], [0, 80, 36], [24, 32, 75], [82, 24, 75], [78, 5, 46], [46, 5, 67], [4, 48, 84], [84, 48, 26], [4, 49, 83], [83, 49, 25], [25, 80, 83], [28, 42, 68], [54, 42, 28], [28, 20, 91], [28, 84, 20], [28, 68, 4], [4, 84, 28], [10, 42, 54], [54, 28, 10], [91, 20, 39], [1, 63, 39], [106, 43, 30], [18, 65, 30], [30, 43, 18], [18, 43, 74], [47, 65, 18], [74, 33, 18], [18, 33, 47], [37, 47, 52], [52, 8, 37], [47, 8, 52], [53, 33, 37], [37, 8, 53], [53, 8, 33], [47, 33, 88], [6, 58, 48], [26, 58, 6], [25, 49, 95], [95, 36, 25], [76, 42, 2], [2, 42, 70], [2, 32, 76], [2, 75, 32], [44, 80, 0], [67, 73, 14], [14, 46, 67], [101, 28, 91], [101, 10, 28], [91, 39, 101], [101, 39, 10], [41, 69, 3], [3, 72, 41], [41, 72, 66], [1, 64, 27], [27, 63, 1], [64, 69, 27], [69, 41, 27], [1, 39, 61], [61, 39, 20], [61, 26, 1], [61, 84, 26], [20, 84, 61], [107, 39, 42], [30, 11, 105], [11, 43, 59], [43, 106, 59], [59, 105, 11], [59, 106, 30], [30, 105, 59], [33, 8, 102], [102, 88, 33], [102, 8, 47], [47, 88, 102], [48, 29, 51], [92, 23, 26], [92, 29, 23], [36, 95, 9], [9, 95, 49], [31, 2, 70], [31, 66, 2], [31, 41, 66], [66, 72, 15], [34, 83, 80], [80, 44, 34], [34, 44, 98], [34, 14, 73], [34, 73, 4], [4, 83, 34], [79, 44, 0], [0, 37, 79], [37, 33, 79], [79, 33, 78], [78, 46, 17], [17, 79, 78], [44, 79, 17], [57, 41, 7], [7, 27, 57], [57, 27, 41], [10, 39, 86], [39, 107, 86], [42, 10, 86], [86, 107, 42], [29, 6, 100], [100, 51, 29], [100, 6, 48], [48, 51, 100], [103, 6, 29], [29, 92, 103], [26, 6, 103], [103, 92, 26], [96, 32, 24], [24, 36, 96], [49, 32, 85], [85, 9, 49], [32, 9, 85], [87, 31, 27], [41, 31, 55], [63, 27, 19], [27, 31, 19], [19, 31, 70], [19, 39, 63], [70, 42, 19], [42, 39, 19], [16, 77, 81], [82, 75, 45], [45, 81, 0], [0, 82, 45], [45, 16, 81], [94, 16, 45], [45, 90, 12], [46, 50, 108], [108, 50, 44], [108, 17, 46], [44, 17, 108], [46, 14, 89], [14, 34, 89], [89, 50, 46], [36, 9, 104], [104, 96, 36], [104, 9, 32], [32, 96, 104], [109, 27, 7], [109, 87, 27], [7, 31, 109], [31, 87, 109], [7, 41, 99], [41, 55, 99], [99, 31, 7], [99, 55, 31], [77, 16, 35], [35, 16, 94], [3, 77, 35], [35, 72, 3], [35, 15, 72], [40, 45, 75], [90, 45, 40], [75, 2, 40], [2, 66, 40], [66, 15, 40], [12, 90, 40], [40, 60, 12], [15, 35, 40], [40, 35, 60], [50, 89, 13], [98, 44, 13], [44, 50, 13], [13, 34, 98], [13, 89, 34], [12, 60, 97], [60, 35, 97], [97, 35, 94], [97, 45, 12], [94, 45, 97]], 'logical': [{'n': [0.032229, 0.999395, -0.013048], 'c': [0.004886, 0.166964, -0.001308], 's': 0.134606}, {'n': [-0.362773, 0.353281, 0.862315], 'c': [-0.057263, 0.055926, 0.140848], 's': 0.244097}, {'n': [0.365339, -0.35501, -0.860521], 'c': [0.06764, -0.064502, -0.153491], 's': 0.133116}, {'n': [-0.944696, -0.308402, 0.111519], 'c': [-0.163657, -0.054567, 0.018726], 's': 0.252949}, {'n': [-0.549647, 0.337671, -0.764112], 'c': [-0.097885, 0.058272, -0.136391], 's': 0.134302}, {'n': [0.551813, -0.339387, 0.761787], 'c': [0.091635, -0.057904, 0.127632], 's': 0.250145}, {'n': [-0.028814, -0.999502, 0.012837], 'c': [-0.00748, -0.160168, 0.001992], 's': 0.241862}, {'n': [0.945829, 0.304875, -0.111615], 'c': [0.166465, 0.05176, -0.019622], 's': 0.252445}]}, '4': {'v': [[0.015704, -0.149459, -0.177255], [0.190604, -0.129602, 0.178298], [-0.199289, -0.139608, 0.149267], [-0.002581, 0.152344, 0.04297], [0.00512, -0.153953, 0.049657], [0.106539, 0.014617, 0.102441], [-0.096213, 0.01347, 0.108679], [0.016071, 0.01399, -0.066351], [-0.000415, -0.090779, 0.158273], [-0.086926, -0.091651, -0.009206], [0.101369, -0.090313, -0.000391], [0.045845, -0.091957, -0.108369], [-0.121705, -0.092727, 0.15281], [0.157292, -0.090836, 0.107392], [0.001615, 0.087371, 0.087888], [0.069637, -0.152725, 0.01632], [-0.056017, -0.153617, 0.010444], [0.001715, -0.153031, 0.122205], [0.012026, -0.152976, -0.098068], [-0.126276, -0.153609, 0.11753], [0.129577, -0.151788, 0.129505], [0.12087, -0.091003, 0.164173], [-0.152666, -0.093038, 0.092877], [-0.021537, -0.092437, -0.111521], [0.037764, 0.087537, 0.031538], [-0.029119, 0.087068, 0.028413], [-0.068175, -0.032268, 0.002995], [0.080721, -0.031211, 0.009968], [0.000233, -0.03158, 0.135432], [0.060057, -0.002155, 0.126303], [-0.026237, -0.003016, -0.040739], [-0.090527, -0.003319, 0.059478], [-0.171029, -0.090707, 0.140305], [0.013912, -0.089848, -0.147967], [0.171102, -0.088277, 0.156342], [0.044619, -0.139361, -0.133386], [0.177173, -0.13802, 0.123233], [-0.144804, -0.140221, 0.161931], [-0.017323, -0.139796, -0.136261], [-0.173281, -0.140515, 0.106849], [-0.00057, -0.13953, 0.169029], [0.1112, -0.13903, -0.005234], [-0.095587, -0.140499, -0.014887], [0.143691, -0.138161, 0.175444], [0.032782, 0.117655, 0.068203], [-0.028004, 0.117223, 0.065353], [0.004818, 0.117404, 0.014164], [0.042635, -0.002534, -0.037521], [-0.058886, -0.002999, 0.12073], [0.097282, -0.001983, 0.068274], [0.025167, -0.126437, -0.17352], [0.094929, 0.014559, 0.120548], [0.196263, -0.147628, 0.172298], [-0.18945, -0.127428, 0.16103], [0.005627, -0.126584, -0.17443], [0.198597, -0.124698, 0.162242], [0.002453, 0.151423, 0.060264], [-0.001444, 0.013612, -0.065968], [-0.103176, 0.013122, 0.092618], [0.012834, 0.151471, 0.044083], [0.048509, -0.126025, -0.130621], [-0.172956, -0.127218, 0.101984], [-0.021626, -0.126534, -0.133899], [0.177121, -0.12474, 0.118375], [-0.140725, -0.126902, 0.164364], [0.139215, -0.124905, 0.177478], [-0.097586, -0.126818, -0.016146], [0.113126, -0.125321, -0.006285], [-0.000771, -0.125843, 0.171271], [0.015664, -0.134884, -0.178394], [-0.198439, -0.127515, 0.143649], [-0.000255, -0.1515, 0.164057], [0.106847, -0.151021, -0.00292], [-0.091296, -0.152429, -0.012175], [0.146051, -0.149842, 0.170668], [-0.170186, -0.152176, 0.111237], [0.039411, -0.151086, -0.132943], [-0.146549, -0.151924, 0.15697], [-0.012014, -0.15144, -0.135336], [0.173852, -0.149717, 0.127325]], 'f': [[54, 62, 23], [6, 32, 53], [45, 32, 6], [23, 62, 66], [62, 42, 66], [62, 54, 38], [38, 42, 62], [55, 5, 34], [34, 1, 55], [51, 1, 34], [59, 5, 24], [13, 5, 55], [69, 54, 50], [7, 59, 50], [78, 18, 73], [73, 38, 78], [42, 38, 73], [2, 53, 70], [70, 53, 32], [21, 1, 51], [55, 1, 52], [52, 36, 55], [79, 36, 52], [20, 79, 52], [52, 74, 20], [52, 43, 74], [1, 43, 52], [57, 54, 23], [57, 25, 3], [39, 75, 2], [2, 70, 39], [39, 73, 75], [42, 73, 39], [56, 59, 3], [3, 45, 56], [56, 14, 51], [56, 45, 6], [6, 53, 56], [3, 59, 46], [46, 59, 7], [46, 57, 3], [55, 36, 63], [63, 13, 55], [72, 36, 79], [72, 79, 20], [18, 76, 72], [41, 63, 36], [36, 72, 41], [33, 50, 54], [7, 50, 33], [54, 57, 33], [33, 46, 7], [57, 46, 33], [69, 50, 0], [0, 54, 69], [0, 18, 78], [0, 76, 18], [78, 38, 0], [0, 38, 54], [35, 50, 60], [60, 41, 35], [76, 0, 35], [35, 0, 50], [35, 72, 76], [35, 41, 72], [60, 50, 11], [47, 59, 24], [47, 50, 59], [47, 11, 50], [24, 27, 47], [27, 11, 47], [17, 4, 20], [75, 73, 19], [4, 17, 19], [22, 70, 58], [3, 25, 58], [58, 45, 3], [32, 45, 58], [58, 70, 32], [22, 66, 61], [61, 70, 22], [61, 39, 70], [61, 66, 42], [42, 39, 61], [53, 64, 12], [12, 64, 68], [77, 53, 2], [2, 75, 77], [75, 19, 77], [51, 14, 29], [29, 21, 51], [29, 14, 28], [28, 21, 29], [65, 21, 68], [1, 21, 65], [65, 43, 1], [9, 26, 23], [22, 26, 9], [23, 66, 9], [9, 66, 22], [23, 26, 30], [30, 26, 25], [30, 57, 23], [25, 57, 30], [31, 26, 22], [25, 26, 31], [22, 58, 31], [31, 58, 25], [59, 56, 44], [44, 34, 5], [5, 59, 44], [51, 34, 44], [44, 56, 51], [15, 72, 20], [18, 72, 15], [20, 4, 15], [15, 4, 18], [67, 41, 60], [63, 41, 67], [13, 63, 67], [60, 11, 67], [49, 27, 24], [13, 27, 49], [24, 5, 49], [5, 13, 49], [10, 27, 13], [10, 11, 27], [13, 67, 10], [10, 67, 11], [16, 73, 18], [16, 19, 73], [18, 4, 16], [4, 19, 16], [53, 12, 48], [48, 56, 53], [14, 56, 48], [28, 14, 48], [48, 12, 28], [68, 21, 8], [8, 12, 68], [8, 21, 28], [28, 12, 8], [37, 64, 53], [53, 77, 37], [74, 43, 71], [71, 37, 77], [20, 74, 71], [71, 17, 20], [71, 19, 17], [71, 77, 19], [40, 71, 43], [37, 71, 40], [40, 65, 68], [43, 65, 40], [68, 64, 40], [64, 37, 40]], 'logical': [{'n': [0.824609, 0.370147, -0.427799], 'c': [0.082617, -0.03808, 0.009597], 's': 0.129057}, {'n': [-0.786136, 0.358704, -0.50331], 'c': [-0.06925, -0.03868, 0.001711], 's': 0.128134}, {'n': [-0.046203, 0.366212, 0.929384], 'c': [-9.5e-05, -0.037922, 0.137043], 's': 0.123333}, {'n': [0.007036, -0.999974, 0.001598], 'c': [0.005112, -0.153108, 0.049656], 's': 0.103518}]}, '6': {'v': [[-0.173205, -0.173689, 0.17272], [0.173205, 0.173688, -0.17272], [-0.176298, 0.000321, -0.176297], [0.176298, -0.176297, -0.000687], [0.162494, 0.161696, 0.186028], [-0.176298, 0.175961, 0.120748], [0.046707, -0.101331, -0.18586], [0.062256, -0.10105, 0.185294], [-0.16251, -0.185238, -0.121113], [-0.007322, 0.110516, -0.185267], [-0.007311, -0.185613, 0.012943], [-0.168212, 0.183711, -0.162732], [-1.5e-05, 0.089908, 0.185828], [0.162494, 0.185575, 0.000326], [0.176298, 0.000321, -0.176297], [0.185575, -0.000628, 0.16305], [0.183256, -0.167724, -0.163714], [-0.027609, 0.185369, 0.07404], [0.183256, -0.168637, 0.162581], [-0.185575, -0.162386, -0.000648], [-0.001672, -0.185269, -0.110094], [-0.16251, 0.185575, 0.000326], [-0.04354, -0.05345, 0.185427], [0.011669, 0.185881, -0.109228], [0.000426, -0.18588, 0.10856], [-0.16251, -0.118998, -0.185909], [-0.16251, 0.118697, 0.185908], [0.035153, 0.044805, -0.185451], [-0.013519, -0.053727, -0.185727], [0.185575, 0.161878, 0.120709], [-0.16251, 0.119735, -0.185241], [-0.16251, 0.175804, 0.17679], [0.162494, -0.118998, -0.185909], [-0.16251, -0.175804, -0.17679], [0.162494, 0.119735, -0.185241], [-0.16251, -0.185911, 0.119736], [-0.183256, -0.119988, 0.168012], [0.162494, -0.185911, 0.119736], [0.162494, -0.185238, -0.121113], [0.183256, -0.119987, 0.168012], [0.176298, 0.118723, 0.17663], [-0.011113, -0.185771, 0.069529], [-0.045238, 0.185274, 0.10784], [0.162494, 0.185118, 0.163569], [0.185575, 0.161759, 0.163504], [0.185575, 0.119672, -0.162911], [-0.185575, 0.119672, -0.162911], [-0.16251, 0.162733, -0.185121], [0.162494, -0.162905, 0.185121], [0.03097, 0.092661, 0.185836], [0.162494, -0.161868, -0.186028], [-0.185575, 0.161759, 0.163504], [-0.185575, -0.16193, -0.163698], [0.168208, 0.182919, 0.120768], [-0.185575, 0.162671, -0.16279], [0.162494, -0.185119, -0.163763], [0.162494, -0.186031, 0.162532], [0.038791, -0.107155, 0.185277], [-0.01635, 0.089333, -0.185327], [0.162494, 0.162733, -0.185121], [-0.16251, -0.162905, 0.185121], [-0.185575, -0.162843, 0.162597], [0.185575, 0.162671, -0.16279], [0.173205, 0.17272, 0.173688], [-0.16251, -0.186031, 0.162532], [0.162494, 0.186031, -0.162725], [-0.176298, 0.175841, 0.163543], [-0.185575, 0.161878, 0.120709], [-0.16251, 0.161696, 0.186028], [0.167617, -0.175673, 0.174694], [0.185575, -0.162386, -0.000648], [-0.176298, -0.176297, -0.000687], [-0.176298, -0.175841, -0.163737], [-0.16251, -0.161868, -0.186028], [-0.175185, 0.168006, -0.174715], [0.167617, -0.174694, -0.175674], [0.176298, 0.176297, 0.000299], [0.162494, 0.118697, 0.185908], [0.162494, 0.000347, -0.185575], [0.176298, -0.119024, -0.176631], [-0.176298, 0.176297, 0.000299], [-0.176298, 0.118723, 0.17663], [-0.16251, 0.000347, -0.185575], [-0.176298, -0.119024, -0.176631], [-0.176298, 0.161722, 0.17675], [-0.16251, 0.185118, 0.163569], [-0.16251, -0.185119, -0.163763], [0.176298, 0.119709, -0.175964], [-0.176298, 0.119709, -0.175964], [-0.176298, -0.161894, -0.17675], [0.176298, -0.161894, -0.17675], [0.176298, -0.16288, 0.175843], [-0.16251, 0.176789, -0.175804], [0.058768, -0.110335, -0.185885], [-0.031184, 0.185279, 0.106106], [0.176298, 0.162708, -0.175843], [0.176298, 0.161722, 0.17675], [-0.176298, -0.16288, 0.175843], [0.162494, 0.175804, 0.17679], [0.162494, 0.176789, -0.175804]], 'f': [[18, 70, 15], [20, 86, 55], [65, 62, 1], [94, 85, 43], [76, 62, 65], [20, 35, 8], [8, 86, 20], [35, 71, 8], [8, 71, 86], [3, 18, 56], [56, 37, 3], [16, 3, 55], [70, 18, 16], [18, 3, 16], [95, 1, 62], [59, 1, 95], [19, 71, 61], [50, 73, 93], [93, 73, 25], [65, 1, 99], [99, 1, 59], [74, 54, 11], [11, 23, 65], [66, 85, 11], [11, 5, 66], [85, 94, 42], [65, 23, 13], [13, 94, 43], [31, 85, 66], [63, 44, 43], [22, 60, 57], [56, 18, 69], [62, 76, 29], [29, 76, 44], [43, 44, 53], [44, 76, 53], [53, 13, 43], [53, 76, 65], [65, 13, 53], [33, 55, 86], [33, 73, 50], [33, 89, 73], [55, 3, 38], [38, 3, 37], [20, 55, 38], [38, 37, 20], [70, 16, 45], [75, 16, 55], [75, 33, 50], [55, 33, 75], [14, 45, 79], [64, 71, 35], [64, 61, 71], [56, 69, 64], [64, 69, 60], [9, 47, 59], [25, 82, 28], [21, 11, 85], [23, 11, 21], [85, 42, 21], [21, 42, 94], [54, 5, 80], [80, 11, 54], [5, 11, 80], [92, 47, 74], [74, 11, 92], [65, 99, 92], [92, 11, 65], [98, 63, 43], [4, 63, 98], [12, 22, 57], [15, 44, 39], [39, 18, 15], [44, 63, 96], [96, 63, 4], [96, 39, 44], [40, 39, 96], [88, 54, 74], [74, 47, 88], [52, 71, 19], [10, 35, 20], [10, 41, 35], [20, 37, 10], [37, 41, 10], [24, 37, 35], [35, 41, 24], [24, 41, 37], [79, 45, 90], [90, 45, 16], [90, 75, 50], [16, 75, 90], [66, 5, 67], [67, 51, 66], [67, 5, 54], [50, 93, 32], [93, 78, 32], [45, 14, 87], [58, 9, 27], [82, 9, 58], [27, 28, 58], [58, 28, 82], [34, 9, 59], [34, 78, 27], [27, 9, 34], [6, 28, 27], [27, 78, 6], [6, 78, 93], [6, 93, 25], [25, 28, 6], [17, 13, 23], [23, 21, 17], [94, 13, 17], [17, 21, 94], [60, 69, 48], [57, 60, 48], [48, 7, 57], [4, 68, 49], [49, 12, 57], [57, 7, 49], [30, 88, 47], [47, 9, 30], [30, 9, 82], [54, 88, 46], [52, 46, 2], [2, 46, 88], [86, 71, 72], [71, 52, 72], [72, 33, 86], [89, 33, 72], [72, 52, 89], [36, 51, 61], [61, 97, 36], [61, 64, 0], [0, 97, 61], [0, 64, 60], [60, 97, 0], [60, 22, 26], [22, 12, 26], [12, 49, 26], [26, 49, 68], [51, 36, 84], [66, 51, 84], [84, 68, 31], [84, 31, 66], [7, 48, 77], [4, 49, 77], [77, 49, 7], [91, 39, 40], [18, 39, 91], [91, 69, 18], [91, 48, 69], [89, 52, 83], [52, 2, 83], [81, 36, 97], [81, 84, 36], [62, 29, 45], [15, 70, 45], [45, 44, 15], [45, 29, 44], [35, 37, 64], [64, 37, 56], [92, 99, 59], [59, 47, 92], [85, 31, 98], [43, 85, 98], [68, 98, 31], [4, 98, 68], [79, 90, 32], [32, 90, 50], [32, 78, 14], [32, 14, 79], [62, 45, 87], [87, 95, 62], [95, 87, 34], [59, 95, 34], [14, 78, 34], [34, 87, 14], [19, 61, 46], [46, 52, 19], [61, 51, 46], [46, 67, 54], [51, 67, 46], [88, 30, 2], [2, 30, 82], [40, 96, 77], [77, 96, 4], [40, 77, 91], [91, 77, 48], [73, 89, 83], [25, 73, 83], [83, 2, 82], [83, 82, 25], [60, 26, 81], [81, 97, 60], [81, 26, 68], [68, 84, 81]], 'logical': [{'n': [-1.0, 3e-06, 0.0], 'c': [-0.155838, -0.000529, 0.001245], 's': 0.240742}, {'n': [-2e-06, 0.002797, -0.999996], 'c': [0.001354, -0.000909, -0.16813], 's': 0.248109}, {'n': [4e-06, -0.002797, 0.999996], 'c': [0.002542, -0.000953, 0.164772], 's': 0.229849}, {'n': [-5e-06, -0.999996, -0.002789], 'c': [-0.00138, -0.165542, 0.000666], 's': 0.249005}, {'n': [1.0, 1e-06, 3e-06], 'c': [0.175545, 0.000947, 0.001634], 's': 0.252934}, {'n': [1e-06, 0.999996, 0.002795], 'c': [-0.001719, 0.177995, 0.004616], 's': 0.240964}]}}

class DiceStage(QWidget):
    """Polyhedral dice renderer / Jolt stage.

    D4/D8/D12 code remains in this class for future work, but the current UI exposes
    only D6. D6 can now roll up to 100 fully independent rigid bodies at once.
    """

    rollFinished = Signal(object)
    countsChanged = Signal(int, int, int, int, int, int)
    populationChanged = Signal(int, int, int)
    loadingProgress = Signal(int, int)
    loadingStateChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(430)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

        self.animating = False
        self.last_tick = time.perf_counter()
        self.physics_accumulator = 0.0
        self.physics_dt = 1.0 / 240.0
        self.sleep_time = 0.0
        self.unstable_time = 0.0
        self.result = 1
        self.die_type = 6
        self.die_radius = 0.300

        # Multi-D6 runtime state. Each item owns its own Jolt body and OpenGL meshes.
        self.dice = []
        self.roll_count = 1
        self.current_counts = {i: 0 for i in range(1, 7)}
        self.discarded_count = 0
        self.max_rescues_per_die = 10

        # Batch-spawn state. Creating 100 OpenGL items in one UI event is the main
        # source of the perceived "freeze"; Jolt body creation is comparatively cheap.
        # We therefore cache D6 geometry and instantiate dice in small UI-friendly
        # batches while reporting real progress.
        self.loading_dice = False
        self.pending_spawn_count = 0
        self.spawned_count = 0
        self.spawn_batch_size = 5
        self._cached_d6_visual_data = None

        # User-adjustable release height. 150% is the new, more dramatic default.
        self.release_height_percent = 150

        # Safety recovery: if a die falls well below the tabletop, move it back to
        # its own original release position while preserving its current linear and
        # angular momentum. This prevents out-of-world bodies from running forever.
        self.fall_rescue_z = -2.6
        self.rescue_cooldown = 0.45

        self.vertices = []
        self.render_faces = []
        self.face_values = []
        self.face_normals_local = []
        self.face_centers_local = []
        self.face_scales = []
        self.d4_corner_dirs = []
        self.d4_corner_positions = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        if not OPENGL_3D_AVAILABLE or not JOLT_AVAILABLE:
            self.view = None
            self.die_mesh = None
            self.face_plate_mesh = None
            self.pip_shadow_mesh = None
            self.pip_mesh_black = None
            self.pip_mesh_red = None
            self.world = None
            self.die_body = None
            msg = QLabel(TXT(
                "3D 骰子组件尚未加载。请使用新版 run.bat 启动器。",
                "3D dice components are unavailable. Please use the updated run.bat launcher."
            ))
            msg.setAlignment(Qt.AlignCenter)
            msg.setWordWrap(True)
            msg.setObjectName("pageDesc")
            root.addWidget(msg, 1)
            return

        self.view = gl.GLViewWidget()
        self.view.setBackgroundColor((7, 7, 7, 255))
        self.view.setCameraPosition(pos=QVector3D(0.10, 0.08, 0.38),
                                    distance=10.4, elevation=22, azimuth=-42)
        root.addWidget(self.view, 1)

        # Circular tabletop: visual disc + outline. The physical collider is a
        # matching static convex prism, rebuilt only when the requested table size
        # changes before a roll.
        self.floor = None
        self.floor_border = None
        self.floor_body = None
        self.table_radius = 4.6

        self.die_mesh = None
        self.face_plate_mesh = None
        self.pip_shadow_mesh = None
        self.pip_mesh_black = None
        self.pip_mesh_red = None

        self.world = culverin.PhysicsWorld({
            "gravity": (0.0, 0.0, -9.81),
            "penetration_slop": 0.002,
            "num_threads": 2,
        })
        self._set_circular_table_radius(self.table_radius)
        self.die_body = None
        self.set_die_type(6)

    def _set_circular_table_radius(self, radius):
        """Create a circular transparent grid tabletop and matching Jolt collider."""
        radius = max(3.5, float(radius))
        if (
            abs(radius - getattr(self, "table_radius", 0.0)) < 0.01
            and self.floor is not None
            and self.floor_body is not None
        ):
            return
        self.table_radius = radius

        if self.floor is not None:
            try:
                self.view.removeItem(self.floor)
            except Exception:
                pass
        if self.floor_border is not None:
            try:
                self.view.removeItem(self.floor_border)
            except Exception:
                pass
        if self.floor_body is not None:
            try:
                self.world.destroy_body(self.floor_body)
            except Exception:
                pass

        # Visual tabletop: restore the old transparent dark grid language, but clip
        # every grid line mathematically to the circular tabletop boundary.
        spacing = 1.0
        grid_points = []
        limit = int(math.floor(radius / spacing))

        for i in range(-limit, limit + 1):
            x = i * spacing
            y_extent = math.sqrt(max(0.0, radius * radius - x * x))
            grid_points.append((x, -y_extent, 0.008))
            grid_points.append((x,  y_extent, 0.008))

        for i in range(-limit, limit + 1):
            y = i * spacing
            x_extent = math.sqrt(max(0.0, radius * radius - y * y))
            grid_points.append((-x_extent, y, 0.008))
            grid_points.append(( x_extent, y, 0.008))

        self.floor = gl.GLLinePlotItem(
            pos=np.asarray(grid_points, dtype=float),
            color=(0.14, 0.14, 0.14, 0.31),
            width=1.0,
            antialias=True,
            mode="lines",
        )
        self.view.addItem(self.floor)

        # Solid black circular rim around the transparent grid.
        segments = 96
        outline = []
        for i in range(segments + 1):
            a = 2.0 * math.pi * i / segments
            outline.append((
                math.cos(a) * radius,
                math.sin(a) * radius,
                0.014,
            ))

        self.floor_border = gl.GLLinePlotItem(
            pos=np.asarray(outline, dtype=float),
            color=(0.01, 0.01, 0.01, 1.0),
            width=2.2,
            antialias=True,
            mode="line_strip",
        )
        self.view.addItem(self.floor_border)

        # Physics remains genuinely circular: a thin many-sided static convex prism.
        # Only the table's rendering style changed.
        hull_points = []
        thickness = 0.12
        for z in (0.0, -thickness):
            for i in range(32):
                a = 2.0 * math.pi * i / 32
                hull_points.append((
                    math.cos(a) * radius,
                    math.sin(a) * radius,
                    z,
                ))

        self.floor_body = self.world.create_convex_hull(
            pos=(0.0, 0.0, 0.0),
            rot=(0.0, 0.0, 0.0, 1.0),
            points=np.asarray(hull_points, dtype=np.float32),
            motion=culverin.MOTION_STATIC,
            mass=0.0,
            friction=0.72,
            restitution=0.18,
        )

    @staticmethod
    def _normalize(v):
        a = np.asarray(v, dtype=float)
        n = float(np.linalg.norm(a))
        return a if n < 1e-12 else a / n

    @staticmethod
    def _face_normal(vertices, face):
        a = np.asarray(vertices[face[0]], dtype=float)
        b = np.asarray(vertices[face[1]], dtype=float)
        c = np.asarray(vertices[face[2]], dtype=float)
        n = np.cross(b-a, c-a)
        n = DiceStage._normalize(n)
        center = np.mean([np.asarray(vertices[i], dtype=float) for i in face], axis=0)
        if float(np.dot(n, center)) < 0:
            n = -n
        return n

    @staticmethod
    def _dual_polyhedron(vertices, faces, target_radius=0.30):
        verts = [np.asarray(v, dtype=float) for v in vertices]
        centers = []
        oriented = []
        for f in faces:
            f = list(f)
            c = np.mean([verts[i] for i in f], axis=0)
            a, b, cc = verts[f[0]], verts[f[1]], verts[f[2]]
            if float(np.dot(np.cross(b-a, cc-a), c)) < 0:
                f.reverse()
            oriented.append(f)
            centers.append(DiceStage._normalize(c))

        dual_faces = []
        for vi, v in enumerate(verts):
            incident = [fi for fi, f in enumerate(oriented) if vi in f]
            axis = DiceStage._normalize(v)
            ref = np.asarray((1.,0.,0.))
            if abs(float(np.dot(axis, ref))) > .85:
                ref = np.asarray((0.,1.,0.))
            u = DiceStage._normalize(np.cross(axis, ref))
            w = np.cross(axis, u)
            def ang(fi):
                p = centers[fi]
                t = p - axis * float(np.dot(p, axis))
                return math.atan2(float(np.dot(t,w)), float(np.dot(t,u)))
            incident.sort(key=ang)
            dual_faces.append(incident)

        centers = [tuple(float(x)*target_radius for x in c) for c in centers]
        return centers, dual_faces

    def _procedural_d10(self):
        n = 5
        z = .62
        primal = []
        for i in range(n):
            a = 2*math.pi*i/n
            primal.append((math.cos(a), math.sin(a), z))
        for i in range(n):
            a = 2*math.pi*(i+.5)/n
            primal.append((math.cos(a), math.sin(a), -z))
        pf = [tuple(range(n)), tuple(range(2*n-1,n-1,-1))]
        for i in range(n):
            j=(i+1)%n
            pf.append((i,j,n+i))
            pf.append((j,n+j,n+i))
        return self._dual_polyhedron(primal, pf, self.die_radius)

    def _load_geometry(self, sides):
        # The uploaded dice set contains D4/D6/D8/D12/D20. We use its clean
        # convex outer silhouettes for D4/D6/D8/D12 and deliberately discard
        # every sculpted numeral. D10 is not present, so it remains procedural.
        if str(sides) in REFERENCE_DICE_MODELS:
            d = REFERENCE_DICE_MODELS[str(sides)]
            verts = [tuple(v) for v in d["v"]]
            faces = [tuple(f) for f in d["f"]]
            logical = d["logical"]
            normals = [self._normalize(x["n"]) for x in logical]
            centers = [np.asarray(x["c"], dtype=float) for x in logical]
            scales = [float(x["s"]) for x in logical]
            return verts, faces, normals, centers, scales

        verts, poly_faces = self._procedural_d10()
        tri_faces = []
        normals, centers, scales = [], [], []
        for face in poly_faces:
            pts=[np.asarray(verts[i],dtype=float) for i in face]
            c=np.mean(pts,axis=0)
            n=self._face_normal(verts, face)
            normals.append(n)
            centers.append(c)
            scales.append(min(float(np.linalg.norm(p-c)) for p in pts)*.66)
            for i in range(1,len(face)-1):
                tri_faces.append((face[0],face[i],face[i+1]))
        return verts, tri_faces, normals, centers, scales

    def _pip_positions(self, value):
        if value == 1:
            return [(0.,0.)]
        cols = 3
        rows = math.ceil(value/cols)
        spacing = .30
        out=[]
        for i in range(value):
            row=i//cols
            col=i%cols
            count=min(cols,value-row*cols)
            x0=-(count-1)*spacing/2
            out.append((x0+col*spacing, (rows-1)*spacing/2-row*spacing))
        return out

    @staticmethod
    def _append_disc(verts, faces, colors, center, u, v, radius, color, segments=12):
        center=np.asarray(center,dtype=float)
        u=DiceStage._normalize(u)
        v=DiceStage._normalize(v)
        base=len(verts)
        verts.append(tuple(center))
        for k in range(segments):
            a=2*math.pi*k/segments
            q=center+(u*math.cos(a)+v*math.sin(a))*radius
            verts.append(tuple(q))
        for k in range(segments):
            faces.append((base,base+1+k,base+1+((k+1)%segments)))
            colors.append(color)

    def _make_face_texture(self, polygon_sides, value, size=256):
        img = QImage(size, size, QImage.Format_RGBA8888)
        img.fill(QColor(0, 0, 0, 0))
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx = size / 2.0
        cy = size / 2.0
        radius = size * 0.42
        rotation = {3: -math.pi / 2, 4: math.pi / 4, 5: -math.pi / 2}.get(polygon_sides, 0.0)

        pts = []
        for i in range(polygon_sides):
            ang = rotation + 2.0 * math.pi * i / polygon_sides
            pts.append(QPointF(cx + math.cos(ang) * radius, cy + math.sin(ang) * radius))

        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        path.closeSubpath()

        p.fillPath(path, QColor(236, 236, 234, 255))
        edge_pen = QPen(QColor(198, 198, 196, 255), max(2.0, size * 0.018))
        edge_pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(edge_pen)
        p.drawPath(path)

        p.setPen(Qt.NoPen)
        shadow_color = QColor(150, 150, 150, 130)
        black_color = QColor(18, 18, 18)
        red_color = QColor(200, 26, 26)
        pip_color = red_color if value == 1 else black_color
        pip_shadow_r = size * 0.030
        pip_r = size * 0.023

        def draw_cluster(origin, cluster_value, cluster_scale):
            for px, py in self._pip_positions(cluster_value):
                x = origin.x() + px * cluster_scale
                y = origin.y() - py * cluster_scale
                p.setBrush(shadow_color)
                p.drawEllipse(QPointF(x, y + size * 0.005), pip_shadow_r, pip_shadow_r)
                p.setBrush(pip_color)
                p.drawEllipse(QPointF(x, y), pip_r, pip_r)

        if self.die_type == 4:
            for pt in pts:
                origin = QPointF(cx + (pt.x() - cx) * 0.54, cy + (pt.y() - cy) * 0.54)
                draw_cluster(origin, value, size * 0.075)
        else:
            draw_cluster(QPointF(cx, cy), value, size * 0.105)

        p.end()
        return img

    def _build_legacy_d6_visual(self):
        """Restore the rounded D6 appearance from the earlier v0.11.5-era build.

        This is visual-only. The physics body remains a separate invisible perfect
        box collider, exactly as requested.
        """
        h = 0.335
        r = 0.058
        s = h - r

        verts=[]
        faces=[]
        colors=[]
        vertex_lookup={}

        face_base={
            ('x',  1):0.900, ('x',-1):0.835,
            ('y',  1):0.925, ('y',-1):0.860,
            ('z',  1):0.955, ('z',-1):0.805,
        }
        light=np.asarray((-0.30,-0.40,0.86),dtype=float)
        light/=np.linalg.norm(light)

        def rounded_point(p):
            p=np.asarray(p,dtype=float)
            q=np.clip(p,-s,s)
            d=p-q
            n=float(np.linalg.norm(d))
            if n<1e-12:
                return p
            return q+d*(r/n)

        def vertex_id(p):
            key=tuple(round(float(v),9) for v in p)
            idx=vertex_lookup.get(key)
            if idx is None:
                idx=len(verts)
                vertex_lookup[key]=idx
                verts.append(tuple(float(v) for v in p))
            return idx

        def tri_color(a,b,c,base):
            pa=np.asarray(verts[a],dtype=float)
            pb=np.asarray(verts[b],dtype=float)
            pc=np.asarray(verts[c],dtype=float)
            normal=np.cross(pb-pa,pc-pa)
            ln=float(np.linalg.norm(normal))
            if ln>1e-9:
                normal/=ln
            illum=0.93+0.07*max(0.0,float(np.dot(normal,light)))
            shade=max(0.68,min(0.985,base*illum))
            return (shade,shade,max(0.0,shade-0.008),1.0)

        steps=12
        vals=np.linspace(-h,h,steps+1)

        def build_face(axis,sign):
            base=face_base[(axis,sign)]
            grid=[[None]*(steps+1) for _ in range(steps+1)]
            for iu,u0 in enumerate(vals):
                for iv,v0 in enumerate(vals):
                    if axis=='x': raw=(sign*h,u0,v0)
                    elif axis=='y': raw=(u0,sign*h,v0)
                    else: raw=(u0,v0,sign*h)
                    grid[iu][iv]=vertex_id(rounded_point(raw))
            for iu in range(steps):
                for iv in range(steps):
                    a=grid[iu][iv]; b=grid[iu+1][iv]; c=grid[iu+1][iv+1]; d=grid[iu][iv+1]
                    tris=((a,b,c),(a,c,d)) if sign>0 else ((a,c,b),(a,d,c))
                    if axis=='y':
                        tris=tuple((t[0],t[2],t[1]) for t in tris)
                    for tri in tris:
                        faces.append(tri)
                        colors.append(tri_color(*tri,base))

        for axis in ('x','y','z'):
            build_face(axis,1)
            build_face(axis,-1)

        body_md=gl.MeshData(
            vertexes=np.asarray(verts,dtype=float),
            faces=np.asarray(faces,dtype=np.int32),
            faceColors=np.asarray(colors,dtype=float)
        )
        body_mesh=gl.GLMeshItem(meshdata=body_md,smooth=True,drawFaces=True,drawEdges=False)
        body_mesh.setGLOptions('opaque')

        black_v=[]; black_f=[]
        red_v=[]; red_f=[]
        eps=0.0035
        pip_scale=s*0.90
        pip_r=h*0.070

        def add_disc(target_v,target_f,center,axis,radius,segments=18):
            cx,cy,cz=center
            base=len(target_v)
            target_v.append((cx,cy,cz))
            for i in range(segments):
                a=2.0*math.pi*i/segments
                ca,sa=math.cos(a)*radius,math.sin(a)*radius
                if axis=='z': target_v.append((cx+ca,cy+sa,cz))
                elif axis=='x': target_v.append((cx,cy+ca,cz+sa))
                else: target_v.append((cx+ca,cy,cz+sa))
            for i in range(segments):
                target_f.append((base,base+1+i,base+1+((i+1)%segments)))

        face_defs=[
            (1,'z',lambda u,v:(u*pip_scale,v*pip_scale,h+eps)),
            (6,'z',lambda u,v:(u*pip_scale,-v*pip_scale,-h-eps)),
            (3,'x',lambda u,v:(h+eps,u*pip_scale,v*pip_scale)),
            (4,'x',lambda u,v:(-h-eps,-u*pip_scale,v*pip_scale)),
            (2,'y',lambda u,v:(u*pip_scale,h+eps,v*pip_scale)),
            (5,'y',lambda u,v:(-u*pip_scale,-h-eps,v*pip_scale)),
        ]
        for value,axis,mapper in face_defs:
            target_v,target_f=(red_v,red_f) if value==1 else (black_v,black_f)
            for u,v in self._pip_positions(value):
                add_disc(target_v,target_f,mapper(u,v),axis,pip_r)

        def make_pips(vs,fs,color):
            if not vs:
                return None
            pm=gl.GLMeshItem(
                vertexes=np.asarray(vs,dtype=float),
                faces=np.asarray(fs,dtype=np.int32),
                color=color,smooth=False,drawFaces=True,drawEdges=False,shader=None
            )
            pm.setGLOptions('opaque')
            return pm

        black_mesh=make_pips(black_v,black_f,(0.02,0.02,0.02,1.0))
        red_mesh=make_pips(red_v,red_f,(0.82,0.04,0.035,1.0))
        return body_mesh,None,None,black_mesh,red_mesh

    def _build_classic_poly_visual(self):
        """Classic white physical-die look for D4 / D8 / D12.

        This keeps the same visual language as the restored D6: a softly shaded
        white body with dark pips and a red single pip, while physics continue to
        use the separate invisible collider.
        """
        face_colors = []
        light = np.asarray((-0.28, -0.36, 0.89), dtype=float)
        light /= max(1e-9, np.linalg.norm(light))
        verts_arr = np.asarray(self.vertices, dtype=float)
        for face in self.render_faces:
            a = verts_arr[face[0]]
            b = verts_arr[face[1]]
            c = verts_arr[face[2]]
            normal = np.cross(b - a, c - a)
            ln = float(np.linalg.norm(normal))
            if ln > 1e-9:
                normal /= ln
            shade = 0.84 + 0.10 * max(0.0, float(np.dot(normal, light)))
            warm_b = max(0.0, shade - 0.010)
            face_colors.append((min(0.97, shade), min(0.97, shade), min(0.965, warm_b), 1.0))

        md = gl.MeshData(
            vertexes=np.asarray(self.vertices, dtype=float),
            faces=np.asarray(self.render_faces, dtype=np.int32),
            faceColors=np.asarray(face_colors, dtype=float)
        )
        mesh = gl.GLMeshItem(meshdata=md, smooth=True, drawFaces=True, drawEdges=False)
        mesh.setGLOptions('opaque')

        wall_v, wall_f = [], []
        black_v, black_f = [], []
        red_v, red_f = [], []

        def append_recessed_pip(center, u, v, normal, radius, depth, bottom_target_v, bottom_target_f, segments=18):
            center = np.asarray(center, dtype=float)
            u = self._normalize(u)
            v = self._normalize(v)
            normal = self._normalize(normal)

            top_ring = []
            bottom_ring = []
            rim_radius = radius
            bottom_radius = radius * 0.90
            bottom_center = center - normal * depth

            for k in range(segments):
                a = 2 * math.pi * k / segments
                ring_dir = u * math.cos(a) + v * math.sin(a)
                top_ring.append(center + ring_dir * rim_radius)
                bottom_ring.append(bottom_center + ring_dir * bottom_radius)

            wall_base = len(wall_v)
            for p in top_ring:
                wall_v.append(tuple(p))
            for p in bottom_ring:
                wall_v.append(tuple(p))

            for k in range(segments):
                t0 = wall_base + k
                t1 = wall_base + ((k + 1) % segments)
                b0 = wall_base + segments + k
                b1 = wall_base + segments + ((k + 1) % segments)
                wall_f.append((t0, b0, b1))
                wall_f.append((t0, b1, t1))

            base = len(bottom_target_v)
            bottom_target_v.append(tuple(bottom_center))
            for p in bottom_ring:
                bottom_target_v.append(tuple(p))
            for k in range(segments):
                bottom_target_f.append((base, base + 1 + k, base + 1 + ((k + 1) % segments)))

        def prepare_axes(normal):
            ref = np.asarray((1.0, 0.0, 0.0))
            if abs(float(np.dot(ref, normal))) > 0.85:
                ref = np.asarray((0.0, 1.0, 0.0))
            u = self._normalize(np.cross(normal, ref))
            v = self._normalize(np.cross(normal, u))
            return u, v

        pit_depth = max(0.010, self.die_radius * 0.060)

        if self.die_type == 4:
            corner_values = [1, 2, 3, 4]
            for fi, (normal, center, scale) in enumerate(zip(self.face_normals_local, self.face_centers_local, self.face_scales)):
                u, v = prepare_axes(normal)
                for ci in range(4):
                    if ci == fi:
                        continue
                    corner = self.d4_corner_positions[ci]
                    target = corner * 0.72 + center * 0.28
                    target = target - normal * float(np.dot(target - center, normal))
                    target = target - normal * (pit_depth * 0.30)
                    value = corner_values[ci]
                    cluster_scale = scale * 0.25
                    radius = max(0.0070, self.die_radius * 0.024)
                    for px, py in self._pip_positions(value):
                        c = target + (u * px + v * py) * cluster_scale
                        if value == 1:
                            append_recessed_pip(c, u, v, normal, radius, pit_depth, red_v, red_f, 14)
                        else:
                            append_recessed_pip(c, u, v, normal, radius, pit_depth, black_v, black_f, 14)
        else:
            for normal, center, scale, value in zip(self.face_normals_local, self.face_centers_local, self.face_scales, self.face_values):
                u, v = prepare_axes(normal)
                radius = max(0.0080, self.die_radius * 0.026)
                cluster_scale = scale * 1.00
                for px, py in self._pip_positions(value):
                    c = center + (u * px + v * py) * cluster_scale
                    c = c - normal * (pit_depth * 0.32)
                    if value == 1:
                        append_recessed_pip(c, u, v, normal, radius, pit_depth, red_v, red_f, 18)
                    else:
                        append_recessed_pip(c, u, v, normal, radius, pit_depth, black_v, black_f, 18)

        def make_colored_mesh(vs, fs, color):
            if not vs or not fs:
                return None
            pm = gl.GLMeshItem(
                vertexes=np.asarray(vs, dtype=float),
                faces=np.asarray(fs, dtype=np.int32),
                color=color, smooth=False, drawFaces=True, drawEdges=False, shader=None
            )
            pm.setGLOptions('opaque')
            return pm

        wall_mesh = make_colored_mesh(wall_v, wall_f, (0.32, 0.32, 0.32, 1.0))
        black_mesh = make_colored_mesh(black_v, black_f, (0.02, 0.02, 0.02, 1.0))
        red_mesh = make_colored_mesh(red_v, red_f, (0.82, 0.04, 0.035, 1.0))
        return mesh, wall_mesh, None, black_mesh, red_mesh

    def _build_render_meshes(self):
        if self.die_type == 6:
            return self._build_legacy_d6_visual()
        if self.die_type in (4, 8, 12):
            return self._build_classic_poly_visual()

        # Fallback renderer (kept for safety even though D10 is currently removed).
        body_face_colors = []
        for i in range(len(self.render_faces)):
            shade = 0.76 + 0.020 * ((i % 4) - 1.5)
            body_face_colors.append((shade, shade, shade, 1.0))

        body_md = gl.MeshData(
            vertexes=np.asarray(self.vertices, dtype=float),
            faces=np.asarray(self.render_faces, dtype=np.int32),
            faceColors=np.asarray(body_face_colors, dtype=float)
        )
        body_mesh = gl.GLMeshItem(meshdata=body_md, smooth=True, drawFaces=True, drawEdges=False)
        body_mesh.setGLOptions("opaque")

        face_entries = []

        def prepare_axes(normal):
            ref = np.asarray((1.0, 0.0, 0.0))
            if abs(float(np.dot(ref, normal))) > 0.85:
                ref = np.asarray((0.0, 1.0, 0.0))
            u = self._normalize(np.cross(normal, ref))
            v = self._normalize(np.cross(normal, u))
            return u, v

        face_sides = {4: 3, 6: 4, 8: 3, 12: 5}.get(self.die_type, 4)
        radius_mult = {3: 1.60, 4: 1.48, 5: 1.36}
        face_radius_mult = radius_mult.get(face_sides, 1.45)
        face_rot = {3: -math.pi / 2, 4: math.pi / 4, 5: -math.pi / 2}.get(face_sides, 0.0)
        plate_offset = max(0.010, self.die_radius * 0.030)
        uv_radius = 0.44

        for normal, center, scale, value in zip(self.face_normals_local, self.face_centers_local, self.face_scales, self.face_values):
            u, v = prepare_axes(normal)
            cap_center = np.asarray(center, dtype=float) + np.asarray(normal, dtype=float) * plate_offset
            verts = [tuple(cap_center)]
            uvs = [(0.5, 0.5)]
            radius = scale * face_radius_mult
            for k in range(face_sides):
                ang = face_rot + 2 * math.pi * k / face_sides
                pos = cap_center + (u * math.cos(ang) + v * math.sin(ang)) * radius
                verts.append(tuple(pos))
                uvs.append((0.5 + math.cos(ang) * uv_radius, 0.5 - math.sin(ang) * uv_radius))
            face_entries.append({
                "verts": verts,
                "uvs": uvs,
                "image": self._make_face_texture(face_sides, value)
            })

        textured_faces = TexturedFaceSetItem(face_entries)
        return body_mesh, textured_faces, None, None, None

    def set_die_type(self,sides):
        if self.animating:
            return
        sides=int(sides)
        if sides not in (4,6,8,12):
            sides=6
        self.die_type=sides
        verts,faces,normals,centers,scales=self._load_geometry(self.die_type)
        self.vertices=verts
        self.render_faces=faces
        self.face_centers_local=[np.asarray(c,dtype=float) for c in centers]
        # Rounded imported models contain many bevel triangles. For D6/D8/D12,
        # using the radial direction of each logical face centre is substantially
        # more robust than a triangle-derived normal and reaches exactly the
        # expected "face up" direction when the convex body settles.
        if self.die_type in (6,8,12):
            self.face_normals_local=[self._normalize(c) for c in self.face_centers_local]
        else:
            self.face_normals_local=[self._normalize(n) for n in normals]
        self.face_scales=list(scales)
        self.face_values=list(range(1,self.die_type+1))

        if self.die_type==6:
            # Keep result detection aligned with the restored legacy D6 visual.
            face_half = 0.335
            face_scale = face_half * 0.54
            self.face_normals_local = [
                np.asarray((0.0, 0.0, 1.0), dtype=float),   # 1
                np.asarray((0.0, 0.0, -1.0), dtype=float),  # 6
                np.asarray((1.0, 0.0, 0.0), dtype=float),   # 3
                np.asarray((-1.0, 0.0, 0.0), dtype=float),  # 4
                np.asarray((0.0, 1.0, 0.0), dtype=float),   # 2
                np.asarray((0.0, -1.0, 0.0), dtype=float),  # 5
            ]
            self.face_centers_local = [
                np.asarray((0.0, 0.0, face_half), dtype=float),
                np.asarray((0.0, 0.0, -face_half), dtype=float),
                np.asarray((face_half, 0.0, 0.0), dtype=float),
                np.asarray((-face_half, 0.0, 0.0), dtype=float),
                np.asarray((0.0, face_half, 0.0), dtype=float),
                np.asarray((0.0, -face_half, 0.0), dtype=float),
            ]
            self.face_scales = [face_scale] * 6
            self.face_values = [1, 6, 3, 4, 2, 5]

        if self.die_type==4:
            self.d4_corner_dirs=[-self._normalize(n) for n in self.face_normals_local]
            vv=np.asarray(self.vertices,dtype=float)
            self.d4_corner_positions=[]
            for d in self.d4_corner_dirs:
                self.d4_corner_positions.append(vv[int(np.argmax(vv@d))])

        if self.die_mesh is not None:
            self.view.removeItem(self.die_mesh)
        if self.face_plate_mesh is not None:
            self.view.removeItem(self.face_plate_mesh)
        if self.pip_shadow_mesh is not None:
            self.view.removeItem(self.pip_shadow_mesh)
        if self.pip_mesh_black is not None:
            self.view.removeItem(self.pip_mesh_black)
        if self.pip_mesh_red is not None:
            self.view.removeItem(self.pip_mesh_red)

        self.die_mesh,self.face_plate_mesh,self.pip_shadow_mesh,self.pip_mesh_black,self.pip_mesh_red=self._build_render_meshes()
        self.view.addItem(self.die_mesh)
        if self.face_plate_mesh is not None:
            self.view.addItem(self.face_plate_mesh)
        if self.pip_shadow_mesh is not None:
            self.view.addItem(self.pip_shadow_mesh)
        if self.pip_mesh_black is not None:
            self.view.addItem(self.pip_mesh_black)
        if self.pip_mesh_red is not None:
            self.view.addItem(self.pip_mesh_red)
        self._spawn_idle_body()
        self._apply_transform_from_world()

    def _remove_single_visual(self):
        for item in (
            self.die_mesh,
            self.face_plate_mesh,
            self.pip_shadow_mesh,
            self.pip_mesh_black,
            self.pip_mesh_red,
        ):
            if item is not None:
                try:
                    self.view.removeItem(item)
                except Exception:
                    pass
        self.die_mesh = None
        self.face_plate_mesh = None
        self.pip_shadow_mesh = None
        self.pip_mesh_black = None
        self.pip_mesh_red = None

    def _clear_multi_dice(self):
        for die in self.dice:
            body = die.get("body")
            if body is not None:
                try:
                    self.world.destroy_body(body)
                except Exception:
                    pass
            for item in die.get("meshes", ()):
                if item is not None:
                    try:
                        self.view.removeItem(item)
                    except Exception:
                        pass
        self.dice = []

    def _prepare_cached_d6_visual_data(self):
        """Build immutable D6 mesh data once instead of regenerating it up to 100 times."""
        if self._cached_d6_visual_data is not None:
            return self._cached_d6_visual_data

        h = 0.335
        r = 0.058
        s = h - r
        verts, faces, colors = [], [], []
        vertex_lookup = {}
        face_base = {
            ('x', 1): 0.900, ('x', -1): 0.835,
            ('y', 1): 0.925, ('y', -1): 0.860,
            ('z', 1): 0.955, ('z', -1): 0.805,
        }
        light = np.asarray((-0.30, -0.40, 0.86), dtype=float)
        light /= np.linalg.norm(light)

        def rounded_point(p):
            p = np.asarray(p, dtype=float)
            q = np.clip(p, -s, s)
            d = p - q
            n = float(np.linalg.norm(d))
            return p if n < 1e-12 else q + d * (r / n)

        def vertex_id(p):
            key = tuple(round(float(v), 9) for v in p)
            idx = vertex_lookup.get(key)
            if idx is None:
                idx = len(verts)
                vertex_lookup[key] = idx
                verts.append(tuple(float(v) for v in p))
            return idx

        def tri_color(a, b, c, base):
            pa, pb, pc = np.asarray(verts[a]), np.asarray(verts[b]), np.asarray(verts[c])
            normal = np.cross(pb-pa, pc-pa)
            ln = float(np.linalg.norm(normal))
            if ln > 1e-9:
                normal /= ln
            illum = 0.93 + 0.07 * max(0.0, float(np.dot(normal, light)))
            shade = max(0.68, min(0.985, base * illum))
            return (shade, shade, max(0.0, shade-0.008), 1.0)

        steps = 12
        vals = np.linspace(-h, h, steps + 1)

        def build_face(axis, sign):
            base = face_base[(axis, sign)]
            grid = [[None]*(steps+1) for _ in range(steps+1)]
            for iu, u0 in enumerate(vals):
                for iv, v0 in enumerate(vals):
                    if axis == 'x':
                        raw = (sign*h, u0, v0)
                    elif axis == 'y':
                        raw = (u0, sign*h, v0)
                    else:
                        raw = (u0, v0, sign*h)
                    grid[iu][iv] = vertex_id(rounded_point(raw))
            for iu in range(steps):
                for iv in range(steps):
                    a = grid[iu][iv]; b = grid[iu+1][iv]
                    c = grid[iu+1][iv+1]; d = grid[iu][iv+1]
                    tris = ((a,b,c),(a,c,d)) if sign > 0 else ((a,c,b),(a,d,c))
                    if axis == 'y':
                        tris = tuple((t[0], t[2], t[1]) for t in tris)
                    for tri in tris:
                        faces.append(tri)
                        colors.append(tri_color(*tri, base))

        for axis in ('x', 'y', 'z'):
            build_face(axis, 1)
            build_face(axis, -1)

        black_v, black_f, red_v, red_f = [], [], [], []
        eps = 0.0035
        pip_scale = s * 0.90
        pip_r = h * 0.070

        def add_disc(target_v, target_f, center, axis, radius, segments=18):
            cx, cy, cz = center
            base = len(target_v)
            target_v.append((cx,cy,cz))
            for i in range(segments):
                a = 2.0*math.pi*i/segments
                ca, sa = math.cos(a)*radius, math.sin(a)*radius
                if axis == 'z':
                    target_v.append((cx+ca, cy+sa, cz))
                elif axis == 'x':
                    target_v.append((cx, cy+ca, cz+sa))
                else:
                    target_v.append((cx+ca, cy, cz+sa))
            for i in range(segments):
                target_f.append((base, base+1+i, base+1+((i+1)%segments)))

        face_defs = [
            (1,'z',lambda u,v:(u*pip_scale,v*pip_scale,h+eps)),
            (6,'z',lambda u,v:(u*pip_scale,-v*pip_scale,-h-eps)),
            (3,'x',lambda u,v:(h+eps,u*pip_scale,v*pip_scale)),
            (4,'x',lambda u,v:(-h-eps,-u*pip_scale,v*pip_scale)),
            (2,'y',lambda u,v:(u*pip_scale,h+eps,v*pip_scale)),
            (5,'y',lambda u,v:(-u*pip_scale,-h-eps,v*pip_scale)),
        ]
        for value, axis, mapper in face_defs:
            target_v, target_f = (red_v, red_f) if value == 1 else (black_v, black_f)
            for u, v in self._pip_positions(value):
                add_disc(target_v, target_f, mapper(u,v), axis, pip_r)

        self._cached_d6_visual_data = {
            "body_v": np.asarray(verts, dtype=float),
            "body_f": np.asarray(faces, dtype=np.int32),
            "body_c": np.asarray(colors, dtype=float),
            "black_v": np.asarray(black_v, dtype=float),
            "black_f": np.asarray(black_f, dtype=np.int32),
            "red_v": np.asarray(red_v, dtype=float),
            "red_f": np.asarray(red_f, dtype=np.int32),
        }
        return self._cached_d6_visual_data

    def set_release_height_percent(self, percent):
        self.release_height_percent = max(100, min(300, int(percent)))

    def _configure_table_for_count(self, count):
        """Scale visible table and camera with both batch size and release height."""
        t = max(0.0, min(1.0, (count - 1) / 99.0))
        height_scale = self.release_height_percent / 100.0

        # Circular table grows with the number of dice.
        table_radius = 4.6 + 3.4 * t
        self._set_circular_table_radius(table_radius)

        # Pull farther back when either the batch is large or the user raises the
        # release height, so the top of the drop remains visible.
        height_extra = max(0.0, height_scale - 1.0)
        distance = 10.4 + 5.5 * t + 2.6 * height_extra
        elevation = 22 + 5 * t + 2.0 * height_extra
        center_z = 0.45 + 0.30 * height_extra
        self.view.setCameraPosition(
            pos=QVector3D(0.10, 0.08, center_z),
            distance=distance,
            elevation=elevation,
            azimuth=-42
        )

    def _new_d6_visual_instance(self):
        data = self._prepare_cached_d6_visual_data()

        body_md = gl.MeshData(
            vertexes=data["body_v"],
            faces=data["body_f"],
            faceColors=data["body_c"],
        )
        body_mesh = gl.GLMeshItem(
            meshdata=body_md, smooth=True, drawFaces=True, drawEdges=False
        )
        body_mesh.setGLOptions("opaque")

        def make_pips(vkey, fkey, color):
            vs = data[vkey]
            fs = data[fkey]
            if len(vs) == 0:
                return None
            item = gl.GLMeshItem(
                vertexes=vs,
                faces=fs,
                color=color,
                smooth=False,
                drawFaces=True,
                drawEdges=False,
                shader=None,
            )
            item.setGLOptions("opaque")
            return item

        black_mesh = make_pips("black_v", "black_f", (0.02,0.02,0.02,1.0))
        red_mesh = make_pips("red_v", "red_f", (0.82,0.04,0.035,1.0))
        meshes = [body_mesh, None, None, black_mesh, red_mesh]
        for item in meshes:
            if item is not None:
                self.view.addItem(item)
        return meshes

    def _apply_transform_to_meshes(self, meshes, pos, quat):
        q = QQuaternion(float(quat[3]), float(quat[0]), float(quat[1]), float(quat[2]))
        m = QMatrix4x4()
        m.translate(float(pos[0]), float(pos[1]), float(pos[2]))
        m.rotate(q)
        for item in meshes:
            if item is not None:
                item.setTransform(m)

    def _d6_result_from_quat(self, quat):
        """Read the same face numbers that are actually painted on legacy D6."""
        R = self._rotation_matrix_from_quat(quat)
        mapping = (
            (1, np.asarray((0.0, 0.0, 1.0), dtype=float)),
            (6, np.asarray((0.0, 0.0,-1.0), dtype=float)),
            (3, np.asarray((1.0, 0.0, 0.0), dtype=float)),
            (4, np.asarray((-1.0,0.0, 0.0), dtype=float)),
            (2, np.asarray((0.0, 1.0, 0.0), dtype=float)),
            (5, np.asarray((0.0,-1.0, 0.0), dtype=float)),
        )
        return max(mapping, key=lambda pair: float((R @ pair[1])[2]))[0]

    def _d6_flatness_from_quat(self, quat):
        R = self._rotation_matrix_from_quat(quat)
        normals = (
            np.asarray((0.0, 0.0, 1.0), dtype=float),
            np.asarray((0.0, 0.0,-1.0), dtype=float),
            np.asarray((1.0, 0.0, 0.0), dtype=float),
            np.asarray((-1.0,0.0, 0.0), dtype=float),
            np.asarray((0.0, 1.0, 0.0), dtype=float),
            np.asarray((0.0,-1.0, 0.0), dtype=float),
        )
        return max(float((R @ n)[2]) for n in normals)

    def _spawn_idle_body(self):
        if self.world is None:
            return
        if self.die_body is not None:
            try:self.world.destroy_body(self.die_body)
            except Exception:pass

        if self.die_type==6:
            d6_half=0.335
            self.die_body=self.world.create_body(
                pos=(0.,0.,d6_half),rot=(0.,0.,0.,1.),
                size=(d6_half,d6_half,d6_half),
                shape=culverin.SHAPE_BOX,motion=culverin.MOTION_DYNAMIC,
                mass=1.,friction=.58,restitution=.28,ccd=True)
        else:
            self.die_body=self.world.create_convex_hull(
                pos=(0.,0.,self.die_radius),rot=(0.,0.,0.,1.),
                points=np.asarray(self.vertices,dtype=np.float32),
                motion=culverin.MOTION_DYNAMIC,mass=1.,
                friction=.58,restitution=.28,ccd=True)

    @staticmethod
    def _quat_from_euler(rx,ry,rz):
        cr,sr=math.cos(rx*.5),math.sin(rx*.5)
        cp,sp=math.cos(ry*.5),math.sin(ry*.5)
        cy,sy=math.cos(rz*.5),math.sin(rz*.5)
        return (sr*cp*cy-cr*sp*sy,
                cr*sp*cy+sr*cp*sy,
                cr*cp*sy-sr*sp*cy,
                cr*cp*cy+sr*sp*sy)

    @staticmethod
    def _rotation_matrix_from_quat(quat):
        x,y,z,w=[float(q) for q in quat]
        return np.asarray((
            (1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)),
            (2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)),
            (2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y))
        ),dtype=float)

    def _top_face_value(self,quat):
        R=self._rotation_matrix_from_quat(quat)
        if self.die_type==4:
            # D4 is the exception: read the highest corner, not the highest face.
            return 1+max(range(4),key=lambda i:float((R@self.d4_corner_dirs[i])[2]))
        return self.face_values[max(
            range(len(self.face_normals_local)),
            key=lambda i:float((R@self.face_normals_local[i])[2])
        )]

    def _flatness(self,quat):
        R=self._rotation_matrix_from_quat(quat)
        if self.die_type==4:
            return max(float((R@d)[2]) for d in self.d4_corner_dirs)
        return max(float((R@n)[2]) for n in self.face_normals_local)

    def roll(self, count=1):
        if self.animating or self.loading_dice or self.world is None:
            return

        count = max(1, min(100, int(count)))
        self.roll_count = count
        self.current_counts = {i: 0 for i in range(1, 7)}
        self.discarded_count = 0
        self.countsChanged.emit(0, 0, 0, 0, 0, 0)
        self.populationChanged.emit(count, count, 0)

        self._configure_table_for_count(count)

        # Clear previous scene before reporting load progress.
        self._remove_single_visual()
        if self.die_body is not None:
            try:
                self.world.destroy_body(self.die_body)
            except Exception:
                pass
            self.die_body = None
        self._clear_multi_dice()

        self.pending_spawn_count = count
        self.spawned_count = 0
        self.loading_dice = True
        self.loadingStateChanged.emit(True)
        self.loadingProgress.emit(0, count)

        # For small throws we can load in a larger batch; for large 60-100 rolls smaller
        # batches keep Qt responsive and make progress genuinely visible.
        self.spawn_batch_size = 8 if count <= 12 else (6 if count <= 30 else (4 if count <= 60 else 3))

        # Start the batched preparation on the next event-loop turn.
        QTimer.singleShot(0, self._spawn_next_batch)

    def _spawn_next_batch(self):
        if not self.loading_dice:
            return

        count = self.pending_spawn_count
        remaining = count - self.spawned_count
        batch = min(self.spawn_batch_size, remaining)

        # Larger throws use a taller and wider release volume for visual impact.
        # Height is additionally multiplied by the user-controlled release slider.
        t = max(0.0, min(1.0, (count - 1) / 99.0))
        height_scale = self.release_height_percent / 100.0
        spread_x = 2.2 + 3.2 * t
        spread_y = 1.6 + 2.6 * t

        # Raised from the previous 3.6–6.2 world-unit baseline to 4.4–7.8,
        # before applying the user's 100–300% multiplier.
        base_height = (4.4 + 3.4 * t) * height_scale
        height_jitter = (0.9 + 2.0 * t) * height_scale

        d6_half = 0.335

        for _ in range(batch):
            i = self.spawned_count
            # Stratified-but-random distribution: avoids exact overlaps while looking
            # much more explosive than a rigid rectangular spawn grid.
            angle = (i * 2.399963229728653) + random.uniform(-0.25, 0.25)
            radial = math.sqrt((i + 0.5) / max(1, count))
            x = math.cos(angle) * spread_x * radial + random.uniform(-0.16, 0.16)
            y = math.sin(angle) * spread_y * radial + random.uniform(-0.14, 0.14)
            z = base_height + random.uniform(0.0, height_jitter)

            quat = self._quat_from_euler(
                random.uniform(-math.pi, math.pi),
                random.uniform(-math.pi, math.pi),
                random.uniform(-math.pi, math.pi),
            )

            body = self.world.create_body(
                pos=(x, y, z),
                rot=quat,
                size=(d6_half, d6_half, d6_half),
                shape=culverin.SHAPE_BOX,
                motion=culverin.MOTION_DYNAMIC,
                mass=1.0,
                friction=0.58,
                restitution=0.28,
                ccd=True,
            )

            # Stronger outward/downward release for large batches.
            horizontal_push = 0.18 + 0.34 * t
            self.world.apply_impulse(
                body,
                math.cos(angle) * random.uniform(0.05, horizontal_push),
                math.sin(angle) * random.uniform(0.05, horizontal_push),
                random.uniform(-0.42 - 0.18*t, -0.10),
            )
            self.world.apply_angular_impulse(
                body,
                random.uniform(-0.10, 0.10),
                random.uniform(-0.10, 0.10),
                random.uniform(-0.085, 0.085),
            )

            meshes = self._new_d6_visual_instance()
            self._apply_transform_to_meshes(meshes, (x, y, z), quat)

            self.dice.append({
                "body": body,
                "meshes": meshes,
                "spawn_pos": (float(x), float(y), float(z)),
                "last_rescue": -999.0,
                "rescue_count": 0,
                "discarded": False,
                "sleep_time": 0.0,
                # A die can come to rest on top of other dice and never reach the
                # tabletop. Track that independently so stacked dice cannot keep
                # the whole batch alive forever.
                "stack_quiet_time": 0.0,
                "unstable_time": 0.0,
                "settled": False,
                "result": None,
            })

            self.spawned_count += 1

        self.loadingProgress.emit(self.spawned_count, count)

        if self.spawned_count < count:
            QTimer.singleShot(0, self._spawn_next_batch)
            return

        # Only after all bodies and render items exist do we start Jolt. Therefore
        # every die begins moving together, even though preparation was incremental.
        self.loading_dice = False
        self.loadingStateChanged.emit(False)
        self.physics_accumulator = 0.0
        self.last_tick = time.perf_counter()
        self.animating = True
        self._timer.start()


    def _tick(self):
        if not self.animating:
            return

        now = time.perf_counter()
        frame_dt = max(0.001, min(0.05, now - self.last_tick))
        self.last_tick = now
        self.physics_accumulator += frame_dt

        steps = 0
        while self.physics_accumulator >= self.physics_dt and steps < 14:
            self._physics_step_multi()
            self.physics_accumulator -= self.physics_dt
            steps += 1

        newly_settled = False

        for die in self.dice:
            if die.get("discarded", False):
                continue
            body = die["body"]
            pos = self.world.get_position(body)
            quat = self.world.get_rotation(body)
            lv = self.world.get_velocity(body)
            av = self.world.get_angular_velocity(body)
            if not pos or not quat or not lv or not av:
                continue

            if float(pos[2]) < self.fall_rescue_z:
                if self._rescue_fallen_die(die, pos, quat, lv, av):
                    continue

            self._apply_transform_to_meshes(die["meshes"], pos, quat)

            if die["settled"]:
                continue

            linear = math.sqrt(sum(float(v) * float(v) for v in lv))
            angular = math.sqrt(sum(float(v) * float(v) for v in av))
            grounded = float(pos[2]) < 0.72

            # Normal tabletop settling.
            if grounded and linear < 0.115 and angular < 0.42:
                die["sleep_time"] += frame_dt
            else:
                die["sleep_time"] = max(0.0, die["sleep_time"] - frame_dt * 1.5)

            # Stacked-die settling: a die resting on top of other dice can be well
            # above z=0.72. If it is genuinely still (or only microscopically
            # trembling) for long enough, accept its current top-view result.
            # The longer dwell avoids falsely counting a die at the apex of a jump.
            if linear < 0.070 and angular < 0.30:
                die["stack_quiet_time"] += frame_dt
            else:
                die["stack_quiet_time"] = max(
                    0.0, die["stack_quiet_time"] - frame_dt * 2.0
                )

            tabletop_done = die["sleep_time"] > 0.34
            stacked_done = die["stack_quiet_time"] > 0.78

            if tabletop_done or stacked_done:
                # For a cube, all six faces have equal area. Under orthographic
                # projection from directly above, a face's projected area is
                # proportional to max(0, normal_z). Therefore the face with the
                # largest positive world-space Z normal is exactly the largest
                # visible top projection. _d6_result_from_quat implements this.
                value = self._d6_result_from_quat(quat)
                die["result"] = value
                die["settled"] = True
                self.current_counts[value] += 1
                newly_settled = True
                # Freeze only a die that has already satisfied the low-motion dwell.
                # Orientation is not changed, so the recorded face stays physical.
                try:
                    self.world.set_velocity(body, 0.0, 0.0, 0.0)
                    self.world.set_angular_velocity(body, 0.0, 0.0, 0.0)
                except Exception:
                    # Culverin builds may not expose explicit setters; counting still
                    # works because the body has already been marked settled.
                    pass

        if newly_settled:
            self.countsChanged.emit(
                self.current_counts[1],
                self.current_counts[2],
                self.current_counts[3],
                self.current_counts[4],
                self.current_counts[5],
                self.current_counts[6],
            )

        if self.dice and all(die["settled"] or die.get("discarded", False) for die in self.dice):
            results = [
                int(die["result"])
                for die in self.dice
                if not die.get("discarded", False) and die.get("result") is not None
            ]
            self.animating = False
            self._timer.stop()
            self.rollFinished.emit(results)

    def _discard_die_after_rescues(self, die):
        """Remove one pathological die after ten fall recoveries without recording it."""
        if die.get("discarded", False):
            return True

        body = die.get("body")
        if body is not None:
            try:
                self.world.destroy_body(body)
            except Exception:
                pass

        for item in die.get("meshes", ()):
            if item is not None:
                try:
                    self.view.removeItem(item)
                except Exception:
                    pass

        die["body"] = None
        die["meshes"] = []
        die["discarded"] = True
        die["settled"] = False
        die["result"] = None
        self.discarded_count += 1

        valid = max(0, self.roll_count - self.discarded_count)
        self.populationChanged.emit(valid, self.roll_count, self.discarded_count)
        return True

    def _rescue_fallen_die(self, die, pos, quat, lv, av):
        """Teleport a fallen D6 back to its own release point without killing momentum.

        Culverin/Jolt does not expose the same transform setter on every build, so the
        robust fallback is to rebuild that one rigid body at the original spawn point.
        Linear momentum is exact here because every D6 has mass 1. Angular momentum is
        reconstructed from the solid-cube inertia I = (1/6) * side^2 for each axis.
        The current orientation is preserved; only position changes.
        """
        now = time.perf_counter()
        if now - float(die.get("last_rescue", -999.0)) < self.rescue_cooldown:
            return False

        die["rescue_count"] = int(die.get("rescue_count", 0)) + 1
        if die["rescue_count"] >= self.max_rescues_per_die:
            return self._discard_die_after_rescues(die)

        old_body = die["body"]
        spawn_pos = die.get("spawn_pos", (0.0, 0.0, 4.0))
        d6_half = 0.335

        # Snapshot all motion before replacing the out-of-bounds rigid body.
        linear_velocity = tuple(float(v) for v in lv)
        angular_velocity = tuple(float(v) for v in av)
        orientation = tuple(float(v) for v in quat)

        try:
            self.world.destroy_body(old_body)
        except Exception:
            pass

        new_body = self.world.create_body(
            pos=spawn_pos,
            rot=orientation,
            size=(d6_half, d6_half, d6_half),
            shape=culverin.SHAPE_BOX,
            motion=culverin.MOTION_DYNAMIC,
            mass=1.0,
            friction=0.58,
            restitution=0.28,
            ccd=True,
        )

        # p = m*v and m == 1, so applying this impulse restores linear momentum.
        self.world.apply_impulse(
            new_body,
            linear_velocity[0], linear_velocity[1], linear_velocity[2]
        )

        # Solid cube principal inertia for mass 1.0 and side length 2*h.
        side = d6_half * 2.0
        inertia = (side * side) / 6.0
        self.world.apply_angular_impulse(
            new_body,
            angular_velocity[0] * inertia,
            angular_velocity[1] * inertia,
            angular_velocity[2] * inertia,
        )

        die["body"] = new_body
        die["last_rescue"] = now
        die["sleep_time"] = 0.0
        die["stack_quiet_time"] = 0.0
        die["unstable_time"] = 0.0
        die["settled"] = False
        die["result"] = None

        # Render immediately at the restored position so there is no frame where the
        # visual remains below the table after the physics body has been recovered.
        self._apply_transform_to_meshes(die["meshes"], spawn_pos, orientation)
        return True

    def _physics_step_multi(self):
        # Apply the tiny anti-edge equilibrium perturbation independently to every
        # D6 before advancing the shared Jolt world. Dice-dice collisions are solved
        # by Jolt in the same world, so all 1-100 dice genuinely move together.
        for die in self.dice:
            if die["settled"] or die.get("discarded", False):
                continue

            body = die["body"]
            pos = self.world.get_position(body)
            quat = self.world.get_rotation(body)
            lv = self.world.get_velocity(body)
            av = self.world.get_angular_velocity(body)
            if not pos or not quat or not lv or not av:
                continue

            # A die that has left the visible/physical tabletop is recovered far
            # below the surface rather than being allowed to fall forever.
            if float(pos[2]) < self.fall_rescue_z:
                self._rescue_fallen_die(die, pos, quat, lv, av)
                continue

            linear = math.sqrt(sum(float(v) * float(v) for v in lv))
            angular = math.sqrt(sum(float(v) * float(v) for v in av))
            flat = self._d6_flatness_from_quat(quat)
            grounded = float(pos[2]) < 0.70

            if grounded and flat < 0.975 and linear < 0.12 and angular < 0.32:
                die["unstable_time"] += self.physics_dt
                if die["unstable_time"] > 0.10:
                    phase = (id(die) % 997) * 0.013
                    t = time.perf_counter() + phase
                    self.world.apply_torque(
                        body,
                        0.00016 * math.sin(t * 17.1),
                        0.00015 * math.cos(t * 13.7),
                        0.00006 * math.sin(t * 9.3),
                    )
            else:
                die["unstable_time"] = 0.0

        self.world.step(self.physics_dt)

    def _physics_step(self):
        # Legacy single-die entry point retained for the hidden D4/D8/D12 code.
        if self.dice:
            self._physics_step_multi()
        else:
            self.world.step(self.physics_dt)

    def _apply_transform_from_world(self):
        # Legacy idle-die transform path.
        if self.die_mesh is None or self.die_body is None:
            return
        pos = self.world.get_position(self.die_body)
        quat = self.world.get_rotation(self.die_body)
        if not pos or not quat:
            return
        q = QQuaternion(float(quat[3]), float(quat[0]), float(quat[1]), float(quat[2]))
        m = QMatrix4x4()
        m.translate(float(pos[0]), float(pos[1]), float(pos[2]))
        m.rotate(q)
        self.die_mesh.setTransform(m)
        if self.face_plate_mesh is not None:
            self.face_plate_mesh.setTransform(m)
        if self.pip_shadow_mesh is not None:
            self.pip_shadow_mesh.setTransform(m)
        if self.pip_mesh_black is not None:
            self.pip_mesh_black.setTransform(m)
        if self.pip_mesh_red is not None:
            self.pip_mesh_red.setTransform(m)


class DicePage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(38, 28, 38, 24)
        outer.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(18)

        text_col = QVBoxLayout()
        text_col.setSpacing(3)
        kicker = QLabel("DICE / 3D PHYSICAL ROLL")
        kicker.setObjectName("kicker")
        title = QLabel(TXT("骰子", "Dice"))
        title.setObjectName("pageTitle")
        desc = QLabel(TXT(
            "目前仅开放 D6 骰子。可调整数量与投放高度后开始投掷。",
            "Only D6 dice are currently available. Adjust the quantity and release height, then roll."
        ))
        desc.setObjectName("pageDesc")
        desc.setWordWrap(True)
        text_col.addWidget(kicker)
        text_col.addWidget(title)
        text_col.addWidget(desc)
        head.addLayout(text_col, 1)

        # Upper-right result counter.
        self.stats_panel = QFrame()
        self.stats_panel.setObjectName("configPanel")
        stats = QGridLayout(self.stats_panel)
        stats.setContentsMargins(14, 10, 14, 10)
        stats.setHorizontalSpacing(14)
        stats.setVerticalSpacing(4)

        stats_title = QLabel(TXT("结果统计", "RESULT COUNTS"))
        stats_title.setObjectName("settingCategory")
        stats.addWidget(stats_title, 0, 0, 1, 6)

        self.count_labels = {}
        for idx, value in enumerate(range(1, 7)):
            num = QLabel(str(value))
            num.setAlignment(Qt.AlignCenter)
            num.setObjectName("statusText")
            count_lab = QLabel("0")
            count_lab.setAlignment(Qt.AlignCenter)
            count_lab.setStyleSheet("font-size: 18px; font-weight: 700;")
            stats.addWidget(num, 1, idx)
            stats.addWidget(count_lab, 2, idx)
            self.count_labels[value] = count_lab

        head.addWidget(self.stats_panel, 0, Qt.AlignTop)

        self.roll_button = QPushButton(TXT("投掷", "Roll"))
        self.roll_button.setObjectName("primaryButton")
        self.roll_button.setFixedSize(104, 42)
        self.roll_button.setCursor(Qt.PointingHandCursor)
        head.addWidget(self.roll_button, 0, Qt.AlignTop)
        outer.addLayout(head)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        d6_chip = QPushButton("D6")
        d6_chip.setObjectName("chipButton")
        d6_chip.setCheckable(True)
        d6_chip.setChecked(True)
        d6_chip.setEnabled(False)
        d6_chip.setMinimumWidth(64)
        controls.addWidget(d6_chip)

        quantity_label = QLabel(TXT("骰子数量", "Dice count"))
        quantity_label.setObjectName("statusText")
        controls.addWidget(quantity_label)

        self.quantity_input = QLineEdit("1")
        self.quantity_input.setObjectName("settingInput")
        self.quantity_input.setValidator(QIntValidator(1, 100, self.quantity_input))
        self.quantity_input.setFixedWidth(72)
        self.quantity_input.setAlignment(Qt.AlignCenter)
        controls.addWidget(self.quantity_input)

        range_hint = QLabel(TXT("1–100 个", "1–100"))
        range_hint.setObjectName("statusText")
        controls.addWidget(range_hint)

        controls.addSpacing(18)

        height_label = QLabel(TXT("投放高度", "Release height"))
        height_label.setObjectName("statusText")
        controls.addWidget(height_label)

        self.height_slider = QSlider(Qt.Horizontal)
        self.height_slider.setRange(100, 300)
        self.height_slider.setSingleStep(10)
        self.height_slider.setPageStep(25)
        self.height_slider.setValue(150)
        self.height_slider.setFixedWidth(180)
        self.height_slider.setToolTip(TXT(
            "调整骰子从桌面上方释放的高度（100%–300%）",
            "Adjust how high above the table the dice are released (100%–300%)"
        ))
        controls.addWidget(self.height_slider)

        self.height_value = QLabel("150%")
        self.height_value.setObjectName("statusText")
        self.height_value.setMinimumWidth(48)
        controls.addWidget(self.height_value)

        controls.addStretch(1)
        outer.addLayout(controls)

        self.loading_bar = QProgressBar()
        self.loading_bar.setRange(0, 100)
        self.loading_bar.setValue(0)
        self.loading_bar.setTextVisible(True)
        self.loading_bar.setFormat(TXT("准备骰子 %p%", "Preparing dice %p%"))
        self.loading_bar.setFixedHeight(18)
        self.loading_bar.hide()
        outer.addWidget(self.loading_bar)

        self.stage = DiceStage()
        outer.addWidget(self.stage, 1)

        bottom = QHBoxLayout()
        self.status = QLabel(TXT("D6 · 1 个 · 点击投掷", "D6 · 1 die · press Roll"))
        self.status.setObjectName("statusText")
        bottom.addWidget(self.status)
        bottom.addStretch(1)

        self.population_status = QLabel()
        self.population_status.setObjectName("statusText")
        self.population_status.setTextFormat(Qt.RichText)
        self.population_status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        bottom.addWidget(self.population_status)
        outer.addLayout(bottom)

        self._set_population_text(1, 1, 0)

        self.roll_button.clicked.connect(self._roll)
        self.stage.rollFinished.connect(self._finished)
        self.stage.countsChanged.connect(self._update_counts)
        self.stage.populationChanged.connect(self._set_population_text)
        self.stage.loadingProgress.connect(self._loading_progress)
        self.stage.loadingStateChanged.connect(self._loading_state)
        self.quantity_input.textChanged.connect(self._quantity_changed)
        self.height_slider.valueChanged.connect(self._height_changed)

        self.stage.set_release_height_percent(self.height_slider.value())

        if not OPENGL_3D_AVAILABLE or not JOLT_AVAILABLE:
            self.roll_button.setEnabled(False)
            self.quantity_input.setEnabled(False)
            self.height_slider.setEnabled(False)
            self.status.setText(TXT(
                "缺少 3D / Jolt 组件 · 请使用新版 run.bat 启动",
                "3D / Jolt components missing · launch with the updated run.bat"
            ))

    def _set_population_text(self, valid, total, discarded):
        valid = int(valid)
        total = int(total)
        discarded = int(discarded)
        red = "#d84a4a"
        if APP_SETTINGS.get("language") == "en":
            prefix = "VALID "
        else:
            prefix = "有效 "
        self.population_status.setText(
            f"{prefix}<b>{valid}</b> "
            f"<span style='color:{red}; font-weight:700'>({total}-{discarded})</span>"
        )

    def _quantity(self):
        try:
            return max(1, min(100, int(self.quantity_input.text() or "1")))
        except ValueError:
            return 1

    def _quantity_changed(self, _text):
        if self.stage.animating:
            return
        count = self._quantity()
        self._set_population_text(count, count, 0)
        self.status.setText(TXT(
            f"D6 · {count} 个 · 点击投掷",
            f"D6 · {count} {'die' if count == 1 else 'dice'} · press Roll"
        ))

    def _update_counts(self, c1, c2, c3, c4, c5, c6):
        counts = (c1, c2, c3, c4, c5, c6)
        for value, count in enumerate(counts, start=1):
            self.count_labels[value].setText(str(int(count)))

    def _height_changed(self, value):
        value = int(value)
        self.height_value.setText(f"{value}%")
        self.stage.set_release_height_percent(value)
        if not self.stage.animating and not self.stage.loading_dice:
            count = self._quantity()
            self.stage._configure_table_for_count(count)

    def _loading_state(self, loading):
        self.loading_bar.setVisible(bool(loading))
        self.height_slider.setEnabled(not loading)
        if loading:
            self.loading_bar.setValue(0)
        else:
            count = self._quantity()
            self.status.setText(TXT(
                f"{count} 个 D6 已全部生成 · 正在同时弹跳…",
                f"All {count} D6 {'die is' if count == 1 else 'dice are'} ready · rolling together…"
            ))

    def _loading_progress(self, current, total):
        total = max(1, int(total))
        current = max(0, min(total, int(current)))
        percent = int(round(current * 100 / total))
        self.loading_bar.setValue(percent)
        self.loading_bar.setFormat(TXT(
            f"准备骰子 {current}/{total} · {percent}%",
            f"Preparing dice {current}/{total} · {percent}%"
        ))
        self.status.setText(TXT(
            f"正在创建实体骰子 {current}/{total}…",
            f"Creating physical dice {current}/{total}…"
        ))

    def _roll(self):
        if self.stage.animating:
            return
        count = self._quantity()
        self.quantity_input.setText(str(count))
        self.roll_button.setEnabled(False)
        self.quantity_input.setEnabled(False)
        self.height_slider.setEnabled(False)
        self.status.setText(TXT(
            f"正在准备 {count} 个 D6…",
            f"Preparing {count} D6 {'die' if count == 1 else 'dice'}…"
        ))
        self.stage.roll(count)

    def _finished(self, results):
        self.roll_button.setEnabled(True)
        self.quantity_input.setEnabled(True)
        self.height_slider.setEnabled(True)
        total = len(results)
        total_sum = sum(results)
        discarded = int(self.stage.discarded_count)
        requested = int(self.stage.roll_count)
        self._set_population_text(total, requested, discarded)
        if discarded:
            self.status.setText(TXT(
                f"{total} 个有效 D6 已停止 · {discarded} 个弃用 · 点数总和：{total_sum}",
                f"{total} valid D6 settled · {discarded} discarded · total: {total_sum}"
            ))
        else:
            self.status.setText(TXT(
                f"{total} 个 D6 已停止 · 点数总和：{total_sum}",
                f"{total} D6 {'die has' if total == 1 else 'dice have'} settled · total: {total_sum}"
            ))


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

        # ---- Hotkeys ----
        layout.addSpacing(4)
        layout.addWidget(category("热键", "HOTKEYS"))
        hotkey_panel, hotkey_form = make_panel()
        self.hover_hotkey_button = HotkeyCaptureButton(APP_SETTINGS.get("hover_preview_hotkey", "V"))
        hotkey_form.addWidget(ResponsiveSettingRow(
            TXT("小卡牌悬停放大", "Small-card Hover Preview"),
            TXT(
                "按住绑定按键并悬停在较小的自由移动卡牌上时，会立即放大该卡牌。",
                "Hold the bound key while hovering a small free-move card to magnify it immediately."
            ),
            self.hover_hotkey_button
        ))
        layout.addWidget(hotkey_panel)

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
        self.hover_hotkey_button.hotkeyChanged.connect(self._hotkey_changed)
        self.riffle_input.editingFinished.connect(self._changed)
        self.cut_input.editingFinished.connect(self._changed)

    def _hotkey_changed(self, name):
        APP_SETTINGS["hover_preview_hotkey"] = (name or "V").upper()
        self.settingsChanged.emit()

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



AUDIO_EXTENSIONS = {".mp3", ".flac", ".wav", ".wave", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
LYRIC_EXTENSIONS = {".lrc", ".lyc"}


def _format_ms(ms):
    ms = max(0, int(ms or 0))
    sec = ms // 1000
    return f"{sec // 60}:{sec % 60:02d}"


def _safe_extract_zip(zip_path, out_dir):
    """Extract a zip without allowing path traversal."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.infolist():
            target = (out_dir / member.filename).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as src_f, open(target, "wb") as dst_f:
                shutil.copyfileobj(src_f, dst_f)


def _metadata_text(easy_tags, key, default=""):
    if not easy_tags:
        return default
    value = easy_tags.get(key)
    if not value:
        return default
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else default
    return str(value)


def _extract_cover_bytes(raw_audio):
    if raw_audio is None:
        return None
    try:
        pictures = getattr(raw_audio, "pictures", None)
        if pictures:
            return bytes(pictures[0].data)
    except Exception:
        pass

    tags = getattr(raw_audio, "tags", None)
    if tags is None:
        return None

    try:
        if hasattr(tags, "getall"):
            apic = tags.getall("APIC")
            if apic:
                return bytes(apic[0].data)
    except Exception:
        pass

    try:
        covr = tags.get("covr")
        if covr:
            return bytes(covr[0])
    except Exception:
        pass

    return None


def _read_track_info(path):
    path = Path(path)
    info = {
        "path": path,
        "title": path.stem,
        "artist": TXT("未知艺术家", "Unknown Artist"),
        "album": "",
        "track": "",
        "year": "",
        "duration_ms": 0,
        "cover_bytes": None,
        "cover_desc": TXT("无", "None"),
        "lyrics": [],
    }
    if not MUTAGEN_AVAILABLE:
        return info

    try:
        easy = MutagenFile(str(path), easy=True)
        if easy is not None:
            tags = getattr(easy, "tags", None) or {}
            info["title"] = _metadata_text(tags, "title", path.stem)
            info["artist"] = _metadata_text(tags, "artist", info["artist"])
            info["album"] = _metadata_text(tags, "album", "")
            info["track"] = _metadata_text(tags, "tracknumber", "")
            info["year"] = _metadata_text(tags, "date", "")
            length = getattr(getattr(easy, "info", None), "length", 0) or 0
            info["duration_ms"] = int(float(length) * 1000)
    except Exception:
        pass

    try:
        raw = MutagenFile(str(path), easy=False)
        cover = _extract_cover_bytes(raw)
        if cover:
            info["cover_bytes"] = cover
            info["cover_desc"] = TXT("内嵌封面", "Embedded")
    except Exception:
        pass

    # Plain WAV files frequently contain no metadata. Even then, read duration
    # directly from the RIFF/WAVE header so playlist timing still works.
    if path.suffix.lower() in {".wav", ".wave"} and not info["duration_ms"]:
        try:
            with wave.open(str(path), "rb") as wf:
                rate = wf.getframerate()
                frames = wf.getnframes()
                if rate:
                    info["duration_ms"] = int((frames / float(rate)) * 1000)
        except Exception:
            pass

    return info


def _parse_lrc(path):
    """Parse LRC/LYC and group same-timestamp lines into one lyric slot.

    This supports bilingual lyrics naturally:
        [00:12.00]Original language
        [00:12.00]Translated language

    Both lines are kept together and displayed at the same time.
    More than two same-timestamp lines are also preserved.
    """
    grouped = {}
    if not path or not Path(path).exists():
        return []

    try:
        raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return []

    stamp_re = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")

    for line in raw.splitlines():
        stamps = list(stamp_re.finditer(line))
        if not stamps:
            continue

        lyric = stamp_re.sub("", line).strip()
        if not lyric:
            continue

        for m in stamps:
            minutes = int(m.group(1))
            seconds = int(m.group(2))
            frac = m.group(3) or "0"

            if len(frac) == 1:
                frac_ms = int(frac) * 100
            elif len(frac) == 2:
                frac_ms = int(frac) * 10
            else:
                frac_ms = int(frac[:3])

            stamp_ms = minutes * 60000 + seconds * 1000 + frac_ms
            bucket = grouped.setdefault(stamp_ms, [])

            # Avoid accidental duplicated bilingual lines while preserving order.
            if lyric not in bucket:
                bucket.append(lyric)

    return [(stamp, lines) for stamp, lines in sorted(grouped.items())]


class MusicTrackRow(QFrame):
    clicked = Signal(int)

    def __init__(self, index, track, cover_pixmap, parent=None):
        super().__init__(parent)
        self.index = index
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("musicTrackRow")
        self.setStyleSheet("""
            QFrame#musicTrackRow {
                background: transparent;
                border: 0;
                border-radius: 8px;
            }
            QFrame#musicTrackRow:hover {
                background: rgba(255,255,255,0.045);
            }
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 7, 8, 7)
        lay.setSpacing(10)

        cover = QLabel()
        cover.setFixedSize(48, 48)
        cover.setPixmap(cover_pixmap.scaled(48, 48, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation))
        cover.setStyleSheet("border-radius: 6px;")
        lay.addWidget(cover)

        words = QVBoxLayout()
        words.setSpacing(1)
        title = QLabel(track["title"])
        title.setStyleSheet("font-size: 14px; font-weight: 650; color: #ededed;")
        artist = QLabel(track["artist"])
        artist.setStyleSheet("font-size: 12px; color: #919191;")
        title.setTextInteractionFlags(Qt.NoTextInteraction)
        artist.setTextInteractionFlags(Qt.NoTextInteraction)
        words.addWidget(title)
        words.addWidget(artist)
        lay.addLayout(words, 1)

        duration = QLabel(_format_ms(track["duration_ms"]))
        duration.setStyleSheet("color:#969696; font-size:12px;")
        duration.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(duration)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class DesktopLyricsWindow(QWidget):
    def __init__(self, parent=None):
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.label = QLabel("", self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(26, 12, 26, 12)
        lay.addWidget(self.label)

        self.settings = {}
        self._hue = 0
        self.rainbow_timer = QTimer(self)
        self.rainbow_timer.setInterval(55)
        self.rainbow_timer.timeout.connect(self._advance_rainbow)
        self.apply_settings({
            "font_family": QApplication.font().family(),
            "font_size": 30,
            "font_weight": 700,
            "text_color": "#ffffff",
            "rainbow": False,
            "background_enabled": False,
            "background_color": "#000000",
        })

    def apply_settings(self, settings):
        self.settings = dict(settings)
        if self.settings.get("rainbow"):
            self.rainbow_timer.start()
        else:
            self.rainbow_timer.stop()
        self._apply_style()
        self._reposition()

    def _advance_rainbow(self):
        self._hue = (self._hue + 3) % 360
        self._apply_style()

    def _apply_style(self):
        font = QFont(self.settings.get("font_family", QApplication.font().family()))
        font.setPointSize(int(self.settings.get("font_size", 30)))
        font.setWeight(QFont.Weight(int(self.settings.get("font_weight", 700))))
        self.label.setFont(font)

        if self.settings.get("rainbow"):
            color = QColor.fromHsv(self._hue, 220, 255).name()
        else:
            color = self.settings.get("text_color", "#ffffff")

        if self.settings.get("background_enabled"):
            bg = QColor(self.settings.get("background_color", "#000000"))
            bg_rgba = f"rgba({bg.red()},{bg.green()},{bg.blue()},170)"
        else:
            bg_rgba = "rgba(0,0,0,0)"

        self.label.setStyleSheet(
            f"color:{color}; background:{bg_rgba}; border-radius:10px; padding:8px 16px;"
        )

    def set_lyric(self, text):
        self.label.setText(text or "")
        if self.isVisible():
            self._reposition()

    def _reposition(self):
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        width = max(520, int(geo.width() * 0.72))
        height = 92
        self.resize(width, height)
        self.move(
            geo.x() + (geo.width() - width) // 2,
            geo.y() + geo.height() - height - 34,
        )


class MusicPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.library_dir = MUSIC_LIBRARY_DIR
        self.library_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.library_dir / ".music_cache"
        # The extraction cache is disposable. Rebuild it every time the app starts
        # so ZIP contents can never become stale after users replace archives.
        self._clear_music_cache()

        self.tracks = []
        self.current_index = -1
        self.current_lyrics = []
        self.current_lyric_index = -1
        self._seeking = False

        self.settings_store = QSettings("5imp1e 5atebox", "Music")
        self.desktop_lyrics = DesktopLyricsWindow(self)

        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.82)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._play_state_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)

        self._build_ui()
        self._load_customization()
        QTimer.singleShot(0, self.reload_library)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 22)
        root.setSpacing(12)

        top = QHBoxLayout()
        title = QLabel(TXT("听点音乐？", "Listen to some music?"))
        title.setObjectName("pageTitle")
        top.addWidget(title)
        top.addStretch(1)

        self.desktop_btn = QPushButton(TXT("桌面歌词", "Desktop Lyrics"))
        self.desktop_btn.setCheckable(True)
        self.desktop_btn.setObjectName("chipButton")
        self.desktop_btn.toggled.connect(self._toggle_desktop_lyrics)
        top.addWidget(self.desktop_btn)

        self.refresh_btn = QPushButton(TXT("刷新音乐", "Refresh"))
        self.refresh_btn.setObjectName("chipButton")
        self.refresh_btn.clicked.connect(self._refresh_music_library)
        top.addWidget(self.refresh_btn)

        self.settings_btn = QPushButton(TXT("音乐设置", "Music Settings"))
        self.settings_btn.setCheckable(True)
        self.settings_btn.setObjectName("chipButton")
        self.settings_btn.toggled.connect(self._toggle_settings)
        top.addWidget(self.settings_btn)
        root.addLayout(top)

        self.main_stack = QStackedWidget()
        root.addWidget(self.main_stack, 1)
        self.main_stack.addWidget(self._build_player_view())
        self.main_stack.addWidget(self._build_settings_view())

    def _build_player_view(self):
        page = QWidget()
        main = QHBoxLayout(page)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(18)

        # Left playlist
        left = QFrame()
        left.setMinimumWidth(300)
        left.setMaximumWidth(380)
        left.setStyleSheet("QFrame { background:#0c0c0c; border:1px solid #181818; border-radius:10px; }")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(10, 10, 10, 10)
        ll.setSpacing(7)

        label = QLabel(TXT("歌单", "Playlist"))
        label.setStyleSheet("font-size:15px; font-weight:700; color:#dddddd;")
        ll.addWidget(label)

        self.scan_status = QLabel("")
        self.scan_status.setStyleSheet("color:#777; font-size:11px;")
        self.scan_status.setWordWrap(True)
        ll.addWidget(self.scan_status)

        self.playlist_scroll = QScrollArea()
        self.playlist_scroll.setWidgetResizable(True)
        self.playlist_scroll.setFrameShape(QFrame.NoFrame)
        self.playlist_host = QWidget()
        self.playlist_layout = QVBoxLayout(self.playlist_host)
        self.playlist_layout.setContentsMargins(0, 0, 0, 0)
        self.playlist_layout.setSpacing(3)
        self.playlist_layout.addStretch(1)
        self.playlist_scroll.setWidget(self.playlist_host)
        ll.addWidget(self.playlist_scroll, 1)
        main.addWidget(left)

        # Right detail / lyrics / transport
        right = QFrame()
        right.setStyleSheet("QFrame { background:#090909; border:1px solid #171717; border-radius:10px; }")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 18, 20, 18)
        rl.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(18)

        info_box = QWidget()
        info_box.setMaximumWidth(430)
        info_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        info_grid = QGridLayout(info_box)
        info_grid.setContentsMargins(0, 0, 0, 0)
        info_grid.setHorizontalSpacing(8)
        info_grid.setVerticalSpacing(2)
        info_grid.setColumnStretch(1, 1)

        self.detail_values = {}
        details = [
            ("Title", TXT("歌名", "Title")),
            ("Artist", TXT("艺术家", "Artist")),
            ("Album", TXT("专辑名", "Album")),
            ("Track", TXT("曲目编号", "Track")),
            ("Year", TXT("年份", "Year")),
            ("Cover", TXT("封面", "Cover")),
        ]
        for row, (key, zhlabel) in enumerate(details):
            lab = QLabel(f"{zhlabel}:")
            lab.setFixedWidth(66)
            lab.setFixedHeight(21)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            lab.setStyleSheet(
                "color:#777; font-size:11px; background:transparent; border:0; padding:0;"
            )

            val = QLabel("—")
            val.setFixedHeight(21)
            val.setMinimumWidth(120)
            val.setMaximumWidth(340)
            val.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            val.setStyleSheet(
                "color:#cecece; font-size:11px; background:transparent; border:0; padding:0;"
            )
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            val.setToolTip("")

            info_grid.addWidget(lab, row, 0)
            info_grid.addWidget(val, row, 1)
            self.detail_values[key] = val

        header.addWidget(info_box, 0, Qt.AlignLeft | Qt.AlignTop)
        header.addStretch(1)

        now = QVBoxLayout()
        now.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self.hero_cover = QLabel()
        self.hero_cover.setFixedSize(112, 112)
        self.hero_cover.setAlignment(Qt.AlignCenter)
        self.hero_cover.setStyleSheet("background:#121212; border:1px solid #252525; border-radius:10px;")
        now.addWidget(self.hero_cover, 0, Qt.AlignRight)
        self.hero_title = QLabel(TXT("未选择歌曲", "No track selected"))
        self.hero_title.setAlignment(Qt.AlignRight)
        self.hero_title.setWordWrap(True)
        self.hero_title.setMaximumWidth(260)
        self.hero_title.setStyleSheet("font-size:15px; font-weight:700; color:#eeeeee;")
        now.addWidget(self.hero_title, 0, Qt.AlignRight)
        self.hero_artist = QLabel("")
        self.hero_artist.setAlignment(Qt.AlignRight)
        self.hero_artist.setStyleSheet("color:#888; font-size:12px;")
        now.addWidget(self.hero_artist, 0, Qt.AlignRight)
        header.addLayout(now)
        rl.addLayout(header)

        # Large lyric roller area between metadata and transport.
        lyric_frame = QFrame()
        lyric_frame.setStyleSheet("QFrame { background:#070707; border:0; }")
        lyric_lay = QVBoxLayout(lyric_frame)
        lyric_lay.setContentsMargins(30, 12, 30, 12)
        lyric_lay.setSpacing(4)

        self.lyric_labels = []
        for i in range(7):
            lbl = QLabel("")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setWordWrap(True)
            lbl.setMinimumHeight(34 if i != 3 else 50)
            self.lyric_labels.append(lbl)
            lyric_lay.addWidget(lbl)
        rl.addWidget(lyric_frame, 1)

        transport = QFrame()
        transport.setStyleSheet("QFrame { background:#0d0d0d; border:1px solid #181818; border-radius:9px; }")
        tl = QVBoxLayout(transport)
        tl.setContentsMargins(14, 10, 14, 10)
        tl.setSpacing(7)

        time_row = QHBoxLayout()
        self.current_time = QLabel("0:00")
        self.current_time.setStyleSheet("color:#777; font-size:11px;")
        self.total_time = QLabel("0:00")
        self.total_time.setStyleSheet("color:#777; font-size:11px;")
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 1000)
        self.progress.sliderPressed.connect(self._seek_started)
        self.progress.sliderReleased.connect(self._seek_finished)
        time_row.addWidget(self.current_time)
        time_row.addWidget(self.progress, 1)
        time_row.addWidget(self.total_time)
        tl.addLayout(time_row)

        controls = QHBoxLayout()
        controls.addStretch(1)
        self.prev_btn = QPushButton("◀")
        self.prev_btn.setFixedSize(38, 34)
        self.prev_btn.clicked.connect(self._previous)
        self.play_btn = QPushButton("▶")
        self.play_btn.setFixedSize(52, 38)
        self.play_btn.clicked.connect(self._toggle_play)
        self.next_btn = QPushButton("▶")
        self.next_btn.setFixedSize(38, 34)
        self.next_btn.clicked.connect(self._next)
        for b in (self.prev_btn, self.play_btn, self.next_btn):
            b.setObjectName("chipButton")
        controls.addWidget(self.prev_btn)
        controls.addWidget(self.play_btn)
        controls.addWidget(self.next_btn)

        controls.addSpacing(14)
        vol = QLabel(TXT("音量", "Vol"))
        vol.setStyleSheet("color:#777; font-size:11px;")
        controls.addWidget(vol)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(82)
        self.volume.setFixedWidth(110)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100.0))
        controls.addWidget(self.volume)
        tl.addLayout(controls)
        rl.addWidget(transport)

        main.addWidget(right, 1)
        return page

    def _build_settings_view(self):
        page = QWidget()
        outer = QHBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(16)

        rail = QFrame()
        rail.setFixedWidth(230)
        rail.setStyleSheet("QFrame { background:#0b0b0b; border:1px solid #181818; border-radius:10px; }")
        rlay = QVBoxLayout(rail)
        rlay.setContentsMargins(12, 12, 12, 12)

        heading = QLabel(TXT("音乐设置", "Music Settings"))
        heading.setStyleSheet("font-size:17px; font-weight:700;")
        rlay.addWidget(heading)

        self.customize_nav = QPushButton(TXT("自定义", "Customize"))
        self.customize_nav.setObjectName("chipButton")
        self.customize_nav.setCheckable(True)
        self.customize_nav.setChecked(True)
        rlay.addWidget(self.customize_nav)

        self.open_folder_btn = QPushButton(TXT("打开音乐文件所在位置", "Open Music Folder"))
        self.open_folder_btn.setObjectName("chipButton")
        self.open_folder_btn.clicked.connect(self._open_music_folder)
        rlay.addWidget(self.open_folder_btn)
        rlay.addStretch(1)
        outer.addWidget(rail)

        custom = QScrollArea()
        custom.setWidgetResizable(True)
        custom.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(24, 18, 24, 18)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)

        title = QLabel(TXT("桌面歌词自定义", "Desktop Lyrics Customization"))
        title.setStyleSheet("font-size:20px; font-weight:700;")
        grid.addWidget(title, 0, 0, 1, 3)

        row = 1
        grid.addWidget(QLabel(TXT("字幕大小", "Subtitle Size")), row, 0)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(14, 72)
        self.font_size_spin.setSuffix(" pt")
        self.font_size_spin.valueChanged.connect(self._customization_changed)
        grid.addWidget(self.font_size_spin, row, 1)
        row += 1

        grid.addWidget(QLabel(TXT("字体", "Font")), row, 0)
        self.font_combo = QFontComboBox()
        self.font_combo.currentFontChanged.connect(self._customization_changed)
        grid.addWidget(self.font_combo, row, 1, 1, 2)
        row += 1

        grid.addWidget(QLabel(TXT("粗细", "Weight")), row, 0)
        self.weight_combo = QComboBox()
        self.weight_combo.addItem(TXT("常规", "Regular"), 400)
        self.weight_combo.addItem(TXT("中等", "Medium"), 500)
        self.weight_combo.addItem(TXT("粗体", "Bold"), 700)
        self.weight_combo.addItem(TXT("特粗", "Extra Bold"), 800)
        self.weight_combo.currentIndexChanged.connect(self._customization_changed)
        grid.addWidget(self.weight_combo, row, 1)
        row += 1

        grid.addWidget(QLabel(TXT("字幕颜色", "Subtitle Color")), row, 0)
        color_row = QHBoxLayout()

        self.rgb_r = QSpinBox()
        self.rgb_g = QSpinBox()
        self.rgb_b = QSpinBox()
        for prefix, spin in (("R", self.rgb_r), ("G", self.rgb_g), ("B", self.rgb_b)):
            spin.setRange(0, 255)
            spin.setPrefix(prefix + " ")
            spin.setFixedWidth(76)
            spin.valueChanged.connect(self._rgb_changed)
            color_row.addWidget(spin)

        self.text_color_edit = QLineEdit("#ffffff")
        self.text_color_edit.setFixedWidth(92)
        self.text_color_edit.editingFinished.connect(self._hex_color_changed)
        color_row.addWidget(self.text_color_edit)

        self.text_color_btn = QPushButton(TXT("色轮", "Color Wheel"))
        self.text_color_btn.setObjectName("chipButton")
        self.text_color_btn.clicked.connect(self._choose_text_color)
        color_row.addWidget(self.text_color_btn)

        self.rainbow_check = QCheckBox(TXT("彩虹模式", "Rainbow"))
        self.rainbow_check.toggled.connect(self._customization_changed)
        color_row.addWidget(self.rainbow_check)
        color_row.addStretch(1)
        grid.addLayout(color_row, row, 1, 1, 2)
        row += 1

        grid.addWidget(QLabel(TXT("字幕背景", "Subtitle Background")), row, 0)
        bg_row = QHBoxLayout()
        self.bg_enable = QCheckBox(TXT("启用背景", "Enable Background"))
        self.bg_enable.toggled.connect(self._customization_changed)
        self.bg_color_edit = QLineEdit("#000000")
        self.bg_color_edit.setFixedWidth(100)
        self.bg_color_edit.editingFinished.connect(self._customization_changed)
        self.bg_color_btn = QPushButton(TXT("背景色轮", "Background Color"))
        self.bg_color_btn.setObjectName("chipButton")
        self.bg_color_btn.clicked.connect(self._choose_bg_color)
        bg_row.addWidget(self.bg_enable)
        bg_row.addWidget(self.bg_color_edit)
        bg_row.addWidget(self.bg_color_btn)
        bg_row.addStretch(1)
        grid.addLayout(bg_row, row, 1, 1, 2)
        row += 1

        note = QLabel(TXT(
            "默认背景为透明。RGB/十六进制颜色与色轮可同时使用；开启彩虹模式后字幕颜色会持续变化。",
            "Background is transparent by default. Hex/RGB-style color entry and the color wheel can be used together; Rainbow mode continuously cycles lyric color."
        ))
        note.setWordWrap(True)
        note.setStyleSheet("color:#787878; font-size:12px;")
        grid.addWidget(note, row, 0, 1, 3)
        row += 1
        grid.setRowStretch(row, 1)

        custom.setWidget(host)
        outer.addWidget(custom, 1)
        return page

    def _placeholder_cover(self):
        pix = QPixmap(160, 160)
        pix.fill(QColor("#151515"))
        p = QPainter(pix)
        p.setPen(QColor("#555555"))
        f = QFont()
        f.setPointSize(28)
        f.setBold(True)
        p.setFont(f)
        p.drawText(pix.rect(), Qt.AlignCenter, "515")
        p.end()
        return pix

    def _pixmap_for_track(self, track, size=160):
        data = track.get("cover_bytes")
        if data:
            pix = QPixmap()
            if pix.loadFromData(data):
                return pix.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        return self._placeholder_cover().scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)

    def _clear_music_cache(self):
        """Completely remove the disposable extraction cache before a rescan."""
        # QMediaPlayer on Windows may briefly retain a handle to the current source.
        # Disconnect the source first, then give Qt a chance to release that handle.
        try:
            self.player.stop()
            self.player.setSource(QUrl())
        except Exception:
            pass
        QApplication.processEvents()

        if self.cache_dir.exists():
            for _ in range(3):
                shutil.rmtree(self.cache_dir, ignore_errors=True)
                if not self.cache_dir.exists():
                    break
                QApplication.processEvents()

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        return self.cache_dir.exists()

    def _refresh_music_library(self):
        """Refresh button path: clear cache first, then perform a clean rescan."""
        self.refresh_btn.setEnabled(False)
        self.scan_status.setText(TXT(
            "正在清除音乐缓存…",
            "Clearing music cache…"
        ))
        QApplication.processEvents()

        self._clear_music_cache()

        self.scan_status.setText(TXT(
            "缓存已清除，正在重新读取音乐…",
            "Cache cleared. Rescanning music…"
        ))
        QApplication.processEvents()

        # Run the actual scan on the next event-loop turn so the user can see that
        # cache removal completed before the library scan begins.
        QTimer.singleShot(0, self._reload_library_after_cache_clear)

    def _reload_library_after_cache_clear(self):
        try:
            self.reload_library(clear_cache=False)
        finally:
            self.refresh_btn.setEnabled(True)

    def _extract_archives_recursive(self):
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        queue = [
            p for p in self.library_dir.rglob("*")
            if p.is_file() and p.suffix.lower() == ".zip" and self.cache_dir not in p.parents
        ]
        seen = set()

        while queue:
            zp = queue.pop(0)
            try:
                stat = zp.stat()
                signature = f"{zp.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
            except Exception:
                continue
            digest = hashlib.sha1(signature.encode("utf-8", errors="ignore")).hexdigest()[:14]
            if digest in seen:
                continue
            seen.add(digest)

            out = self.cache_dir / digest
            marker = out / ".source_signature"
            needs_extract = True
            if marker.exists():
                try:
                    needs_extract = marker.read_text(encoding="utf-8") != signature
                except Exception:
                    pass
            if needs_extract:
                if out.exists():
                    shutil.rmtree(out, ignore_errors=True)
                out.mkdir(parents=True, exist_ok=True)
                try:
                    _safe_extract_zip(zp, out)
                    marker.write_text(signature, encoding="utf-8")
                except Exception:
                    continue

            for nested in out.rglob("*.zip"):
                queue.append(nested)

    def reload_library(self, clear_cache=True):
        if clear_cache:
            self.scan_status.setText(TXT(
                "正在清理缓存并读取音乐文件…",
                "Clearing cache and scanning music files…"
            ))
            QApplication.processEvents()
            self._clear_music_cache()
        else:
            self.scan_status.setText(TXT(
                "正在重新读取音乐文件…",
                "Rescanning music files…"
            ))
            QApplication.processEvents()

        self._extract_archives_recursive()

        all_files = [p for p in self.library_dir.rglob("*") if p.is_file()]
        lyric_files = [p for p in all_files if p.suffix.lower() in LYRIC_EXTENSIONS]
        audio_files = [p for p in all_files if p.suffix.lower() in AUDIO_EXTENSIONS]

        # Same-folder LRC gets priority; otherwise fall back to any same-stem LRC.
        by_stem = {}
        for lp in lyric_files:
            by_stem.setdefault(lp.stem.casefold(), []).append(lp)

        tracks = []
        for path in sorted(audio_files, key=lambda p: (p.name.casefold(), str(p).casefold())):
            info = _read_track_info(path)
            local_lrc = path.with_suffix(".lrc")
            lrc = local_lrc if local_lrc.exists() else None
            if lrc is None:
                candidates = by_stem.get(path.stem.casefold(), [])
                if candidates:
                    lrc = candidates[0]
            info["lyrics"] = _parse_lrc(lrc) if lrc else []
            tracks.append(info)

        self.tracks = tracks
        self._rebuild_playlist()
        self.scan_status.setText(TXT(
            f"已读取 {len(tracks)} 首歌曲",
            f"{len(tracks)} tracks loaded"
        ))

        if self.current_index >= len(self.tracks):
            self.current_index = -1
        if self.current_index < 0 and self.tracks:
            self.select_track(0)

    def _rebuild_playlist(self):
        while self.playlist_layout.count() > 1:
            item = self.playlist_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for i, track in enumerate(self.tracks):
            row = MusicTrackRow(i, track, self._pixmap_for_track(track, 64))
            row.clicked.connect(self.select_track)
            self.playlist_layout.insertWidget(self.playlist_layout.count() - 1, row)

    def select_track(self, index):
        if index < 0 or index >= len(self.tracks):
            return
        self.current_index = index
        track = self.tracks[index]
        self.current_lyrics = track.get("lyrics", [])
        self.current_lyric_index = -1
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(track["path"])))

        self.detail_values["Title"].setText(track["title"] or "—")
        self.detail_values["Artist"].setText(track["artist"] or "—")
        self.detail_values["Album"].setText(track["album"] or "—")
        self.detail_values["Track"].setText(track["track"] or "—")
        self.detail_values["Year"].setText(track["year"] or "—")
        self.detail_values["Cover"].setText(track["cover_desc"] or "—")

        self.hero_cover.setPixmap(self._pixmap_for_track(track, 112))
        self.hero_title.setText(track["title"])
        self.hero_artist.setText(track["artist"])
        self.total_time.setText(_format_ms(track["duration_ms"]))
        self.progress.setValue(0)
        self.current_time.setText("0:00")
        self._update_lyric_roller(-1)

    def _toggle_play(self):
        if self.current_index < 0 and self.tracks:
            self.select_track(0)
        if self.current_index < 0:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _play_state_changed(self, state):
        self.play_btn.setText("Ⅱ" if state == QMediaPlayer.PlaybackState.PlayingState else "▶")

    def _next(self):
        if not self.tracks:
            return
        nxt = (self.current_index + 1) % len(self.tracks)
        self.select_track(nxt)
        self.player.play()

    def _previous(self):
        if not self.tracks:
            return
        prev = (self.current_index - 1) % len(self.tracks)
        self.select_track(prev)
        self.player.play()

    def _media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._next()

    def _duration_changed(self, duration):
        self.total_time.setText(_format_ms(duration))

    def _position_changed(self, pos):
        if not self._seeking:
            duration = self.player.duration()
            if duration > 0:
                self.progress.setValue(int(pos * 1000 / duration))
        self.current_time.setText(_format_ms(pos))

        idx = -1
        for i, (stamp, _) in enumerate(self.current_lyrics):
            if stamp <= pos:
                idx = i
            else:
                break
        if idx != self.current_lyric_index:
            self.current_lyric_index = idx
            self._update_lyric_roller(idx)

    def _seek_started(self):
        self._seeking = True

    def _seek_finished(self):
        self._seeking = False
        duration = self.player.duration()
        if duration > 0:
            self.player.setPosition(int(duration * self.progress.value() / 1000))

    @staticmethod
    def _lyric_slot_text(slot):
        """Return one display string for a bilingual/multilingual lyric slot."""
        if not slot:
            return ""
        if isinstance(slot, str):
            return slot
        return "\n".join(str(line) for line in slot if str(line).strip())

    def _lyric_slot_lines(self, slot):
        if not slot:
            return []
        if isinstance(slot, str):
            return [slot]
        return [str(line) for line in slot if str(line).strip()]

    def _update_lyric_roller(self, current):
        if not self.current_lyrics:
            slot_lines = [[], [], [], [TXT("暂无歌词", "No lyrics available")], [], [], []]
        else:
            slot_lines = []
            for offset in range(-3, 4):
                idx = current + offset
                if current < 0:
                    idx = offset + 3
                if 0 <= idx < len(self.current_lyrics):
                    slot_lines.append(self._lyric_slot_lines(self.current_lyrics[idx][1]))
                else:
                    slot_lines.append([])

        for i, (lbl, lines) in enumerate(zip(self.lyric_labels, slot_lines)):
            value = "\n".join(lines)
            lbl.setText(value)

            # One timestamp = one roller position. If two languages are present,
            # they remain stacked inside this same position instead of consuming
            # two separate lyric rows.
            is_bilingual = len(lines) >= 2
            dist = abs(i - 3)

            if i == 3:
                size = 20 if is_bilingual else 22
                min_h = 72 if is_bilingual else 50
                lbl.setMinimumHeight(min_h)
                lbl.setStyleSheet(
                    f"color:#f0f0f0; font-size:{size}px; font-weight:750; "
                    "background:transparent; border:0;"
                )
            elif dist == 1:
                size = 14 if is_bilingual else 16
                lbl.setMinimumHeight(54 if is_bilingual else 34)
                lbl.setStyleSheet(
                    f"color:#8d8d8d; font-size:{size}px; font-weight:550; "
                    "background:transparent; border:0;"
                )
            elif dist == 2:
                size = 13 if is_bilingual else 14
                lbl.setMinimumHeight(48 if is_bilingual else 34)
                lbl.setStyleSheet(
                    f"color:#565656; font-size:{size}px; "
                    "background:transparent; border:0;"
                )
            else:
                size = 12 if is_bilingual else 13
                lbl.setMinimumHeight(44 if is_bilingual else 34)
                lbl.setStyleSheet(
                    f"color:#343434; font-size:{size}px; "
                    "background:transparent; border:0;"
                )

        current_text = "\n".join(slot_lines[3]) if len(slot_lines) >= 4 else ""
        self.desktop_lyrics.set_lyric(current_text)


    def _toggle_desktop_lyrics(self, enabled):
        if enabled:
            self.desktop_lyrics.show()
            self.desktop_lyrics.raise_()
            self.desktop_lyrics._reposition()
        else:
            self.desktop_lyrics.hide()

    def _toggle_settings(self, enabled):
        self.main_stack.setCurrentIndex(1 if enabled else 0)
        self.settings_btn.setText(
            TXT("返回播放器", "Back to Player") if enabled else TXT("音乐设置", "Music Settings")
        )

    def _open_music_folder(self):
        self.library_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.library_dir.resolve())))

    def _set_text_color_controls(self, color):
        if not color.isValid():
            return
        widgets = (self.rgb_r, self.rgb_g, self.rgb_b)
        for widget in widgets:
            widget.blockSignals(True)
        self.text_color_edit.blockSignals(True)
        self.rgb_r.setValue(color.red())
        self.rgb_g.setValue(color.green())
        self.rgb_b.setValue(color.blue())
        self.text_color_edit.setText(color.name())
        self.text_color_edit.blockSignals(False)
        for widget in widgets:
            widget.blockSignals(False)

    def _rgb_changed(self, *args):
        color = QColor(self.rgb_r.value(), self.rgb_g.value(), self.rgb_b.value())
        self.text_color_edit.blockSignals(True)
        self.text_color_edit.setText(color.name())
        self.text_color_edit.blockSignals(False)
        self._customization_changed()

    def _hex_color_changed(self):
        color = QColor(self.text_color_edit.text())
        if not color.isValid():
            color = QColor("#ffffff")
        self._set_text_color_controls(color)
        self._customization_changed()

    def _choose_text_color(self):
        initial = QColor(self.text_color_edit.text())
        color = QColorDialog.getColor(initial if initial.isValid() else QColor("#ffffff"), self)
        if color.isValid():
            self._set_text_color_controls(color)
            self._customization_changed()

    def _choose_bg_color(self):
        initial = QColor(self.bg_color_edit.text())
        color = QColorDialog.getColor(initial if initial.isValid() else QColor("#000000"), self)
        if color.isValid():
            self.bg_color_edit.setText(color.name())
            self._customization_changed()

    def _load_customization(self):
        family = self.settings_store.value("font_family", QApplication.font().family())
        size = int(self.settings_store.value("font_size", 30))
        weight = int(self.settings_store.value("font_weight", 700))
        text_color = str(self.settings_store.value("text_color", "#ffffff"))
        rainbow = str(self.settings_store.value("rainbow", "false")).lower() == "true"
        bg_enabled = str(self.settings_store.value("background_enabled", "false")).lower() == "true"
        bg_color = str(self.settings_store.value("background_color", "#000000"))

        self.font_combo.setCurrentFont(QFont(family))
        self.font_size_spin.setValue(size)
        idx = self.weight_combo.findData(weight)
        self.weight_combo.setCurrentIndex(max(0, idx))
        self._set_text_color_controls(QColor(text_color))
        self.rainbow_check.setChecked(rainbow)
        self.bg_enable.setChecked(bg_enabled)
        self.bg_color_edit.setText(bg_color)
        self._customization_changed()

    def _customization_changed(self, *args):
        color = QColor(self.text_color_edit.text())
        text_color = color.name() if color.isValid() else "#ffffff"
        bg = QColor(self.bg_color_edit.text())
        bg_color = bg.name() if bg.isValid() else "#000000"

        settings = {
            "font_family": self.font_combo.currentFont().family(),
            "font_size": self.font_size_spin.value(),
            "font_weight": int(self.weight_combo.currentData() or 400),
            "text_color": text_color,
            "rainbow": self.rainbow_check.isChecked(),
            "background_enabled": self.bg_enable.isChecked(),
            "background_color": bg_color,
        }

        self.settings_store.setValue("font_family", settings["font_family"])
        self.settings_store.setValue("font_size", settings["font_size"])
        self.settings_store.setValue("font_weight", settings["font_weight"])
        self.settings_store.setValue("text_color", settings["text_color"])
        self.settings_store.setValue("rainbow", settings["rainbow"])
        self.settings_store.setValue("background_enabled", settings["background_enabled"])
        self.settings_store.setValue("background_color", settings["background_color"])
        self.desktop_lyrics.apply_settings(settings)



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
        music = NavButton(TXT("听点音乐？", "Listen to some music?"), "♪")
        settings = NavButton(TXT("设置", "Settings"), "⚙")
        about = NavButton(TXT("关于", "About"), "·")
        self.nav_group.addButton(card_games, 6)
        self.nav_group.addButton(music, 7)
        self.nav_group.addButton(settings, 8)
        self.nav_group.addButton(about, 9)
        self.nav_buttons.extend([card_games, music, settings, about])
        sl.addWidget(card_games)
        sl.addWidget(music)
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
        self.home.startDice.connect(lambda: self._navigate(2))
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.tarot)
        self.dice = DicePage()
        self.stack.addWidget(self.dice)
        self.stack.addWidget(PlaceholderPage(TXT("符文", "Runes")))
        self.stack.addWidget(PlaceholderPage(TXT("硬币", "Coin")))
        self.stack.addWidget(PlaceholderPage(TXT("抽签", "Lots")))
        self.stack.addWidget(PlaceholderPage(TXT("卡牌游戏", "Card Games")))
        self.music_page = MusicPage()
        self.stack.addWidget(self.music_page)
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
            name = qt_key_name(key)
            if name and not event.isAutoRepeat():
                HELD_KEYS.add(name)
                for stage in self.findChildren(TarotStage):
                    stage._ensure_timer()
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
        if event.type() == QEvent.KeyRelease:
            name = qt_key_name(event.key())
            if name and not event.isAutoRepeat():
                HELD_KEYS.discard(name)
                for stage in self.findChildren(TarotStage):
                    stage._ensure_timer()
        return super().eventFilter(obj, event)

    def _navigate(self, idx):
        self.stack.setCurrentIndex(idx)
        names = [
            TXT("首页", "Home"),
            TXT("塔罗牌", "Tarot"),
            TXT("骰子", "Dice"),
            TXT("符文", "Runes"),
            TXT("硬币", "Coin"),
            TXT("抽签", "Lots"),
            TXT("卡牌游戏", "Card Games"),
            TXT("听点音乐？", "Listen to some music?"),
            TXT("设置", "Settings"),
            TXT("关于", "About"),
        ]
        self.section_title.setText(names[idx])
        btn = self.nav_group.button(idx)
        if btn:
            btn.setChecked(True)

    def _settings_changed(self):
        idx = self.stack.currentIndex() if hasattr(self, "stack") else 8
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
