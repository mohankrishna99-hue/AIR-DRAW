"""
AirDraw - Hand Gesture Controlled Canvas

AUTHOR: MOHANA KRISHNA A S

==========================================
Draw on a virtual canvas using nothing but your webcam and your hand.

Uses MediaPipe's Tasks API (HandLandmarker) - compatible with MediaPipe
1.0.x, which removed the older `mp.solutions` interface.

Controls (gesture):
    - Index finger up only          -> DRAW mode (draws a line at fingertip)
    - Index + Middle finger up      -> SELECT mode (hover over top toolbar to click buttons)
    - Fist / other combos           -> IDLE (no drawing)

Controls (keyboard):
    1-7   Colors (Blue, Green, Red, Yellow, Purple, Cyan, White)
    E     Eraser
    D     Draw mode toggle back to last color
    C     Clear canvas
    U     Undo
    R     Redo
    S     Save canvas as PNG
    +/-   Brush size up/down
    Q     Quit

Requirements:
    pip install opencv-python mediapipe numpy
"""

import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_tasks
from mediapipe.tasks.python import vision as mp_vision
import time
import os
import urllib.request
from collections import deque

CAM_INDEX = 0
FRAME_W, FRAME_H = 1280, 720

TOOLBAR_HEIGHT = 110
BUTTON_MARGIN = 10
BUTTON_GAP = 12

COLORS = [
    ("Blue",   (255, 100, 0)),
    ("Green",  (60, 200, 60)),
    ("Red",    (50, 50, 220)),
    ("Yellow", (0, 220, 235)),
    ("Purple", (200, 40, 190)),
    ("Cyan",   (235, 220, 0)),
    ("White",  (245, 245, 245)),
]

ACTION_BUTTONS = ["Eraser", "Clear", "Undo", "Redo", "Save", "Size -", "Size +"]

MIN_BRUSH, MAX_BRUSH = 2, 40
DEFAULT_BRUSH = 8
ERASER_SIZE_MULT = 4

SMOOTHING = 0.55          
MAX_UNDO_STACK = 25
SELECT_HOVER_TIME = 0.9  

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(SCRIPT_DIR, "hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)


def ensure_model():
    if not os.path.exists(MODEL_PATH):
        print("Downloading hand landmark model (one-time, ~7 MB)...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("Model downloaded to", MODEL_PATH)


HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),          # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),          # index
    (5, 9), (9, 10), (10, 11), (11, 12),     # middle
    (9, 13), (13, 14), (14, 15), (15, 16),   # ring
    (13, 17), (17, 18), (18, 19), (19, 20),  # pinky
    (0, 17),                                  # palm base
]

FINGER_TIPS = [4, 8, 12, 16, 20]
FINGER_PIPS = [3, 6, 10, 14, 18]


def fingers_up(landmarks, handedness_label):
    """landmarks: list of objects with .x/.y in [0,1]. Returns [thumb,index,middle,ring,pinky]."""
    up = [False] * 5
    if handedness_label == "Right":
        up[0] = landmarks[4].x < landmarks[3].x
    else:
        up[0] = landmarks[4].x > landmarks[3].x
    for i in range(1, 5):
        up[i] = landmarks[FINGER_TIPS[i]].y < landmarks[FINGER_PIPS[i]].y
    return up

# UI LAYOUT

