"""Local server: python run.py  →  http://localhost:8000"""
import os
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from energyos.api_core import dispatch
from energyos.platform import Platform

platform = Platform(seed=int(os.getenv("SIM_SEED", "42")))
app = FastAPI(title="EnergyOS Secure (prototype)")
WEB = Path(__file__).parent / "web"


@app.on_event("startup")
def _start():
    threading.Thread(target=platform.run_forever, daemon=True).start()


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def api(path: str, request: Request):
    body = await request.json() if request.method == "POST" and await request.body() else {}
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip() or None
    status, data = dispatch(platform, request.method, "/api/" + path, dict(request.query_params), body, token)
    return JSONResponse(data, status_code=status)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")))
