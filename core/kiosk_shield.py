#!/usr/bin/env python3
"""
Kiosk Shield: while kiosks run, ONLY the Control Board may appear above the browser.

Two layers (X11 / Xwayland):
  1. Super-key block. The Super/Windows keysyms are stripped from the XKB keymap of every
     master keyboard (core + seat masters), so the desktop's menu / overview / taskbar
     shortcut never fires. The keymap is re-checked every couple of seconds (desktops like
     to re-apply their layout) and restored when the shield stops.
  2. Window policing (python-xlib). Any window that sits above a kiosk on that kiosk's
     monitor and belongs to a foreign process is pushed below it (managed windows) or
     unmapped (override-redirect popups). Panels (DOCK) and notifications are handled
     even without a pid. Allowed: the kiosk browsers (and their popups, by pid), and the
     Control Board (pid / xid published through overlay_state).

Settings (config.json -> "settings", all default to true / defaults):
    kiosk_shield, block_super_key, police_windows, shield_ignore_classes
"""

import logging
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core import overlay_state

logger = logging.getLogger("xinput_seating.shield")

CONFIG_DIR = Path.home() / ".config" / "xinput-seats"
KEYMAP_DIR = CONFIG_DIR / "keymaps"

DEFAULT_IGNORE_CLASSES = ("xscreensaver", "light-locker", "i3lock", "kscreenlocker",
                          "gnome-screensaver", "slock", "xsecurelock")


def _run(args, timeout=8):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip(), p.stderr.strip(), p.returncode
    except Exception as exc:
        return "", str(exc), -1


def patch_super_keys(xkb_text: str) -> Tuple[str, int]:
    """
    Replaces Super_L / Super_R with NoSymbol, but ONLY inside xkb_symbols sections
    (replacing them in xkb_compat `interpret` lines would make them match every key).
    Returns (patched_text, number_of_replacements).
    """
    out, depth, in_sym, count = [], 0, False, 0
    for line in xkb_text.splitlines(keepends=True):
        if not in_sym and re.match(r"\s*xkb_symbols\b", line):
            in_sym, depth = True, 0
        if in_sym:
            line, n = re.subn(r"\bSuper_[LR]\b", "NoSymbol", line)
            count += n
            depth += line.count("{") - line.count("}")
            if depth <= 0 and "}" in line:
                in_sym = False
        out.append(line)
    return "".join(out), count