class Toolbar:
    def __init__(self, width):
        self.width = width
        self.buttons = []
        self._layout()

    def _layout(self):
        labels = [c[0] for c in COLORS] + ACTION_BUTTONS
        n = len(labels)
        x = BUTTON_MARGIN
        y1 = BUTTON_MARGIN
        y2 = TOOLBAR_HEIGHT - BUTTON_MARGIN
        avail = self.width - BUTTON_MARGIN * 2 - BUTTON_GAP * (n - 1)
        bw = avail // n
        for idx, label in enumerate(labels):
            x1 = x
            x2 = x + bw
            kind = "color" if idx < len(COLORS) else "action"
            color = COLORS[idx][1] if kind == "color" else (70, 70, 70)
            self.buttons.append(dict(label=label, color=color, x1=x1, y1=y1,
                                      x2=x2, y2=y2, kind=kind))
            x = x2 + BUTTON_GAP

    def hit_test(self, px, py):
        for b in self.buttons:
            if b["x1"] <= px <= b["x2"] and b["y1"] <= py <= b["y2"]:
                return b
        return None

    def draw(self, img, active_color_name, active_button_label=None, hover_label=None, hover_progress=0.0):
        for b in self.buttons:
            is_active = (b["kind"] == "color" and b["label"] == active_color_name) or \
                        (b["kind"] == "action" and b["label"] == active_button_label)
            cv2.rectangle(img, (b["x1"], b["y1"]), (b["x2"], b["y2"]), b["color"], -1)

            border_color = (255, 255, 255) if is_active else (30, 30, 30)
            border_thick = 3 if is_active else 1
            cv2.rectangle(img, (b["x1"], b["y1"]), (b["x2"], b["y2"]), border_color, border_thick)

            text_color = (0, 0, 0) if b["label"] in ("Yellow", "White", "Cyan") else (255, 255, 255)
            font_scale = 0.55
            (tw, th), _ = cv2.getTextSize(b["label"], cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
            tx = b["x1"] + (b["x2"] - b["x1"] - tw) // 2
            ty = b["y1"] + (b["y2"] - b["y1"] + th) // 2
            cv2.putText(img, b["label"], (tx, ty), cv2.FONT_HERSHEY_SIMPLEX,
                        font_scale, text_color, 1, cv2.LINE_AA)

            if hover_label == b["label"] and hover_progress > 0:
                bar_w = int((b["x2"] - b["x1"]) * hover_progress)
                cv2.rectangle(img, (b["x1"], b["y2"] - 4), (b["x1"] + bar_w, b["y2"]), (255, 255, 255), -1)



# MAIN APP

class AirDraw:
    def __init__(self):
        ensure_model()

        self.cap = cv2.VideoCapture(CAM_INDEX)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        if not self.cap.isOpened():
            raise RuntimeError("Could not open webcam. Check CAM_INDEX / camera permissions.")

        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("Could not read from webcam.")
        self.h, self.w = frame.shape[:2]

        self.canvas = np.zeros((self.h, self.w, 3), dtype=np.uint8)
        self.toolbar = Toolbar(self.w)

        self.color_name = "Cyan"
        self.color = dict(COLORS)[self.color_name]
        self.brush_size = DEFAULT_BRUSH
        self.eraser_on = False

        self.mode = "IDLE"
        self.prev_point = None
        self.smoothed_point = None

        self.undo_stack = deque(maxlen=MAX_UNDO_STACK)
        self.redo_stack = deque(maxlen=MAX_UNDO_STACK)
        self._push_undo()

        self.hover_label = None
        self.hover_start_time = None

        base_options = mp_tasks.BaseOptions(model_asset_path=MODEL_PATH)
        options = mp_vision.HandLandmarkerOptions(
            base_options=base_options,
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.6,
            min_hand_presence_confidence=0.6,
            min_tracking_confidence=0.5,
        )
        self.landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._frame_ts = 0

        self.prev_time = time.time()
        self.fps = 0.0
        self.last_hand_seen = time.time()

    # ---------------- undo / redo ----------------
    def _push_undo(self):
        self.undo_stack.append(self.canvas.copy())
        self.redo_stack.clear()

    def undo(self):
        if len(self.undo_stack) > 1:
            self.redo_stack.append(self.undo_stack.pop())
            self.canvas = self.undo_stack[-1].copy()

    def redo(self):
        if self.redo_stack:
            state = self.redo_stack.pop()
            self.undo_stack.append(state)
            self.canvas = state.copy()

    def clear(self):
        self.canvas[:] = 0
        self._push_undo()

    def save(self):
        fname = f"airdraw_{time.strftime('%Y%m%d_%H%M%S')}.png"
        cv2.imwrite(fname, self.canvas)
        return fname

    # ---------------- drawing helpers ----------------
    def set_color(self, name):
        self.color_name = name
        self.color = dict(COLORS)[name]
        self.eraser_on = False

    def toggle_eraser(self):
        self.eraser_on = True

    def change_size(self, delta):
        self.brush_size = int(np.clip(self.brush_size + delta, MIN_BRUSH, MAX_BRUSH))

    def draw_point(self, pt):
        size = self.brush_size * (ERASER_SIZE_MULT if self.eraser_on else 1)
        draw_color = (0, 0, 0) if self.eraser_on else self.color
        if self.prev_point is None:
            cv2.circle(self.canvas, pt, size // 2, draw_color, -1)
        else:
            cv2.line(self.canvas, self.prev_point, pt, draw_color, size)
        self.prev_point = pt

    def handle_button(self, label):
        if label in dict(COLORS):
            self.set_color(label)
        elif label == "Eraser":
            self.toggle_eraser()
        elif label == "Clear":
            self.clear()
        elif label == "Undo":
            self.undo()
        elif label == "Redo":
            self.redo()
        elif label == "Save":
            self.saved_filename = self.save()
            self.save_flash_until = time.time() + 2.0
        elif label == "Size -":
            self.change_size(-2)
        elif label == "Size +":
            self.change_size(2)

    # ---------------- main loop ----------------
    def run(self):
        self.saved_filename = None
        self.save_flash_until = 0

        while True:
            ok, frame = self.cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

            self._frame_ts += 1
            result = self.landmarker.detect_for_video(mp_image, self._frame_ts)

            self.mode = "IDLE"
            index_tip_px = None

            if result.hand_landmarks:
                self.last_hand_seen = time.time()
                landmarks = result.hand_landmarks[0]
                handedness = "Right"
                if result.handedness and result.handedness[0]:
                    handedness = result.handedness[0][0].category_name

                up = fingers_up(landmarks, handedness)
                index_up = up[1]
                middle_up = up[2]
                others_down = not up[3] and not up[4]

                ix, iy = landmarks[8].x * self.w, landmarks[8].y * self.h
                index_tip_px = (int(ix), int(iy))

                # draw skeleton manually (drawing_utils no longer exists)
                pts = [(int(lm.x * self.w), int(lm.y * self.h)) for lm in landmarks]
                for a, b in HAND_CONNECTIONS:
                    cv2.line(frame, pts[a], pts[b], (0, 200, 0), 2)
                for p in pts:
                    cv2.circle(frame, p, 4, (0, 140, 255), -1)
                cv2.circle(frame, index_tip_px, 10, (0, 255, 255), 2)
                cv2.circle(frame, index_tip_px, 4, (0, 255, 0), -1)

                if index_up and middle_up and others_down:
                    self.mode = "SELECT"
                elif index_up and not middle_up and others_down:
                    self.mode = "DRAW"
                else:
                    self.mode = "IDLE"

            # ---- handle modes ----
            if self.mode == "DRAW" and index_tip_px:
                if index_tip_px[1] > TOOLBAR_HEIGHT:
                    if self.smoothed_point is None:
                        self.smoothed_point = index_tip_px
                    else:
                        sx = int(SMOOTHING * self.smoothed_point[0] + (1 - SMOOTHING) * index_tip_px[0])
                        sy = int(SMOOTHING * self.smoothed_point[1] + (1 - SMOOTHING) * index_tip_px[1])
                        self.smoothed_point = (sx, sy)
                    self.draw_point(self.smoothed_point)
                else:
                    self.prev_point = None
                    self.smoothed_point = None
                self.hover_label = None
                self.hover_start_time = None
            else:
                if self.prev_point is not None:
                    self._push_undo()
                self.prev_point = None
                self.smoothed_point = None

            if self.mode == "SELECT" and index_tip_px:
                btn = self.toolbar.hit_test(*index_tip_px)
                now = time.time()
                if btn:
                    if self.hover_label != btn["label"]:
                        self.hover_label = btn["label"]
                        self.hover_start_time = now
                    elapsed = now - self.hover_start_time
                    if elapsed >= SELECT_HOVER_TIME:
                        self.handle_button(btn["label"])
                        self.hover_start_time = now
                else:
                    self.hover_label = None
                    self.hover_start_time = None
            elif self.mode != "SELECT":
                self.hover_label = None
                self.hover_start_time = None

            # ---- compose output frame ----
            mask = np.any(self.canvas != 0, axis=2)
            frame[mask] = self.canvas[mask]

            self.toolbar.draw(
                frame,
                active_color_name=None if self.eraser_on else self.color_name,
                active_button_label="Eraser" if self.eraser_on else None,
                hover_label=self.hover_label,
                hover_progress=((time.time() - self.hover_start_time) / SELECT_HOVER_TIME)
                if self.hover_start_time else 0.0,
            )

            self._draw_hud(frame)

            cv2.imshow("AirDraw - Hand Gesture Controlled Canvas", frame)

            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif ord('1') <= key <= ord('7'):
                self.set_color(COLORS[key - ord('1')][0])
            elif key == ord('e'):
                self.toggle_eraser()
            elif key == ord('d'):
                self.eraser_on = False
            elif key == ord('c'):
                self.clear()
            elif key == ord('u'):
                self.undo()
            elif key == ord('r'):
                self.redo()
            elif key == ord('s'):
                self.saved_filename = self.save()
                self.save_flash_until = time.time() + 2.0
            elif key in (ord('+'), ord('=')):
                self.change_size(2)
            elif key == ord('-'):
                self.change_size(-2)

        self.cap.release()
        cv2.destroyAllWindows()
        self.landmarker.close()

    # ---------------- HUD ----------------
    def _draw_hud(self, frame):
        now = time.time()
        dt = now - self.prev_time
        self.prev_time = now
        if dt > 0:
            inst_fps = 1.0 / dt
            self.fps = self.fps * 0.9 + inst_fps * 0.1 if self.fps else inst_fps

        panel_w, panel_h = 230, 90
        px1 = self.w - panel_w - 20
        py1 = TOOLBAR_HEIGHT + 15
        overlay = frame.copy()
        cv2.rectangle(overlay, (px1, py1), (px1 + panel_w, py1 + panel_h), (40, 40, 40), -1)
        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame)

        cv2.putText(frame, f"FPS: {self.fps:.1f}", (px1 + 15, py1 + 32),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
        mode_color = {"DRAW": (0, 255, 0), "SELECT": (0, 200, 255), "IDLE": (150, 150, 150)}[self.mode]
        cv2.putText(frame, f"MODE: {self.mode}", (px1 + 15, py1 + 68),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, mode_color, 2, cv2.LINE_AA)

        chip_w, chip_h = 260, 50
        cx1, cy1 = 20, self.h - chip_h - 60
        overlay = frame.copy()
        cv2.rectangle(overlay, (cx1, cy1), (cx1 + chip_w, cy1 + chip_h), (20, 20, 20), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        swatch_color = (0, 0, 0) if self.eraser_on else self.color
        cv2.circle(frame, (cx1 + 30, cy1 + chip_h // 2), 18, swatch_color, -1)
        cv2.circle(frame, (cx1 + 30, cy1 + chip_h // 2), 18, (255, 255, 255), 2)
        label = "Eraser" if self.eraser_on else self.color_name
        cv2.putText(frame, f"{label} | Size: {self.brush_size}px", (cx1 + 60, cy1 + chip_h // 2 + 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)

        help_text = "[1-7] Colors | [E] Eraser | [D] Draw | [C] Clear | [U] Undo | [R] Redo | [S] Save | [Q] Quit"
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, self.h - 40), (self.w, self.h), (15, 15, 15), -1)
        cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)
        cv2.putText(frame, help_text, (20, self.h - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1, cv2.LINE_AA)

        if self.saved_filename and time.time() < self.save_flash_until:
            msg = f"Saved: {self.saved_filename}"
            (tw, th), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            tx = (self.w - tw) // 2
            ty = TOOLBAR_HEIGHT + 40
            cv2.rectangle(frame, (tx - 15, ty - th - 12), (tx + tw + 15, ty + 12), (0, 150, 0), -1)
            cv2.putText(frame, msg, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)

        if time.time() - self.last_hand_seen > 1.5:
            msg = "Show your hand to the camera..."
            (tw, th), _ = cv2.getTextSize(msg, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
            tx = (self.w - tw) // 2
            ty = self.h // 2
            cv2.putText(frame, msg, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(frame, msg, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)


if __name__ == "__main__":
    app = AirDraw()
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.cap.release()
        cv2.destroyAllWindows()
