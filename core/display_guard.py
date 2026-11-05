#!/usr/bin/env python3
"""
Display Guard: per-seat sleep / resume when a monitor is unplugged / replugged.

While seating is running in this process:
  * A seat whose configured display disappears is put to SLEEP: its Firefox kiosk is
    closed (so it never jumps to the remaining monitor), its mouse/keyboard are floated
    (so they cannot type into the other seat) and its pointer barrier is released.
  * When the display is detected again, the layout is forced back to EXTENDED mode
    (never mirrored), the seat's devices are reseated, the pointer is re-confined and the
    kiosk browser is relaunched on that monitor.

Settings (config.json -> "settings", both default to true when absent):
    display_sleep_mode, auto_extended_display
"""

import logging
import subprocess
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from core import overlay_state

logger = logging.getLogger("xinput_seating.display_guard")


def _run(args, timeout=10):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip(), p.stderr.strip(), p.returncode
    except Exception as exc:
        return "", str(exc), -1


def _overlap(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0] or
                a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def is_mirrored(displays: List[Dict]) -> bool:
    """True if two active outputs share the same origin (mirror / clone mode)."""
    seen = set()
    for d in displays:
        if d.get("width", 0) > 0:
            key = (d["x"], d["y"])
            if key in seen:
                return True
            seen.add(key)
    return False


def ensure_extended(displays: List[Dict], saved_layout: Optional[Dict] = None) -> List[str]:
    """
    Forces extended-desktop mode with xrandr:
      * connected-but-disabled outputs are switched on,
      * mirrored outputs are pulled apart.
    Each placed output goes back to its remembered position if that is free, otherwise to
    the right of the right-most active output. Returns a list of human-readable actions.
    """
    saved_layout = saved_layout or {}
    actions: List[str] = []
    conn = [dict(d) for d in displays if d.get("connected", True)]
    active = [d for d in conn if d.get("width", 0) > 0]
    off = [d for d in conn if d.get("width", 0) == 0]

    seen, mirrored = {}, []
    for d in list(active):
        key = (d["x"], d["y"])
        if key in seen:
            mirrored.append(d)
        else:
            seen[key] = d
    for d in mirrored:
        active.remove(d)

    for d in mirrored + off:
        name = d["name"]
        base = ["xrandr", "--output", name]
        s = saved_layout.get(name)
        pos_args: List[str] = []
        mode_opts: List[List[str]] = [["--auto"]]
        desc = ""

        if s and all(k in s for k in ("x", "y", "width", "height")):
            rect = (s["x"], s["y"], s["width"], s["height"])
            if not any(_overlap(rect, (a["x"], a["y"], a["width"], a["height"])) for a in active):
                pos_args = ["--pos", f"{s['x']}x{s['y']}"]
                mode_opts = [["--mode", f"{s['width']}x{s['height']}"], ["--auto"]]
                desc = f"restored to +{s['x']}+{s['y']}"

        if not pos_args and active:
            ref = max(active, key=lambda a: a["x"] + a["width"])
            pos_args = ["--right-of", ref["name"]]
            desc = f"extended right of {ref['name']}"

        ok = False
        for mode in mode_opts:
            _, err, rc = _run(base + mode + pos_args)
            if rc == 0:
                ok = True
                break
            logger.warning(f"xrandr {' '.join(base + mode + pos_args)} failed: {err}")

        if ok:
            actions.append(f"{name}: {desc or 'enabled'}")
            if "--pos" in pos_args:
                d["x"], d["y"] = s["x"], s["y"]
                d["width"], d["height"] = s["width"], s["height"]
            elif active:
                d["x"], d["y"] = ref["x"] + ref["width"], ref["y"]
                d["width"] = d.get("width") or 1920
                d["height"] = d.get("height") or 1080
            else:
                d["x"] = d["y"] = 0
                d["width"] = d.get("width") or 1920
                d["height"] = d.get("height") or 1080
            active.append(d)
    return actions


