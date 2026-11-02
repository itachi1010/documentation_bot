import os
import pathlib
from dataclasses import dataclass, field

SUPPORTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".java", ".cs",
    ".cpp", ".c", ".h", ".rs", ".rb", ".php", ".dart", ".kt", ".swift",
}
IGNORED_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next"}

MAX_FILE_CHARS = 12_000  # truncate large files before sending to LLM


@dataclass
class CodeFile:
    path: str
    language: str
    content: str
    size: int


@dataclass
class CodebaseSnapshot:
    root: str
    files: list[CodeFile] = field(default_factory=list)
    total_chars: int = 0


def detect_language(ext: str) -> str:
    return {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "jsx", ".tsx": "tsx", ".go": "go", ".java": "java",
        ".cs": "csharp", ".cpp": "cpp", ".c": "c", ".h": "c",
        ".rs": "rust", ".rb": "ruby", ".php": "php", ".dart": "dart",
        ".kt": "kotlin", ".swift": "swift",
    }.get(ext, "text")


def scan_directory(root: str, max_total_chars: int = 200_000) -> CodebaseSnapshot:
    snapshot = CodebaseSnapshot(root=root)
    root_path = pathlib.Path(root)

    for path in sorted(root_path.rglob("*")):
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix not in SUPPORTED_EXTENSIONS:
            continue
        if snapshot.total_chars >= max_total_chars:
            break
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            if len(content) > MAX_FILE_CHARS:
                content = content[:MAX_FILE_CHARS] + "\n... (truncated)"
            snapshot.files.append(CodeFile(
                path=str(path.relative_to(root_path)),
                language=detect_language(path.suffix),
                content=content,
                size=len(content),
            ))
            snapshot.total_chars += len(content)
        except Exception:
            continue

    return snapshot


def format_file_for_prompt(f: CodeFile) -> str:
    return f"### {f.path}\n```{f.language}\n{f.content}\n```"


def build_prompt_context(snapshot: CodebaseSnapshot, max_chars: int = 80_000) -> str:
    parts = []
    total = 0
    for f in snapshot.files:
        block = format_file_for_prompt(f)
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)
