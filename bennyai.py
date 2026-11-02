# coding: utf-8
"""
BennyAI  —  Local AI Coding Assistant
Powered by DeepSeek Coder via Ollama + Kubernetes
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import json, os, re, subprocess, queue, time, zipfile, shutil, textwrap
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    import requests
except ImportError:
    import subprocess as _sp, sys
    _sp.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

for _pkg in ("pdfplumber", "beautifulsoup4"):
    try:
        __import__("pdfplumber" if _pkg == "pdfplumber" else "bs4")
    except ImportError:
        import subprocess as _sp2, sys
        _sp2.check_call([sys.executable, "-m", "pip", "install", _pkg],
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                        if "subprocess" in dir() else 0)

# ── Config ────────────────────────────────────────────────────────────────────
OLLAMA_BASE   = os.getenv("OLLAMA_BASE",  "http://localhost:11434")
DOCBOT_BASE   = os.getenv("DOCBOT_API",   "http://localhost:8000")
REMOTE_PORT   = 7788
DEFAULT_MODEL = "deepseek-coder:6.7b"
APP_NAME      = "BennyAI"
VERSION       = "1.0"
CHATS_DIR     = Path.home() / "BennyAI" / "chats"

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = "#0f0f17"
PANEL     = "#16161e"
SIDEBAR   = "#1a1a2e"
ENTRY_BG  = "#1e1e30"
ACCENT    = "#7aa2f7"
ACCENT2   = "#bb9af7"
SUCCESS   = "#9ece6a"
WARNING   = "#e0af68"
DANGER    = "#f7768e"
FG        = "#c0caf5"
FG_DIM    = "#565f89"
FG_BRIGHT = "#e0e0ff"
BTN       = "#3d59a1"
BTN_HOV   = "#4a6bc7"
BORDER    = "#2a2a4a"
USER_BG   = "#1c2a4a"
BOT_BG    = "#1a1a2e"
TOOL_BG   = "#1a1a00"
PERM_BG   = "#2a1200"
TERM_BG   = "#0a0a10"

# ── System prompt (built dynamically so online tools appear only when enabled) ─
_PROMPT_BASE = """You are BennyAI, a local AI coding assistant running on the user's machine.
You have access to the user's local file system. When the user mentions a file path or project
folder, the system AUTOMATICALLY reads those files and injects their content into the conversation
for you — look for [Auto-fetched file: ...] or [Auto-fetched directory listing: ...] blocks below
the user's message. Use that content directly to answer. Never say you cannot read files.

For actions that need execution (run commands, write files), output a JSON block like:
```json
{"tool":"run_command","args":{"cmd":"python manage.py runserver"}}
```
The system will ask permission and execute it.

Available action tools (requires user permission):
  run_command  {"cmd":"command","cwd":"optional_path"}   — run a shell command
  write_file   {"path":"D:\\\\path","content":"text"}    — create or overwrite a file
  pip_install  {"package":"name"}                        — install a Python package"""

_PROMPT_ONLINE_EXTRA = """
ONLINE TOOLS (internet enabled — use to learn, research and fetch docs):
  web_search        {"query":"search terms"}                          — DuckDuckGo search [SAFE]
  fetch_url         {"url":"https://..."}                             — fetch webpage as text [SAFE]
  fetch_github      {"owner":"user","repo":"repo","path":"file.py"}   — read raw GitHub file [SAFE]
  download_resource {"url":"https://...","title":"name","category":"docs|books|tutorials|general"}
                                                                      — download & save to local library [SAFE]

Guidelines for online tools:
- Use web_search to find documentation, error solutions, or package info.
- Use fetch_url to read official docs pages or articles (text only, no JS).
- Use fetch_github to read source files or examples from public repos.
- Use download_resource to permanently save docs/books to BennyAI's library for offline use.
- Never POST or send data to external services. All fetches are read-only.
"""

_PROMPT_LIBRARY = """
LIBRARY TOOLS (always available — local saved resources):
  list_library      {}                                              — list saved books/docs [SAFE]
  search_library    {"query":"text"}                               — full-text search library [SAFE]
  read_library_item {"item_id":0}                                  — read a saved resource [SAFE]
  delete_library_item {"item_id":0}                                — remove a library item [NEEDS PERMISSION]

The library is at ~/BennyAI/library/. When a user asks you to teach them something, check the library first
with search_library, then offer to download relevant docs or books if not found.
Proactively build the library — if the user wants to learn Python, download the Python docs.
"""

_PROMPT_OFFLINE_NOTE = """
MODE: OFFLINE — no internet tools available. Use local tools only.
"""

_PROMPT_FOOTER = """
Rules:
- You have REAL file system tools. Use them immediately when needed — never refuse.
- Read files proactively before answering — never ask the user to paste code.
- SAFE tools: use immediately without asking. PERMISSION tools: user will approve/deny in a dialog.
- After a tool result, continue your response naturally — summarise what you found and proceed.
- Code goes in fenced blocks with language tags.
- You are BennyAI, not DeepSeek or Claude. You live on this machine and can act on it.
- You are a full teacher with no topic limits. Teach programming, math, science, or anything else.
  When teaching: use examples, exercises, and build up from basics. Check the library first.
- CRITICAL: Respond conversationally. NEVER generate text like "### Instruction:", "### Response:",
  "Human:", "Assistant:", or any other template/training-data format. Just reply naturally.
  Do not roleplay a multi-turn conversation by yourself.
