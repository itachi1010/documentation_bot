from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import os

from .ollama_client import generate, stream_generate, list_models, pull_model
from .code_analyzer import scan_directory
from .doc_generator import generate_readme, document_file, answer_question, generate_api_docs

app = FastAPI(title="Documentation Bot", version="1.0.0")

_snapshots: dict[str, object] = {}


class ScanRequest(BaseModel):
    path: str
    label: str | None = None


class QuestionRequest(BaseModel):
    snapshot_label: str
    question: str


class FileDocRequest(BaseModel):
    file_path: str
    content: str
    language: str = "python"


class GenerateRequest(BaseModel):
    prompt: str
    system: str = ""
    model: str | None = None


@app.get("/health")
async def health():
    models = await list_models()
    return {"status": "ok", "ollama_models": models}


@app.get("/models")
async def models():
    return {"models": await list_models()}


@app.post("/models/pull/{model_name}")
async def pull(model_name: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(pull_model, model_name)
    return {"status": "pulling", "model": model_name}


@app.post("/scan")
async def scan(req: ScanRequest):
    if not os.path.isdir(req.path):
        raise HTTPException(status_code=400, detail=f"Directory not found: {req.path}")
    snapshot = scan_directory(req.path)
    label = req.label or os.path.basename(req.path)
    _snapshots[label] = snapshot
    return {
        "label": label,
        "files": len(snapshot.files),
        "total_chars": snapshot.total_chars,
        "file_list": [f.path for f in snapshot.files],
    }


@app.post("/generate/readme/{snapshot_label}")
async def readme(snapshot_label: str):
    if snapshot_label not in _snapshots:
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_label}' not found. Run /scan first.")
    doc = await generate_readme(_snapshots[snapshot_label])
    return {"readme": doc}


@app.post("/generate/api-docs/{snapshot_label}")
async def api_docs(snapshot_label: str):
    if snapshot_label not in _snapshots:
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_label}' not found.")
    doc = await generate_api_docs(_snapshots[snapshot_label])
    return {"api_docs": doc}


@app.post("/generate/file-doc")
async def file_doc(req: FileDocRequest):
    doc = await document_file(req.file_path, req.content, req.language)
    return {"documentation": doc}


@app.post("/ask")
async def ask(req: QuestionRequest):
    if req.snapshot_label not in _snapshots:
        raise HTTPException(status_code=404, detail=f"Snapshot '{req.snapshot_label}' not found.")
    answer = await answer_question(req.question, _snapshots[req.snapshot_label])
    return {"answer": answer}


@app.post("/generate")
async def raw_generate(req: GenerateRequest):
    kwargs = {}
    if req.model:
        kwargs["model"] = req.model
    response = await generate(req.prompt, system=req.system, **kwargs)
    return {"response": response}


@app.post("/generate/stream")
async def raw_stream(req: GenerateRequest):
    kwargs = {}
    if req.model:
        kwargs["model"] = req.model

    async def event_stream():
        async for chunk in stream_generate(req.prompt, system=req.system, **kwargs):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/plain")


@app.get("/snapshots")
async def list_snapshots():
    return {
        label: {"files": len(s.files), "total_chars": s.total_chars}
        for label, s in _snapshots.items()
    }
