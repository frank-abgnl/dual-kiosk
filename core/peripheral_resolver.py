#!/usr/bin/env python3
"""
Peripheral priority queue: which physical mouse / keyboard sits on which seat.

Every (seat, class) pair is a *slot*. A slot is filled from a priority queue:

  tier 0  LOCKED device        the device recorded in config.json (mouse_name / mouse_ident).
                               Set automatically the first time hardware is detected, or by
                               hand in the Seats tab. Always wins its own slot.
  tier 1  SHORT-TERM MEMORY    a device that recently occupied this slot (matched by USB serial,
                               or name + vendor:product, plus the USB port = its "position").
                               Restores the old seating after unplug/replug, no random reshuffle.
  tier 2  STAND-IN ("beggars can't be choosers")
                               when the locked device is missing, ANY other external USB device
                               of the same class that no other seat is entitled to.

A stand-in that stays connected continuously for PROMOTE_AFTER (3 h) is promoted: the old
locked device is forgotten and the stand-in becomes the locked device. If the locked device
returns earlier it reclaims its seat and the stand-in becomes a spare.

Identity is never an xinput ID (those are recycled): serial number when the device has one,
otherwise name + vendor:product + USB port path.
"""

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("xinput_seating.resolver")

from core.paths import STATE_DIR

STATE_FILE = STATE_DIR / "peripheral_state.json"

CORE = {"pointer": 2, "keyboard": 3}
KEYS = {
    "pointer": ("mouse_name", "mouse_id", "mouse_ident"),
    "keyboard": ("keyboard_name", "keyboard_id", "keyboard_ident"),
}

PROMOTE_AFTER_SEC = 3 * 3600   # stand-in connected this long -> becomes the locked device
RESTART_GRACE_SEC = 600        # a stand-in clock survives a restart if it was seen this recently
ABSENT_GRACE_SEC = 120         # a stand-in unplugged longer than this loses its clock
RECENT_MAX = 4

# Nodes that are never a mouse/keyboard a person uses (nor a sibling to adopt)
DENY = ("power button", "sleep button", "video bus", "lid switch", "consumer control", "system control",
        "hotkey", "speaker", "headset", "headphone", "webcam", "camera", "hdmi", "wmi", "virtual", "xtest")


def read_identity(dev_id: int) -> Dict:
    """Returns {'product': (vendor, product)|None, 'usb_path': str|None, 'serial': str|None}."""
    ident = {"product": None, "usb_path": None, "serial": None}
    try:
        p = subprocess.run(["xinput", "list-props", str(dev_id)],
                           capture_output=True, text=True, timeout=5)
    except Exception:
        return ident
    if p.returncode != 0:
        return ident
    out = p.stdout
    m = re.search(r"Device Product ID \(\d+\):\s*(\d+),\s*(\d+)", out)
    if m:
        ident["product"] = (int(m.group(1)), int(m.group(2)))
    m = re.search(r'Device Node \(\d+\):\s*"([^"]+)"', out)
    if m:
        cur = os.path.realpath(f"/sys/class/input/{os.path.basename(m.group(1))}/device")
        while cur and cur not in ("/", "/sys"):
            if os.path.exists(os.path.join(cur, "idVendor")):
                ident["usb_path"] = cur
                try:
                    with open(os.path.join(cur, "serial")) as f:
                        ident["serial"] = f.read().strip() or None
                except OSError:
                    pass
                break
            cur = os.path.dirname(cur)
    return ident


def _prod(i: Dict) -> tuple:
    return tuple(i["product"]) if i.get("product") else ()


def _denied(name: str) -> bool:
    n = name.lower()
    return any(d in n for d in DENY)


def mem_score(entry: Dict, cand: Dict) -> int:
    """How strongly `cand` looks like the remembered device `entry` (0 = not the same device)."""
    if (entry.get("name") or "").lower() != (cand.get("name") or "").lower():
        return 0
    ep, cp = _prod(entry), _prod(cand)
    if ep and cp and ep != cp:
        return 0
    if entry.get("serial") and cand.get("serial"):
        return 50 if entry["serial"] == cand["serial"] else 0   # a different unit
    score = 20
    if entry.get("usb_path") and entry["usb_path"] == cand.get("usb_path"):
        score += 15                                              # same USB port = same position
    return score