"""

def build_system_prompt(online: bool, workspace: str = "") -> str:
    p = _PROMPT_BASE
    p += _PROMPT_ONLINE_EXTRA if online else _PROMPT_OFFLINE_NOTE
    p += _PROMPT_LIBRARY
    p += _PROMPT_FOOTER
    if workspace:
        p += f"\nWorkspace: {workspace}"
    return p

# ── Tool executor ─────────────────────────────────────────────────────────────
class ToolExecutor:
    PERM_TOOLS = {"run_command", "write_file", "delete_file", "pip_install",
                  "delete_library_item"}

    def __init__(self, docbot: str = DOCBOT_BASE):
        self.docbot  = docbot
        self.online  = False   # flipped by the UI toggle

    def needs_permission(self, tool: str) -> bool:
        return tool in self.PERM_TOOLS

    def execute(self, tool: str, args: dict) -> str:
        fn = getattr(self, f"_t_{tool}", None)
        if fn is None:
            return f"[ERROR] Unknown tool: {tool}"
        try:
            return fn(**args)
        except Exception as e:
            return f"[ERROR] {tool}: {e}"

    def _t_read_file(self, path: str) -> str:
        p = Path(path)
        if not p.exists():
            return f"[File not found: {path}]"
        content = p.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        if len(lines) > 300:
            content = "\n".join(lines[:300]) + f"\n... ({len(lines)-300} more lines truncated)"
        return f"```{p.suffix.lstrip('.')}\n{content}\n```"

    def _t_list_dir(self, path: str) -> str:
        p = Path(path)
        if not p.exists():
            return f"[Directory not found: {path}]"
        out = []
        for item in sorted(p.iterdir()):
            tag = "[DIR] " if item.is_dir() else "      "
            out.append(f"{tag}{item.name}")
        return "\n".join(out) if out else "(empty)"

    def _t_search_files(self, path: str, pattern: str) -> str:
        hits, skips = [], {".git", "__pycache__", "node_modules", ".venv", "venv"}
        for f in Path(path).rglob("*"):
            if any(s in f.parts for s in skips) or not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                    if pattern.lower() in line.lower():
                        hits.append(f"{f.relative_to(path)}:{i}  {line.strip()}")
                        if len(hits) >= 40:
                            break
            except Exception:
                pass
            if len(hits) >= 40:
                break
        return "\n".join(hits) if hits else f"No matches for '{pattern}'"

    def _t_run_command(self, cmd: str, cwd: str = None) -> str:
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                               cwd=cwd, timeout=30, encoding="utf-8", errors="replace")
            return (r.stdout + r.stderr).strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return "[Timed out after 30s]"
        except Exception as e:
            return f"[ERROR] {e}"

    def _t_write_file(self, path: str, content: str) -> str:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"

    def _t_delete_file(self, path: str) -> str:
        Path(path).unlink()
        return f"Deleted {path}"

    def _t_pip_install(self, package: str) -> str:
        r = subprocess.run(
            [__import__("sys").executable, "-m", "pip", "install", package],
            capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW)
        out = (r.stdout + r.stderr).strip()
        return out if r.returncode == 0 else f"[ERROR] pip install failed:\n{out}"

    # ── Online tools (only reachable when self.online is True) ───────────
    def _t_web_search(self, query: str) -> str:
        if not self.online:
            return "[OFFLINE] Enable online mode to use web_search."
        try:
            # DuckDuckGo HTML endpoint — no API key needed
            r = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers={"User-Agent": "Mozilla/5.0 (BennyAI)"},
                timeout=10)
            r.raise_for_status()
            # Extract result snippets with simple regex
            titles   = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', r.text, re.S)
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</span>', r.text, re.S)
            urls     = re.findall(r'uddg=([^&"]+)', r.text)
            titles   = [re.sub(r"<.*?>", "", t).strip() for t in titles[:6]]
            snippets = [re.sub(r"<.*?>", "", s).strip() for s in snippets[:6]]
            urls     = [requests.utils.unquote(u) for u in urls[:6]]
            if not titles:
                return f"No results found for: {query}"
            lines = [f"Search: {query}\n"]
            for i, (t, s, u) in enumerate(zip(titles, snippets, urls), 1):
                lines.append(f"{i}. {t}\n   {s}\n   {u}\n")
            return "\n".join(lines)
        except Exception as e:
            return f"[ERROR] web_search: {e}"

    def _t_fetch_url(self, url: str) -> str:
        if not self.online:
            return "[OFFLINE] Enable online mode to use fetch_url."
        _ALLOWED = ("http://", "https://")
        if not any(url.startswith(p) for p in _ALLOWED):
            return "[ERROR] Only http/https URLs are allowed."
        try:
            r = requests.get(url,
                             headers={"User-Agent": "Mozilla/5.0 (BennyAI)"},
                             timeout=15)
            r.raise_for_status()
            # Strip HTML to readable text
            text = r.text
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.S|re.I)
            text = re.sub(r"<style[^>]*>.*?</style>",  "", text, flags=re.S|re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"[ \t]+", " ", text)
            text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
            # Cap at 12 KB
            if len(text) > 12000:
                text = text[:12000] + "\n… (truncated to 12 KB)"
            return f"URL: {url}\n\n{text}"
        except Exception as e:
            return f"[ERROR] fetch_url: {e}"

    def _t_fetch_github(self, owner: str, repo: str, path: str,
                        branch: str = "main") -> str:
        if not self.online:
            return "[OFFLINE] Enable online mode to use fetch_github."
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        try:
            r = requests.get(raw_url,
                             headers={"User-Agent": "Mozilla/5.0 (BennyAI)"},
                             timeout=15)
            if r.status_code == 404:
                # Try 'master' branch
                raw_url = raw_url.replace(f"/{branch}/", "/master/")
                r = requests.get(raw_url, timeout=15)
            r.raise_for_status()
            content = r.text
            if len(content) > 12000:
                content = content[:12000] + "\n… (truncated to 12 KB)"
            return f"GitHub: {owner}/{repo}/{path}\n\n```\n{content}\n```"
        except Exception as e:
            return f"[ERROR] fetch_github: {e}"

    # ── Library tools ─────────────────────────────────────────────────────
    LIBRARY_DIR = Path.home() / "BennyAI" / "library"

    def _lib_index_path(self):
        return self.LIBRARY_DIR / "index.json"

    def _lib_load(self) -> list:
        self.LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        p = self._lib_index_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _lib_save(self, index: list):
        self._lib_index_path().write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    def _lib_text_from_pdf(self, raw_bytes: bytes) -> str:
        import io
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                pages = []
                for page in pdf.pages[:80]:   # cap at 80 pages
                    t = page.extract_text()
                    if t:
                        pages.append(t)
            return "\n\n".join(pages)
        except Exception as e:
            return f"[PDF extract error: {e}]"

    def _lib_text_from_html(self, html: str, url: str = "") -> str:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer"]):
                tag.decompose()
            text = soup.get_text(separator="\n")
        except ImportError:
            text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"[ \t]+", " ", text)
        text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
        return text

    def _t_download_resource(self, url: str,
                              title: str = "",
                              category: str = "general") -> str:
        if not self.online:
            return "[OFFLINE] Enable online mode to download resources."
        _ALLOWED = ("http://", "https://")
        if not any(url.startswith(p) for p in _ALLOWED):
            return "[ERROR] Only http/https URLs are allowed."
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (BennyAI)"},
                             timeout=30, stream=True)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "")
            raw = b""
            size = 0
            for chunk in r.iter_content(8192):
                raw += chunk
                size += len(chunk)
                if size > 20 * 1024 * 1024:   # 20 MB cap
                    break

            if "pdf" in content_type or url.lower().endswith(".pdf"):
                text = self._lib_text_from_pdf(raw)
                fmt  = "pdf"
            else:
                text = self._lib_text_from_html(raw.decode("utf-8", errors="replace"), url)
                fmt  = "html"

            if not text.strip():
                return "[ERROR] Could not extract text from resource."

            index = self._lib_load()
            item_id = max((i["id"] for i in index), default=-1) + 1
            safe_name = re.sub(r"[^a-z0-9_-]", "_", (title or url[:40]).lower())[:50]
            fname = f"{item_id:04d}_{safe_name}.txt"
            fpath = self.LIBRARY_DIR / fname
            fpath.write_text(text, encoding="utf-8")

            meta = {
                "id":       item_id,
                "title":    title or url,
                "url":      url,
                "category": category,
                "format":   fmt,
                "file":     fname,
                "chars":    len(text),
                "date":     datetime.now().isoformat(timespec="seconds"),
            }
            index.append(meta)
            self._lib_save(index)
            return (f"Saved to library: [{item_id}] \"{meta['title']}\" "
                    f"({len(text):,} chars, {fmt.upper()}, category: {category})")
        except Exception as e:
            return f"[ERROR] download_resource: {e}"

    def _t_list_library(self) -> str:
        index = self._lib_load()
        if not index:
            return "Library is empty. Use download_resource to add books or docs."
        lines = [f"Library ({len(index)} items):\n"]
        for item in index:
            lines.append(
                f"  [{item['id']:>3}] {item['title'][:60]:<60} "
                f"{item['category']:<12} {item['chars']//1000:>4}K  {item['date'][:10]}")
        return "\n".join(lines)

    def _t_search_library(self, query: str) -> str:
        index = self._lib_load()
        if not index:
            return "Library is empty."
        q = query.lower()
        hits = []
        for item in index:
            score = 0
            if q in item["title"].lower():
                score += 10
            if q in item.get("category", "").lower():
                score += 5
            if score == 0:
                fpath = self.LIBRARY_DIR / item["file"]
                try:
                    text = fpath.read_text(encoding="utf-8", errors="replace")
                    count = text.lower().count(q)
                    if count:
                        score = min(count, 5)
                except Exception:
                    pass
            if score:
                hits.append((score, item))
        hits.sort(key=lambda x: -x[0])
        if not hits:
            return f"No library items match '{query}'."
        lines = [f"Search '{query}' — {len(hits)} result(s):\n"]
        for _, item in hits[:10]:
            lines.append(
                f"  [{item['id']:>3}] {item['title'][:60]:<60} "
                f"{item['category']:<12} {item['chars']//1000:>4}K")
        return "\n".join(lines)

    def _t_read_library_item(self, item_id: int) -> str:
        index = self._lib_load()
        item = next((i for i in index if i["id"] == int(item_id)), None)
        if not item:
            return f"[ERROR] No library item with id={item_id}"
        fpath = self.LIBRARY_DIR / item["file"]
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"[ERROR] reading library item: {e}"
        # Return up to 16 KB for context
        if len(text) > 16000:
            text = text[:16000] + f"\n… ({len(text)-16000:,} chars truncated — ask me to continue)"
        return (f"Library item [{item_id}]: {item['title']}\n"
                f"Source: {item['url']}\nCategory: {item['category']}\n\n{text}")

    def _t_delete_library_item(self, item_id: int) -> str:
        index = self._lib_load()
        item = next((i for i in index if i["id"] == int(item_id)), None)
        if not item:
            return f"[ERROR] No library item with id={item_id}"
        try:
            (self.LIBRARY_DIR / item["file"]).unlink(missing_ok=True)
        except Exception:
            pass
        index = [i for i in index if i["id"] != int(item_id)]
        self._lib_save(index)
        return f"Deleted library item [{item_id}]: {item['title']}"

    def _t_scan_docs(self, path: str, label: str) -> str:
        p = Path(path)
        parts = p.parts
        if parts and ":" in parts[0]:
            drive = parts[0][0].lower()
            rest = "/".join(parts[1:])
            linux = f"/mnt/{drive}/{rest}"
        else:
            linux = path.replace("\\", "/")
        r = requests.post(f"{self.docbot}/scan", json={"path": linux, "label": label}, timeout=30)
        r.raise_for_status()
        d = r.json()
        return f"Indexed '{label}': {d['files']} files, {d['total_chars']:,} chars"

    def _t_gen_readme(self, label: str) -> str:
        r = requests.post(f"{self.docbot}/generate/readme/{label}", timeout=600)
        r.raise_for_status()
        return r.json()["readme"]

    def _t_gen_api_docs(self, label: str) -> str:
        r = requests.post(f"{self.docbot}/generate/api-docs/{label}", timeout=600)
        r.raise_for_status()
        return r.json()["api_docs"]


# ── Model catalogue ───────────────────────────────────────────────────────────
# Each entry: (ollama_name, vram_gb, speed 1-5, quality 1-5, specialty, description)
MODEL_CATALOG = [
    ("tinyllama:1.1b",        0.6,  5, 1, "general", "Tiny & ultra-fast; limited quality"),
    ("deepseek-coder:1.3b",   0.8,  5, 3, "coding",  "DeepSeek Coder 1.3B — fast coding"),
    ("llama3.2:1b",           0.9,  5, 2, "general", "Meta Llama 3.2 1B — ultra-fast"),
    ("phi3:mini",             1.8,  4, 3, "general", "Microsoft Phi-3 Mini — fast & smart"),
    ("llama3.2:3b",           2.0,  4, 3, "general", "Meta Llama 3.2 3B — great balance"),
    ("deepseek-coder:6.7b",   3.8,  3, 4, "coding",  "DeepSeek Coder 6.7B ✦ current default"),
    ("codellama:7b",          4.0,  3, 4, "coding",  "Meta CodeLlama 7B — solid coder"),
    ("mistral:7b",            4.1,  3, 4, "general", "Mistral 7B — strong general purpose"),
    ("llama3:8b",             4.7,  3, 4, "general", "Meta Llama 3 8B — well-rounded"),
    ("codellama:13b",         7.4,  2, 5, "coding",  "Meta CodeLlama 13B — high quality"),
    ("deepseek-coder:33b",   19.0,  1, 5, "coding",  "DeepSeek Coder 33B — top-tier coding"),
    ("llama3:70b",           40.0,  1, 5, "general", "Meta Llama 3 70B — best quality"),
]

# Performance presets  {name: {option_key: value}}
PERF_PRESETS = {
    "Fast": {
        "temperature": 0.1, "num_predict": 1024,
        "num_ctx": 2048,    "num_gpu": 999,
    },
    "Balanced": {
        "temperature": 0.3, "num_predict": 2048,
        "num_ctx": 4096,    "num_gpu": 999,
    },
    "Quality": {
        "temperature": 0.7, "num_predict": 4096,
        "num_ctx": 8192,    "num_gpu": 999,
    },
    "CPU Only": {
        "temperature": 0.3, "num_predict": 1024,
        "num_ctx": 2048,    "num_gpu": 0,
    },
}

# ── Ollama chat client ────────────────────────────────────────────────────────
# Matches both explicit <TOOL>…</TOOL> tags AND ```json {…} ``` code blocks
# that contain a "tool" key — DeepSeek naturally outputs the latter format.
TOOL_RE     = re.compile(r"<TOOL>(.*?)</TOOL>", re.DOTALL)
TOOL_CODE_RE = re.compile(
    r"```(?:json)?\s*(\{[^`]*\"tool\"\s*:[^`]*\})\s*```", re.DOTALL)

def _extract_tool_json(raw: str) -> list[str]:
    """Return all tool-call JSON strings found in a model response."""
    hits = TOOL_RE.findall(raw)
    if not hits:
        hits = TOOL_CODE_RE.findall(raw)
    return hits

def _strip_tool_markup(raw: str) -> str:
    """Remove tool call markup from text before displaying to user."""
    out = TOOL_RE.sub("", raw)
    out = TOOL_CODE_RE.sub("", out)
    return out.strip()

class OllamaClient:
    def __init__(self, base=OLLAMA_BASE, model=DEFAULT_MODEL):
        self.base    = base.rstrip("/")
        self.model   = model
        self.options = dict(PERF_PRESETS["Balanced"])  # mutable, shared with UI
        self._active_resp = None  # stored so abort() can close it immediately

    def abort(self):
        """Close the active HTTP stream so Ollama sees a broken pipe and cancels."""
        resp = self._active_resp
        if resp is not None:
            try:
                resp.close()
            except Exception:
                pass
            self._active_resp = None

    def chat(self, messages: list, on_chunk=None, stop_check=None) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": on_chunk is not None,
            "options": self.options,
        }
        if on_chunk:
            # (connect_timeout, read_timeout) — 90 s without a new byte
            with requests.post(f"{self.base}/api/chat", json=payload,
                               stream=True, timeout=(15, 90)) as resp:
                self._active_resp = resp
                try:
                    resp.raise_for_status()
                    full = ""
                    for line in resp.iter_lines():
                        if stop_check and stop_check():
                            resp.close()  # signal Ollama to cancel immediately
                            break
                        if line:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            full += token
                            on_chunk(token)
                            if chunk.get("done"):
                                break
                    return full
                finally:
                    self._active_resp = None
        r = requests.post(f"{self.base}/api/chat", json=payload, timeout=600)
        r.raise_for_status()
        return r.json()["message"]["content"]

    def models(self) -> list:
        r = requests.get(f"{self.base}/api/tags", timeout=5)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]


# ── Agent loop ────────────────────────────────────────────────────────────────
class Agent:
    def __init__(self, ollama, executor,
                 on_token, on_response_done,
                 on_tool_start, on_tool_result,
                 on_permission, workspace=""):
        self.ollama    = ollama
        self.executor  = executor
        self.history   = []
        self.on_token         = on_token          # fn(str)   — streaming token
        self.on_response_done = on_response_done  # fn(str)   — full cleaned text
        self.on_tool_start    = on_tool_start     # fn(tool, args)
        self.on_tool_result   = on_tool_result    # fn(tool, result)
        self.on_permission    = on_permission     # fn(tool, args) -> (granted, always)
        self.workspace        = workspace
        self._always_allow    = set()
        self._stop            = False

    def stop(self):
        """Signal stop and immediately close the HTTP stream so Ollama cancels."""
        self._stop = True
        self.ollama.abort()

    def reset(self):
        self.history.clear()
        self._always_allow.clear()

    def inject(self, role: str, content: str):
        self.history.append({"role": role, "content": content})

    def _auto_context(self, user_message: str) -> str:
        """Detect file/dir paths in the message and inject their contents automatically."""
        path_re = re.compile(r'[A-Za-z]:\\(?:[^\s"\'<>\n]+)', re.IGNORECASE)
        found = path_re.findall(user_message)
        if not found:
            return user_message
        injected = []
        seen = set()
        for raw_path in found:
            raw_path = raw_path.rstrip('.,)"\'')
            if raw_path in seen:
                continue
            seen.add(raw_path)
            p = Path(raw_path)
            try:
                if p.is_dir():
                    result = self.executor._t_list_dir(str(p))
                    injected.append(f"[Auto-fetched directory listing: {raw_path}]\n{result}")
                    # Also read the most important files automatically
                    for fname in ("settings.py", "urls.py", "models.py",
                                  "views.py", "README.md", "manage.py",
                                  "requirements.txt", "apps.py"):
                        candidate = p / fname
                        if not candidate.exists():
                            # Try one level deep
                            for sub in p.iterdir():
                                c2 = sub / fname
                                if c2.exists():
                                    candidate = c2
                                    break
                        if candidate.exists():
                            content = self.executor._t_read_file(str(candidate))
                            injected.append(
                                f"[Auto-fetched file: {candidate}]\n{content}")
                elif p.is_file():
                    result = self.executor._t_read_file(str(p))
                    injected.append(f"[Auto-fetched file: {raw_path}]\n{result}")
            except Exception:
                pass
        if not injected:
            return user_message
        context_block = "\n\n".join(injected)
        return (f"{user_message}\n\n"
                f"[BennyAI has automatically read the relevant files. "
                f"Use the content below to answer — do not say you cannot read files.]\n\n"
                f"{context_block}")

    def send(self, user_message: str):
        """Run in a background thread."""
        self._stop = False
        # Store the clean user message in history; inject file contents only
        # for the model's first-turn context so follow-up turns stay lean.
        self.history.append({"role": "user", "content": user_message})
        enriched = self._auto_context(user_message)
        system   = build_system_prompt(self.executor.online, self.workspace)
        # Build messages with the enriched first turn replacing the plain one
        history_copy = self.history[:-1] + [{"role": "user", "content": enriched}]
        messages = [{"role": "system", "content": system}] + history_copy

        for _ in range(10):  # max tool-call iterations
            if self._stop:
                break
            buf = []

            def _tok(t):
                buf.append(t)
                visible = _strip_tool_markup("".join(buf))
                if visible:
                    self.on_token("".join(buf))

            raw = self.ollama.chat(messages, on_chunk=_tok, stop_check=lambda: self._stop)
            # Strip instruction-template hallucinations (deepseek-coder training format)
            raw = re.split(r'\n###\s*(Instruction|Response|Human|Assistant)\b', raw)[0].strip()
            clean = _strip_tool_markup(raw)
            self.on_response_done(clean)

            tool_calls = _extract_tool_json(raw)
            if not tool_calls:
                if not self._stop:
                    self.history.append({"role": "assistant", "content": raw})
                break

            if not self._stop:
                self.history.append({"role": "assistant", "content": raw})
            tool_result_parts = []

            for match in tool_calls:
                try:
                    call = json.loads(match.strip())
                except json.JSONDecodeError:
                    continue
                tool = call.get("tool", "")
                args = call.get("args", {})

                self.on_tool_start(tool, args)

                if self.executor.needs_permission(tool) and tool not in self._always_allow:
                    granted, always = self.on_permission(tool, args)
                    if not granted:
                        self.on_tool_result(tool, "[DENIED by user]")
                        tool_result_parts.append(f"<tool_result>{tool}: denied</tool_result>")
                        continue
                    if always:
                        self._always_allow.add(tool)

                result = self.executor.execute(tool, args)
                self.on_tool_result(tool, result)
                tool_result_parts.append(
                    f"<tool_result tool=\"{tool}\">\n{result}\n</tool_result>")

            if tool_result_parts:
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "user", "content": "\n\n".join(tool_result_parts)})
            else:
                break

        # If stopped before any assistant reply was stored, remove the orphaned
        # user message so the next send() doesn't see consecutive user turns.
        if self._stop and self.history and self.history[-1]["role"] == "user":
            self.history.pop()


# ── Remote control server (for Claude to inject messages) ─────────────────────
class RemoteServer:
    """Tiny HTTP server on port 7788. Claude can POST /inject to push messages."""
    def __init__(self, inject_fn):
        self._inject = inject_fn
        self._server = None

    def start(self):
        inject = self._inject

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a): pass  # silence

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length)
                try:
                    data = json.loads(body)
                except Exception:
                    self.send_response(400); self.end_headers()
                    return
                path = self.path.rstrip("/")
                if path == "/inject":
                    inject(data.get("message", ""), data.get("role", "user"))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                else:
                    self.send_response(404); self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"BennyAI remote control active"}')

        try:
            self._server = HTTPServer(("127.0.0.1", REMOTE_PORT), Handler)
            threading.Thread(target=self._server.serve_forever, daemon=True).start()
        except OSError:
            pass  # port in use — skip remote control

    def stop(self):
        if self._server:
            self._server.shutdown()


# ── Terminal frame ────────────────────────────────────────────────────────────
class TerminalFrame(ttk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.configure(style="TFrame")
        self._proc   = None
        self._q      = queue.Queue()
        self._build()
        self._start_shell()
        self._poll()

    def _build(self):
        self.output = tk.Text(self, bg=TERM_BG, fg=SUCCESS, font=("Consolas", 10),
                              insertbackground=SUCCESS, state=tk.DISABLED,
                              relief="flat", wrap=tk.WORD)
        sb = ttk.Scrollbar(self, command=self.output.yview)
        self.output.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.output.pack(fill=tk.BOTH, expand=True)

        bar = tk.Frame(self, bg=PANEL)
        bar.pack(fill=tk.X)
        tk.Label(bar, text="PS>", bg=PANEL, fg=ACCENT,
                 font=("Consolas", 10)).pack(side=tk.LEFT, padx=(8, 4))
        self._entry = tk.Entry(bar, bg=ENTRY_BG, fg=FG, insertbackground=FG,
                               relief="flat", font=("Consolas", 10))
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4), pady=4)
        self._entry.bind("<Return>", self._send)
        tk.Button(bar, text="Run", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 9), command=self._send,
                  activebackground=BTN_HOV, activeforeground=FG).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(bar, text="Clear", bg=ENTRY_BG, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 9), command=self._clear,
                  activebackground=PANEL, activeforeground=FG).pack(side=tk.LEFT, padx=(0, 8))

    def _start_shell(self):
        try:
            self._proc = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoExit", "-Command", "-"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW
            )
            threading.Thread(target=self._read_output, daemon=True).start()
            self._append("BennyAI Terminal  (PowerShell)\n")
            self._append("─" * 36 + "\n")
            # send a no-op to flush the prompt
            self._proc.stdin.write("echo ''\n")
            self._proc.stdin.flush()
        except Exception as e:
            self._append(f"[Terminal error: {e}]\n")

    def _read_output(self):
        for line in self._proc.stdout:
            self._q.put(line)

    def _poll(self):
        while not self._q.empty():
            self._append(self._q.get_nowait())
        self.after(80, self._poll)

    def _append(self, text: str):
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, text)
        self.output.see(tk.END)
        self.output.configure(state=tk.DISABLED)

    def _send(self, _=None):
        cmd = self._entry.get().strip()
        if not cmd or not self._proc:
            return
        self._append(f"PS> {cmd}\n")
        self._entry.delete(0, tk.END)
        try:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()
        except Exception:
            pass

    def run_command(self, cmd: str):
        """Inject a command from the agent or external control."""
        self._entry.delete(0, tk.END)
        self._entry.insert(0, cmd)
        self._send()

    def _clear(self):
        self.output.configure(state=tk.NORMAL)
        self.output.delete("1.0", tk.END)
        self.output.configure(state=tk.DISABLED)


# ── Model settings dialog ─────────────────────────────────────────────────────
class SettingsDialog(tk.Toplevel):
    """Modal for tuning Ollama inference parameters."""

    def __init__(self, parent, ollama: "OllamaClient", on_apply):
        super().__init__(parent)
        self.title("Model Settings")
        self.configure(bg=PANEL)
        self.resizable(False, False)
        self.grab_set()
        self._ollama    = ollama
        self._on_apply  = on_apply
        self._vars      = {}
        self._build()
        self.geometry("+%d+%d" % (parent.winfo_rootx()+120, parent.winfo_rooty()+80))

    def _build(self):
        tk.Label(self, text="Model Settings", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 12, "bold")).pack(padx=24, pady=(18, 4))
        tk.Label(self, text="Tune inference parameters for your hardware",
                 bg=PANEL, fg=FG_DIM, font=("Segoe UI", 9)).pack()

        # Preset row
        preset_row = tk.Frame(self, bg=PANEL)
        preset_row.pack(padx=24, pady=(14, 4), anchor="w")
        tk.Label(preset_row, text="Preset:", bg=PANEL, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 8))
        for name, vals in PERF_PRESETS.items():
            tk.Button(preset_row, text=name, bg=ENTRY_BG, fg=FG,
                      relief="flat", font=("Segoe UI", 9),
                      activebackground=BTN, activeforeground=FG,
                      command=lambda v=vals: self._apply_preset(v)
                      ).pack(side=tk.LEFT, padx=2)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=24, pady=10)

        sliders = [
            ("temperature",  "Temperature",
             "Creativity vs determinism. Low=focused code, High=creative text.",
             0.0, 1.0, 0.01),
            ("num_predict",  "Max Tokens",
             "Maximum tokens to generate per response.",
             256, 8192, 1),
            ("num_ctx",      "Context Window",
             "How many tokens of conversation history to keep.",
             512, 16384, 1),
            ("num_gpu",      "GPU Layers",
             "Layers offloaded to GPU. 0=CPU only, 999=max GPU (recommended).",
             0, 999, 1),
        ]

        self._slider_widgets = {}
        for key, label, tip, lo, hi, res in sliders:
            cur = self._ollama.options.get(key, PERF_PRESETS["Balanced"][key])
            frame = tk.Frame(self, bg=PANEL)
            frame.pack(fill=tk.X, padx=24, pady=4)

            top = tk.Frame(frame, bg=PANEL)
            top.pack(fill=tk.X)
            tk.Label(top, text=label, bg=PANEL, fg=FG_BRIGHT,
                     font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)
            val_lbl = tk.Label(top, text=str(cur), bg=PANEL, fg=ACCENT,
                               font=("Consolas", 10), width=7)
            val_lbl.pack(side=tk.RIGHT)

            tk.Label(frame, text=tip, bg=PANEL, fg=FG_DIM,
                     font=("Segoe UI", 8), wraplength=340,
                     justify=tk.LEFT).pack(anchor="w")

            var = tk.DoubleVar(value=cur)
            self._vars[key] = var

            def _update(v, lbl=val_lbl, k=key, r=res):
                n = float(v)
                lbl.configure(text=f"{n:.2f}" if r < 1 else str(int(n)))

            sl = ttk.Scale(frame, from_=lo, to=hi, variable=var,
                           orient=tk.HORIZONTAL, command=_update)
            sl.pack(fill=tk.X, pady=(2, 0))
            self._slider_widgets[key] = sl

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=24, pady=12)
        btn_row = tk.Frame(self, bg=PANEL)
        btn_row.pack(padx=24, pady=(0, 18))
        tk.Button(btn_row, text="Apply", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=20, pady=6,
                  activebackground=BTN_HOV,
                  command=self._do_apply).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(btn_row, text="Cancel", bg=ENTRY_BG, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 10), padx=14, pady=6,
                  command=self.destroy).pack(side=tk.LEFT)

    def _apply_preset(self, vals: dict):
        for k, v in vals.items():
            if k in self._vars:
                self._vars[k].set(v)

    def _do_apply(self):
        for k, var in self._vars.items():
            v = var.get()
            self._ollama.options[k] = int(v) if k != "temperature" else round(v, 3)
        self._on_apply()
        self.destroy()


# ── Transfer / Portable Pack dialog ──────────────────────────────────────────
OLLAMA_MODELS_DIR = Path.home() / ".ollama" / "models"

# Embedded setup script — written into every export ZIP
_SETUP_SCRIPT = textwrap.dedent(r"""
#!/usr/bin/env python3
\"\"\"BennyAI auto-setup — run this on the new PC to get everything ready.\"\"\"
import sys, os, subprocess, shutil, json, platform
from pathlib import Path

