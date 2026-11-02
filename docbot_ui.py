"""
DocBot UI  —  Tkinter interface for the Documentation Bot API
Run:  python docbot_ui.py
Dep:  pip install requests
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
import threading
import json
import os
from pathlib import Path
from datetime import datetime

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

API_BASE = os.getenv("DOCBOT_API", "http://localhost:8000")

# ── Palette ────────────────────────────────────────────────────────────────
BG        = "#1e1e2e"
PANEL     = "#252535"
ENTRY_BG  = "#2a2a3d"
ACCENT    = "#7aa2f7"
SUCCESS   = "#9ece6a"
WARNING   = "#e0af68"
DANGER    = "#f7768e"
FG        = "#c0caf5"
FG_DIM    = "#6b7099"
OUTPUT_BG = "#13131f"
BTN       = "#3d59a1"
BTN_HOV   = "#4a6bc7"
BORDER    = "#3b3b5c"


# ── Helpers ─────────────────────────────────────────────────────────────────
def win_to_linux(win_path: str) -> str:
    """D:\\foo\\bar  →  /mnt/d/foo/bar"""
    p = Path(win_path)
    parts = p.parts
    if not parts:
        return win_path
    drive = parts[0].rstrip(":\\/").lower()
    rest = "/".join(parts[1:])
    return f"/mnt/{drive}/{rest}"


def detect_language(filename: str) -> str:
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".go": "go", ".java": "java", ".cs": "csharp", ".cpp": "cpp",
        ".c": "c", ".rs": "rust", ".dart": "dart", ".kt": "kotlin",
        ".rb": "ruby", ".php": "php", ".tsx": "tsx", ".jsx": "jsx",
    }.get(Path(filename).suffix.lower(), "text")


# ── API client ───────────────────────────────────────────────────────────────
class APIClient:
    def __init__(self, base=API_BASE):
        self.base = base.rstrip("/")

    def _get(self, path, timeout=8):
        r = requests.get(f"{self.base}{path}", timeout=timeout)
        r.raise_for_status()
        return r.json()

    def _post(self, path, body=None, timeout=600):
        r = requests.post(f"{self.base}{path}", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def health(self):        return self._get("/health", timeout=5)
    def snapshots(self):     return self._get("/snapshots")
    def models(self):        return self._get("/models")

    def scan(self, path, label):
        return self._post("/scan", {"path": path, "label": label}, timeout=30)

    def readme(self, label):
        return self._post(f"/generate/readme/{label}")["readme"]

    def api_docs(self, label):
        return self._post(f"/generate/api-docs/{label}")["api_docs"]

    def doc_file(self, file_path, content, language):
        return self._post("/generate/file-doc",
                          {"file_path": file_path, "content": content, "language": language}
                          )["documentation"]

    def ask(self, label, question):
        return self._post("/ask", {"snapshot_label": label, "question": question})["answer"]

    def raw_generate(self, prompt, system=""):
        return self._post("/generate", {"prompt": prompt, "system": system})["response"]


# ── Main window ──────────────────────────────────────────────────────────────
class DocBotUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DocBot — Documentation Generator")
        self.geometry("1180x800")
        self.minsize(940, 640)
        self.configure(bg=BG)
        self.api = APIClient()
        self._busy = False

        self._apply_style()
        self._build_ui()
        self.after(200, self._refresh_api)

    # ── Style ──────────────────────────────────────────────────────────────
    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")

        s.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("TFrame",        background=BG)
        s.configure("Panel.TFrame",  background=PANEL)
        s.configure("TLabel",        background=BG,    foreground=FG)
        s.configure("Dim.TLabel",    background=BG,    foreground=FG_DIM, font=("Segoe UI", 9))
        s.configure("Panel.TLabel",  background=PANEL, foreground=FG)
        s.configure("Head.TLabel",   background=PANEL, foreground=ACCENT,
                    font=("Segoe UI", 9, "bold"))
        s.configure("TEntry",        fieldbackground=ENTRY_BG, foreground=FG,
                    insertcolor=FG, bordercolor=BORDER, relief="flat")
        s.configure("TButton",       background=BTN, foreground=FG,
                    borderwidth=0, relief="flat", padding=(10, 5))
        s.map("TButton", background=[("active", BTN_HOV), ("pressed", ACCENT)])
        s.configure("Small.TButton", background=ENTRY_BG, foreground=FG_DIM,
                    borderwidth=0, relief="flat", padding=(6, 3), font=("Segoe UI", 9))
        s.map("Small.TButton", background=[("active", BTN), ("pressed", ACCENT)])
        s.configure("TScrollbar",    background=PANEL, troughcolor=BG,
                    bordercolor=BG, arrowcolor=FG_DIM)
        s.configure("Treeview",      background=ENTRY_BG, foreground=FG,
                    fieldbackground=ENTRY_BG, borderwidth=0, rowheight=26)
        s.configure("Treeview.Heading", background=PANEL, foreground=ACCENT,
                    relief="flat", font=("Segoe UI", 9, "bold"))
        s.map("Treeview", background=[("selected", BTN)])
        s.configure("TSeparator",    background=BORDER)

    # ── Build UI ───────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_topbar()

        pw = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=BORDER,
                            sashwidth=4, sashpad=0, relief="flat")
        pw.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._left  = self._build_left(pw)
        self._right = self._build_right(pw)
        pw.add(self._left,  minsize=320, width=380)
        pw.add(self._right, minsize=400)

        self._build_statusbar()

    # ── Top bar ────────────────────────────────────────────────────────────
    def _build_topbar(self):
        bar = tk.Frame(self, bg=PANEL, height=42)
        bar.pack(fill=tk.X, side=tk.TOP)
        bar.pack_propagate(False)

        # Status dot + label
        self._dot = tk.Label(bar, text="●", fg=WARNING, bg=PANEL,
                             font=("Segoe UI", 14))
        self._dot.pack(side=tk.LEFT, padx=(14, 4), pady=8)
        self._conn_lbl = tk.Label(bar, text="Connecting…", fg=FG_DIM, bg=PANEL,
                                  font=("Segoe UI", 10))
        self._conn_lbl.pack(side=tk.LEFT, padx=(0, 20))

        # Model label
        tk.Label(bar, text="Model:", fg=FG_DIM, bg=PANEL,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._model_lbl = tk.Label(bar, text="—", fg=ACCENT, bg=PANEL,
                                   font=("Segoe UI", 10, "bold"))
        self._model_lbl.pack(side=tk.LEFT, padx=(4, 0))

        # API URL entry
        tk.Label(bar, text="API:", fg=FG_DIM, bg=PANEL,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(20, 4))
        self._api_var = tk.StringVar(value=API_BASE)
        api_entry = tk.Entry(bar, textvariable=self._api_var, bg=ENTRY_BG,
                             fg=FG, insertbackground=FG, relief="flat",
                             font=("Segoe UI", 10), width=28)
        api_entry.pack(side=tk.LEFT)
        api_entry.bind("<Return>", lambda _: self._refresh_api())

        # Refresh button
        ttk.Button(bar, text="⟳ Refresh", style="Small.TButton",
                   command=self._refresh_api).pack(side=tk.RIGHT, padx=12)

    # ── Left panel ─────────────────────────────────────────────────────────
    def _build_left(self, parent):
        outer = ttk.Frame(parent, style="Panel.TFrame")

        canvas = tk.Canvas(outer, bg=PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        inner = ttk.Frame(canvas, style="Panel.TFrame")
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _resize(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=e.width)
        inner.bind("<Configure>", _resize)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))

        canvas.bind_all("<MouseWheel>", lambda e:
            canvas.yview_scroll(int(-1 * e.delta / 120), "units"))

        self._build_scan_section(inner)
        self._build_snapshots_section(inner)
        self._build_generate_section(inner)
        self._build_ask_section(inner)
        self._build_raw_section(inner)

        return outer

    def _section(self, parent, title):
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(
            fill=tk.X, padx=12, pady=(14, 0))
        ttk.Label(parent, text=f"  {title}", style="Head.TLabel",
                  background=PANEL).pack(fill=tk.X, padx=12, pady=(6, 8))
        f = ttk.Frame(parent, style="Panel.TFrame")
        f.pack(fill=tk.X, padx=16, pady=(0, 4))
        return f

    # ── Scan section ───────────────────────────────────────────────────────
    def _build_scan_section(self, parent):
        ttk.Label(parent, text="  SCAN CODEBASE", style="Head.TLabel",
                  background=PANEL, font=("Segoe UI", 9, "bold")
                  ).pack(fill=tk.X, padx=12, pady=(14, 6))

        f = ttk.Frame(parent, style="Panel.TFrame")
        f.pack(fill=tk.X, padx=16, pady=(0, 4))

        # Path row
        path_row = ttk.Frame(f, style="Panel.TFrame")
        path_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(path_row, text="Path:", style="Panel.TLabel",
                  width=6).pack(side=tk.LEFT)
        self._scan_path = tk.StringVar()
        tk.Entry(path_row, textvariable=self._scan_path, bg=ENTRY_BG,
                 fg=FG, insertbackground=FG, relief="flat",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
        ttk.Button(path_row, text="📁", style="Small.TButton",
                   command=self._browse_dir).pack(side=tk.LEFT)

        # Label row
        lbl_row = ttk.Frame(f, style="Panel.TFrame")
        lbl_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(lbl_row, text="Label:", style="Panel.TLabel",
                  width=6).pack(side=tk.LEFT)
        self._scan_label = tk.StringVar()
        tk.Entry(lbl_row, textvariable=self._scan_label, bg=ENTRY_BG,
                 fg=FG, insertbackground=FG, relief="flat",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        ttk.Button(f, text="⬡  Scan Codebase",
                   command=self._do_scan).pack(fill=tk.X)

    def _browse_dir(self):
        d = filedialog.askdirectory(title="Select project root")
        if d:
            self._scan_path.set(d)
            if not self._scan_label.get():
                self._scan_label.set(Path(d).name)

    # ── Snapshots section ──────────────────────────────────────────────────
    def _build_snapshots_section(self, parent):
        f = self._section(parent, "SNAPSHOTS")

        tree_frame = ttk.Frame(f, style="Panel.TFrame")
        tree_frame.pack(fill=tk.X)

        self._snap_tree = ttk.Treeview(tree_frame, columns=("files", "chars"),
                                       show="headings", height=5,
                                       selectmode="browse")
        self._snap_tree.heading("files", text="Label / Files")
        self._snap_tree.heading("chars", text="Size")
        self._snap_tree.column("files", width=180)
        self._snap_tree.column("chars", width=80, anchor="e")
        self._snap_tree.pack(fill=tk.X)

        ttk.Button(f, text="↺  Refresh Snapshots", style="Small.TButton",
                   command=self._refresh_snapshots).pack(fill=tk.X, pady=(6, 0))

    # ── Generate section ───────────────────────────────────────────────────
    def _build_generate_section(self, parent):
        f = self._section(parent, "GENERATE DOCS")

        btn_row = ttk.Frame(f, style="Panel.TFrame")
        btn_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Button(btn_row, text="📄 README",
                   command=self._do_readme).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ttk.Button(btn_row, text="📋 API Docs",
                   command=self._do_api_docs).pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Button(f, text="🔍 Document a File…",
                   command=self._do_file_doc).pack(fill=tk.X)

    # ── Ask section ────────────────────────────────────────────────────────
    def _build_ask_section(self, parent):
        f = self._section(parent, "ASK DEEPSEEK")

        self._ask_text = tk.Text(f, height=3, bg=ENTRY_BG, fg=FG,
                                 insertbackground=FG, relief="flat",
                                 font=("Segoe UI", 10), wrap=tk.WORD)
        self._ask_text.pack(fill=tk.X, pady=(0, 6))
        self._ask_text.insert("1.0", "What does this project do?")
        self._ask_text.bind("<FocusIn>", lambda e: (
            self._ask_text.get("1.0", tk.END).strip() == "What does this project do?"
            and self._ask_text.delete("1.0", tk.END)))

        ttk.Button(f, text="✦  Ask",
                   command=self._do_ask).pack(fill=tk.X)

    # ── Raw generate section ───────────────────────────────────────────────
    def _build_raw_section(self, parent):
        f = self._section(parent, "RAW PROMPT")

        ttk.Label(f, text="Prompt:", style="Panel.TLabel").pack(anchor="w")
        self._raw_prompt = tk.Text(f, height=4, bg=ENTRY_BG, fg=FG,
                                   insertbackground=FG, relief="flat",
                                   font=("Segoe UI", 10), wrap=tk.WORD)
        self._raw_prompt.pack(fill=tk.X, pady=(2, 6))

        ttk.Label(f, text="System (optional):", style="Panel.TLabel").pack(anchor="w")
        self._raw_system = tk.Entry(f, bg=ENTRY_BG, fg=FG,
                                    insertbackground=FG, relief="flat",
                                    font=("Segoe UI", 10))
        self._raw_system.pack(fill=tk.X, pady=(2, 6))

        ttk.Button(f, text="▶  Generate",
                   command=self._do_raw).pack(fill=tk.X, pady=(0, 12))

    # ── Right panel (output) ───────────────────────────────────────────────
    def _build_right(self, parent):
        outer = ttk.Frame(parent)
        outer.configure(style="TFrame")

        # Output header
        hdr = tk.Frame(outer, bg=PANEL, height=36)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        self._out_title = tk.Label(hdr, text="Output", bg=PANEL, fg=ACCENT,
                                   font=("Segoe UI", 10, "bold"))
        self._out_title.pack(side=tk.LEFT, padx=14, pady=8)

        # Progress bar (hidden until busy)
        self._progress = ttk.Progressbar(hdr, mode="indeterminate", length=120)
        self._progress.pack(side=tk.RIGHT, padx=12, pady=10)
        self._progress.pack_forget()

        # Output text
        out_frame = tk.Frame(outer, bg=OUTPUT_BG)
        out_frame.pack(fill=tk.BOTH, expand=True)

        self._output = scrolledtext.ScrolledText(
            out_frame, bg=OUTPUT_BG, fg=FG, insertbackground=FG,
            relief="flat", font=("Cascadia Code", 10) if self._has_font("Cascadia Code")
            else ("Consolas", 10),
            wrap=tk.WORD, padx=16, pady=14,
            state=tk.DISABLED,
        )
        self._output.pack(fill=tk.BOTH, expand=True)
        self._output.configure(
            selectbackground=BTN, selectforeground=FG,
        )
        # Tag for timestamp headers
        self._output.tag_configure("header", foreground=ACCENT,
                                   font=("Segoe UI", 9, "bold"))

        # Bottom action bar
        action = tk.Frame(outer, bg=PANEL, height=44)
        action.pack(fill=tk.X)
        action.pack_propagate(False)
        ttk.Button(action, text="⎘ Copy", style="Small.TButton",
                   command=self._copy_output).pack(side=tk.LEFT, padx=10, pady=8)
        ttk.Button(action, text="💾 Save As…", style="Small.TButton",
                   command=self._save_output).pack(side=tk.LEFT, pady=8)
        ttk.Button(action, text="✕ Clear", style="Small.TButton",
                   command=self._clear_output).pack(side=tk.RIGHT, padx=10, pady=8)

        return outer

    def _has_font(self, name):
        try:
            import tkinter.font as tkfont
            return name in tkfont.families()
        except Exception:
            return False

    # ── Status bar ─────────────────────────────────────────────────────────
    def _build_statusbar(self):
        bar = tk.Frame(self, bg=PANEL, height=26)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self._status_var = tk.StringVar(value="Ready")
        tk.Label(bar, textvariable=self._status_var, bg=PANEL, fg=FG_DIM,
                 font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT, padx=10)

    # ── API helpers ────────────────────────────────────────────────────────
    def _refresh_api(self):
        self.api = APIClient(self._api_var.get())
        self._set_status("Checking API…")
        self._run_bg(self._check_api_bg, self._on_api_checked)

    def _check_api(self):
        self._run_bg(self._check_api_bg, self._on_api_checked)

    def _check_api_bg(self):
        return self.api.health()

    def _on_api_checked(self, result, error):
        if error:
            self._dot.configure(fg=DANGER)
            self._conn_lbl.configure(text="Disconnected", fg=DANGER)
            self._model_lbl.configure(text="—")
            self._set_status(f"API unreachable: {error}")
        else:
            self._dot.configure(fg=SUCCESS)
            self._conn_lbl.configure(text="Connected", fg=SUCCESS)
            models = result.get("ollama_models", [])
            self._model_lbl.configure(text=models[0] if models else "no model")
            self._set_status("API connected")
            self._refresh_snapshots()

    # ── Snapshots ──────────────────────────────────────────────────────────
    def _refresh_snapshots(self):
        self._run_bg(lambda: self.api.snapshots(), self._on_snapshots)

    def _on_snapshots(self, result, error):
        self._snap_tree.delete(*self._snap_tree.get_children())
        if error or not result:
            return
        for label, info in result.items():
            files = info.get("files", "?")
            chars = info.get("total_chars", 0)
            size = f"{chars//1000}k" if chars >= 1000 else f"{chars}"
            self._snap_tree.insert("", tk.END, iid=label,
                                   values=(f"  {label}  ({files} files)", size))

    def _selected_snapshot(self):
        sel = self._snap_tree.selection()
        if not sel:
            messagebox.showwarning("No snapshot", "Please scan a codebase first, or select one from the list.")
            return None
        return sel[0]

    # ── Actions ────────────────────────────────────────────────────────────
    def _do_scan(self):
        win_path = self._scan_path.get().strip()
        label    = self._scan_label.get().strip()
        if not win_path:
            messagebox.showwarning("No path", "Choose a directory first.")
            return
        if not label:
            label = Path(win_path).name
            self._scan_label.set(label)

        linux_path = win_to_linux(win_path)
        self._set_status(f"Scanning {linux_path}…")
        self._set_busy(True)
        self._out_title.configure(text=f"Scan — {label}")

        def bg():
            return self.api.scan(linux_path, label)

        def done(result, error):
            self._set_busy(False)
            if error:
                self._set_status(f"Scan failed: {error}")
                self._write_output(f"[ERROR] {error}", clear=True)
            else:
                n = result.get("files", 0)
                self._set_status(f"Scanned {n} files — label '{label}'")
                lines = [f"✓  Scanned '{label}'",
                         f"   Files : {n}",
                         f"   Chars : {result.get('total_chars',0):,}",
                         "", "Files found:"]
                lines += [f"   • {f}" for f in result.get("file_list", [])]
                self._write_output("\n".join(lines), clear=True)
                self._refresh_snapshots()

        self._run_bg(bg, done)

    def _do_readme(self):
        label = self._selected_snapshot()
        if not label:
            return
        self._set_status(f"Generating README for '{label}'…")
        self._set_busy(True)
        self._out_title.configure(text=f"README — {label}")

        def done(result, error):
            self._set_busy(False)
            if error:
                self._set_status(f"Failed: {error}")
                self._write_output(f"[ERROR] {error}", clear=True)
            else:
                self._set_status("README generated ✓")
                self._write_output(result, clear=True)

        self._run_bg(lambda: self.api.readme(label), done)

    def _do_api_docs(self):
        label = self._selected_snapshot()
        if not label:
            return
        self._set_status(f"Generating API docs for '{label}'…")
        self._set_busy(True)
        self._out_title.configure(text=f"API Docs — {label}")

        def done(result, error):
            self._set_busy(False)
            if error:
                self._set_status(f"Failed: {error}")
                self._write_output(f"[ERROR] {error}", clear=True)
            else:
                self._set_status("API docs generated ✓")
                self._write_output(result, clear=True)

        self._run_bg(lambda: self.api.api_docs(label), done)

    def _do_file_doc(self):
        path = filedialog.askopenfilename(
            title="Select a source file",
            filetypes=[("Source files",
                        "*.py *.js *.ts *.go *.java *.cs *.cpp *.c *.rs *.dart *.kt *.rb"),
                       ("All files", "*.*")])
        if not path:
            return

        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
        except Exception as e:
            messagebox.showerror("Read error", str(e))
            return

        lang = detect_language(path)
        name = Path(path).name
        self._set_status(f"Documenting {name}…")
        self._set_busy(True)
        self._out_title.configure(text=f"File Docs — {name}")

        def done(result, error):
            self._set_busy(False)
            if error:
                self._set_status(f"Failed: {error}")
                self._write_output(f"[ERROR] {error}", clear=True)
            else:
                self._set_status(f"Documentation for {name} done ✓")
                self._write_output(result, clear=True)

        self._run_bg(lambda: self.api.doc_file(name, content, lang), done)

    def _do_ask(self):
        label = self._selected_snapshot()
        if not label:
            return
        question = self._ask_text.get("1.0", tk.END).strip()
        if not question:
            return
        self._set_status(f"Asking DeepSeek about '{label}'…")
        self._set_busy(True)
        self._out_title.configure(text=f"Q&A — {label}")

        def done(result, error):
            self._set_busy(False)
            if error:
                self._set_status(f"Failed: {error}")
                self._write_output(f"[ERROR] {error}", clear=True)
            else:
                self._set_status("Answer received ✓")
                self._write_output(f"Q: {question}\n\n{result}", clear=True)

        self._run_bg(lambda: self.api.ask(label, question), done)

    def _do_raw(self):
        prompt = self._raw_prompt.get("1.0", tk.END).strip()
        if not prompt:
            return
        system = self._raw_system.get().strip()
        self._set_status("Generating…")
        self._set_busy(True)
        self._out_title.configure(text="Raw Output")

        def done(result, error):
            self._set_busy(False)
            if error:
                self._set_status(f"Failed: {error}")
                self._write_output(f"[ERROR] {error}", clear=True)
            else:
                self._set_status("Done ✓")
                self._write_output(result, clear=True)

        self._run_bg(lambda: self.api.raw_generate(prompt, system), done)

    # ── Output helpers ─────────────────────────────────────────────────────
    def _write_output(self, text: str, clear=False):
        self._output.configure(state=tk.NORMAL)
        if clear:
            self._output.delete("1.0", tk.END)
        ts = datetime.now().strftime("%H:%M:%S")
        self._output.insert(tk.END, f"─── {ts} ─────────────────────────────\n", "header")
        self._output.insert(tk.END, text.strip() + "\n\n")
        self._output.configure(state=tk.DISABLED)
        self._output.see(tk.END)

    def _clear_output(self):
        self._output.configure(state=tk.NORMAL)
        self._output.delete("1.0", tk.END)
        self._output.configure(state=tk.DISABLED)
        self._out_title.configure(text="Output")

    def _copy_output(self):
        text = self._output.get("1.0", tk.END).strip()
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Copied to clipboard ✓")

    def _save_output(self):
        text = self._output.get("1.0", tk.END).strip()
        if not text:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")])
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            self._set_status(f"Saved to {Path(path).name} ✓")

    # ── Threading ──────────────────────────────────────────────────────────
    def _run_bg(self, fn, callback):
        def worker():
            try:
                result = fn()
                error  = None
            except Exception as e:
                result = None
                error  = str(e)
            self.after(0, callback, result, error)

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy(self, busy: bool):
        self._busy = busy
        if busy:
            self._progress.pack(side=tk.RIGHT, padx=12, pady=10)
            self._progress.start(12)
        else:
            self._progress.stop()
            self._progress.pack_forget()

    def _set_status(self, msg: str):
        self._status_var.set(msg)


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    DocBotUI().mainloop()
