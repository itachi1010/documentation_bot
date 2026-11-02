"""
BennyAI Launcher
Run: python startbennyai.py
"""
import tkinter as tk
from tkinter import ttk
import subprocess
import threading
import sys
import os
import time
import math
import requests as _req
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
BENNYAI_PY  = SCRIPT_DIR / "bennyai.py"
OLLAMA_BASE = "http://localhost:11434"

# ── Palette ───────────────────────────────────────────────────────────────────
BG       = "#0b0e18"
PANEL    = "#13172a"
BORDER   = "#1f2440"
FG       = "#e2e8f0"
DIM      = "#4a5568"
ACCENT   = "#6c8ef7"
ACCENT2  = "#f7a26c"
GREEN    = "#4ade80"
RED      = "#f87171"
YELLOW   = "#fbbf24"
GLOW_A   = "#6c8ef740"


# ── Animated glow ring ────────────────────────────────────────────────────────
class GlowRing(tk.Canvas):
    def __init__(self, parent, size=160, **kw):
        super().__init__(parent, width=size, height=size,
                         bg=BG, highlightthickness=0, **kw)
        self._size = size
        self._phase = 0.0
        self._running = False
        self._draw(1.0)

    def _draw(self, alpha):
        s = self._size
        c = s // 2
        r = c - 12
        self.delete("all")
        # Outer glow rings
        for i, (dr, a) in enumerate([(14, 0.08), (9, 0.14), (5, 0.22)]):
            col = self._alpha_hex(ACCENT, alpha * a)
            self.create_oval(c-r-dr, c-r-dr, c+r+dr, c+r+dr,
                             outline=col, width=1)
        # Spinning arc (drawn as segments)
        arc_len = 220
        for deg in range(0, arc_len, 3):
            ang = math.radians(self._phase + deg)
            t   = deg / arc_len
            a   = alpha * (0.15 + 0.85 * t)
            col = self._blend(ACCENT, ACCENT2, t, a)
            x1  = c + (r-1) * math.cos(ang)
            y1  = c - (r-1) * math.sin(ang)
            x2  = c + (r+1) * math.cos(ang)
            y2  = c - (r+1) * math.sin(ang)
            self.create_line(x1, y1, x2, y2, fill=col, width=2)
        # Inner circle
        ir = r - 10
        self.create_oval(c-ir, c-ir, c+ir, c+ir,
                         fill=PANEL, outline=BORDER, width=1)

    def _alpha_hex(self, color, alpha):
        r, g, b = int(color[1:3],16), int(color[3:5],16), int(color[5:7],16)
        br, bg2, bb = int(BG[1:3],16), int(BG[3:5],16), int(BG[5:7],16)
        r2 = int(br + (r-br)*alpha)
        g2 = int(bg2 + (g-bg2)*alpha)
        b2 = int(bb + (b-bb)*alpha)
        return f"#{r2:02x}{g2:02x}{b2:02x}"

    def _blend(self, c1, c2, t, alpha=1.0):
        r1,g1,b1 = int(c1[1:3],16),int(c1[3:5],16),int(c1[5:7],16)
        r2,g2,b2 = int(c2[1:3],16),int(c2[3:5],16),int(c2[5:7],16)
        br,bg2,bb = int(BG[1:3],16),int(BG[3:5],16),int(BG[5:7],16)
        r = int(r1 + (r2-r1)*t); g = int(g1 + (g2-g1)*t); b = int(b1 + (b2-b1)*t)
        r = int(br + (r-br)*alpha); g = int(bg2 + (g-bg2)*alpha); b = int(bb + (b-bb)*alpha)
        return f"#{r:02x}{g:02x}{b:02x}"

    def start_spin(self):
        self._running = True
        self._tick()

    def stop_spin(self):
        self._running = False

    def _tick(self):
        if not self._running:
            return
        self._phase = (self._phase + 4) % 360
        self._draw(1.0)
        self.after(30, self._tick)

    def pulse(self):
        for i in range(10):
            self.after(i * 60, lambda a=math.sin(i/10*math.pi): self._draw(0.5 + 0.5*a))