ROOT   = Path(__file__).parent
DATA   = ROOT / "data"
MODELS = ROOT / "models"
HOME   = Path.home()
BENNY  = HOME / "BennyAI"
OLLAMA = HOME / ".ollama" / "models"

def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

def pip(*pkgs):
    subprocess.check_call([sys.executable, "-m", "pip", "install", *pkgs])

print("=== BennyAI Setup ===")

# 1. Python packages
print("[1/5] Installing Python packages...")
pip("requests", "pdfplumber", "beautifulsoup4", "psutil")
print("      OK")

# 2. Check Ollama
print("[2/5] Checking Ollama...")
r = run("ollama --version")
if r.returncode != 0:
    print("      Ollama not found.")
    if platform.system() == "Windows":
        print("      Download from: https://ollama.com/download/windows")
    else:
        print("      Install with: curl -fsSL https://ollama.com/install.sh | sh")
    input("      Press Enter after installing Ollama, then re-run this script.")
    sys.exit(1)
print("     ", r.stdout.strip())

# 3. Import model blobs (offline)
blob_dir = MODELS / "blobs"
manifest_dir = MODELS / "manifests"
if blob_dir.exists():
    print("[3/5] Importing model blobs...")
    OLLAMA.mkdir(parents=True, exist_ok=True)
    dest_blobs = OLLAMA / "blobs"
    dest_mani  = OLLAMA / "manifests"
    dest_blobs.mkdir(exist_ok=True)
    dest_mani.mkdir(exist_ok=True)
    for f in blob_dir.iterdir():
        dst = dest_blobs / f.name
        if not dst.exists():
            shutil.copy2(f, dst)
            print(f"      Copied blob: {f.name[:16]}...")
    if manifest_dir.exists():
        shutil.copytree(str(manifest_dir), str(dest_mani), dirs_exist_ok=True)
    print("      Blobs imported.")
else:
    print("[3/5] No offline blobs — skipping blob import.")

# 4. Pull models listed for online install
model_list = MODELS / "model_list.txt"
if model_list.exists():
    models = [l.strip() for l in model_list.read_text().splitlines() if l.strip()]
    if models:
        print(f"[4/5] Pulling {len(models)} model(s) from Ollama registry...")
        for m in models:
            print(f"      ollama pull {m}")
            subprocess.run(f"ollama pull {m}", shell=True)
    else:
        print("[4/5] No models to pull.")
else:
    print("[4/5] model_list.txt not found — skipping.")

# 5. Restore chats + library
print("[5/5] Restoring chats and library...")
for folder in ("chats", "library"):
    src = DATA / folder
    dst = BENNY / folder
    if src.exists():
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            shutil.copy2(f, dst / f.name)
        print(f"      {folder}: {len(list(src.iterdir()))} files")

# Copy bennyai.py to home
script = ROOT / "bennyai.py"
dest   = HOME / "BennyAI" / "bennyai.py"
dest.parent.mkdir(exist_ok=True)
if script.exists():
    shutil.copy2(script, dest)
    print(f"      bennyai.py → {dest}")

print()
print("=== Setup complete! ===")
print(f"Run BennyAI with:")
print(f"  python \"{dest}\"")
input("Press Enter to exit...")
""")

_SETUP_BAT = textwrap.dedent(r"""
@echo off
echo === BennyAI Auto-Setup ===
python setup.py
pause
""")

_SETUP_SH = textwrap.dedent(r"""
#!/bin/bash
echo "=== BennyAI Auto-Setup ==="
python3 setup.py
""")

_README = textwrap.dedent(r"""
BennyAI Portable Transfer Pack
================================
This ZIP contains everything needed to run BennyAI on a new PC.

