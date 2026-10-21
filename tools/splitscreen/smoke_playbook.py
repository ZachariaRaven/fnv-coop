#!/usr/bin/env python3
"""Split-screen smoke playbook: windows placed, pad N reaches copy N only, every copy keeps simulating while unfocused.

    python -I tools/splitscreen/smoke_playbook.py --players 2 --split lr              # against the running copies (bridge pipes nvcoop-<id>)
    python -I tools/splitscreen/smoke_playbook.py --players 4 --movement               # also walk each seat with its own (synthetic) pad
    python -I tools/splitscreen/smoke_playbook.py --selftest                           # game-free: runs the steps against a model, plus broken models

Needs the copies started with:  launch.py up --players N --dev-inject   ([splitscreen] devInject=1 registers split.inject, a synthetic physical
pad per copy; the playbook presses "pad k" on EVERY copy and checks that each copy sees only the pad it was assigned). Nobody needs to hold a
controller. Use --no-inject to run only the window and hook steps (real controllers, nothing synthetic).

Steps (each prints PASS or FAIL with the numbers):
  windows     every copy: plugin active, window found, client area == its cell, borderless, not minimized
  hooks       the XInput and GetActiveWindow import slots are patched and still ours
  pads        press physical pads 0..3 with different buttons on every copy; copy i must see exactly the buttons of ITS pad
              (keyboard and mouse seat: "not connected", engine in keyboard mode; ghost seat: neutral pad, engine in gamepad mode), and must
              not have read any other physical pad
  unfocused   the engine keeps polling the pad on every copy, focused or not (statePolls grows on all of them)
  movement    (--movement, needs a loaded world in every copy) stick forward on seat s's pad: seat s walks, the others stay put
Exit 0 and "ALL OK" when every step passes.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for sub in (HERE, REPO / "tools" / "runner", REPO / "tools" / "pool"):
    sys.path.insert(0, str(sub))

import plan as planmod  # noqa: E402
import split_mut  # noqa: E402
from layout import Rect  # noqa: E402

A, B, X, Y = 0x1000, 0x2000, 0x4000, 0x8000
MARKERS = {k: {"buttons": (A, B, X, Y)[k], "lx": 1000 * (k + 1)} for k in range(4)}   # pad k presses A/B/X/Y and tilts the stick 1000*(k+1)
NOT_CONNECTED = 1167


# ------------------------------------------------------------------------------------------------------ machines
class Machine:
    """One game copy as the playbook sees it."""
    seat: planmod.Seat

    def status(self) -> dict: raise NotImplementedError
    def inject(self, pad: int, state: Optional[dict]) -> None: raise NotImplementedError   # None clears the pad
    def frames(self) -> int: raise NotImplementedError
    def getpos(self) -> Optional[Tuple[float, float, float]]: raise NotImplementedError
    def wait(self, seconds: float) -> None: raise NotImplementedError
    def menu_kind(self) -> Optional[str]: return None          # bot.menu kind ("none", "message", ...); None = unknown
    def dismiss_messages(self) -> str: return "not supported"


class BridgeMachine(Machine):
    def __init__(self, seat: planmod.Seat, wait_s: float = 5.0):
        import nvbridge
        self.seat = seat
        self.nv = nvbridge.NvBridge.connect(seat.instance, wait_s=wait_s)

    def status(self) -> dict:
        return self.nv.call("split.status", timeout_s=15)

    def inject(self, pad: int, state: Optional[dict]) -> None:
        self.nv.call("split.inject", {"pad": pad, "clear": True} if state is None else dict(state, pad=pad), timeout_s=15)

    def frames(self) -> int:
        return int(self.nv.ping().get("frames", 0))

    def getpos(self) -> Optional[Tuple[float, float, float]]:
        try:
            return tuple(self.nv.eval(f"player.GetPos {a}") for a in "XYZ")      # type: ignore[return-value]
        except Exception:
            pass
        try:
            out = []
            for a in "xyz":
                m = re.search(r"-?\d+(\.\d+)?", self.nv.exec(f"player.getpos {a}").get("output", ""))
                out.append(float(m.group(0)) if m else float("nan"))
            return tuple(out) if all(v == v for v in out) else None             # type: ignore[return-value]
        except Exception:
            return None

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def menu_kind(self) -> Optional[str]:
        try:
            return str(self.nv.call("bot.menu", timeout_s=15).get("kind"))   # needs [dev] bots=1
        except Exception:
            return None

    def dismiss_messages(self) -> str:
        if str(REPO / "tools") not in sys.path:
            sys.path.insert(0, str(REPO / "tools"))
        from bots import Bot
        return str(Bot(self.nv, f"split{self.seat.index + 1}").dismiss_messages())


class FakeMachine(Machine):
    """A model of one copy with the same observable behaviour as the plugin. `bug` switches on one fault (the playbook must catch each)."""

    def __init__(self, seat: planmod.Seat, foreground: bool = False, bug: str = ""):
        self.seat, self.fg, self.bug = seat, foreground, bug
        self.inj: Dict[int, dict] = {}
        self.polls = 0
        self.phys = [0, 0, 0, 0]
        self.last = {"result": NOT_CONNECTED, "buttons": 0, "lx": 0}
        self.mode = 0
        self.pos = [100.0 * seat.index, 0.0, 0.0]
        self.frame = 0
        self.menu = "message" if bug == "msgbox" else "none"     # msgbox: the DLC notices after a load hold the copy in menu mode

    def _poll(self) -> None:
        s = self.seat
        pad = s.pad
        if self.bug == "crosstalk" and pad is not None:
            pad = 0
        res, buttons, lx = NOT_CONNECTED, 0, 0
        if pad is not None:
            self.phys[pad] += 1
            if pad in self.inj:
                res, buttons, lx = 0, self.inj[pad]["buttons"], self.inj[pad].get("lx", 0)
        if res != 0 and s.ghost:
            res, buttons, lx = 0, 0, 0
        if self.bug == "peek" and pad is not None and pad != 0:
            self.phys[0] += 1
        self.polls += 1
        self.last = {"result": res, "buttons": buttons, "lx": lx}
        self.mode = 1 if res == 0 else 0
        stick_y = self.inj.get(pad, {}).get("ly", 0) if pad is not None else 0
        if self.mode and stick_y > 16000 and self.menu == "none" and not (self.bug == "frozen_unfocused" and not self.fg):
            self.pos[1] += 5.0

    def wait(self, seconds: float) -> None:
        if self.bug == "no_poll_unfocused" and not self.fg:
            self.frame += int(seconds * 60)
            return
        for _ in range(max(1, int(seconds * 60))):
            self._poll()
            self.frame += 1

    def status(self) -> dict:
        c = self.seat.cell
        cw = c.w // 2 if self.bug == "wrong_size" else c.w
        return {"enabled": True, "player": self.seat.index, "players": 2,
                "hooks": {"xinputGetState": 1, "xinputSetState": 1, "xinputGetCapabilities": 0, "getActiveWindow": 1, "intact": self.bug != "chained"},
                "pins": {"ok": True, "mismatch": []},
                "padStats": {"statePolls": self.polls, "physicalPolls": list(self.phys), "lastResult": self.last["result"],
                             "lastButtons": self.last["buttons"], "lastLX": self.last["lx"], "gamepadMode": self.mode},
                "window": {"found": True, "clientW": cw, "clientH": c.h, "clientX": c.x, "clientY": c.y, "borderless": True,
                           "minimized": self.bug == "minimized", "foreground": self.fg}}

    def inject(self, pad: int, state: Optional[dict]) -> None:
        if state is None:
            self.inj.pop(pad, None)
        else:
            self.inj[pad] = dict(state)

    def frames(self) -> int:
        return self.frame

    def getpos(self) -> Optional[Tuple[float, float, float]]:
        return tuple(self.pos)                                                   # type: ignore[return-value]

    def menu_kind(self) -> Optional[str]:
        return self.menu

    def dismiss_messages(self) -> str:
        self.menu = "none"
        if self.bug == "msgbox_wave" and not getattr(self, "_waved", False):
            self._waved = True                  # a second wave arrives while the other copies are being cleared
            self.menu = "message"
        return "dismiss_messages: ok"


# ------------------------------------------------------------------------------------------------------ steps
@dataclass
class Result:
    step: str
    ok: bool
    detail: str


def step_windows(ms: List[Machine]) -> Result:
    bad = []
    for m in ms:
        st, c = m.status(), m.seat.cell
        w = st.get("window") or {}
        who = f"P{m.seat.index + 1}"
        if not st.get("enabled"):
            bad.append(f"{who}: plugin split-screen is off ({st.get('configError') or 'enable=0'})")
        elif not w.get("found"):
            bad.append(f"{who}: no window")
        else:
            if (w.get("clientW"), w.get("clientH")) != (c.w, c.h):
                bad.append(f"{who}: client {w.get('clientW')}x{w.get('clientH')} != cell {c.w}x{c.h}")
            if (w.get("clientX"), w.get("clientY")) != (c.x, c.y):
                bad.append(f"{who}: origin {w.get('clientX')},{w.get('clientY')} != {c.x},{c.y}")
            if not w.get("borderless"):
                bad.append(f"{who}: has a frame")
            if w.get("minimized"):
                bad.append(f"{who}: minimized")
    return Result("windows", not bad, "; ".join(bad) or f"{len(ms)} windows on their cells")


def step_hooks(ms: List[Machine]) -> Result:
    bad = []
    for m in ms:
        h = m.status().get("hooks") or {}
        who = f"P{m.seat.index + 1}"
        if not h.get("xinputGetState"):
            bad.append(f"{who}: XInputGetState slot not patched")
        if not h.get("getActiveWindow"):
            bad.append(f"{who}: GetActiveWindow slot not patched")
        if h.get("intact") is False:
            bad.append(f"{who}: a patched slot was overwritten")
    return Result("hooks", not bad, "; ".join(bad) or "XInput and GetActiveWindow slots patched on every copy")


def _press_all(ms: List[Machine]) -> None:
    for m in ms:
        for k in range(4):
            m.inject(k, dict(MARKERS[k]))


def _release_all(ms: List[Machine]) -> None:
    for m in ms:
        for k in range(4):
            m.inject(k, None)


def _wait_all(ms: List[Machine], seconds: float) -> None:
    for m in ms:
        m.wait(seconds)


def step_pads(ms: List[Machine]) -> Result:
    bad = []
    before = {m.seat.index: m.status()["padStats"] for m in ms}
    _press_all(ms)
    _wait_all(ms, 0.5)
    for m in ms:
        s, ps = m.seat, m.status()["padStats"]
        who = f"P{s.index + 1}"
        base = before[s.index]
        if s.pad is not None:
            want = MARKERS[s.pad]
            if ps["lastResult"] != 0 or ps["lastButtons"] != want["buttons"] or ps["lastLX"] != want["lx"]:
                bad.append(f"{who}: saw result={ps['lastResult']} buttons={ps['lastButtons']:#x} lx={ps['lastLX']}, wanted pad {s.pad}'s "
                           f"{want['buttons']:#x}/{want['lx']}")
            grew = [k for k in range(4) if ps["physicalPolls"][k] > base["physicalPolls"][k]]
            if grew != [s.pad] and not split_mut.on("accept_any_physical"):
                bad.append(f"{who}: read physical pads {grew}, only {s.pad} is allowed")
            if ps["gamepadMode"] not in (1, -1):
                bad.append(f"{who}: the engine is not in gamepad mode (byte {ps['gamepadMode']})")
        elif s.ghost:
            if ps["lastResult"] != 0 or ps["lastButtons"] != 0 or ps["lastLX"] != 0:
                bad.append(f"{who}: ghost seat saw {ps['lastButtons']:#x}/{ps['lastLX']}, wanted a neutral pad")
            if any(ps["physicalPolls"][k] > base["physicalPolls"][k] for k in range(4)):
                bad.append(f"{who}: ghost seat read a physical pad")
            if ps["gamepadMode"] not in (1, -1):
                bad.append(f"{who}: ghost seat is not in gamepad mode: it would listen to the keyboard")
        else:
            if ps["lastResult"] != NOT_CONNECTED:
                bad.append(f"{who}: keyboard seat sees a pad (result {ps['lastResult']})")
            if ps["gamepadMode"] not in (0, -1):
                bad.append(f"{who}: keyboard seat is in gamepad mode (byte {ps['gamepadMode']})")
    _release_all(ms)
    return Result("pads", not bad, "; ".join(bad) or "every copy saw only its own pad")


def step_unfocused(ms: List[Machine]) -> Result:
    before = {m.seat.index: m.status() for m in ms}
    _wait_all(ms, 1.0)
    bad, fg = [], []
    for m in ms:
        st = m.status()
        grew = st["padStats"]["statePolls"] - before[m.seat.index]["padStats"]["statePolls"]
        if (st.get("window") or {}).get("foreground"):
            fg.append(m.seat.index + 1)
        if grew < 5:
            bad.append(f"P{m.seat.index + 1}: only {grew} pad polls in 1 s ({'in front' if (st.get('window') or {}).get('foreground') else 'not in front'})")
    return Result("unfocused", not bad, "; ".join(bad) or f"all copies polled their pad; in front: {fg or 'none of them'}")


def step_movement(ms: List[Machine]) -> Result:
    bad = []
    for m in ms:
        if m.getpos() is None:
            return Result("movement", False, f"P{m.seat.index + 1}: no player position (load a save in every copy first)")
    # In game 2026-10-21 the DLC notices after a load held both copies in menu mode, and the step only said "did not walk".
    boxed = [f"P{m.seat.index + 1}" for m in ms if m.menu_kind() == "message"]
    if boxed:
        return Result("movement", False, f"{', '.join(boxed)}: a message box is open (the DLC notices after a load); "
                                         f"clear it, or run with --dismiss-messages")
    for s_m in ms:
        s = s_m.seat
        if s.pad is None:
            continue
        start = {m.seat.index: m.getpos() for m in ms}
        for m in ms:
            m.inject(s.pad, {"buttons": 0, "ly": 32000})
        _wait_all(ms, 3.0)
        for m in ms:
            a, b = start[m.seat.index], m.getpos()
            moved = ((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5            # type: ignore[index]
            if m.seat.index == s.index and moved < 50:
                bad.append(f"P{s.index + 1} did not walk with its own pad (moved {moved:.0f})")
            if m.seat.index != s.index and moved > 5:
                bad.append(f"P{m.seat.index + 1} moved {moved:.0f} when pad {s.pad} (P{s.index + 1}'s) was pressed")
        for m in ms:
            m.inject(s.pad, None)
        _wait_all(ms, 1.0)
    return Result("movement", not bad, "; ".join(bad) or "each seat walked with its own pad only")


DISMISS_ROUNDS = 4   # --dismiss-messages: rounds over all copies at most


def run_steps(ms: List[Machine], inject: bool = True, movement: bool = False, out: Callable[[str], None] = print,
              dismiss: bool = False) -> List[Result]:
    if dismiss:
        # The DLC notices come in waves (in game 2026-10-21, 3 copies: a new wave reached P1 and P2 while P3 was cleared), so clear every
        # copy, then look again, until one round finds no box on any copy.
        for rnd in range(DISMISS_ROUNDS):
            for m in ms:
                out(f"P{m.seat.index + 1}: {m.dismiss_messages()}")
            if not any(m.menu_kind() == "message" for m in ms):
                break
            out(f"a message box is open again after round {rnd + 1}: clearing again")
    steps: List[Callable[[List[Machine]], Result]] = [step_windows, step_hooks]
    if inject:
        steps += [step_pads, step_unfocused]
        if movement:
            steps.append(step_movement)
    results = []
    for fn in steps:
        try:
            r = fn(ms)
        except Exception as e:                       # a dead pipe or a missing method is a failed step, not a crash
            r = Result(fn.__name__.replace("step_", ""), False, f"{type(e).__name__}: {e}")
        out(f"{'PASS' if r.ok else 'FAIL'} {r.step}: {r.detail}")
        results.append(r)
    return results


# ------------------------------------------------------------------------------------------------------ selftest
def _plan(players: int = 2, pads=None) -> planmod.Plan:
    pads = pads if pads is not None else [None] + list(range(players - 1))
    return planmod.build_plan(players, "lr", Rect(0, 0, 1920, 1080), 1, pads, dev_inject=True)


def selftest() -> int:
    fails = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            fails.append(name)

    def run(bug: str = "", players: int = 2, pads=None, movement: bool = True, fg: int = 0, bug_seat: Optional[int] = None):
        p = _plan(players, pads)
        ms = [FakeMachine(s, foreground=(s.index == fg), bug=(bug if bug_seat in (None, s.index) else "")) for s in p.seats]
        return run_steps(ms, True, movement, out=lambda _l: None)

    def names(rs, ok):
        return {r.step for r in rs if r.ok == ok}

    rs = run()
    check("a correct 2 player model passes every step", all(r.ok for r in rs) and len(rs) == 5)
    rs = run(players=4)
    check("a correct 4 player model passes", all(r.ok for r in rs))
    rs = run(pads=[None, None], movement=False)
    check("an idle (ghost) seat passes", all(r.ok for r in rs))
    rs = run(pads=[1, 0], movement=True)
    check("player one on pad 1 and player two on pad 0 passes", all(r.ok for r in rs))
    for bug, step in (("wrong_size", "windows"), ("minimized", "windows"), ("chained", "hooks"),
                      ("no_poll_unfocused", "unfocused"), ("frozen_unfocused", "movement")):
        rs = run(bug, fg=0, bug_seat=1)
        check(f"fault '{bug}' is caught by step '{step}'", step in names(rs, False))
    # a copy that reads another copy's pad: seat 2 owns pad 1 but reads pad 0 (crosstalk), or reads its own and also peeks at pad 0
    rs = run("crosstalk", pads=[None, 1], fg=0, bug_seat=1, movement=False)
    check("crosstalk is caught by step 'pads'", "pads" in names(rs, False))
    rs = run("peek", pads=[None, 1], fg=0, bug_seat=1, movement=False)
    check("a copy that also polls another physical pad is caught by step 'pads'", "pads" in names(rs, False))
    rs = run("crosstalk", pads=[None, 1], fg=0, bug_seat=1, movement=True)
    check("crosstalk moves the wrong player in step 'movement'", "movement" in names(rs, False))
    rs = run("msgbox", fg=0, bug_seat=1)
    mv = [r for r in rs if r.step == "movement"]
    check("an open message box fails step 'movement' and says so", bool(mv) and not mv[0].ok and "message box" in mv[0].detail)
    p = _plan(2)
    ms = [FakeMachine(s, foreground=(s.index == 0), bug=("msgbox" if s.index == 1 else "")) for s in p.seats]
    check("--dismiss-messages clears the box and every step passes", all(r.ok for r in run_steps(ms, True, True, out=lambda _l: None, dismiss=True)))
    ms = [FakeMachine(s, foreground=(s.index == 0), bug=("msgbox_wave" if s.index == 1 else "")) for s in p.seats]
    check("--dismiss-messages clears a second wave too", all(r.ok for r in run_steps(ms, True, True, out=lambda _l: None, dismiss=True)))
    global DISMISS_ROUNDS
    keep, DISMISS_ROUNDS = DISMISS_ROUNDS, 1          # twin: one round only (the first version) misses the second wave
    ms = [FakeMachine(s, foreground=(s.index == 0), bug=("msgbox_wave" if s.index == 1 else "")) for s in p.seats]
    check("twin: one dismiss round misses a second wave", not all(r.ok for r in run_steps(ms, True, True, out=lambda _l: None, dismiss=True)))
    DISMISS_ROUNDS = keep
    if fails:
        print("smoke_playbook selftest FAILED: " + ", ".join(fails))
        return 1
    print("smoke_playbook selftest: ALL OK")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--players", type=int, default=2, choices=(2, 3, 4))
    ap.add_argument("--split", default="lr", choices=("lr", "tb"))
    ap.add_argument("--screen", default="", help="WxH of the monitor the layout was made for (default: the primary monitor)")
    ap.add_argument("--monitor", default="primary")
    ap.add_argument("--pads", default="", help="same as launch.py --pads")
    ap.add_argument("--p1-pad", action="store_true")
    ap.add_argument("--fit", default="fill")
    ap.add_argument("--base-id", type=int, default=planmod.DEFAULT_BASE_ID)
    ap.add_argument("--movement", action="store_true")
    ap.add_argument("--dismiss-messages", action="store_true", help="first clear the engine message boxes on every copy (needs [dev] bots=1)")
    ap.add_argument("--no-inject", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    import monitors as mon
    try:
        if a.screen:
            w, h = (int(x) for x in a.screen.lower().split("x"))
            area, idx = Rect(0, 0, w, h), 1
        else:
            m = mon.choose(mon.list_monitors(), a.monitor)
            area, idx = m.rect, m.index
        pads = planmod.parse_pad_spec(a.pads, a.players) if a.pads else \
            planmod.assign_pads(a.players, [0, 1, 2], p1_pad=a.p1_pad)
        plan = planmod.build_plan(a.players, a.split, area, idx, pads, base_id=a.base_id, fit_ratio=a.fit, dev_inject=True)
    except (ValueError, planmod.PlanError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        ms: List[Machine] = [BridgeMachine(s) for s in plan.seats]
    except Exception as e:
        print(f"cannot reach every copy: {e}. Start them with: launch.py up --players {a.players} --dev-inject", file=sys.stderr)
        return 3
    rs = run_steps(ms, inject=not a.no_inject, movement=a.movement, dismiss=a.dismiss_messages)
    if all(r.ok for r in rs):
        print("ALL OK")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