# ── Status row ────────────────────────────────────────────────────────────────
class StatusRow(tk.Frame):
    def __init__(self, parent, label, **kw):
        super().__init__(parent, bg=PANEL, **kw)
        self._dot = tk.Label(self, text="●", fg=DIM, bg=PANEL, font=("Segoe UI", 10))
        self._dot.pack(side="left", padx=(0, 6))
        tk.Label(self, text=label, fg=DIM, bg=PANEL,
                 font=("Segoe UI", 10)).pack(side="left")
        self._status = tk.Label(self, text="Checking…", fg=DIM, bg=PANEL,
                                font=("Segoe UI", 10))
        self._status.pack(side="right")

    def set(self, text, color=DIM):
        self._dot.configure(fg=color)
        self._status.configure(text=text, fg=color)


# ── Main launcher window ───────────────────────────────────────────────────────
class Launcher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BennyAI Launcher")
        self.resizable(False, False)
        self.configure(bg=BG)
        self._center(440, 600)
        self._build_ui()
        self.after(100, self._check_all)

    def _center(self, w, h):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        # Top bar
        bar = tk.Frame(self, bg=PANEL, height=44)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        tk.Label(bar, text="Benny", fg=ACCENT, bg=PANEL,
                 font=("Segoe UI", 15, "bold")).pack(side="left", padx=(18,0), pady=10)
        tk.Label(bar, text="AI", fg=ACCENT2, bg=PANEL,
                 font=("Segoe UI", 15, "bold")).pack(side="left")
        tk.Label(bar, text="v1.0", fg=DIM, bg=PANEL,
                 font=("Segoe UI", 9)).pack(side="left", padx=(6,0), pady=14)

        # Separator
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # Hero area
        hero = tk.Frame(self, bg=BG, pady=28)
        hero.pack(fill="x")

        # Glow ring + logo text
        ring_frame = tk.Frame(hero, bg=BG)
        ring_frame.pack()
        self._ring = GlowRing(ring_frame, size=160)
        self._ring.pack()

        # BennyAI text inside ring (overlay via place)
        inner = tk.Frame(self._ring, bg=PANEL, width=100, height=100)
        inner.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(inner, text="B", fg=ACCENT, bg=PANEL,
                 font=("Segoe UI", 36, "bold")).pack(expand=True)

        tk.Label(hero, text="Local AI Coding Assistant",
                 fg=DIM, bg=BG, font=("Segoe UI", 11)).pack(pady=(12, 0))
        tk.Label(hero, text="Powered by DeepSeek Coder · Runs on your machine",
                 fg=DIM, bg=BG, font=("Segoe UI", 9)).pack(pady=(2, 0))

        # Status card
        card = tk.Frame(self, bg=PANEL, bd=0, relief="flat",
                        highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", padx=28, pady=(0, 20))

        tk.Label(card, text="SYSTEM CHECK", fg=DIM, bg=PANEL,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=16, pady=(12,6))
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=16)

        self._row_ollama  = StatusRow(card, "Ollama server")
        self._row_ollama.pack(fill="x", padx=16, pady=6)
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=16)

        self._row_model   = StatusRow(card, "DeepSeek model")
        self._row_model.pack(fill="x", padx=16, pady=6)
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=16)

        self._row_script  = StatusRow(card, "bennyai.py")
        self._row_script.pack(fill="x", padx=16, pady=6)
        tk.Frame(card, bg=BORDER, height=1).pack(fill="x", padx=16)

        self._row_deps    = StatusRow(card, "Python packages")
        self._row_deps.pack(fill="x", padx=16, pady=(6, 14))

        # Launch button
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(fill="x", padx=28, pady=(0, 14))

        self._launch_btn = tk.Button(
            btn_frame, text="⬡  Launch BennyAI",
            bg=ACCENT, fg="#ffffff", activebackground="#8aaafb",
            activeforeground="#ffffff", relief="flat", bd=0,
            font=("Segoe UI", 13, "bold"), cursor="hand2",
            pady=14, command=self._launch)
        self._launch_btn.pack(fill="x", ipady=2)

        # Status label
        self._status_lbl = tk.Label(self, text="Checking prerequisites…",
                                    fg=DIM, bg=BG, font=("Segoe UI", 9))
        self._status_lbl.pack(pady=(0, 8))

        # Footer
        tk.Label(self, text="© 2024 BennyAI · Runs 100% locally · No data leaves your machine",
                 fg=DIM, bg=BG, font=("Segoe UI", 8)).pack(pady=(0, 14))

        # Start ring animation
        self._ring.start_spin()

    # ── Checks ────────────────────────────────────────────────────────────────
    def _check_all(self):
        self._ready = False
        threading.Thread(target=self._run_checks, daemon=True).start()

    def _run_checks(self):
        ok_all = True

        # Ollama
        try:
            r = _req.get(f"{OLLAMA_BASE}/api/tags", timeout=3)
            models = [m["name"] for m in r.json().get("models", [])]
            self.after(0, self._row_ollama.set, "Running", GREEN)

            # Model
            has_model = any("deepseek-coder" in m for m in models)
            if has_model:
                self.after(0, self._row_model.set, "Found", GREEN)
            else:
                self.after(0, self._row_model.set, "Not pulled — run: ollama pull deepseek-coder:6.7b", YELLOW)
                ok_all = False
        except Exception:
            self.after(0, self._row_ollama.set, "Not running — start Ollama first", RED)
            self.after(0, self._row_model.set, "Unavailable", DIM)
            ok_all = False

        # Script
        if BENNYAI_PY.exists():
            self.after(0, self._row_script.set, "Found", GREEN)
        else:
            self.after(0, self._row_script.set, f"Not found at {BENNYAI_PY}", RED)
            ok_all = False

        # Packages
        missing = []
        for pkg, imp in [("requests","requests"), ("pdfplumber","pdfplumber"), ("bs4","bs4")]:
            try:
                __import__(imp)
            except ImportError:
                missing.append(pkg)
        if missing:
            self.after(0, self._row_deps.set, f"Missing: {', '.join(missing)} (will auto-install)", YELLOW)
        else:
            self.after(0, self._row_deps.set, "All installed", GREEN)

        self._ready = ok_all
        if ok_all:
            self.after(0, self._status_lbl.configure,
                       {"text": "All systems ready — press Launch!", "fg": GREEN})
        else:
            self.after(0, self._status_lbl.configure,
                       {"text": "Fix issues above, then try again.", "fg": YELLOW})
            self.after(0, self._set_btn_warn)

    def _set_btn_warn(self):
        self._launch_btn.configure(bg="#7c5a1a", text="⚠  Launch Anyway")

    # ── Launch ────────────────────────────────────────────────────────────────
    def _launch(self):
        self._launch_btn.configure(state="disabled", text="Launching…", bg=DIM)
        self._status_lbl.configure(text="Starting BennyAI…", fg=ACCENT)
        self._ring.pulse()
        threading.Thread(target=self._do_launch, daemon=True).start()

    def _do_launch(self):
        try:
            proc = subprocess.Popen(
                [sys.executable, str(BENNYAI_PY)],
                cwd=str(SCRIPT_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            # Give it a moment to start
            time.sleep(1.5)
            if proc.poll() is not None:
                # Crashed immediately
                self.after(0, self._launch_failed, "BennyAI crashed on startup. Check the terminal for errors.")
            else:
                self.after(0, self._launch_success)
        except Exception as e:
            self.after(0, self._launch_failed, str(e))

    def _launch_success(self):
        self._status_lbl.configure(text="BennyAI is running!", fg=GREEN)
        self._launch_btn.configure(text="✓  Running", bg="#14532d")
        # Close launcher after 2 seconds
        self.after(2000, self.destroy)

    def _launch_failed(self, msg):
        self._launch_btn.configure(state="normal", text="⬡  Retry Launch", bg=RED)
        self._status_lbl.configure(text=f"Error: {msg}", fg=RED)


if __name__ == "__main__":
    app = Launcher()
    app.mainloop()