class KioskShield:
    POLICE_INTERVAL = 0.25
    KEYMAP_INTERVAL = 2.0
    ACT_COOLDOWN = 0.5

    def __init__(self, kiosk_mgr, engine=None):
        self.kiosk = kiosk_mgr
        self.engine = engine
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._backups: Dict[int, Path] = {}
        self._warned: set = set()
        self._acted: Dict[int, float] = {}
        self._lowered: set = set()
        self._logged: set = set()
        self._frame_cache: Dict[int, int] = {}
        self._atoms: Dict[str, int] = {}

    # ------------------------------------------------------------------
    def start(self):
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="KioskShield")
        self._thread.start()
        logger.info("Kiosk shield started (Super key blocked, only the Control Board may cover a kiosk).")

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=8)  # the thread restores keymaps / window states on its way out
        self._thread = None
        logger.info("Kiosk shield stopped.")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @staticmethod
    def _settings() -> Dict:
        try:
            from core.config_manager import ConfigManager
            st = ConfigManager().load_config().get("settings", {})
        except Exception:
            st = {}
        return {
            "enabled": st.get("kiosk_shield", True),
            "super": st.get("block_super_key", True),
            "police": st.get("police_windows", True),
            "ignore": tuple(str(c).lower() for c in st.get("shield_ignore_classes", DEFAULT_IGNORE_CLASSES)),
        }

    # ------------------------------------------------------------------
    def _loop(self):
        disp = None
        S = self._settings()
        last_cfg = last_keys = 0.0
        try:
            while not self._stop.wait(self.POLICE_INTERVAL):
                now = time.time()
                if now - last_cfg > 3:
                    S, last_cfg = self._settings(), now
                if not S["enabled"]:
                    continue
                if S["super"] and now - last_keys > self.KEYMAP_INTERVAL:
                    last_keys = now
                    try:
                        self._enforce_keymaps()
                    except Exception as exc:
                        logger.debug(f"keymap enforcement: {exc}")
                if S["police"]:
                    try:
                        if disp is None:
                            disp = self._open_display()
                            if disp is None:
                                S["police"] = False
                                continue
                        self._police(disp, S, now)
                    except Exception as exc:
                        logger.debug(f"window policing: {exc}")
                        try:
                            disp.close()
                        except Exception:
                            pass
                        disp = None
        finally:
            try:
                self._restore_windows(disp)
            except Exception:
                pass
            self._restore_keymaps()

    # ------------------------------------------------------------------
    # Layer 1: Super key
    # ------------------------------------------------------------------
    def _master_keyboards(self) -> List[int]:
        ids = {3}
        if self.engine:
            ids |= {v for v in self.engine.seat_keyboards.values() if isinstance(v, int)}
        return sorted(ids)

    def _enforce_keymaps(self):
        if not shutil.which("xkbcomp"):
            if "xkbcomp" not in self._warned:
                self._warned.add("xkbcomp")
                logger.warning("xkbcomp not found: Super key cannot be blocked "
                               "(Arch: xorg-xkbcomp, Debian/Ubuntu: x11-xkb-utils).")
            return
        KEYMAP_DIR.mkdir(parents=True, exist_ok=True)
        disp = os.environ.get("DISPLAY") or ":0"
        for dev in self._master_keyboards():
            dump = KEYMAP_DIR / f"dump_{dev}.xkb"
            _, _, rc = _run(["xkbcomp", "-i", str(dev), "-xkb", "-w", "0", disp, str(dump)])
            if rc != 0 or not dump.exists():
                continue
            text = dump.read_text(errors="replace")
            patched, n = patch_super_keys(text)
            if n == 0:
                continue  # already neutral
            if dev not in self._backups:
                backup = KEYMAP_DIR / f"orig_{dev}.xkb"
                backup.write_text(text)
                self._backups[dev] = backup
            new = KEYMAP_DIR / f"nosuper_{dev}.xkb"
            new.write_text(patched)
            _, err, rc = _run(["xkbcomp", "-i", str(dev), "-w", "0", str(new), disp])
            if rc == 0:
                logger.info(f"Super keys disabled on master keyboard {dev}.")
            else:
                logger.warning(f"Could not load patched keymap on device {dev}: {err}")

    def _restore_keymaps(self):
        disp = os.environ.get("DISPLAY") or ":0"
        for dev, backup in list(self._backups.items()):
            _run(["xkbcomp", "-i", str(dev), "-w", "0", str(backup), disp])
        if self._backups:
            logger.info("Original keymaps restored (Super key works again).")
        self._backups.clear()

    # ------------------------------------------------------------------
    # Layer 2: window policing
    # ------------------------------------------------------------------
    def _open_display(self):
        try:
            from Xlib import display as xdisplay
            d = xdisplay.Display()
        except Exception as exc:
            if "xlib" not in self._warned:
                self._warned.add("xlib")
                logger.warning(f"python-xlib unavailable ({exc}); window policing disabled "
                               f"(pip install python-xlib). Super-key block still active.")
            return None
        names = ["_NET_WM_PID", "_NET_WM_WINDOW_TYPE", "_NET_WM_WINDOW_TYPE_DOCK",
                 "_NET_WM_WINDOW_TYPE_NOTIFICATION", "_NET_WM_WINDOW_TYPE_DESKTOP",
                 "_NET_WM_STATE", "_NET_WM_STATE_BELOW", "_NET_WM_STATE_ABOVE", "WM_STATE"]
        self._atoms = {n: d.intern_atom(n) for n in names}
        return d

    @staticmethod
    def _prop(win, atom):
        try:
            p = win.get_full_property(atom, 0)  # 0 = AnyPropertyType
            return p.value if p is not None else None
        except Exception:
            return None

    def _pid(self, win) -> Optional[int]:
        v = self._prop(win, self._atoms["_NET_WM_PID"])
        try:
            return int(v[0]) if v is not None and len(v) else None
        except (TypeError, ValueError):
            return None

    def _kiosk_targets(self, d) -> List[Dict]:
        k = self.kiosk
        with k._lock:
            cfgs, wins = dict(k.kiosk_configs), dict(k.window_ids)
        root = d.screen().root.get_geometry()
        out = []
        for sid, cfg in cfgs.items():
            try:
                xid = int(str(wins.get(sid)), 0)
            except (TypeError, ValueError):
                continue
            g = cfg.get("display_geometry")  # (w, h, x, y)
            rect = (g[2], g[3], g[0], g[1]) if g else (0, 0, root.width, root.height)
            out.append({"seat": sid, "xid": xid, "rect": rect})
        return out

    def _toplevel_of(self, ids, children, xid) -> Optional[int]:
        if xid in ids:
            return xid
        f = self._frame_cache.get(xid)
        if f in ids:
            return f
        for c in children:
            try:
                if any(k.id == xid for k in c.query_tree().children):
                    self._frame_cache[xid] = c.id
                    return c.id
            except Exception:
                continue
        return None

    def _set_state(self, d, win, add: bool, atom: int):
        from Xlib import X, protocol
        ev = protocol.event.ClientMessage(
            window=win, client_type=self._atoms["_NET_WM_STATE"],
            data=(32, [1 if add else 0, atom, 0, 1, 0]))
        d.screen().root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)

    def _police(self, d, S: Dict, now: float):
        from Xlib import X
        A = self._atoms
        k = self.kiosk
        targets = self._kiosk_targets(d)
        if not targets:
            return
        root = d.screen().root
        children = list(root.query_tree().children)  # bottom -> top
        ids = {c.id: i for i, c in enumerate(children)}

        allowed_xids = {t["xid"] for t in targets}
        allowed_pids = {os.getpid()}
        with k._lock:
            allowed_pids |= {p.pid for p in k.running_processes.values() if p.poll() is None}
        for info in overlay_state.active_info().values():
            if info.get("pid"):
                allowed_pids.add(int(info["pid"]))
            if info.get("xid"):
                allowed_xids.add(int(info["xid"]))
        for xid in list(allowed_xids):
            pid = self._pid(d.create_resource_object("window", xid))
            if pid:
                allowed_pids.add(pid)

        hostile_types = {A["_NET_WM_WINDOW_TYPE_DOCK"], A["_NET_WM_WINDOW_TYPE_NOTIFICATION"]}
        desktop = A["_NET_WM_WINDOW_TYPE_DESKTOP"]

        for t in targets:
            top = self._toplevel_of(ids, children, t["xid"])
            if top is None:
                continue
            rx, ry, rw, rh = t["rect"]
            for c in children[ids[top] + 1:]:
                try:
                    attrs = c.get_attributes()
                    if attrs.map_state != X.IsViewable:
                        continue
                    g = c.get_geometry()
                    if g.x + g.width <= rx or rx + rw <= g.x or g.y + g.height <= ry or ry + rh <= g.y:
                        continue
                    if now - self._acted.get(c.id, 0) < self.ACT_COOLDOWN:
                        continue
                    kids = list(c.query_tree().children)
                    if c.id in allowed_xids or any(kd.id in allowed_xids for kd in kids):
                        continue
                    client = next((w for w in [c] + kids
                                   if self._prop(w, A["WM_STATE"]) is not None), c)
                    pid = self._pid(client) or self._pid(c)
                    if pid is not None and pid in allowed_pids:
                        continue
                    types = set(int(x) for x in (self._prop(client, A["_NET_WM_WINDOW_TYPE"]) or []))
                    if desktop in types:
                        continue
                    try:
                        wm_class = " ".join(client.get_wm_class() or ()).lower()
                    except Exception:
                        wm_class = ""
                    if any(ign in wm_class for ign in S["ignore"]):
                        continue
                    if pid is None and not (types & hostile_types):
                        continue  # cannot attribute it: leave it alone
                    self._neutralize(d, c, client, attrs.override_redirect, pid, wm_class, now)
                    if not overlay_state.is_active(t["seat"]):
                        self.kiosk._notify_window(t["seat"], hex(t["xid"]))
                except Exception as exc:
                    if exc.__class__.__name__ in ("BadWindow", "BadDrawable", "BadAccess"):
                        continue
                    raise
        d.flush()

    def _neutralize(self, d, frame, client, override: bool, pid, wm_class: str, now: float):
        self._acted[frame.id] = now
        if override:
            frame.unmap()
            how = "unmapped"
        else:
            self._set_state(d, client, False, self._atoms["_NET_WM_STATE_ABOVE"])
            self._set_state(d, client, True, self._atoms["_NET_WM_STATE_BELOW"])
            self._lowered.add(client.id)
            how = "pushed below the kiosk"
        if frame.id not in self._logged:
            self._logged.add(frame.id)
            logger.info(f"Shield: foreign window {wm_class or hex(frame.id)} (pid {pid}) {how}.")

    def _restore_windows(self, disp):
        if not self._lowered or disp is None:
            return
        for xid in list(self._lowered):
            try:
                self._set_state(disp, disp.create_resource_object("window", xid),
                                False, self._atoms["_NET_WM_STATE_BELOW"])
            except Exception:
                pass
        disp.flush()
        self._lowered.clear()
