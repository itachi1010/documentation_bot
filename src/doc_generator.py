from .ollama_client import generate
from .code_analyzer import CodebaseSnapshot, build_prompt_context

SYSTEM_PROMPT = """You are a senior software engineer specializing in technical documentation.
You write clear, accurate, and well-structured documentation for codebases.
Use Markdown. Be concise but thorough. Include:
- What the project does and its purpose
- Architecture overview with key components
- How to set it up and run it
- Key APIs or entry points
- Any non-obvious design decisions
Do NOT add filler phrases or unnecessary padding."""


async def generate_readme(snapshot: CodebaseSnapshot) -> str:
    context = build_prompt_context(snapshot)
    prompt = f"""Analyze this codebase and generate a comprehensive README.md.

Project root: {snapshot.root}
Files scanned: {len(snapshot.files)}

{context}

Generate the README.md now:"""
    return await generate(prompt, system=SYSTEM_PROMPT)


async def document_file(file_path: str, content: str, language: str) -> str:
    prompt = f"""Document this {language} file. Include:
- Module/file purpose
- All public functions/classes with their parameters, return types, and what they do
- Any important side effects or dependencies

File: {file_path}
```{language}
{content}
```

Write the documentation in Markdown:"""
    return await generate(prompt, system=SYSTEM_PROMPT)


async def answer_question(question: str, snapshot: CodebaseSnapshot) -> str:
    context = build_prompt_context(snapshot, max_chars=60_000)
    prompt = f"""Based on this codebase, answer the following question:

Question: {question}

Codebase context:
{context}

Answer:"""
    return await generate(prompt, system=SYSTEM_PROMPT)


async def generate_api_docs(snapshot: CodebaseSnapshot) -> str:
    context = build_prompt_context(snapshot)
    prompt = f"""Analyze this codebase and generate API documentation.
List all public endpoints, functions, or interfaces with their inputs, outputs, and purpose.

{context}

API Documentation:"""
    return await generate(prompt, system=SYSTEM_PROMPT)