HOW TO SET UP ON A NEW PC
--------------------------
1. Extract this ZIP to any folder (e.g. Desktop or C:\BennyAI\)
2. Make sure Python 3.8+ is installed  (https://python.org)
3. Run setup:
     Windows:  double-click  setup.bat
     Linux/Mac: bash setup.sh
4. The setup script will:
     - Install Python packages (requests, pdfplumber, etc.)
     - Check/prompt for Ollama installation
     - Import any offline model blobs
     - Pull models that need an internet download
     - Restore your chats and knowledge library
     - Copy bennyai.py to ~/BennyAI/

REQUIREMENTS
------------
- Python 3.8 or newer
- Ollama  (https://ollama.com)  — must be installed and running
- Internet connection (only if models are listed for online pull)
""")


class TransferDialog(tk.Toplevel):
    """Export/Import portable BennyAI transfer packs."""

    def __init__(self, parent, ollama: "OllamaClient"):
        super().__init__(parent)
        self.title("📦 Transfer — Portable Pack")
        self.configure(bg=BG)
        self.geometry("740x600")
        self.minsize(640, 480)
        self.grab_set()
        self._ollama = ollama
        self._progress_var = tk.DoubleVar(value=0)
        self._log_var = tk.StringVar(value="")
        self._build()
        self.geometry("+%d+%d" % (parent.winfo_rootx()+80, parent.winfo_rooty()+60))

    # ── build ──────────────────────────────────────────────────────────
    def _build(self):
        hdr = tk.Frame(self, bg=PANEL, pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="  📦 Transfer — Portable Pack", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        tk.Button(hdr, text="✕", bg=PANEL, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 12), command=self.destroy).pack(side=tk.RIGHT, padx=8)

        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
        nb.add(self._build_export_tab(nb), text=" Export ")
        nb.add(self._build_import_tab(nb), text=" Import / Setup ")

    # ── Export tab ──────────────────────────────────────────────────────
    def _build_export_tab(self, nb):
        f = tk.Frame(nb, bg=BG)

        tk.Label(f, text="Choose what to include in the transfer ZIP:",
                 bg=BG, fg=FG_BRIGHT, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=16, pady=(14, 6))

        # Checkboxes
        checks = tk.Frame(f, bg=BG)
        checks.pack(fill=tk.X, padx=16)

        self._inc_script  = tk.BooleanVar(value=True)
        self._inc_chats   = tk.BooleanVar(value=True)
        self._inc_library = tk.BooleanVar(value=True)
        self._inc_blobs   = tk.BooleanVar(value=False)

        for var, label, tip in [
            (self._inc_script,  "bennyai.py script",
             "Include the main BennyAI Python file"),
            (self._inc_chats,   "Chat history",
             f"Saved chats from {CHATS_DIR}"),
            (self._inc_library, "Knowledge library",
             "Downloaded books and docs"),
            (self._inc_blobs,   "Model blobs (offline transfer — can be very large!)",
             "Pack Ollama model files so the new PC works without internet"),
        ]:
            row = tk.Frame(checks, bg=BG)
            row.pack(fill=tk.X, pady=2)
            tk.Checkbutton(row, variable=var, text=label,
                           bg=BG, fg=FG_BRIGHT, selectcolor=ENTRY_BG,
                           activebackground=BG, font=("Segoe UI", 10),
                           command=self._refresh_model_list).pack(
                side=tk.LEFT, anchor="w")
            tk.Label(row, text=tip, bg=BG, fg=FG_DIM,
                     font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(8, 0))

        tk.Frame(f, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=8)

        # Model list
        tk.Label(f, text="Models — check to include for online pull on new PC:",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).pack(
            anchor="w", padx=16, pady=(0, 4))

        model_frame = tk.Frame(f, bg=ENTRY_BG)
        model_frame.pack(fill=tk.X, padx=16)
        self._model_checks   = {}
        self._model_sizes_kb = {}
        self._model_list_frame = model_frame
        self._refresh_model_list()

        tk.Frame(f, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=8)

        # Size estimate
        self._size_lbl = tk.Label(f, text="", bg=BG, fg=FG_DIM,
                                  font=("Segoe UI", 9))
        self._size_lbl.pack(anchor="w", padx=16)

        # Progress
        self._exp_progress = ttk.Progressbar(f, variable=self._progress_var,
                                             maximum=100)
        self._exp_progress.pack(fill=tk.X, padx=16, pady=(8, 4))
        self._exp_log = tk.Label(f, textvariable=self._log_var, bg=BG, fg=FG_DIM,
                                 font=("Segoe UI", 8), anchor="w")
        self._exp_log.pack(fill=tk.X, padx=16)

        # Export button
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(padx=16, pady=12)
        tk.Button(btn_row, text="📦  Export ZIP…", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 11, "bold"), padx=20, pady=8,
                  activebackground=BTN_HOV,
                  command=self._do_export).pack(side=tk.LEFT)
        tk.Label(btn_row, text="  Choose where to save the ZIP file",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9)).pack(side=tk.LEFT)

        return f

    def _refresh_model_list(self):
        for w in self._model_list_frame.winfo_children():
            w.destroy()
        self._model_checks.clear()

        try:
            models_raw = subprocess.run("ollama list", shell=True,
                                        capture_output=True, text=True, timeout=5)
            lines = [l for l in models_raw.stdout.strip().splitlines()[1:]
                     if l.strip()]
        except Exception:
            lines = []

        if not lines:
            tk.Label(self._model_list_frame, text="  (no Ollama models found)",
                     bg=ENTRY_BG, fg=FG_DIM, font=("Segoe UI", 9)).pack(padx=8, pady=4)
            return

        include_blobs = self._inc_blobs.get()
        total_kb = 0
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            size_str = parts[3] if len(parts) > 3 else "?"
            unit = parts[4].upper() if len(parts) > 4 else ""
            try:
                kb = float(size_str) * (1024 if unit == "GB" else 1)
            except Exception:
                kb = 0
            self._model_sizes_kb[name] = kb
            var = tk.BooleanVar(value=True)
            self._model_checks[name] = var
            row = tk.Frame(self._model_list_frame, bg=ENTRY_BG)
            row.pack(fill=tk.X)
            tk.Checkbutton(row, variable=var, text=name,
                           bg=ENTRY_BG, fg=FG, selectcolor=SIDEBAR,
                           activebackground=ENTRY_BG,
                           font=("Consolas", 9),
                           command=self._update_size_label).pack(side=tk.LEFT, padx=4)
            size_info = f"{kb/1024:.1f} GB" if kb > 1024 else f"{kb:.0f} MB"
            blob_note = " (blob)" if include_blobs else " (online pull)"
            tk.Label(row, text=f"  {size_info}{blob_note}",
                     bg=ENTRY_BG, fg=FG_DIM, font=("Segoe UI", 8)).pack(side=tk.LEFT)
        self._update_size_label()

    def _update_size_label(self):
        total_kb = 0
        for name, var in self._model_checks.items():
            if var.get() and self._inc_blobs.get():
                total_kb += self._model_sizes_kb.get(name, 0)
        # Add rough estimate for chats + library
        chats_kb   = sum(f.stat().st_size for f in CHATS_DIR.glob("*.json")
                        if f.exists()) // 1024 if CHATS_DIR.exists() else 0
        lib_kb     = sum(f.stat().st_size for f in
                        (ToolExecutor.LIBRARY_DIR).glob("*")
                        if f.is_file()) // 1024 if ToolExecutor.LIBRARY_DIR.exists() else 0
        total_kb  += chats_kb + lib_kb
        if total_kb > 1024:
            size_str = f"{total_kb/1024:.2f} GB"
        else:
            size_str = f"{total_kb:.0f} MB"
        self._size_lbl.configure(
            text=f"Estimated ZIP size: ~{size_str}  "
                 f"(chats: {chats_kb}KB  library: {lib_kb}KB)")

    # ── Import tab ──────────────────────────────────────────────────────
    def _build_import_tab(self, nb):
        f = tk.Frame(nb, bg=BG)

        tk.Label(f, text="Import a BennyAI transfer ZIP onto this PC:",
                 bg=BG, fg=FG_BRIGHT, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", padx=16, pady=(14, 6))

        tk.Label(f, text=(
            "The ZIP will be extracted and setup will run automatically:\n"
            "  • Python packages installed\n"
            "  • Model blobs copied to Ollama storage (if included)\n"
            "  • Online models pulled from Ollama registry\n"
            "  • Chats and library restored to ~/BennyAI/"
        ), bg=BG, fg=FG_DIM, font=("Segoe UI", 9), justify=tk.LEFT).pack(
            anchor="w", padx=24)

        tk.Frame(f, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=12)

        # File picker
        pick = tk.Frame(f, bg=BG)
        pick.pack(fill=tk.X, padx=16)
        self._import_path = tk.StringVar()
        tk.Entry(pick, textvariable=self._import_path, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief="flat",
                 font=("Segoe UI", 10)).pack(side=tk.LEFT, fill=tk.X,
                                              expand=True, padx=(0, 8))
        tk.Button(pick, text="Browse…", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 9),
                  command=self._browse_import).pack(side=tk.LEFT)

        # Options
        opts = tk.Frame(f, bg=BG)
        opts.pack(fill=tk.X, padx=16, pady=8)
        self._imp_chats   = tk.BooleanVar(value=True)
        self._imp_library = tk.BooleanVar(value=True)
        self._imp_models  = tk.BooleanVar(value=True)
        for var, label in [
            (self._imp_chats,   "Restore chat history"),
            (self._imp_library, "Restore knowledge library"),
            (self._imp_models,  "Import / pull models"),
        ]:
            tk.Checkbutton(opts, variable=var, text=label,
                           bg=BG, fg=FG, selectcolor=ENTRY_BG,
                           activebackground=BG,
                           font=("Segoe UI", 10)).pack(anchor="w")

        # Progress log
        tk.Frame(f, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=8)
        self._imp_progress = ttk.Progressbar(f, maximum=100)
        self._imp_progress.pack(fill=tk.X, padx=16, pady=(0, 4))
        self._imp_text = tk.Text(f, bg=ENTRY_BG, fg=FG, height=10,
                                 font=("Consolas", 8), relief="flat",
                                 state=tk.DISABLED)
        self._imp_text.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        tk.Button(f, text="▶  Run Import & Setup", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 11, "bold"), padx=20, pady=8,
                  activebackground=BTN_HOV,
                  command=self._do_import).pack(pady=(0, 12))
        return f

    def _browse_import(self):
        p = filedialog.askopenfilename(
            title="Select BennyAI Transfer ZIP",
            filetypes=[("ZIP files", "*.zip"), ("All files", "*.*")])
        if p:
            self._import_path.set(p)

    # ── Export logic ────────────────────────────────────────────────────
    def _do_export(self):
        dest = filedialog.asksaveasfilename(
            title="Save Transfer ZIP as…",
            defaultextension=".zip",
            filetypes=[("ZIP files", "*.zip")],
            initialfile="bennyai_transfer.zip")
        if not dest:
            return
        self._progress_var.set(0)
        self._log_var.set("Starting export…")
        threading.Thread(target=self._export_worker, args=(dest,), daemon=True).start()

    def _export_log(self, msg: str, pct: float = None):
        self.after(0, self._log_var.set, msg)
        if pct is not None:
            self.after(0, self._progress_var.set, pct)

    def _export_worker(self, dest: str):
        try:
            with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED,
                                 compresslevel=6) as zf:
                step = 0

                # 1. Script
                if self._inc_script.get():
                    self._export_log("Adding bennyai.py…", 5)
                    zf.write(__file__, "bennyai.py")

                # 2. Setup scripts + README
                self._export_log("Adding setup scripts…", 10)
                zf.writestr("setup.py",  _SETUP_SCRIPT)
                zf.writestr("setup.bat", _SETUP_BAT)
                zf.writestr("setup.sh",  _SETUP_SH)
                zf.writestr("README.txt", _README)
                zf.writestr("requirements.txt",
                            "requests\npdfplumber\nbeautifulsoup4\npsutil\n")

                # 3. Chats
                if self._inc_chats.get():
                    self._export_log("Adding chats…", 18)
                    if CHATS_DIR.exists():
                        for f in CHATS_DIR.iterdir():
                            if f.is_file():
                                zf.write(f, f"data/chats/{f.name}")

                # 4. Library
                if self._inc_library.get():
                    self._export_log("Adding knowledge library…", 26)
                    lib_dir = ToolExecutor.LIBRARY_DIR
                    if lib_dir.exists():
                        for f in lib_dir.iterdir():
                            if f.is_file():
                                zf.write(f, f"data/library/{f.name}")

                # 5. Model list (always write — names for online pull)
                selected_models = [n for n, v in self._model_checks.items() if v.get()]
                self._export_log("Writing model list…", 30)
                zf.writestr("models/model_list.txt",
                            "\n".join(selected_models))

                # 6. Blobs (optional — large!)
                if self._inc_blobs.get() and selected_models:
                    self._export_log("Adding model blobs — this may take a while…", 32)
                    total = len(selected_models)
                    for idx, model_name in enumerate(selected_models):
                        pct = 32 + (idx / total) * 60
                        self._export_log(
                            f"Packing model {idx+1}/{total}: {model_name}…", pct)
                        self._pack_model_blobs(zf, model_name)

                self._export_log("Finalising ZIP…", 96)

            zip_size_mb = Path(dest).stat().st_size / (1024*1024)
            self._export_log(
                f"✓ Export complete!  {zip_size_mb:.1f} MB  →  {dest}", 100)

        except Exception as e:
            self._export_log(f"[ERROR] Export failed: {e}", 0)

    def _pack_model_blobs(self, zf: zipfile.ZipFile, model_name: str):
        """Find and pack the manifest + blobs for a given ollama model name."""
        # Normalise  "model:tag" → path components
        parts = model_name.split(":")
        name  = parts[0]
        tag   = parts[1] if len(parts) > 1 else "latest"

        manifest_path = (OLLAMA_MODELS_DIR / "manifests" /
                         "registry.ollama.ai" / "library" / name / tag)
        if not manifest_path.exists():
            return  # model not found locally

        # Add manifest
        rel = manifest_path.relative_to(OLLAMA_MODELS_DIR)
        zf.write(manifest_path, f"models/manifests/{rel}")

        # Parse manifest to find blob references
        try:
            mdata = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return

        refs = []
        if "config" in mdata:
            refs.append(mdata["config"].get("digest", ""))
        for layer in mdata.get("layers", []):
            refs.append(layer.get("digest", ""))

        for digest in refs:
            if not digest:
                continue
            blob_name = digest.replace(":", "-")
            blob_path = OLLAMA_MODELS_DIR / "blobs" / blob_name
            if blob_path.exists():
                zf.write(blob_path, f"models/blobs/{blob_name}")

    # ── Import logic ────────────────────────────────────────────────────
    def _imp_log(self, msg: str, pct: float = None):
        def _do():
            self._imp_text.configure(state=tk.NORMAL)
            self._imp_text.insert(tk.END, msg + "\n")
            self._imp_text.see(tk.END)
            self._imp_text.configure(state=tk.DISABLED)
            if pct is not None:
                self._imp_progress["value"] = pct
        self.after(0, _do)

    def _do_import(self):
        src = self._import_path.get().strip()
        if not src or not Path(src).exists():
            messagebox.showerror("No file", "Please browse for the transfer ZIP first.",
                                 parent=self)
            return
        threading.Thread(target=self._import_worker, args=(src,), daemon=True).start()

    def _import_worker(self, zip_path: str):
        self._imp_log("=== BennyAI Import ===", 0)
        try:
            tmp = Path(zip_path).parent / "_bennyai_import_tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir()

            self._imp_log(f"Extracting {zip_path}…", 5)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp)
            self._imp_log("Extracted.", 15)

            # 1. Restore chats
            if self._imp_chats.get():
                src_chats = tmp / "data" / "chats"
                if src_chats.exists():
                    CHATS_DIR.mkdir(parents=True, exist_ok=True)
                    count = 0
                    for f in src_chats.iterdir():
                        if f.is_file():
                            shutil.copy2(f, CHATS_DIR / f.name)
                            count += 1
                    self._imp_log(f"  Restored {count} chat file(s).", 25)
                else:
                    self._imp_log("  No chat data found in ZIP.", 25)

            # 2. Restore library
            if self._imp_library.get():
                src_lib = tmp / "data" / "library"
                if src_lib.exists():
                    lib_dir = ToolExecutor.LIBRARY_DIR
                    lib_dir.mkdir(parents=True, exist_ok=True)
                    count = 0
                    for f in src_lib.iterdir():
                        if f.is_file():
                            shutil.copy2(f, lib_dir / f.name)
                            count += 1
                    self._imp_log(f"  Restored {count} library file(s).", 40)
                else:
                    self._imp_log("  No library data found in ZIP.", 40)

            # 3. Model blobs
            if self._imp_models.get():
                blob_src = tmp / "models" / "blobs"
                mani_src = tmp / "models" / "manifests"
                if blob_src.exists():
                    self._imp_log("  Copying model blobs to Ollama storage…", 45)
                    dest_blobs = OLLAMA_MODELS_DIR / "blobs"
                    dest_blobs.mkdir(parents=True, exist_ok=True)
                    count = 0
                    for f in blob_src.iterdir():
                        dst = dest_blobs / f.name
                        if not dst.exists():
                            shutil.copy2(f, dst)
                            count += 1
                    if mani_src.exists():
                        shutil.copytree(str(mani_src),
                                        str(OLLAMA_MODELS_DIR / "manifests"),
                                        dirs_exist_ok=True)
                    self._imp_log(f"  Copied {count} blob(s).", 65)

                # Pull models
                model_list_f = tmp / "models" / "model_list.txt"
                if model_list_f.exists():
                    models = [l.strip() for l in
                              model_list_f.read_text().splitlines() if l.strip()]
                    self._imp_log(
                        f"  Pulling {len(models)} model(s) — this may take a while…", 68)
                    for i, m in enumerate(models):
                        pct = 68 + (i / max(len(models), 1)) * 25
                        self._imp_log(f"    ollama pull {m}", pct)
                        subprocess.run(f"ollama pull {m}", shell=True,
                                       capture_output=True)
                    self._imp_log("  Model pull complete.", 93)

            # 4. Copy bennyai.py
            script_src = tmp / "bennyai.py"
            if script_src.exists():
                dest_script = Path.home() / "BennyAI" / "bennyai.py"
                dest_script.parent.mkdir(exist_ok=True)
                shutil.copy2(script_src, dest_script)
                self._imp_log(f"  Copied bennyai.py → {dest_script}", 96)

            shutil.rmtree(tmp, ignore_errors=True)
            self._imp_log("=== Import complete! Restart BennyAI. ===", 100)

        except Exception as e:
            self._imp_log(f"[ERROR] {e}", 0)


# ── Benchmark + model picker dialog ───────────────────────────────────────────
class BenchmarkDialog(tk.Toplevel):
    """Detect hardware, benchmark inference speed, suggest and install models."""

    def __init__(self, parent, ollama: "OllamaClient", terminal, on_model_change):
        super().__init__(parent)
        self.title("PC Benchmark & Model Picker")
        self.configure(bg=BG)
        self.geometry("820x640")
        self.minsize(700, 500)
        self.grab_set()
        self._ollama          = ollama
        self._terminal        = terminal
        self._on_model_change = on_model_change
        self._hw              = {}
        self._build()
        self.geometry("+%d+%d" % (parent.winfo_rootx()+60, parent.winfo_rooty()+40))
        self.after(200, self._run_detection)

    # ── UI ────────────────────────────────────────────────────────────────
    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=PANEL, pady=12)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="  PC Benchmark & Model Picker", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 13, "bold")).pack(side=tk.LEFT)
        tk.Button(hdr, text="✕", bg=PANEL, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 12), command=self.destroy).pack(side=tk.RIGHT, padx=8)

        # Specs strip
        specs = tk.Frame(self, bg=SIDEBAR, pady=10)
        specs.pack(fill=tk.X)
        self._spec_labels = {}
        for key in ("CPU", "RAM", "GPU", "VRAM"):
            col = tk.Frame(specs, bg=SIDEBAR)
            col.pack(side=tk.LEFT, expand=True)
            tk.Label(col, text=key, bg=SIDEBAR, fg=FG_DIM,
                     font=("Segoe UI", 8, "bold")).pack()
            lbl = tk.Label(col, text="detecting…", bg=SIDEBAR, fg=FG_BRIGHT,
                           font=("Segoe UI", 9))
            lbl.pack()
            self._spec_labels[key] = lbl

        # Benchmark result
        bench_row = tk.Frame(self, bg=BG, pady=6)
        bench_row.pack(fill=tk.X, padx=16)
        tk.Label(bench_row, text="Benchmark:", bg=BG, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._bench_lbl = tk.Label(bench_row, text="running…", bg=BG, fg=WARNING,
                                   font=("Segoe UI", 9, "bold"))
        self._bench_lbl.pack(side=tk.LEFT, padx=8)
        self._bench_btn = tk.Button(bench_row, text="Re-run benchmark",
                                    bg=ENTRY_BG, fg=FG_DIM, relief="flat",
                                    font=("Segoe UI", 9),
                                    command=lambda: threading.Thread(
                                        target=self._benchmark, daemon=True).start())
        self._bench_btn.pack(side=tk.RIGHT)

        tk.Frame(self, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=4)

        # Legend
        leg = tk.Frame(self, bg=BG)
        leg.pack(fill=tk.X, padx=16, pady=(0, 4))
        tk.Label(leg, text="Model recommendations — filtered to what fits your GPU VRAM",
                 bg=BG, fg=FG_DIM, font=("Segoe UI", 9, "italic")).pack(side=tk.LEFT)

        # Header row for table
        cols = tk.Frame(self, bg=SIDEBAR)
        cols.pack(fill=tk.X, padx=16)
        for text, w in [("Model", 22), ("VRAM", 6), ("Speed", 6),
                         ("Quality", 7), ("Type", 8), ("", 10)]:
            tk.Label(cols, text=text, bg=SIDEBAR, fg=ACCENT,
                     font=("Segoe UI", 9, "bold"), width=w,
                     anchor="w").pack(side=tk.LEFT)

        # Scrollable model list
        self._list_frame_outer = tk.Frame(self, bg=BG)
        self._list_frame_outer.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))
        canvas = tk.Canvas(self._list_frame_outer, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(self._list_frame_outer, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._list_inner = tk.Frame(canvas, bg=BG)
        wid = canvas.create_window((0, 0), window=self._list_inner, anchor="nw")
        self._list_inner.bind("<Configure>",
                              lambda e: canvas.configure(
                                  scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(wid, width=e.width))
        self._model_canvas = canvas

    def _star(self, n, mx=5):
        return "★" * n + "☆" * (mx - n)

    # ── Detection ─────────────────────────────────────────────────────────
    def _run_detection(self):
        threading.Thread(target=self._detect, daemon=True).start()

    def _detect(self):
        hw = {"cpu": "Unknown", "ram_gb": 0, "gpu": "Unknown",
              "vram_gb": 0, "cpu_cores": 1}
        try:
            import psutil
            hw["ram_gb"]   = psutil.virtual_memory().total / 1024**3
            hw["cpu_cores"] = psutil.cpu_count(logical=False) or 1
            hw["cpu"]      = "CPU"
        except Exception:
            pass

        # CPU name from registry/wmic
        try:
            r = subprocess.run(
                ["wmic", "cpu", "get", "name", "/value"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW)
            for line in r.stdout.splitlines():
                if "=" in line:
                    hw["cpu"] = line.split("=", 1)[1].strip()
        except Exception:
            pass

        # GPU + VRAM from nvidia-smi
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                parts = [p.strip() for p in r.stdout.strip().split(",")]
                hw["gpu"]     = parts[0]
                hw["vram_gb"] = float(parts[1]) / 1024
        except Exception:
            pass

        self._hw = hw
        self.after(0, self._update_specs)
        self.after(0, lambda: threading.Thread(
            target=self._benchmark, daemon=True).start())

    def _update_specs(self):
        hw = self._hw
        self._spec_labels["CPU"].configure(
            text=hw.get("cpu","?")[:30])
        self._spec_labels["RAM"].configure(
            text=f"{hw.get('ram_gb',0):.1f} GB")
        self._spec_labels["GPU"].configure(
            text=hw.get("gpu","?")[:22])
        self._spec_labels["VRAM"].configure(
            text=f"{hw.get('vram_gb',0):.1f} GB")
        self._populate_models()

    # ── Benchmark ─────────────────────────────────────────────────────────
    def _benchmark(self):
        self.after(0, self._bench_lbl.configure, {"text": "running…", "fg": WARNING})
        try:
            import time as _t
            prompt = "Write a Python function that sorts a list of integers."
            payload = {
                "model": self._ollama.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "options": {"num_predict": 60, "temperature": 0.1},
            }
            t0     = _t.time()
            tokens = 0
            with requests.post(f"{self._ollama.base}/api/chat",
                               json=payload, stream=True, timeout=120) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if line:
                        chunk = json.loads(line)
                        if chunk.get("message", {}).get("content"):
                            tokens += 1
                        if chunk.get("done"):
                            break
            elapsed = _t.time() - t0
            tps     = tokens / max(elapsed, 0.1)
            result  = f"{tps:.1f} tok/sec  ({tokens} tokens in {elapsed:.1f}s)"
            self.after(0, self._bench_lbl.configure,
                       {"text": result, "fg": SUCCESS})
        except Exception as e:
            self.after(0, self._bench_lbl.configure,
                       {"text": f"Failed: {e}", "fg": DANGER})

    # ── Model list ────────────────────────────────────────────────────────
    def _populate_models(self):
        for w in self._list_inner.winfo_children():
            w.destroy()

        vram_gb   = self._hw.get("vram_gb", 99)
        installed = set()
        try:
            installed = set(self._ollama.models())
        except Exception:
            pass

        current = self._ollama.model

        for (name, vram_req, speed, quality,
             specialty, desc) in MODEL_CATALOG:
            fits = vram_req <= max(vram_gb, 0.1)  # show all, dim those that don't fit

            row_bg = SIDEBAR if name == current else (ENTRY_BG if fits else BG)
            row = tk.Frame(self._list_inner, bg=row_bg, pady=6)
            row.pack(fill=tk.X, pady=1)

            # Dim indicator if won't fit
            fg_row = FG if fits else FG_DIM

            tk.Label(row, text=name, bg=row_bg, fg=fg_row,
                     font=("Consolas", 9, "bold"), width=24,
                     anchor="w").pack(side=tk.LEFT, padx=(8, 0))
            tk.Label(row, text=f"{vram_req}G", bg=row_bg,
                     fg=SUCCESS if fits else DANGER,
                     font=("Consolas", 9), width=6).pack(side=tk.LEFT)
            tk.Label(row, text=self._star(speed), bg=row_bg, fg=WARNING,
                     font=("Segoe UI", 9), width=6).pack(side=tk.LEFT)
            tk.Label(row, text=self._star(quality), bg=row_bg, fg=ACCENT2,
                     font=("Segoe UI", 9), width=7).pack(side=tk.LEFT)
            tk.Label(row, text=specialty, bg=row_bg, fg=fg_row,
                     font=("Segoe UI", 9), width=8).pack(side=tk.LEFT)

            # Action button
            if name == current:
                tk.Label(row, text="✦ active", bg=row_bg, fg=ACCENT,
                         font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=8)
            elif name in installed:
                tk.Button(row, text="Use", bg=BTN, fg=FG, relief="flat",
                          font=("Segoe UI", 9),
                          activebackground=BTN_HOV,
                          command=lambda n=name: self._use_model(n)
                          ).pack(side=tk.LEFT, padx=8)
            else:
                lbl = "Install" if fits else "Install (may not fit)"
                tk.Button(row, text=lbl,
                          bg=BTN if fits else ENTRY_BG,
                          fg=FG if fits else FG_DIM,
                          relief="flat", font=("Segoe UI", 9),
                          activebackground=BTN_HOV,
                          command=lambda n=name: self._install_model(n)
                          ).pack(side=tk.LEFT, padx=8)

            tk.Label(row, text=desc, bg=row_bg, fg=FG_DIM,
                     font=("Segoe UI", 8), wraplength=200,
                     anchor="w").pack(side=tk.LEFT, padx=4)

    def _use_model(self, name: str):
        self._ollama.model = name
        self._on_model_change(name)
        self._populate_models()

    def _install_model(self, name: str):
        cmd = f"ollama pull {name}"
        self._terminal.run_command(cmd)
        self.after(0, self.chat_note, f"Installing {name} via terminal…")
        # Poll until installed, then offer to switch
        def _wait():
            import time as _t
            for _ in range(120):   # wait up to 4 min
                _t.sleep(2)
                try:
                    installed = set(self._ollama.models())
                    if name in installed:
                        self.after(0, self._on_installed, name)
                        return
                except Exception:
                    pass
        threading.Thread(target=_wait, daemon=True).start()

    def _on_installed(self, name: str):
        self._populate_models()
        if messagebox.askyesno(
                "Model ready",
                f"{name} is installed.\nSwitch to it now?",
                parent=self):
            self._use_model(name)

    def chat_note(self, text):
        pass   # placeholder; parent wires this if needed


# ── Animated dual-needle gauge ────────────────────────────────────────────────
import math as _math

class GaugeMeter(tk.Canvas):
    """
    Circular arc gauge with TWO concentric rings:
      Outer ring  — total system usage   (green→yellow→red)
      Inner ring  — BennyAI / app usage  (blue accent, always)
    Both animate smoothly toward their targets.
    """
    _SIZE   = 104          # canvas width/height (px)
    _PAD_O  = 8            # outer ring padding from edge
    _PAD_I  = 22           # inner ring padding from edge
    _W_O    = 10           # outer arc stroke width
    _W_I    = 7            # inner arc stroke width
    _APP_COLOR = "#7aa2f7" # accent blue for BennyAI needle
    _APP_GLOW  = "#1a2a4a"

    def __init__(self, parent, label: str, **kw):
        S = self._SIZE
        super().__init__(parent, width=S, height=S + 26,
                         bg=PANEL, highlightthickness=0, **kw)
        self._label   = label
        self._sys_cur = 0.0;  self._sys_tgt = 0.0
        self._app_cur = 0.0;  self._app_tgt = 0.0
        self._ticking = False
        self._draw()

    # ── public API ────────────────────────────────────────────────────────
    def rev(self, sys_pct: float, app_pct: float = 0.0):
        """Update both needles and animate toward new targets."""
        self._sys_tgt = max(0.0, min(100.0, float(sys_pct)))
        self._app_tgt = max(0.0, min(100.0, float(app_pct)))
        if not self._ticking:
            self._ticking = True
            self._tick()

    # ── animation ─────────────────────────────────────────────────────────
    def _tick(self):
        sd = self._sys_tgt - self._sys_cur
        ad = self._app_tgt - self._app_cur
        if abs(sd) < 0.35 and abs(ad) < 0.35:
            self._sys_cur = self._sys_tgt
            self._app_cur = self._app_tgt
            self._ticking = False
            self._draw()
            return
        self._sys_cur += sd * 0.18
        self._app_cur += ad * 0.18
        self._draw()
        self.after(26, self._tick)

    # ── colors ────────────────────────────────────────────────────────────
    @staticmethod
    def _sys_color(pct: float) -> str:
        if pct < 55:  return SUCCESS
        if pct < 75:  return WARNING
        if pct < 90:  return "#ff9f3f"
        return DANGER

    @staticmethod
    def _dim(color: str, alpha: float) -> str:
        def p(h): return int(h[1:3],16), int(h[3:5],16), int(h[5:7],16)
        cr,cg,cb = p(color); br,bg_,bb = p(PANEL)
        return "#{:02x}{:02x}{:02x}".format(
            int(br+(cr-br)*alpha), int(bg_+(cg-bg_)*alpha), int(bb+(cb-bb)*alpha))

    # ── drawing ───────────────────────────────────────────────────────────
    def _draw(self):
        self.delete("all")
        S  = self._SIZE
        cx = S / 2
        cy = S / 2 - 2   # shift center slightly up to leave room for text

        def arc_bbox(pad):
            return pad, pad, S - pad, S - pad

        # ── outer background track ───────────────────────────────────────
        ox1,oy1,ox2,oy2 = arc_bbox(self._PAD_O)
        self.create_arc(ox1,oy1,ox2,oy2, start=225, extent=-270,
                        style=tk.ARC, width=self._W_O+4, outline="#09091a")
        self.create_arc(ox1,oy1,ox2,oy2, start=225, extent=-270,
                        style=tk.ARC, width=self._W_O,   outline="#252535")

        # ── inner background track ───────────────────────────────────────
        ix1,iy1,ix2,iy2 = arc_bbox(self._PAD_I)
        self.create_arc(ix1,iy1,ix2,iy2, start=225, extent=-270,
                        style=tk.ARC, width=self._W_I+3, outline="#09091a")
        self.create_arc(ix1,iy1,ix2,iy2, start=225, extent=-270,
                        style=tk.ARC, width=self._W_I,   outline="#1e1e2e")

        # ── tick marks + scale labels ─────────────────────────────────────
        r_o    = (S - 2*self._PAD_O) / 2
        r_tick = r_o + 2
        # labels sit just inside the outer arc's inner edge (safe from arc overdraw)
        r_lbl  = r_o - self._W_O / 2 - 5   # = 34 for default dimensions
        for tick in (0, 25, 50, 75, 100):
            a = _math.radians(225 - tick / 100 * 270)
            tick_len = 7 if tick in (0, 50, 100) else 5
            self.create_line(
                cx + r_tick * _math.cos(a),             cy - r_tick * _math.sin(a),
                cx + (r_tick - tick_len) * _math.cos(a), cy - (r_tick - tick_len) * _math.sin(a),
                fill=FG_DIM, width=1)
            lx = cx + r_lbl * _math.cos(a)
            ly = cy - r_lbl * _math.sin(a)
            self.create_text(lx, ly, text=str(tick), fill=FG_DIM,
                             font=("Segoe UI", 6), anchor="center")

        # ── outer (system) arc ───────────────────────────────────────────
        spct  = self._sys_cur
        scol  = self._sys_color(spct)
        sext  = -(spct/100*270)
        if abs(sext) > 0.5:
            self.create_arc(ox1-1,oy1-1,ox2+1,oy2+1, start=225, extent=sext,
                            style=tk.ARC, width=self._W_O+3,
                            outline=self._dim(scol, 0.28))
            self.create_arc(ox1,oy1,ox2,oy2, start=225, extent=sext,
                            style=tk.ARC, width=self._W_O, outline=scol)
        # outer needle dot
        if spct > 0.5:
            a  = _math.radians(225 - spct/100*270)
            rn = r_o - self._W_O/2
            nx, ny = cx + rn*_math.cos(a), cy - rn*_math.sin(a)
            self.create_oval(nx-4, ny-4, nx+4, ny+4, fill=scol, outline="")

        # ── inner (BennyAI) arc ──────────────────────────────────────────
        apct  = self._app_cur
        acol  = self._APP_COLOR
        aext  = -(apct/100*270)
        r_i   = (S - 2*self._PAD_I) / 2
        if abs(aext) > 0.5:
            self.create_arc(ix1-1,iy1-1,ix2+1,iy2+1, start=225, extent=aext,
                            style=tk.ARC, width=self._W_I+2,
                            outline=self._dim(acol, 0.30))
            self.create_arc(ix1,iy1,ix2,iy2, start=225, extent=aext,
                            style=tk.ARC, width=self._W_I, outline=acol)
        # inner needle dot
        if apct > 0.5:
            a  = _math.radians(225 - apct/100*270)
            rn = r_i - self._W_I/2
            nx, ny = cx + rn*_math.cos(a), cy - rn*_math.sin(a)
            self.create_oval(nx-3, ny-3, nx+3, ny+3, fill=acol, outline="")

        # ── center: system % (large) / app % (small) ─────────────────────
        self.create_text(cx, cy - 5,
                         text=f"{spct:.0f}%",
                         fill=FG_BRIGHT, font=("Segoe UI", 11, "bold"))
        self.create_text(cx, cy + 10,
                         text=f"{apct:.0f}%",
                         fill=self._APP_COLOR, font=("Segoe UI", 8))

        # ── label + legend ────────────────────────────────────────────────
        self.create_text(cx, S + 4,
                         text=self._label,
                         fill=FG_DIM, font=("Segoe UI", 8, "bold"))
        # coloured legend dots
        self.create_oval(cx-28, S+14, cx-22, S+20, fill=scol,  outline="")
        self.create_text(cx-18, S+17, text="Sys", fill=FG_DIM,
                         font=("Segoe UI", 7), anchor="w")
        self.create_oval(cx+4,  S+14, cx+10, S+20, fill=acol,  outline="")
        self.create_text(cx+14, S+17, text="App", fill=FG_DIM,
                         font=("Segoe UI", 7), anchor="w")


# ── Chat display ──────────────────────────────────────────────────────────────
class ChatDisplay(tk.Frame):
    """Scrollable chat area with user/bot/tool message styles."""

    def __init__(self, parent, **kw):
        super().__init__(parent, bg=BG, **kw)
        self._canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        self._sb     = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._sb.set)
        self._sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._inner  = tk.Frame(self._canvas, bg=BG)
        self._win_id = self._canvas.create_window((0, 0), window=self._inner, anchor="nw")
        self._inner.bind("<Configure>", self._on_inner_resize)
        self._canvas.bind("<Configure>", self._on_canvas_resize)
        self._canvas.bind_all("<MouseWheel>", self._on_scroll)
        self._current_bot_frame = None
        self._current_bot_label = None
        self._stream_buffer = ""
        self._thinking = False

    def _on_inner_resize(self, _):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_resize(self, e):
        self._canvas.itemconfig(self._win_id, width=e.width)

    def _on_scroll(self, e):
        self._canvas.yview_scroll(int(-1 * e.delta / 120), "units")

    def _scroll_bottom(self):
        self._canvas.update_idletasks()
        self._canvas.yview_moveto(1.0)

    # ── Message types ─────────────────────────────────────────────────────
    def add_user(self, text: str):
        self._current_bot_frame = None
        outer = tk.Frame(self._inner, bg=BG)
        outer.pack(fill=tk.X, padx=16, pady=(6, 2))
        bubble = tk.Frame(outer, bg=USER_BG, padx=14, pady=10)
        bubble.pack(side=tk.RIGHT)
        tk.Label(bubble, text=text, bg=USER_BG, fg=FG_BRIGHT,
                 font=("Segoe UI", 10), wraplength=520, justify=tk.LEFT,
                 anchor="w").pack()
        self._scroll_bottom()

    def start_bot(self):
        """Open a bot bubble that can be streamed into."""
        self._stream_buffer = ""
        self._thinking = True
        outer = tk.Frame(self._inner, bg=BG)
        outer.pack(fill=tk.X, padx=16, pady=(2, 2))

        icon = tk.Label(outer, text="✦", bg=BG, fg=ACCENT,
                        font=("Segoe UI", 12, "bold"))
        icon.pack(side=tk.LEFT, anchor="n", padx=(0, 8), pady=8)

        bubble = tk.Frame(outer, bg=BOT_BG, padx=14, pady=10)
        bubble.pack(side=tk.LEFT, fill=tk.X, expand=True)

        lbl = tk.Text(bubble, bg=BOT_BG, fg=FG, font=("Segoe UI", 10),
                      relief="flat", wrap=tk.WORD, state=tk.DISABLED,
                      cursor="arrow", height=1, padx=0, pady=0,
                      selectbackground=BTN, selectforeground=FG)
        lbl.pack(fill=tk.X)
        lbl.tag_configure("code",    font=("Consolas", 10), background="#0d0d14",
                          foreground=WARNING)
        lbl.tag_configure("heading", font=("Segoe UI", 11, "bold"), foreground=ACCENT)
        lbl.tag_configure("thinking", foreground=FG_DIM, font=("Segoe UI", 10, "italic"))

        # Show "Thinking…" placeholder
        lbl.configure(state=tk.NORMAL)
        lbl.insert(tk.END, "Thinking…", "thinking")
        lbl.configure(state=tk.DISABLED)

        self._current_bot_frame = bubble
        self._current_bot_label = lbl
        self._scroll_bottom()

    def append_bot_token(self, token: str):
        """token is the full accumulated text so far (agent sends full buffer each call)."""
        self._stream_buffer = token          # replace, not accumulate
        if not self._stream_buffer.strip():
            return
        if self._current_bot_label:
            self._thinking = False
            self._current_bot_label.configure(state=tk.NORMAL)
            # Only show clean text (strip tool markup in both formats)
            visible = _strip_tool_markup(self._stream_buffer)
            self._current_bot_label.delete("1.0", tk.END)
            self._render_text(self._current_bot_label, visible)
            lines = int(self._current_bot_label.index(tk.END).split(".")[0])
            self._current_bot_label.configure(height=max(1, lines), state=tk.DISABLED)
            self._scroll_bottom()

    def finish_bot(self, text: str):
        """Finalize the bot bubble with the clean full text."""
        if self._current_bot_label:
            self._current_bot_label.configure(state=tk.NORMAL)
            self._current_bot_label.delete("1.0", tk.END)
            self._render_text(self._current_bot_label, text.strip())
            lines = int(self._current_bot_label.index(tk.END).split(".")[0])
            self._current_bot_label.configure(height=max(1, lines), state=tk.DISABLED)
        self._current_bot_frame = None
        self._current_bot_label = None
        self._scroll_bottom()

    def _render_text(self, widget: tk.Text, text: str):
        """Render markdown-ish text with code block highlights."""
        in_code = False
        for line in text.split("\n"):
            if line.startswith("```"):
                in_code = not in_code
                widget.insert(tk.END, line + "\n", "code")
            elif in_code:
                widget.insert(tk.END, line + "\n", "code")
            elif re.match(r"^#{1,3} ", line):
                widget.insert(tk.END, line + "\n", "heading")
            else:
                widget.insert(tk.END, line + "\n")

    def add_tool_indicator(self, tool: str, args: dict, result: str = None):
        outer = tk.Frame(self._inner, bg=BG)
        outer.pack(fill=tk.X, padx=36, pady=1)
        summary = f"  {tool}  " + "  ".join(
            f"{k}: {str(v)[:60]}" for k, v in args.items())
        color = SUCCESS if result and not result.startswith("[ERROR]") else TOOL_BG
        box = tk.Frame(outer, bg=TOOL_BG, padx=10, pady=5)
        box.pack(side=tk.LEFT)
        tk.Label(box, text=f"⚙  {summary}", bg=TOOL_BG, fg=WARNING,
                 font=("Consolas", 9)).pack(anchor="w")
        self._scroll_bottom()

    def add_permission_request(self, tool: str, args: dict,
                               grant_cb, deny_cb):
        """Add an inline permission banner to the chat."""
        outer = tk.Frame(self._inner, bg=BG)
        outer.pack(fill=tk.X, padx=36, pady=4)
        box = tk.Frame(outer, bg=PERM_BG, padx=12, pady=10, relief="flat")
        box.pack(fill=tk.X)

        tk.Label(box, text=f"⚠  Permission needed:  {tool}",
                 bg=PERM_BG, fg=WARNING, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        for k, v in args.items():
            tk.Label(box, text=f"   {k}: {v}", bg=PERM_BG, fg=FG,
                     font=("Consolas", 9), wraplength=560, justify=tk.LEFT).pack(anchor="w")

        btn_row = tk.Frame(box, bg=PERM_BG)
        btn_row.pack(anchor="w", pady=(8, 0))

        _always = [False]

        def _allow():
            outer.destroy()
            grant_cb(_always[0])

        def _deny():
            outer.destroy()
            deny_cb()

        tk.Button(btn_row, text="  Allow  ", bg=SUCCESS, fg="#000",
                  relief="flat", font=("Segoe UI", 9, "bold"),
                  command=_allow).pack(side=tk.LEFT, padx=(0, 6))
        always_var = tk.BooleanVar()
        tk.Checkbutton(btn_row, text="Always allow this session",
                       variable=always_var, bg=PERM_BG, fg=FG_DIM,
                       selectcolor=ENTRY_BG, activebackground=PERM_BG,
                       font=("Segoe UI", 9),
                       command=lambda: _always.__setitem__(0, always_var.get())
                       ).pack(side=tk.LEFT, padx=(0, 12))
        tk.Button(btn_row, text="  Deny  ", bg=DANGER, fg="#000",
                  relief="flat", font=("Segoe UI", 9, "bold"),
                  command=_deny).pack(side=tk.LEFT)

        self._scroll_bottom()

    def add_system(self, text: str, color=FG_DIM):
        outer = tk.Frame(self._inner, bg=BG)
        outer.pack(fill=tk.X, pady=2)
        tk.Label(outer, text=text, bg=BG, fg=color,
                 font=("Segoe UI", 9, "italic")).pack()
        self._scroll_bottom()

    def add_bot(self, text: str):
        """Add a completed bot message (used when loading past chats)."""
        outer = tk.Frame(self._inner, bg=BG)
        outer.pack(fill=tk.X, padx=16, pady=(2, 2))
        icon = tk.Label(outer, text="✦", bg=BG, fg=ACCENT,
                        font=("Segoe UI", 12, "bold"))
        icon.pack(side=tk.LEFT, anchor="n", padx=(0, 8), pady=8)
        bubble = tk.Frame(outer, bg=BOT_BG, padx=14, pady=10)
        bubble.pack(side=tk.LEFT, fill=tk.X, expand=True)
        lbl = tk.Text(bubble, bg=BOT_BG, fg=FG, font=("Segoe UI", 10),
                      relief="flat", wrap=tk.WORD, state=tk.DISABLED,
                      cursor="arrow", height=1, padx=0, pady=0,
                      selectbackground=BTN, selectforeground=FG)
        lbl.pack(fill=tk.X)
        lbl.tag_configure("code",    font=("Consolas", 10), background="#0d0d14",
                          foreground=WARNING)
        lbl.tag_configure("heading", font=("Segoe UI", 11, "bold"), foreground=ACCENT)
        lbl.configure(state=tk.NORMAL)
        self._render_text(lbl, text.strip())
        lines = int(lbl.index(tk.END).split(".")[0])
        lbl.configure(height=max(1, lines), state=tk.DISABLED)
        self._scroll_bottom()

    def reload(self, history: list):
        """Clear and rebuild the chat display from an agent history list."""
        self.clear()
        for msg in history:
            role    = msg.get("role", "")
            content = msg.get("content", "").strip()
            if not content:
                continue
            if role == "user":
                self.add_user(content)
            elif role == "assistant":
                self.add_bot(content)

    def clear(self):
        for w in self._inner.winfo_children():
            w.destroy()


# ── Main window ───────────────────────────────────────────────────────────────
class BennyAI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}  —  Local AI Coding Assistant")
        self.geometry("1280x860")
        self.minsize(960, 660)
        self.configure(bg=BG)

        self.ollama   = OllamaClient()
        self.executor = ToolExecutor()
        self._perm_event    = threading.Event()
        self._perm_result   = (False, False)
        self._agent_busy        = False
        self._task_start        = None
        self._token_count       = 0
        self._got_first_token   = False
        self._pending_interrupt = None   # message queued while agent is busy
        # chat history persistence
        CHATS_DIR.mkdir(parents=True, exist_ok=True)
        self._current_chat_id   = datetime.now().strftime("chat_%Y%m%d_%H%M%S")
        self._current_chat_title = ""  # set from first user message

        self.agent = Agent(
            ollama        = self.ollama,
            executor      = self.executor,
            on_token          = self._on_token,
            on_response_done  = self._on_response_done,
            on_tool_start     = self._on_tool_start,
            on_tool_result    = self._on_tool_result,
            on_permission     = self._on_permission_request,
        )

        self.remote = RemoteServer(inject_fn=self._remote_inject)
        self.remote.start()

        self._apply_style()
        self._build_ui()
        self.after(300, self._check_health)
        self._greet()

    # ── Style ─────────────────────────────────────────────────────────────
    def _apply_style(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("TFrame",       background=BG)
        s.configure("Side.TFrame",  background=SIDEBAR)
        s.configure("TLabel",       background=BG, foreground=FG)
        s.configure("Side.TLabel",  background=SIDEBAR, foreground=FG)
        s.configure("Head.TLabel",  background=SIDEBAR, foreground=ACCENT,
                    font=("Segoe UI", 9, "bold"))
        s.configure("TEntry",       fieldbackground=ENTRY_BG, foreground=FG,
                    insertcolor=FG, bordercolor=BORDER, relief="flat")
        s.configure("TButton",      background=BTN, foreground=FG,
                    borderwidth=0, relief="flat", padding=(10, 5))
        s.map("TButton", background=[("active", BTN_HOV)])
        s.configure("Small.TButton", background=ENTRY_BG, foreground=FG_DIM,
                    borderwidth=0, padding=(6, 3), font=("Segoe UI", 9))
        s.map("Small.TButton", background=[("active", BTN)])
        s.configure("TScrollbar", background=PANEL, troughcolor=BG,
                    bordercolor=BG, arrowcolor=FG_DIM)
        s.configure("Treeview",   background=ENTRY_BG, foreground=FG,
                    fieldbackground=ENTRY_BG, borderwidth=0, rowheight=24)
        s.configure("Treeview.Heading", background=SIDEBAR, foreground=ACCENT,
                    relief="flat")
        s.map("Treeview", background=[("selected", BTN)])
        s.configure("TNotebook",        background=BG,    borderwidth=0)
        s.configure("TNotebook.Tab",    background=PANEL, foreground=FG_DIM,
                    padding=(8, 5))
        s.map("TNotebook.Tab",
              background=[("selected", SIDEBAR)],
              foreground=[("selected", ACCENT)])

    # ── Build UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        self._build_header()
        main = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=BORDER,
                              sashwidth=3, sashpad=0, relief="flat")
        main.pack(fill=tk.BOTH, expand=True)
        main.add(self._build_chat_panel(main),     minsize=560, width=820)
        main.add(self._build_sidebar(main),        minsize=280, width=460)
        self._build_statusbar()

    # ── Header ────────────────────────────────────────────────────────────
    def _build_header(self):
        hdr = tk.Frame(self, bg=PANEL, height=50)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)

        # Logo
        tk.Label(hdr, text=f"  {APP_NAME}", bg=PANEL, fg=ACCENT,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=(4, 0))
        tk.Label(hdr, text=f"v{VERSION}", bg=PANEL, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(4, 20), pady=(10, 0))

        # Status dot
        self._dot = tk.Label(hdr, text="●", bg=PANEL, fg=WARNING,
                             font=("Segoe UI", 12))
        self._dot.pack(side=tk.LEFT)
        self._status_lbl = tk.Label(hdr, text="Connecting…", bg=PANEL, fg=FG_DIM,
                                    font=("Segoe UI", 10))
        self._status_lbl.pack(side=tk.LEFT, padx=(4, 8))
        # Chat size indicator
        self._chat_size_lbl = tk.Label(hdr, text="", bg=PANEL, fg=FG_DIM,
                                       font=("Segoe UI", 8))
        self._chat_size_lbl.pack(side=tk.LEFT, padx=(0, 12))

        # Model selector
        tk.Label(hdr, text="Model:", bg=PANEL, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._model_var = tk.StringVar(value=DEFAULT_MODEL)
        self._model_cb  = ttk.Combobox(hdr, textvariable=self._model_var,
                                        width=28, state="readonly")
        self._model_cb.pack(side=tk.LEFT, padx=(4, 16))
        self._model_cb.bind("<<ComboboxSelected>>", self._on_model_change)

        # Workspace
        tk.Label(hdr, text="Workspace:", bg=PANEL, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._ws_var = tk.StringVar()
        tk.Entry(hdr, textvariable=self._ws_var, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief="flat", font=("Segoe UI", 10),
                 width=32).pack(side=tk.LEFT, padx=(4, 4))
        ttk.Button(hdr, text="Browse", style="Small.TButton",
                   command=self._browse_workspace).pack(side=tk.LEFT, padx=(0, 16))

        # Right side buttons
        tk.Button(hdr, text="⛔  STOP", bg=DANGER, fg="#fff",
                  font=("Segoe UI", 10, "bold"), relief="flat",
                  activebackground="#c0122e", activeforeground="#fff",
                  padx=12, pady=4,
                  command=self._emergency_stop).pack(side=tk.RIGHT, padx=(0, 8), pady=8)

        # Online / Offline toggle
        self._online_btn = tk.Button(
            hdr, text="○  Offline", bg=ENTRY_BG, fg=FG_DIM,
            font=("Segoe UI", 9, "bold"), relief="flat", padx=10, pady=4,
            activebackground=ENTRY_BG, activeforeground=FG,
            command=self._toggle_online)
        self._online_btn.pack(side=tk.RIGHT, padx=(0, 4), pady=8)

        # ⚙ Settings
        tk.Button(hdr, text="⚙ Settings", bg=ENTRY_BG, fg=FG_DIM,
                  font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                  activebackground=BTN, activeforeground=FG,
                  command=self._open_settings
                  ).pack(side=tk.RIGHT, padx=(0, 4), pady=8)

        # 🔬 Benchmark
        tk.Button(hdr, text="🔬 Benchmark", bg=ENTRY_BG, fg=FG_DIM,
                  font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                  activebackground=BTN, activeforeground=FG,
                  command=self._open_benchmark
                  ).pack(side=tk.RIGHT, padx=(0, 4), pady=8)

        # 📦 Transfer
        tk.Button(hdr, text="📦 Transfer", bg=ENTRY_BG, fg=FG_DIM,
                  font=("Segoe UI", 9), relief="flat", padx=10, pady=4,
                  activebackground=BTN, activeforeground=FG,
                  command=self._open_transfer
                  ).pack(side=tk.RIGHT, padx=(0, 4), pady=8)

        ttk.Button(hdr, text="New Chat", style="Small.TButton",
                   command=self._new_chat).pack(side=tk.RIGHT, padx=(0, 4))
        ttk.Button(hdr, text="Clear", style="Small.TButton",
                   command=self._clear_chat).pack(side=tk.RIGHT, padx=(0, 4))

    # ── Chat panel ────────────────────────────────────────────────────────
    def _build_chat_panel(self, parent):
        frame = tk.Frame(parent, bg=BG)

        self._build_gauge_strip(frame)

        self.chat = ChatDisplay(frame)
        self.chat.pack(fill=tk.BOTH, expand=True)

        # Thinking / Typing indicator row (hidden when idle)
        ind_row = tk.Frame(frame, bg=BG, height=22)
        ind_row.pack(fill=tk.X)
        ind_row.pack_propagate(False)
        self._ai_indicator = tk.Label(ind_row, text="", bg=BG,
                                      font=("Segoe UI", 9, "italic"), anchor="w")
        self._ai_indicator.pack(side=tk.LEFT, padx=14)

        # Input area
        inp = tk.Frame(frame, bg=PANEL, pady=10)
        inp.pack(fill=tk.X)
        inp.columnconfigure(0, weight=1)

        self._input = tk.Text(inp, height=3, bg=ENTRY_BG, fg=FG,
                              insertbackground=FG, relief="flat",
                              font=("Segoe UI", 11), wrap=tk.WORD,
                              padx=12, pady=8)
        self._input.grid(row=0, column=0, padx=(12, 8), sticky="ew")
        self._input.bind("<Return>",         self._on_enter)
        self._input.bind("<Control-Return>", lambda e: self._send_message() or "break")

        btn_col = tk.Frame(inp, bg=PANEL)
        btn_col.grid(row=0, column=1, padx=(0, 12))
        ttk.Button(btn_col, text="Send",
                   command=self._send_message).pack(fill=tk.X, pady=(0, 4))
        self._stop_btn = ttk.Button(btn_col, text="⏹ Stop Response",
                                    style="Small.TButton",
                                    command=self._stop_response)
        self._stop_btn.pack(fill=tk.X, pady=(0, 4))
        self._stop_btn.state(["disabled"])
        ttk.Button(btn_col, text="Attach", style="Small.TButton",
                   command=self._attach_file).pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btn_col, text="⛔ Abort All", style="Small.TButton",
                   command=self._stop_agent).pack(fill=tk.X)

        tk.Label(inp,
                 text="Enter to send  •  Shift+Enter for newline  •  Type while generating to interrupt",
                 bg=PANEL, fg=FG_DIM, font=("Segoe UI", 8)).grid(
            row=1, column=0, sticky="w", padx=14, pady=(0, 2))

        return frame

    def _build_gauge_strip(self, parent):
        """Horizontal strip of 4 animated circular gauges."""
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X)
        strip = tk.Frame(parent, bg=PANEL, pady=6)
        strip.pack(fill=tk.X)
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X)

        def gauge(label):
            col = tk.Frame(strip, bg=PANEL)
            col.pack(side=tk.LEFT, expand=True)
            g = GaugeMeter(col, label)
            g.pack(pady=(4, 4))
            return g

        self._gauge_cpu  = gauge("CPU")
        self._gauge_ram  = gauge("RAM")
        self._gauge_gpu  = gauge("GPU")
        self._gauge_vram = gauge("VRAM")

    # ── Sidebar ───────────────────────────────────────────────────────────
    def _build_sidebar(self, parent):
        frame = ttk.Frame(parent, style="Side.TFrame")
        nb = ttk.Notebook(frame)
        nb.pack(fill=tk.BOTH, expand=True)
        nb.add(self._build_terminal_tab(nb),   text=" Terminal ")
        nb.add(self._build_files_tab(nb),      text=" Files ")
        nb.add(self._build_docs_tab(nb),       text=" Docs ")
        nb.add(self._build_library_tab(nb),    text=" Library ")
        nb.add(self._build_chats_tab(nb),      text=" Chats ")
        nb.add(self._build_stats_tab(nb),      text=" Stats ")
        nb.add(self._build_remote_tab(nb),     text=" Remote ")
        return frame

    def _build_terminal_tab(self, nb):
        f = ttk.Frame(nb, style="Side.TFrame")
        self.terminal = TerminalFrame(f)
        self.terminal.pack(fill=tk.BOTH, expand=True)
        return f

    def _build_files_tab(self, nb):
        f = ttk.Frame(nb, style="Side.TFrame")
        tk.Label(f, text="File Browser", bg=SIDEBAR, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 4))

        path_row = tk.Frame(f, bg=SIDEBAR)
        path_row.pack(fill=tk.X, padx=8, pady=(0, 6))
        self._file_path = tk.StringVar()
        tk.Entry(path_row, textvariable=self._file_path, bg=ENTRY_BG,
                 fg=FG, insertbackground=FG, relief="flat",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(path_row, text="Go", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 9), activebackground=BTN_HOV,
                  command=self._browse_files).pack(side=tk.LEFT)

        self._file_tree = ttk.Treeview(f, show="tree", selectmode="browse")
        self._file_tree.pack(fill=tk.BOTH, expand=True, padx=4)
        self._file_tree.bind("<Double-1>", self._on_file_double_click)

        btn_row = tk.Frame(f, bg=SIDEBAR)
        btn_row.pack(fill=tk.X, padx=8, pady=4)
        tk.Button(btn_row, text="Read into chat", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 9), activebackground=BTN_HOV,
                  command=self._read_selected_file).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn_row, text="Ask about it", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 9), activebackground=BTN_HOV,
                  command=self._ask_selected_file).pack(side=tk.LEFT)
        return f

    def _build_docs_tab(self, nb):
        f = ttk.Frame(nb, style="Side.TFrame")
        tk.Label(f, text="Documentation", bg=SIDEBAR, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 4))

        snap_row = tk.Frame(f, bg=SIDEBAR)
        snap_row.pack(fill=tk.X, padx=8, pady=(0, 6))
        tk.Label(snap_row, text="Snapshot:", bg=SIDEBAR, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self._snap_var = tk.StringVar()
        self._snap_cb  = ttk.Combobox(snap_row, textvariable=self._snap_var,
                                       width=18, state="readonly")
        self._snap_cb.pack(side=tk.LEFT, padx=(4, 0))

        btn_col = tk.Frame(f, bg=SIDEBAR)
        btn_col.pack(fill=tk.X, padx=8)
        for label, cmd in [("Generate README", self._gen_readme),
                            ("Generate API Docs", self._gen_api_docs),
                            ("Refresh Snapshots", self._refresh_snaps)]:
            tk.Button(btn_col, text=label, bg=BTN, fg=FG, relief="flat",
                      font=("Segoe UI", 9), activebackground=BTN_HOV,
                      command=cmd, anchor="w").pack(fill=tk.X, pady=2)

        tk.Label(f, text="Output:", bg=SIDEBAR, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(10, 2))
        self.docs_out = scrolledtext.ScrolledText(
            f, bg=TERM_BG, fg=FG, font=("Consolas", 9), relief="flat",
            wrap=tk.WORD, state=tk.DISABLED)
        self.docs_out.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        btn2 = tk.Frame(f, bg=SIDEBAR)
        btn2.pack(fill=tk.X, padx=8, pady=4)
        tk.Button(btn2, text="Copy", bg=ENTRY_BG, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 9), command=self._copy_docs).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(btn2, text="Save As...", bg=ENTRY_BG, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 9), command=self._save_docs).pack(side=tk.LEFT)
        return f

    def _build_remote_tab(self, nb):
        f = ttk.Frame(nb, style="Side.TFrame")
        tk.Label(f, text="Remote Control (Claude)", bg=SIDEBAR, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 4))

        info = tk.Text(f, bg=ENTRY_BG, fg=FG_DIM, relief="flat",
                       font=("Consolas", 9), wrap=tk.WORD, height=10, state=tk.NORMAL)
        info.insert(tk.END,
            f"BennyAI is listening on port {REMOTE_PORT}.\n"
            f"Claude can inject messages via:\n\n"
            f"POST http://127.0.0.1:{REMOTE_PORT}/inject\n"
            f"Body: {{\"message\": \"...\", \"role\": \"user\"}}\n\n"
            f"This lets Claude Code drive BennyAI\n"
            f"to instruct DeepSeek on your behalf.\n\n"
            f"GET  http://127.0.0.1:{REMOTE_PORT}/\n"
            f"Returns status JSON."
        )
        info.configure(state=tk.DISABLED)
        info.pack(fill=tk.X, padx=8, pady=(0, 8))

        tk.Label(f, text="Inject message manually:", bg=SIDEBAR, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=10)
        self._inject_text = tk.Text(f, height=4, bg=ENTRY_BG, fg=FG,
                                    insertbackground=FG, relief="flat",
                                    font=("Segoe UI", 10), wrap=tk.WORD)
        self._inject_text.pack(fill=tk.X, padx=8, pady=4)
        tk.Button(f, text="Inject as User", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 9), activebackground=BTN_HOV,
                  command=lambda: self._remote_inject(
                      self._inject_text.get("1.0", tk.END).strip(), "user")
                  ).pack(anchor="w", padx=8, pady=(0, 4))

        self._remote_log = scrolledtext.ScrolledText(
            f, bg=TERM_BG, fg=FG_DIM, font=("Consolas", 8),
            relief="flat", wrap=tk.WORD, height=8, state=tk.DISABLED)
        self._remote_log.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        return f

    # ── Chat persistence helpers ──────────────────────────────────────────
    def _chat_index_path(self):
        return CHATS_DIR / "index.json"

    def _chat_load_index(self) -> list:
        p = self._chat_index_path()
        if not p.exists():
            return []
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _chat_save_index(self, index: list):
        self._chat_index_path().write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")

    def _chat_save(self):
        """Persist current agent.history to disk and update the index."""
        history = self.agent.history
        if not history:
            return
        # Auto-title from first user message
        if not self._current_chat_title:
            first_user = next((m["content"] for m in history if m["role"] == "user"), "")
            self._current_chat_title = first_user[:50].replace("\n", " ") or "Untitled"

        total_chars = sum(len(m.get("content", "")) for m in history)
        fname       = f"{self._current_chat_id}.json"
        fpath       = CHATS_DIR / fname
        data        = {
            "id":       self._current_chat_id,
            "title":    self._current_chat_title,
            "messages": history,
        }
        fpath.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                         encoding="utf-8")

        # Update index
        index = self._chat_load_index()
        entry = next((e for e in index if e["id"] == self._current_chat_id), None)
        now   = datetime.now().isoformat(timespec="seconds")
        meta  = {
            "id":          self._current_chat_id,
            "title":       self._current_chat_title,
            "created":     entry["created"] if entry else now,
            "updated":     now,
            "msg_count":   len(history),
            "total_chars": total_chars,
            "file":        fname,
        }
        if entry:
            index = [meta if e["id"] == self._current_chat_id else e for e in index]
        else:
            index.insert(0, meta)
        self._chat_save_index(index)
        self._update_chat_size_label()

    def _update_chat_size_label(self):
        history = self.agent.history
        msgs    = len(history)
        chars   = sum(len(m.get("content", "")) for m in history)
        if msgs:
            self._chat_size_lbl.configure(
                text=f"💬 {msgs} msg{'s' if msgs != 1 else ''}  ·  {chars//1000:.1f}K chars")
        else:
            self._chat_size_lbl.configure(text="")

    def _new_chat(self):
        """Save current chat and start a fresh session."""
        self._chat_save()
        self.agent.reset()
        self.chat.clear()
        self._current_chat_id    = datetime.now().strftime("chat_%Y%m%d_%H%M%S")
        self._current_chat_title = ""
        self._update_chat_size_label()
        self.chat.add_system("New chat started.", FG_DIM)
        if hasattr(self, "_chats_list"):
            self.after(200, self._chats_refresh)

    def _chat_switch(self, chat_id: str):
        """Save current chat then load and display the selected one."""
        if self._agent_busy:
            self.chat.add_system("Cannot switch chats while BennyAI is responding.", DANGER)
            return
        self._chat_save()
        index = self._chat_load_index()
        entry = next((e for e in index if e["id"] == chat_id), None)
        if not entry:
            return
        fpath = CHATS_DIR / entry["file"]
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as e:
            self.chat.add_system(f"Could not load chat: {e}", DANGER)
            return
        self.agent.history            = data.get("messages", [])
        self._current_chat_id         = chat_id
        self._current_chat_title      = entry["title"]
        self.chat.reload(self.agent.history)
        self._update_chat_size_label()
        self.chat.add_system(f"Switched to: {entry['title']}", FG_DIM)

    def _chat_delete(self, chat_id: str):
        index = self._chat_load_index()
        entry = next((e for e in index if e["id"] == chat_id), None)
        if not entry:
            return
        try:
            (CHATS_DIR / entry["file"]).unlink(missing_ok=True)
        except Exception:
            pass
        index = [e for e in index if e["id"] != chat_id]
        self._chat_save_index(index)
        # If we deleted the current chat, start fresh
        if chat_id == self._current_chat_id:
            self.agent.reset()
            self.chat.clear()
            self._current_chat_id    = datetime.now().strftime("chat_%Y%m%d_%H%M%S")
            self._current_chat_title = ""
            self._update_chat_size_label()
        self._chats_refresh()

    # ── Chats tab ─────────────────────────────────────────────────────────
    def _build_chats_tab(self, nb):
        f = ttk.Frame(nb, style="Side.TFrame")

        # Header row
        hdr = tk.Frame(f, bg=SIDEBAR, pady=6)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="  💬 Chat History", bg=SIDEBAR, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        tk.Button(hdr, text="+ New", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 8),
                  command=self._new_chat).pack(side=tk.RIGHT, padx=6)
        tk.Button(hdr, text="⟳", bg=SIDEBAR, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 10),
                  command=self._chats_refresh).pack(side=tk.RIGHT, padx=(0, 2))

        # Size info strip
        self._chats_info = tk.Label(f, text="", bg=SIDEBAR, fg=FG_DIM,
                                    font=("Segoe UI", 8))
        self._chats_info.pack(fill=tk.X, padx=8, pady=(0, 4))

        tk.Frame(f, bg=BORDER, height=1).pack(fill=tk.X)

        # Scrollable chat list
        container = tk.Frame(f, bg=BG)
        container.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        sb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._chats_inner = tk.Frame(canvas, bg=BG)
        win = canvas.create_window((0, 0), window=self._chats_inner, anchor="nw")
        self._chats_inner.bind("<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win, width=e.width))
        canvas.bind_all("<MouseWheel>",
            lambda e: canvas.yview_scroll(int(-1 * e.delta / 120), "units"))
        self._chats_canvas = canvas
        self._chats_list   = True  # sentinel so _new_chat knows tab exists

        f.after(300, self._chats_refresh)
        return f

    def _chats_refresh(self):
        if not hasattr(self, "_chats_inner"):
            return
        for w in self._chats_inner.winfo_children():
            w.destroy()
        index = self._chat_load_index()
        total_chars = sum(e.get("total_chars", 0) for e in index)
        self._chats_info.configure(
            text=f"{len(index)} saved chats  ·  {total_chars//1000:.1f}K total")

        if not index:
            tk.Label(self._chats_inner, text="\n  No saved chats yet.\n  Messages auto-save after each exchange.",
                     bg=BG, fg=FG_DIM, font=("Segoe UI", 9),
                     justify=tk.LEFT).pack(anchor="w", padx=10)
            return

        for entry in sorted(index, key=lambda x: x.get("updated", ""), reverse=True):
            is_current = entry["id"] == self._current_chat_id
            row_bg = SIDEBAR if is_current else ENTRY_BG
            row = tk.Frame(self._chats_inner, bg=row_bg,
                           padx=8, pady=6, relief="flat")
            row.pack(fill=tk.X, padx=4, pady=2)

            # Title
            title_color = ACCENT if is_current else FG_BRIGHT
            tk.Label(row, text=entry["title"][:44], bg=row_bg, fg=title_color,
                     font=("Segoe UI", 9, "bold" if is_current else "normal"),
                     anchor="w").pack(fill=tk.X)

            # Meta: date, msg count, size
            chars_k = entry.get("total_chars", 0) / 1000
            meta = (f"  {entry.get('updated','')[:16]}  ·  "
                    f"{entry.get('msg_count', 0)} msgs  ·  {chars_k:.1f}K")
            tk.Label(row, text=meta, bg=row_bg, fg=FG_DIM,
                     font=("Segoe UI", 7)).pack(fill=tk.X)

            # Buttons
            btn_row = tk.Frame(row, bg=row_bg)
            btn_row.pack(anchor="w", pady=(4, 0))
            if not is_current:
                tk.Button(btn_row, text="Switch →", bg=BTN, fg=FG,
                          relief="flat", font=("Segoe UI", 8),
                          command=lambda cid=entry["id"]: self._chat_switch(cid)
                          ).pack(side=tk.LEFT, padx=(0, 4))
            else:
                tk.Label(btn_row, text="● current", bg=row_bg, fg=SUCCESS,
                         font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(0, 8))
            tk.Button(btn_row, text="🗑 Delete", bg=ENTRY_BG if is_current else BG,
                      fg=DANGER, relief="flat", font=("Segoe UI", 8),
                      command=lambda cid=entry["id"]: self._confirm_delete_chat(cid)
                      ).pack(side=tk.LEFT)

    def _confirm_delete_chat(self, chat_id: str):
        index  = self._chat_load_index()
        entry  = next((e for e in index if e["id"] == chat_id), None)
        title  = entry["title"] if entry else chat_id
        answer = messagebox.askyesno(
            "Delete Chat",
            f"Delete \"{title}\"?\n\nThis cannot be undone.",
            parent=self)
        if answer:
            self._chat_delete(chat_id)

    # ── Library tab ───────────────────────────────────────────────────────
    def _build_library_tab(self, nb):
        f = ttk.Frame(nb, style="Side.TFrame")

        # Header
        hdr = tk.Frame(f, bg=SIDEBAR, pady=6)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="  📚 Knowledge Library", bg=SIDEBAR, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        tk.Button(hdr, text="⟳", bg=SIDEBAR, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 10),
                  command=self._library_refresh).pack(side=tk.RIGHT, padx=6)

        # Search row
        srch = tk.Frame(f, bg=SIDEBAR)
        srch.pack(fill=tk.X, padx=6, pady=(0, 4))
        self._lib_search_var = tk.StringVar()
        tk.Entry(srch, textvariable=self._lib_search_var, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief="flat",
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(srch, text="Search", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 8),
                  command=self._library_search).pack(side=tk.LEFT)

        # Download row
        dl = tk.Frame(f, bg=SIDEBAR)
        dl.pack(fill=tk.X, padx=6, pady=(0, 6))
        self._lib_url_var = tk.StringVar()
        tk.Entry(dl, textvariable=self._lib_url_var, bg=ENTRY_BG, fg=FG,
                 insertbackground=FG, relief="flat",
                 font=("Segoe UI", 8),
                 ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(dl, text="⬇ Download", bg=BTN, fg=FG, relief="flat",
                  font=("Segoe UI", 8),
                  command=self._library_download_url).pack(side=tk.LEFT)

        # Quick-download buttons
        quick_frame = tk.Frame(f, bg=SIDEBAR)
        quick_frame.pack(fill=tk.X, padx=6, pady=(0, 4))
        tk.Label(quick_frame, text="Quick add:", bg=SIDEBAR, fg=FG_DIM,
                 font=("Segoe UI", 8)).pack(anchor="w")
        quick_row = tk.Frame(quick_frame, bg=SIDEBAR)
        quick_row.pack(fill=tk.X)
        QUICK_DOCS = [
            ("Python",    "https://docs.python.org/3/tutorial/index.html", "docs"),
            ("MDN JS",    "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide", "docs"),
            ("Git",       "https://git-scm.com/book/en/v2", "docs"),
            ("SQL",       "https://www.w3schools.com/sql/default.asp", "docs"),
            ("React",     "https://react.dev/learn", "docs"),
            ("Docker",    "https://docs.docker.com/get-started/overview/", "docs"),
            ("Free Books","https://raw.githubusercontent.com/EbookFoundation/free-programming-books/main/books/free-programming-books-langs.md", "books"),
        ]
        for i, (label, url, cat) in enumerate(QUICK_DOCS):
            tk.Button(quick_row, text=label, bg=ENTRY_BG, fg=FG_DIM,
                      relief="flat", font=("Segoe UI", 7),
                      command=lambda u=url, l=label, c=cat:
                          self._library_download_quick(u, l, c)
                      ).grid(row=i//4, column=i%4, padx=1, pady=1, sticky="ew")
        for col in range(4):
            quick_row.columnconfigure(col, weight=1)

        tk.Frame(f, bg=BORDER, height=1).pack(fill=tk.X, padx=6, pady=2)

        # List
        self._lib_list = tk.Text(f, bg=BG, fg=FG, relief="flat",
                                 font=("Consolas", 8), state=tk.DISABLED,
                                 cursor="arrow", wrap=tk.NONE)
        lib_sb = ttk.Scrollbar(f, orient=tk.VERTICAL,
                               command=self._lib_list.yview)
        self._lib_list.configure(yscrollcommand=lib_sb.set)
        lib_sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._lib_list.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # Status label
        self._lib_status = tk.Label(f, text="", bg=SIDEBAR, fg=FG_DIM,
                                    font=("Segoe UI", 8))
        self._lib_status.pack(anchor="w", padx=6, pady=(0, 4))

        # Load on first render
        f.after(300, self._library_refresh)
        return f

    def _library_refresh(self):
        index = self.executor._lib_load()
        self._lib_list.configure(state=tk.NORMAL)
        self._lib_list.delete("1.0", tk.END)
        if not index:
            self._lib_list.insert(tk.END, "  Library is empty.\n\n"
                                  "  Use the download bar above or Quick Add\n"
                                  "  to fetch documentation and books.\n\n"
                                  "  You can also just ask BennyAI:\n"
                                  "  \"Download the Python tutorial for me\"")
        else:
            for item in sorted(index, key=lambda x: x["date"], reverse=True):
                self._lib_list.insert(
                    tk.END,
                    f"[{item['id']:>3}] {item['title'][:45]}\n"
                    f"       {item['category']:<10}  {item['chars']//1000}K  "
                    f"{item['date'][:10]}\n\n")
        self._lib_list.configure(state=tk.DISABLED)
        self._lib_status.configure(text=f"{len(index)} items  •  "
                                   f"{sum(i['chars'] for i in index)//1000}K total chars")

    def _library_search(self):
        q = self._lib_search_var.get().strip()
        if not q:
            self._library_refresh()
            return
        result = self.executor._t_search_library(query=q)
        self._lib_list.configure(state=tk.NORMAL)
        self._lib_list.delete("1.0", tk.END)
        self._lib_list.insert(tk.END, result)
        self._lib_list.configure(state=tk.DISABLED)

    def _library_download_url(self):
        url = self._lib_url_var.get().strip()
        if not url:
            return
        title = url.split("/")[-1] or url[:40]
        self._library_do_download(url, title, "general")

    def _library_download_quick(self, url: str, label: str, category: str):
        self._library_do_download(url, label, category)

    def _library_do_download(self, url: str, title: str, category: str):
        if not self.executor.online:
            self._lib_status.configure(
                text="⚠ Online mode is OFF — enable it first", fg=WARNING)
            return
        self._lib_status.configure(text=f"⬇ Downloading {title}…", fg=ACCENT)
        def _run():
            result = self.executor._t_download_resource(
                url=url, title=title, category=category)
            def _done():
                if "[ERROR]" in result:
                    self._lib_status.configure(text=result[:80], fg=DANGER)
                else:
                    self._lib_status.configure(
                        text=f"✓ {title} saved", fg=SUCCESS)
                self._library_refresh()
            self.after(0, _done)
        threading.Thread(target=_run, daemon=True).start()

    # ── Stats tab ─────────────────────────────────────────────────────────
    def _build_stats_tab(self, nb):
        f = ttk.Frame(nb, style="Side.TFrame")

        tk.Label(f, text="Performance", bg=SIDEBAR, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(8, 6))

        def bar_row(parent, label):
            row = tk.Frame(parent, bg=SIDEBAR)
            row.pack(fill=tk.X, padx=10, pady=3)
            tk.Label(row, text=f"{label:<6}", bg=SIDEBAR, fg=FG_DIM,
                     font=("Consolas", 9), width=6).pack(side=tk.LEFT)
            canvas = tk.Canvas(row, bg=ENTRY_BG, height=14, highlightthickness=0)
            canvas.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
            pct_lbl = tk.Label(row, text="  0%", bg=SIDEBAR, fg=FG,
                               font=("Consolas", 9), width=5)
            pct_lbl.pack(side=tk.LEFT)
            return canvas, pct_lbl

        self._cpu_bar, self._cpu_lbl  = bar_row(f, "CPU")
        self._ram_bar, self._ram_lbl  = bar_row(f, "RAM")
        self._gpu_bar, self._gpu_lbl  = bar_row(f, "GPU")
        self._vram_bar,self._vram_lbl = bar_row(f, "VRAM")

        sep = tk.Frame(f, bg=BORDER, height=1)
        sep.pack(fill=tk.X, padx=10, pady=8)

        tk.Label(f, text="Current Task", bg=SIDEBAR, fg=ACCENT,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(0, 4))

        info_frame = tk.Frame(f, bg=SIDEBAR)
        info_frame.pack(fill=tk.X, padx=10)

        def info_row(label):
            r = tk.Frame(info_frame, bg=SIDEBAR)
            r.pack(fill=tk.X, pady=1)
            tk.Label(r, text=f"{label}:", bg=SIDEBAR, fg=FG_DIM,
                     font=("Segoe UI", 9), width=10, anchor="w").pack(side=tk.LEFT)
            v = tk.StringVar(value="—")
            tk.Label(r, textvariable=v, bg=SIDEBAR, fg=FG,
                     font=("Consolas", 9)).pack(side=tk.LEFT)
            return v

        self._stat_status   = info_row("Status")
        self._stat_elapsed  = info_row("Elapsed")
        self._stat_tokens   = info_row("Tok/sec")
        self._stat_started  = info_row("Started")

        sep2 = tk.Frame(f, bg=BORDER, height=1)
        sep2.pack(fill=tk.X, padx=10, pady=8)
        tk.Button(f, text="Refresh Now", bg=ENTRY_BG, fg=FG_DIM, relief="flat",
                  font=("Segoe UI", 9), activebackground=BTN, activeforeground=FG,
                  command=self._update_stats).pack(anchor="w", padx=10, pady=(0, 8))

        self._task_start    = None
        self._token_count   = 0
        self._update_stats()
        return f

    def _fill_bar(self, canvas, pct_lbl, pct: float, color: str):
        canvas.update_idletasks()
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        canvas.delete("all")
        fill_w = max(0, int(w * pct / 100))
        if fill_w:
            canvas.create_rectangle(0, 0, fill_w, h, fill=color, outline="")
        pct_lbl.configure(text=f"{pct:3.0f}%")

    def _update_stats(self):
        """Collect system + app metrics in a bg thread, push to gauges on main thread."""
        def _collect():
            app_cpu = app_ram = 0.0
            cpu = ram = 0.0

            # ── System + BennyAI process CPU / RAM ───────────────────────
            try:
                import psutil, os as _os
                cpu = psutil.cpu_percent(interval=0.5)
                vm  = psutil.virtual_memory()
                ram = vm.percent

                proc       = psutil.Process(_os.getpid())
                app_cpu    = min(proc.cpu_percent(interval=None), 100.0)
                app_ram    = proc.memory_info().rss / vm.total * 100
            except Exception:
                app_ram = 0.0

            # ── GPU total + Ollama VRAM via nvidia-smi ────────────────────
            gpu_util = vram_pct = ollama_vram_pct = 0.0
            vram_total = 0.0
            try:
                r = subprocess.run(
                    ["nvidia-smi",
                     "--query-gpu=utilization.gpu,memory.used,memory.total",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=4,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    parts      = [p.strip() for p in r.stdout.strip().split(",")]
                    gpu_util   = float(parts[0])
                    vram_used  = float(parts[1])
                    vram_total = float(parts[2])
                    vram_pct   = (vram_used / vram_total * 100) if vram_total else 0
            except Exception:
                pass

            # Ollama's own VRAM (compute apps query)
            try:
                r2 = subprocess.run(
                    ["nvidia-smi",
                     "--query-compute-apps=pid,process_name,used_gpu_memory",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=4,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                if r2.returncode == 0 and vram_total > 0:
                    ollama_mb = 0.0
                    for line in r2.stdout.strip().splitlines():
                        cols = [c.strip() for c in line.split(",")]
                        if len(cols) >= 3 and "ollama" in cols[1].lower():
                            try: ollama_mb += float(cols[2])
                            except ValueError: pass
                    ollama_vram_pct = (ollama_mb / vram_total * 100) if ollama_mb else 0
            except Exception:
                pass

            self.after(0, self._apply_stats,
                       cpu, app_cpu, ram, app_ram,
                       gpu_util, ollama_vram_pct, vram_pct)

        threading.Thread(target=_collect, daemon=True).start()
        self.after(2000, self._update_stats)

    def _apply_stats(self, cpu, app_cpu, ram, app_ram,
                     gpu, ollama_vram, vram):
        """Called on main thread with fresh metrics."""
        # ── Sidebar flat bars (system total only) ─────────────────────────
        self._fill_bar(self._cpu_bar,  self._cpu_lbl,  cpu,
                       SUCCESS if cpu  < 70 else WARNING if cpu  < 90 else DANGER)
        self._fill_bar(self._ram_bar,  self._ram_lbl,  ram,
                       ACCENT  if ram  < 70 else WARNING if ram  < 90 else DANGER)
        self._fill_bar(self._gpu_bar,  self._gpu_lbl,  gpu,
                       SUCCESS if gpu  < 80 else WARNING)
        self._fill_bar(self._vram_bar, self._vram_lbl, vram,
                       ACCENT2 if vram < 80 else DANGER)

        # ── Main panel gauges — outer=system, inner=app ───────────────────
        self._gauge_cpu.rev(cpu,  app_cpu)
        self._gauge_ram.rev(ram,  app_ram)
        self._gauge_gpu.rev(gpu,  gpu)          # no per-process GPU util, reuse total
        self._gauge_vram.rev(vram, ollama_vram)

        # ── Task timing ───────────────────────────────────────────────────
        if self._agent_busy and self._task_start:
            elapsed = time.time() - self._task_start
            mins, secs = divmod(int(elapsed), 60)
            self._stat_elapsed.set(f"{mins:02d}:{secs:02d}")
            self._stat_status.set("Generating…")
            tok_rate = self._token_count / max(elapsed, 1)
            self._stat_tokens.set(f"~{tok_rate:.1f}")
        else:
            self._stat_status.set("Idle")
            if not self._agent_busy:
                self._stat_elapsed.set("—")

    # ── Status bar ────────────────────────────────────────────────────────
    def _build_statusbar(self):
        bar = tk.Frame(self, bg=PANEL, height=28)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        bar.pack_propagate(False)
        self._sb_var = tk.StringVar(value="Ready")
        tk.Label(bar, textvariable=self._sb_var, bg=PANEL, fg=FG_DIM,
                 font=("Segoe UI", 9), anchor="w").pack(side=tk.LEFT, padx=10)
        tk.Label(bar, text=f"Remote: port {REMOTE_PORT}", bg=PANEL, fg=FG_DIM,
                 font=("Segoe UI", 9)).pack(side=tk.RIGHT, padx=10)
        self._busy_lbl = tk.Label(bar, text="", bg=PANEL, fg=WARNING,
                                  font=("Segoe UI", 9))
        self._busy_lbl.pack(side=tk.RIGHT, padx=10)

    # ── Agent callbacks (called from bg thread → marshal to main thread) ──
    def _on_token(self, token: str):
        self._token_count += 1
        if not self._got_first_token:
            self._got_first_token = True
            self.after(0, self._ai_indicator.configure,
                       {"text": "● Typing…", "fg": ACCENT})
            self.after(0, self._busy_lbl.configure, {"text": "● Typing…"})
        self.after(0, self.chat.append_bot_token, token)

    def _on_response_done(self, text: str):
        self.after(0, self._finish_response, text)

    def _finish_response(self, text: str):
        self.chat.finish_bot(text)
        # auto-save and refresh chats panel
        threading.Thread(target=self._chat_save, daemon=True).start()
        self.after(400, self._update_chat_size_label)
        if hasattr(self, "_chats_inner"):
            self.after(600, self._chats_refresh)

    def _on_tool_start(self, tool: str, args: dict):
        self.after(0, self.chat.add_tool_indicator, tool, args)
        self.after(0, self._set_status, f"Using tool: {tool}…")

    def _on_tool_result(self, tool: str, result: str):
        self.after(0, self._set_status, f"Tool {tool} done")

    def _on_permission_request(self, tool: str, args: dict) -> tuple:
        """Called from bg thread. Blocks until user responds."""
        self._perm_event.clear()
        self._perm_result = (False, False)
        self.after(0, self._show_permission, tool, args)
        self._perm_event.wait(timeout=300)
        return self._perm_result

    def _show_permission(self, tool: str, args: dict):
        def grant(always: bool):
            self._perm_result = (True, always)
            self._perm_event.set()

        def deny():
            self._perm_result = (False, False)
            self._perm_event.set()

        self.chat.add_permission_request(tool, args, grant_cb=grant, deny_cb=deny)

    # ── Send message ──────────────────────────────────────────────────────
    def _on_enter(self, event):
        if event.state & 0x1:  # Shift held
            return
        self._send_message()
        return "break"

    def _send_message(self):
        text = self._input.get("1.0", tk.END).strip()
        if not text:
            return
        self._input.delete("1.0", tk.END)
        if self._agent_busy:
            self._queue_interrupt(text)
        else:
            self._dispatch(text)

    def _queue_interrupt(self, text: str):
        """User typed while agent was generating — stop stream and redirect."""
        self._pending_interrupt = text
        # Show a brief notice only; _dispatch will add the user bubble
        self.chat.add_system(
            "↩ Interrupt received — stopping current response and redirecting…",
            ACCENT)
        self._ai_indicator.configure(text="↩ Interrupting…", fg=ACCENT2)
        self.agent.stop()
        self._agent_busy = False
        self._busy_lbl.configure(text="")
        self._stop_btn.state(["disabled"])
        self.chat.finish_bot("[interrupted]")
        self._perm_event.set()
        self.after(200, self._dispatch_pending_interrupt)

    def _dispatch_pending_interrupt(self):
        if self._pending_interrupt is None:
            return
        if self._agent_busy:
            self.after(150, self._dispatch_pending_interrupt)
            return
        msg = self._pending_interrupt
        self._pending_interrupt = None
        self._ai_indicator.configure(text="")
        # Send the user's message naturally — no wrapper that triggers instruction templates
        self._dispatch(msg)

    def _dispatch(self, text: str):
        self._agent_busy      = True
        self._task_start      = time.time()
        self._token_count     = 0
        self._got_first_token = False
        self._busy_lbl.configure(text="● Thinking…")
        self._ai_indicator.configure(text="● Thinking…", fg=WARNING)
        self._stop_btn.state(["!disabled"])
        self.agent.workspace = self._ws_var.get()
        if hasattr(self, "_stat_started"):
            self._stat_started.set(datetime.now().strftime("%H:%M:%S"))
        self.chat.add_user(text)
        self.chat.start_bot()

        def run():
            try:
                self.agent.send(text)
            except Exception as e:
                self.after(0, self.chat.finish_bot, f"[Error: {e}]")
            finally:
                self.after(0, self._set_idle)

        threading.Thread(target=run, daemon=True).start()

    def _set_idle(self):
        self._agent_busy = False
        self._busy_lbl.configure(text="")
        self._ai_indicator.configure(text="")
        self._stop_btn.state(["disabled"])
        self._set_status("Ready")
        # Dispatch any interrupt that arrived while we were generating
        if self._pending_interrupt:
            self.after(100, self._dispatch_pending_interrupt)

    def _stop_response(self):
        """Stop the current streaming response and immediately unlock the UI."""
        self.agent.stop()
        # Unlock immediately — bg thread is blocked on iter_lines() and can't
        # call _set_idle itself until the network read times out or unblocks.
        self._agent_busy = False
        self._busy_lbl.configure(text="")
        self._ai_indicator.configure(text="")
        self._stop_btn.state(["disabled"])
        self._perm_event.set()
        self.chat.finish_bot("[stopped]")
        self._set_status("Ready")

    def _stop_agent(self):
        """Abort all — kills the agent loop and unblocks permission gate."""
        self.agent.stop()
        self._agent_busy = False
        self._busy_lbl.configure(text="")
        self._ai_indicator.configure(text="")
        self._stop_btn.state(["disabled"])
        self._perm_event.set()
        self.chat.finish_bot("[Stopped by user]")

    def _emergency_stop(self):
        """Hard stop — kills agent, unblocks permission, resets state."""
        self.agent.stop()
        self._agent_busy = False
        self._busy_lbl.configure(text="")
        self._ai_indicator.configure(text="")
        self._stop_btn.state(["disabled"])
        self._perm_event.set()
        self.chat.finish_bot("[⛔ Emergency stop]")
        self.chat.add_system("Agent stopped. Chat history kept. Start a new message to continue.", WARNING)
        self._set_status("Stopped")

    def _open_settings(self):
        def _applied():
            o = self.ollama.options
            self.chat.add_system(
                f"Settings applied — temp:{o['temperature']}  "
                f"max_tokens:{o['num_predict']}  ctx:{o['num_ctx']}  "
                f"gpu_layers:{o['num_gpu']}", FG_DIM)
        SettingsDialog(self, self.ollama, on_apply=_applied)

    def _open_benchmark(self):
        dlg = BenchmarkDialog(
            self, self.ollama,
            terminal=self.terminal,
            on_model_change=self._on_model_switched)
        dlg.chat_note = lambda t: self.chat.add_system(t, FG_DIM)

    def _open_transfer(self):
        TransferDialog(self, self.ollama)

    def _on_model_switched(self, name: str):
        self._model_var.set(name)
        self.chat.add_system(f"Switched model to {name}", SUCCESS)
        self._set_status(f"Model: {name}")

    def _toggle_online(self):
        """Switch between offline and online mode."""
        self.executor.online = not self.executor.online
        if self.executor.online:
            self._online_btn.configure(
                text="●  Online", bg="#0a3020", fg=SUCCESS,
                activebackground="#0a3020")
            self.chat.add_system(
                "Online mode enabled — BennyAI can now search the web, fetch URLs and read GitHub repos.",
                SUCCESS)
            self._set_status("Online mode active")
        else:
            self._online_btn.configure(
                text="○  Offline", bg=ENTRY_BG, fg=FG_DIM,
                activebackground=ENTRY_BG)
            self.chat.add_system(
                "Offline mode — web tools disabled. Only local tools available.",
                FG_DIM)
            self._set_status("Offline mode")

    # ── File actions ──────────────────────────────────────────────────────
    def _attach_file(self):
        path = filedialog.askopenfilename(title="Attach file")
        if not path:
            return
        try:
            content = Path(path).read_text(encoding="utf-8", errors="replace")[:8000]
            cur = self._input.get("1.0", tk.END).strip()
            self._input.delete("1.0", tk.END)
            self._input.insert(tk.END, (cur + "\n\n" if cur else "") +
                               f"[file: {path}]\n```\n{content}\n```")
        except Exception as e:
            messagebox.showerror("Read error", str(e))

    def _browse_workspace(self):
        d = filedialog.askdirectory(title="Set workspace root")
        if d:
            self._ws_var.set(d)
            self.agent.workspace = d
            self._populate_file_tree(d)

    def _browse_files(self):
        p = self._file_path.get().strip()
        if not p:
            p = filedialog.askdirectory(title="Browse directory")
        if p:
            self._file_path.set(p)
            self._populate_file_tree(p)

    def _populate_file_tree(self, path: str):
        self._file_tree.delete(*self._file_tree.get_children())
        try:
            for item in sorted(Path(path).iterdir()):
                tag = "dir" if item.is_dir() else "file"
                prefix = "[D] " if item.is_dir() else "    "
                iid = self._file_tree.insert("", tk.END,
                                             text=f"{prefix}{item.name}",
                                             values=[str(item)], tags=[tag])
        except Exception:
            pass
        self._file_path.set(path)

    def _on_file_double_click(self, _):
        sel = self._file_tree.selection()
        if not sel:
            return
        vals = self._file_tree.item(sel[0], "values")
        if vals:
            p = Path(vals[0])
            if p.is_dir():
                self._populate_file_tree(str(p))
            else:
                self._ask_selected_file()

    def _read_selected_file(self):
        sel = self._file_tree.selection()
        if not sel:
            return
        vals = self._file_tree.item(sel[0], "values")
        if vals and Path(vals[0]).is_file():
            path = vals[0]
            try:
                content = Path(path).read_text(encoding="utf-8", errors="replace")[:6000]
                self._input.delete("1.0", tk.END)
                self._input.insert(tk.END, f"[file: {path}]\n```\n{content}\n```")
            except Exception as e:
                messagebox.showerror("Error", str(e))

    def _ask_selected_file(self):
        sel = self._file_tree.selection()
        if not sel:
            return
        vals = self._file_tree.item(sel[0], "values")
        if vals and Path(vals[0]).is_file():
            self._dispatch(f"Explain what this file does: {vals[0]}")

    # ── Docs tab ──────────────────────────────────────────────────────────
    def _refresh_snaps(self):
        try:
            r = requests.get(f"{DOCBOT_BASE}/snapshots", timeout=5)
            snaps = list(r.json().keys()) if r.ok else []
            self._snap_cb["values"] = snaps
            if snaps and not self._snap_var.get():
                self._snap_var.set(snaps[0])
        except Exception:
            pass

    def _gen_readme(self):
        label = self._snap_var.get()
        if not label:
            messagebox.showwarning("No snapshot", "Scan a codebase first.")
            return
        self._set_status(f"Generating README for {label}…")
        self._write_docs("Generating…")
        threading.Thread(target=self._readme_bg, args=(label,), daemon=True).start()

    def _readme_bg(self, label):
        try:
            r = requests.post(f"{DOCBOT_BASE}/generate/readme/{label}", timeout=600)
            self.after(0, self._write_docs, r.json()["readme"])
        except Exception as e:
            self.after(0, self._write_docs, f"[Error] {e}")
        self.after(0, self._set_status, "Done")

    def _gen_api_docs(self):
        label = self._snap_var.get()
        if not label:
            messagebox.showwarning("No snapshot", "Scan a codebase first.")
            return
        self._set_status(f"Generating API docs for {label}…")
        self._write_docs("Generating…")
        threading.Thread(target=self._api_docs_bg, args=(label,), daemon=True).start()

    def _api_docs_bg(self, label):
        try:
            r = requests.post(f"{DOCBOT_BASE}/generate/api-docs/{label}", timeout=600)
            self.after(0, self._write_docs, r.json()["api_docs"])
        except Exception as e:
            self.after(0, self._write_docs, f"[Error] {e}")
        self.after(0, self._set_status, "Done")

    def _write_docs(self, text: str):
        self.docs_out.configure(state=tk.NORMAL)
        self.docs_out.delete("1.0", tk.END)
        self.docs_out.insert(tk.END, text)
        self.docs_out.configure(state=tk.DISABLED)

    def _copy_docs(self):
        self.clipboard_clear()
        self.clipboard_append(self.docs_out.get("1.0", tk.END))
        self._set_status("Copied ✓")

    def _save_docs(self):
        p = filedialog.asksaveasfilename(defaultextension=".md",
                                         filetypes=[("Markdown","*.md"),("Text","*.txt")])
        if p:
            Path(p).write_text(self.docs_out.get("1.0", tk.END), encoding="utf-8")
            self._set_status(f"Saved to {Path(p).name}")

    # ── Health check ──────────────────────────────────────────────────────
    def _check_health(self):
        def bg():
            try:
                models = self.ollama.models()
                self.after(0, self._on_health_ok, models)
            except Exception as e:
                self.after(0, self._on_health_fail, str(e))

        threading.Thread(target=bg, daemon=True).start()

    def _on_health_ok(self, models):
        self._dot.configure(fg=SUCCESS)
        self._status_lbl.configure(text="Connected", fg=SUCCESS)
        self._model_cb["values"] = models
        if DEFAULT_MODEL in models:
            self._model_var.set(DEFAULT_MODEL)
        elif models:
            self._model_var.set(models[0])
        self.ollama.model = self._model_var.get()
        self._set_status("Connected to Ollama")
        self._refresh_snaps()

    def _on_health_fail(self, err):
        self._dot.configure(fg=DANGER)
        self._status_lbl.configure(text="Offline", fg=DANGER)
        self._set_status(f"Ollama unreachable: {err}")
        self.chat.add_system(
            "Ollama is not reachable. Make sure it's running on port 11434.", DANGER)

    def _on_model_change(self, _=None):
        self.ollama.model = self._model_var.get()

    # ── Remote inject ─────────────────────────────────────────────────────
    def _remote_inject(self, message: str, role: str = "user"):
        """Called from remote server thread or the Remote tab."""
        ts = datetime.now().strftime("%H:%M:%S")
        log = f"[{ts}] {role}: {message[:80]}\n"
        self.after(0, self._log_remote, log)
        if role == "user" and message:
            self.after(0, self._dispatch, message)

    def _log_remote(self, text: str):
        self._remote_log.configure(state=tk.NORMAL)
        self._remote_log.insert(tk.END, text)
        self._remote_log.see(tk.END)
        self._remote_log.configure(state=tk.DISABLED)

    # ── Misc ──────────────────────────────────────────────────────────────
    def _greet(self):
        self.chat.add_system(f"─────────  {APP_NAME} v{VERSION}  ─────────", ACCENT)
        self.chat.start_bot()
        greeting = (
            "Hello! I'm BennyAI, your local AI coding assistant powered by DeepSeek Coder.\n\n"
            "I can help you with:\n"
            "  • Reading and explaining code\n"
            "  • Debugging and fixing issues\n"
            "  • Generating documentation\n"
            "  • Running commands (with your permission)\n"
            "  • Answering questions about your projects\n\n"
            "Set your workspace folder in the header, or just ask me anything.\n"
            "I'll read files and explore your project as needed."
        )
        self.chat.finish_bot(greeting)

    def _clear_chat(self):
        self.agent.reset()
        self.chat.clear()

    def _set_status(self, msg: str):
        self._sb_var.set(msg)

    def destroy(self):
        self.remote.stop()
        super().destroy()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = BennyAI()
    app.mainloop()