class PeripheralMemory:
    """Per-slot short-term memory + stand-in timers, persisted across restarts."""

    def __init__(self, path: Path = STATE_FILE):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data: Dict = {"slots": {}}
        self.dirty = False
        self.load()

    def load(self):
        try:
            self.data = json.loads(self.path.read_text())
            self.data.setdefault("slots", {})
        except Exception:
            self.data = {"slots": {}}

    def save(self):
        with self.lock:
            if not self.dirty:
                return
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, indent=1))
                os.replace(tmp, self.path)
                self.dirty = False
            except OSError as exc:
                logger.warning(f"Could not save peripheral memory: {exc}")

    def slot(self, sid: str, cls: str) -> Dict:
        return self.data["slots"].setdefault(sid, {}).setdefault(cls, {"recent": [], "sub": None})

    def remember(self, sid: str, cls: str, ident: Dict, now: float):
        s = self.slot(sid, cls)
        front = s["recent"][0] if s["recent"] else None
        if front and mem_score(front, ident) >= 35 and now - front.get("last_seen", 0) < 300 \
                and front.get("usb_path") == ident.get("usb_path"):
            return  # nothing new
        rest = [e for e in s["recent"] if not (mem_score(e, ident) > 0 and e.get("usb_path") == ident.get("usb_path"))]
        entry = dict(ident)
        entry["last_seen"] = now
        s["recent"] = ([entry] + rest)[:RECENT_MAX]
        self.dirty = True

    def has_subs(self) -> bool:
        with self.lock:
            return any(c.get("sub") for sl in self.data["slots"].values() for c in sl.values())


