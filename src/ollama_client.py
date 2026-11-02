import httpx
import os
from typing import AsyncGenerator

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "deepseek-coder:6.7b")


async def generate(prompt: str, system: str = "", model: str = DEFAULT_MODEL) -> str:
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 2048},
    }
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
        resp.raise_for_status()
        return resp.json()["response"]


async def stream_generate(prompt: str, system: str = "", model: str = DEFAULT_MODEL) -> AsyncGenerator[str, None]:
    payload = {
        "model": model,
        "prompt": prompt,
        "system": system,
        "stream": True,
        "options": {"temperature": 0.2, "num_predict": 2048},
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", f"{OLLAMA_BASE_URL}/api/generate", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    import json
                    chunk = json.loads(line)
                    if chunk.get("response"):
                        yield chunk["response"]
                    if chunk.get("done"):
                        break


async def list_models() -> list[str]:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]


async def pull_model(model: str = DEFAULT_MODEL) -> bool:
    async with httpx.AsyncClient(timeout=600.0) as client:
        resp = await client.post(f"{OLLAMA_BASE_URL}/api/pull", json={"name": model, "stream": False})
        return resp.status_code == 200