class DisplayGuard:
    MISS_TO_SLEEP = 2   # consecutive polls without the display before sleeping
    HIT_TO_WAKE = 2     # consecutive polls with the display before waking

    def __init__(self, kiosk_mgr, engine=None, interval: float = 1.0,
                 on_event: Optional[Callable[[str], None]] = None):
        from core.config_manager import ConfigManager
        from core.device_manager import DeviceManager
        self.kiosk = kiosk_mgr
        self.engine = engine
        self.interval = interval
        self.on_event = on_event
        self.cfg_mgr = ConfigManager()
        self.dm = DeviceManager()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._miss: Dict[str, int] = {}
        self._hit: Dict[str, int] = {}
        self._retry_at: Dict[str, float] = {}
        self._last_ext = 0.0

    # ------------------------------------------------------------------
    def start(self):
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="DisplayGuard")
        self._thread.start()
        logger.info("Display guard started (sleep seat on monitor loss, resume on reconnect).")

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=3)
        self._thread = None
        logger.info("Display guard stopped.")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _emit(self, msg: str):
        logger.info(msg)
        if self.on_event:
            try:
                self.on_event(msg)
            except Exception as exc:
                logger.debug(f"on_event failed: {exc}")

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self._tick()
            except Exception as exc:
                logger.warning(f"Display guard tick error: {exc}", exc_info=True)

    # ------------------------------------------------------------------
    def _tick(self):
        k = self.kiosk
        with k._lock:
            owned = set(k.kiosk_configs) | set(k.sleeping_cfgs)
        if not owned:  # seating not running in this process
            self._miss.clear()
            self._hit.clear()
            return

        cfg = self.cfg_mgr.load_config()
        st = cfg.get("settings", {})
        if not st.get("display_sleep_mode", True):
            return
        auto_ext = st.get("auto_extended_display", True)
        saved = st.get("display_layout", {})
        seats = cfg.get("seats", [])[:2]

        displays, _ = self.dm.get_displays()
        if not displays:  # xrandr hiccup: never sleep on an empty reading
            return
        by_name = {d["name"]: d for d in displays}
        changed = False

        for idx, seat in enumerate(seats):
            sid = seat.get("id", f"seat{idx + 1}")
            name = seat.get("display")
            if not name or sid not in owned:
                continue
            present = name in by_name

            if k.is_sleeping(sid):
                if not present:
                    self._hit[sid] = 0
                    continue
                self._hit[sid] = self._hit.get(sid, 0) + 1
                if self._hit[sid] < self.HIT_TO_WAKE or time.time() < self._retry_at.get(sid, 0):
                    continue
                if self._wake(idx, seat, auto_ext):
                    changed = True
                else:
                    self._retry_at[sid] = time.time() + 5
                    self._hit[sid] = 0
            else:
                if present:
                    self._miss[sid] = 0
                else:
                    self._miss[sid] = self._miss.get(sid, 0) + 1
                    if self._miss[sid] >= self.MISS_TO_SLEEP:
                        self._sleep(idx, seat)
                        self._miss[sid] = 0
                        changed = True

        if changed:
            self._relayout(seats)
            return

        if auto_ext and is_mirrored(displays) and time.time() - self._last_ext > 10:
            self._last_ext = time.time()
            acts = ensure_extended(displays, saved)
            for a in acts:
                self._emit(f"🖥 Display settings -> extended mode: {a}")
            if acts:
                time.sleep(2.0)
                self._relayout(seats)
            return

        self._remember_layout(seats, displays, by_name)

    # ------------------------------------------------------------------
    def _sleep(self, idx: int, seat: Dict):
        sid = seat.get("id", f"seat{idx + 1}")
        self._emit(f"😴 Display '{seat.get('display')}' of {sid} disconnected -> {sid} is sleeping "
                   f"(browser closed, input isolated) until it is reconnected.")
        self.kiosk.sleep_seat(sid, seat.get("kiosk_url") or "https://duckduckgo.com",
                              idx, seat.get("lock_kiosk", True))
        if self.engine:
            self.engine.sleep_seat(sid, seat)

    def _wake(self, idx: int, seat: Dict, auto_ext: bool) -> bool:
        sid = seat.get("id", f"seat{idx + 1}")
        name = seat.get("display")
        displays, total = self.dm.get_displays()

        if auto_ext:
            saved = self.cfg_mgr.load_config().get("settings", {}).get("display_layout", {})
            acts = ensure_extended(displays, saved)
            for a in acts:
                self._emit(f"🖥 Display settings -> extended mode: {a}")
            if acts:
                time.sleep(2.0)
                displays, total = self.dm.get_displays()

        d = next((x for x in displays if x["name"] == name and x.get("width", 0) > 0), None)
        if d is None:
            logger.info(f"Display '{name}' detected but not active yet; will retry.")
            return False

        if self.engine:
            self.engine.wake_seat(seat, displays, (total["width"], total["height"]))
        ok = self.kiosk.resume_seat(sid, (d["width"], d["height"], d["x"], d["y"]))
        self._emit(f"▶ Display '{name}' reconnected -> {sid} resumed, browser relaunched."
                   if ok else f"⚠ Display '{name}' reconnected but relaunching {sid} failed; will retry.")
        return ok

    # ------------------------------------------------------------------
    def _relayout(self, seats: List[Dict]):
        """Re-confines awake seats and re-places their kiosks after any layout change."""
        displays, total = self.dm.get_displays()
        if not displays:
            return
        if self.engine:
            self.engine.reconfine_awake(seats, displays, (total["width"], total["height"]))
        by_name = {d["name"]: d for d in displays}
        for seat in seats:
            sid = seat.get("id")
            d = by_name.get(seat.get("display"))
            win = self.kiosk.window_ids.get(sid)
            if (not win or not d or not d.get("width") or self.kiosk.is_sleeping(sid)
                    or overlay_state.is_active(sid)):
                continue
            out, _, rc = _run(["xdotool", "getwindowgeometry", "--shell", win])
            vals = dict(l.split("=", 1) for l in out.splitlines() if "=" in l) if rc == 0 else {}
            try:
                cx, cy = int(vals["X"]), int(vals["Y"])
            except (KeyError, ValueError):
                continue
            if abs(cx - d["x"]) > 8 or abs(cy - d["y"]) > 8:
                geom = (d["width"], d["height"], d["x"], d["y"])
                self.kiosk.place_window(win, geom)
                if self.kiosk.kiosk_configs.get(sid, {}).get("enforce_lock", True):
                    self.kiosk.lock_window(win)

    def _remember_layout(self, seats: List[Dict], displays: List[Dict], by_name: Dict[str, Dict]):
        """Stores the healthy extended layout so a reconnected monitor returns to its place."""
        if is_mirrored(displays):
            return
        for seat in seats:
            d = by_name.get(seat.get("display"))
            if not d or d.get("width", 0) <= 0 or self.kiosk.is_sleeping(seat.get("id")):
                return
        layout = {d["name"]: {"x": d["x"], "y": d["y"], "width": d["width"], "height": d["height"]}
                  for d in displays if d.get("width", 0) > 0}
        cfg = self.cfg_mgr.load_config()
        if cfg.get("settings", {}).get("display_layout") != layout:
            cfg.setdefault("settings", {})["display_layout"] = layout
            self.cfg_mgr.save_config(cfg)
