from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, Response
from pathlib import Path
import subprocess, sys, os, uuid, json
from pydantic import BaseModel

app = FastAPI()
class ExportRequest(BaseModel):
    job: str
    months: int | None = None
    include_current: bool | None = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable

RUNS_DIR = PROJECT_ROOT / "runs"
RUNS_DIR.mkdir(parents=True, exist_ok=True)

@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)

@app.get("/health")
def health():
    return {"ok": True, "python_used": sys.executable}

def ensure_run_dir(run_id: str) -> Path:
    d = RUNS_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d

@app.post("/run-export")
async def run_export(data: ExportRequest):

    if data.job != "pfp_export":
        return {"status": "ignored"}

    run_id = str(uuid.uuid4())
    rd = ensure_run_dir(run_id)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src")

    # take Zoho inputs (optional)
    if data.months is not None:
        env["LAST_MONTHS"] = str(int(data.months))
    if data.include_current is not None:
        env["INCLUDE_CURRENT"] = "true" if bool(data.include_current) else "false"

    # write downloads/logs into this run folder
    env["PFP_DOWNLOAD_DIR"] = str(rd)

    out = open(rd / "stdout.log", "w", encoding="utf-8")
    err = open(rd / "stderr.log", "w", encoding="utf-8")

    p = subprocess.Popen(
        [PYTHON, str(PROJECT_ROOT / "src" / "export_downloader.py")],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=out,
        stderr=err,
    )

    (rd / "meta.json").write_text(json.dumps({"pid": p.pid}, indent=2), encoding="utf-8")
    return {"status": "accepted", "run_id": run_id}

@app.get("/status/{run_id}")
def status(run_id: str):
    rd = RUNS_DIR / run_id
    if not rd.exists():
        raise HTTPException(404, "Unknown run_id")

    if (rd / "fail.flag").exists():
        return {"status": "failed"}
    if (rd / "done.flag").exists():
        return {"status": "done"}

    # Optional: if the script crashed, stderr will contain something
    err_path = rd / "stderr.log"
    if err_path.exists() and err_path.stat().st_size > 0:
        return {"status": "failed"}

    return {"status": "running"}

@app.get("/files/{run_id}")
def files(run_id: str):
    rd = RUNS_DIR / run_id
    if not rd.exists():
        raise HTTPException(404, "Unknown run_id")

    csvs = sorted([p for p in rd.rglob("*.csv") if p.is_file()])
    return {
        "run_id": run_id,
        "files": [{"name": p.name, "download_path": f"/download/{run_id}/{p.name}"} for p in csvs],
    }

@app.get("/download/{run_id}/{filename}")
def download(run_id: str, filename: str):
    rd = RUNS_DIR / run_id
    p = rd / filename
    if not p.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(path=str(p), filename=filename, media_type="text/csv")