class PeripheralResolver:
    def __init__(self, promote_after: float = PROMOTE_AFTER_SEC):
        self.promote_after = promote_after

    def resolve(
        self,
        seats: List[Dict],
        ptrs: List[Dict],
        kbds: List[Dict],
        seat_masters: Dict[str, Dict[str, Optional[int]]],
        memory: PeripheralMemory,
        now: Optional[float] = None,
    ):
        """
        Returns (resolved_seats, assignments, events, spares)
          assignments[(seat_id, cls)] = live device dict or None
          events   = [{'type': substitute|locked_back|promote|lock_ident, 'seat','cls','name','ident','locked'}]
          spares   = [(cls, device)] external devices sitting on a seat master that no seat needs
        resolved seats carry live mouse_id / keyboard_id (None if absent). The saved *_name stays
        the LOCKED device's name, even while a stand-in is seated.
        """
        now = time.time() if now is None else now
        resolved = [dict(s) for s in seats]
        assignments: Dict[Tuple[str, str], Optional[Dict]] = {}
        events: List[Dict] = []
        spares: List[Tuple[str, Dict]] = []
        cache: Dict[int, Dict] = {}
        all_cands = {cls: [c for c in pool if not c.get("is_xtest")]
                     for cls, pool in (("pointer", ptrs), ("keyboard", kbds))}

        def ident(c) -> Dict:
            if c["id"] not in cache:
                raw = read_identity(c["id"])
                cache[c["id"]] = {"name": c["name"], "product": raw["product"],
                                  "usb_path": raw["usb_path"], "serial": raw["serial"]}
            return cache[c["id"]]

        with memory.lock:
            for cls in ("pointer", "keyboard"):
                cands = all_cands[cls]
                name_k, id_k, ident_k = KEYS[cls]

                slots = []
                for idx, s in enumerate(resolved):
                    sid = s.get("id", f"seat{idx + 1}")
                    name = (s.get(name_k) or "").strip().lower()
                    if not name and s.get(id_k) is not None:  # config with an ID but no name
                        cur = next((c for c in cands if c["id"] == s.get(id_k)), None)
                        name = cur["name"].strip().lower() if cur else ""
                    lid = s.get(ident_k) or {}
                    if lid and (lid.get("name") or "").strip().lower() != name:
                        lid = {}  # stale identity: the lock was changed by hand
                    mine = seat_masters.get(sid, {}).get(cls)
                    others = {m.get(cls) for osid, m in seat_masters.items()
                              if osid != sid and m.get(cls) not in (None, CORE[cls])}
                    slots.append({"idx": idx, "sid": sid, "seat": s, "name": name, "lid": lid,
                                  "primary": s.get("is_primary", False), "mine": mine, "others": others})

                def attach_bonus(sl, c) -> int:
                    att, b = c.get("attachment"), 0
                    if not sl["primary"] and sl["mine"] is not None and att == sl["mine"]:
                        b += 12            # already sitting on this seat's master
                    if sl["primary"] and att == CORE[cls]:
                        b += 1
                    if att in sl["others"]:
                        b -= 30            # sitting on another seat's master
                    return b

                chosen: Dict[int, Tuple[Dict, int]] = {}
                used: set = set()

                # ---- phase A: locked device (tier 0) and short-term memory (tier 1)
                pairs = []
                for sl in slots:
                    recent = memory.slot(sl["sid"], cls)["recent"]
                    recent_names = {(e.get("name") or "").lower() for e in recent}
                    lid = sl["lid"]
                    for c in cands:
                        cname = c["name"].strip().lower()
                        by_lock = bool(sl["name"]) and cname == sl["name"]
                        if not (by_lock or cname in recent_names):
                            continue
                        ci = ident(c)
                        if by_lock and lid:
                            if _prod(lid) and _prod(ci) and _prod(lid) != _prod(ci):
                                by_lock = False
                            elif lid.get("serial") and ci.get("serial") and lid["serial"] != ci["serial"]:
                                by_lock = False
                        mem_best = 0
                        for r, e in enumerate(recent):
                            m = mem_score(e, ci)
                            if m:
                                mem_best = max(mem_best, m - 2 * r)
                        if not by_lock and mem_best <= 0:
                            continue
                        if by_lock:
                            score, tier = 100 + mem_best, 0
                            if lid.get("serial") and lid["serial"] == ci.get("serial"):
                                score += 50
                            if lid.get("usb_path") and lid["usb_path"] == ci.get("usb_path"):
                                score += 20
                        else:
                            score, tier = 60 + mem_best, 1
                        pairs.append((score + attach_bonus(sl, c), sl["idx"], c["id"], tier, c))
                pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
                for score, idx, cid, tier, c in pairs:
                    if score <= 0 or idx in chosen or cid in used:
                        continue
                    chosen[idx] = (c, tier)
                    used.add(cid)

                # ---- phase B: stand-ins for empty slots (tier 2)
                empty = [sl for sl in slots if sl["idx"] not in chosen]
                rem = [c for c in cands if c["id"] not in used]
                if empty and rem:
                    other = "keyboard" if cls == "pointer" else "pointer"
                    other_paths = {ident(oc)["usb_path"] for oc in all_cands[other]} - {None}
                    taken_paths = {ident(c)["usb_path"] for c, _ in chosen.values()} - {None}
                    pairsB = []
                    for sl in empty:
                        for c in rem:
                            ci = ident(c)
                            if not ci["usb_path"] or ci["usb_path"] in taken_paths or _denied(c["name"]):
                                continue
                            score = 10 + attach_bonus(sl, c) - (5 if ci["usb_path"] in other_paths else 0)
                            if score > 0:
                                pairsB.append((score, sl["idx"], c["id"], c))
                    pairsB.sort(key=lambda p: (-p[0], p[1], p[2]))
                    for score, idx, cid, c in pairsB:
                        if idx in chosen or cid in used:
                            continue
                        chosen[idx] = (c, 2)
                        used.add(cid)

                # ---- finalize slots: events, stand-in clocks, memory
                for sl in slots:
                    sid, s, lid = sl["sid"], sl["seat"], sl["lid"]
                    mslot = memory.slot(sid, cls)
                    got = chosen.get(sl["idx"])
                    if not got:
                        assignments[(sid, cls)] = None
                        s[id_k] = None
                        s[f"{cls}_connected"] = False
                        sub = mslot.get("sub")
                        if sub:
                            if sub.get("absent_since") is None:
                                sub["absent_since"] = now
                                memory.dirty = True
                            elif now - sub["absent_since"] > ABSENT_GRACE_SEC:
                                mslot["sub"] = None          # gone too long: its clock resets
                                memory.dirty = True
                        continue

                    c, tier = got
                    ci = ident(c)
                    assignments[(sid, cls)] = c
                    s[id_k] = c["id"]
                    s[f"{cls}_connected"] = True
                    locked_name = s.get(name_k) or ""
                    sub = mslot.get("sub")

                    if tier == 0:                            # the locked device itself
                        if sub:
                            events.append({"type": "locked_back", "seat": sid, "cls": cls,
                                           "name": c["name"], "ident": ci, "locked": locked_name})
                            mslot["sub"] = None
                            memory.dirty = True
                        s[name_k] = c["name"]
                        if not lid or (not lid.get("product") and ci.get("product")) \
                                or (not lid.get("serial") and ci.get("serial")):
                            s[ident_k] = ci
                            events.append({"type": "lock_ident", "seat": sid, "cls": cls,
                                           "name": c["name"], "ident": ci, "locked": c["name"]})
                        else:
                            s[ident_k] = lid
                    else:                                    # a stand-in (tier 1 memory or tier 2)
                        same = sub and mem_score(sub["ident"], ci) > 0 and now - sub.get("last_seen", 0) <= RESTART_GRACE_SEC
                        if same:
                            sub["absent_since"] = None
                            if now - sub["first_seen"] >= self.promote_after:
                                s[name_k], s[ident_k] = c["name"], ci   # promoted: old lock forgotten
                                events.append({"type": "promote", "seat": sid, "cls": cls, "name": c["name"],
                                               "ident": ci, "locked": locked_name})
                                mslot["sub"] = None
                            else:
                                sub["last_seen"] = now
                            memory.dirty = True
                        else:
                            mslot["sub"] = {"ident": ci, "first_seen": now, "last_seen": now, "absent_since": None}
                            memory.dirty = True
                            events.append({"type": "substitute", "seat": sid, "cls": cls, "name": c["name"],
                                           "ident": ci, "locked": locked_name})
                    memory.remember(sid, cls, ci, now)

                # ---- spares: external devices parked on a seat master that no seat needs
                assigned_ids = {c["id"] for c, _ in chosen.values()}
                assigned_paths = {ident(c)["usb_path"] for c, _ in chosen.values()} - {None}
                for c in cands:
                    att = c.get("attachment")
                    if c["id"] in assigned_ids or att in (None, CORE[cls]) or _denied(c["name"]):
                        continue
                    up = ident(c)["usb_path"]
                    if up and up not in assigned_paths:
                        spares.append((cls, c))
        return resolved, assignments, events, spares
