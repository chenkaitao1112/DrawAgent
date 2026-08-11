from __future__ import annotations

import json
import re
import shutil
from contextlib import asynccontextmanager
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.core.config import AppConfig
from app.core.errors import classify_error
from app.core.logging import debug_event
from app.ingestion.service import DocumentIngestionService
from app.workflow.runtime import WorkflowResult
from app.workflow.runtime import DrawAgentRuntime


ROOT_DIR = Path(__file__).resolve().parents[1]
LEGACY_FRONTEND_DIR = ROOT_DIR / "app" / "frontend"
LEGACY_FRONTEND_STATIC_DIR = LEGACY_FRONTEND_DIR / "static"
REACT_FRONTEND_DIST_DIR = ROOT_DIR.parent / "drawAgent-version2-react" / "dist"
REACT_FRONTEND_ASSETS_DIR = REACT_FRONTEND_DIST_DIR / "assets"
ACTIVE_FRONTEND_DIR = LEGACY_FRONTEND_DIR
CONFIG = AppConfig.load(ROOT_DIR)
RUNTIME = DrawAgentRuntime(ROOT_DIR)
INGESTION = DocumentIngestionService()
JOB_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="drawagent-job")
JOB_LOCK = Lock()
JOBS: dict[str, dict[str, Any]] = {}
JOB_DIR = CONFIG.artifact_dir / "jobs"
JOB_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        yield
    finally:
        RUNTIME.close()
        JOB_EXECUTOR.shutdown(wait=False)


app = FastAPI(title="drawAgent-v2", lifespan=lifespan)
app.mount("/outputs", StaticFiles(directory=CONFIG.output_dir), name="outputs")
if LEGACY_FRONTEND_STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=LEGACY_FRONTEND_STATIC_DIR), name="static")
if REACT_FRONTEND_ASSETS_DIR.exists():
    app.mount("/assets", StaticFiles(directory=REACT_FRONTEND_ASSETS_DIR), name="assets")


class ConfirmGenerationRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1)


class ConfirmOrchestrationRequest(BaseModel):
    session_id: str = Field(min_length=1)


class RevisePromptRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1)
    revision_instruction: str = Field(min_length=1)


class StopSessionRequest(BaseModel):
    session_id: str = ""
    checkpoint_id: str = ""


class JobResponse(BaseModel):
    job_id: str
    session_id: str = ""
    checkpoint_id: str = ""
    status: str
    stage: str = ""


def _normalize_identifier(value: str | None) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", raw):
        return raw
    return ""


def _safe_identifier(value: str | None) -> str:
    normalized = _normalize_identifier(value)
    if normalized:
        return normalized
    return str(uuid4())


def _staging_batch_dir(upload_batch_id: str) -> Path:
    return CONFIG.upload_dir / "staging" / _safe_identifier(upload_batch_id)


def _upload_manifest_path(upload_batch_id: str) -> Path:
    return _staging_batch_dir(upload_batch_id) / "upload_result.json"


def _write_upload_manifest(upload_batch_id: str, uploaded_files: list[dict]) -> None:
    upload_batch_id = _safe_identifier(upload_batch_id)
    manifest_path = _upload_manifest_path(upload_batch_id)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "upload_batch_id": upload_batch_id,
                "uploaded_files": uploaded_files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _read_upload_manifest(upload_batch_id: str) -> dict:
    upload_batch_id = _safe_identifier(upload_batch_id)
    manifest_path = _upload_manifest_path(upload_batch_id)
    if not manifest_path.exists():
        return {
            "upload_batch_id": upload_batch_id,
            "uploaded_files": [],
        }
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _save_uploaded_files(files: list[UploadFile], target_dir: Path) -> list[dict]:
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict] = []
    for item in files:
        original_name = Path(item.filename or "uploaded_file").name
        path = target_dir / original_name
        with path.open("wb") as handle:
            shutil.copyfileobj(item.file, handle)
        saved.append(
            {
                "name": original_name,
                "content_type": item.content_type or "application/octet-stream",
                "size_bytes": path.stat().st_size,
                "saved_path": str(path),
            }
        )
    return saved


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_path(job_id: str) -> Path:
    normalized = _normalize_identifier(job_id)
    if not normalized:
        raise ValueError("Invalid job id.")
    return JOB_DIR / f"{normalized}.json"


def _serializable_job(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if key != "future"}


def _persist_job(job: dict[str, Any]) -> None:
    _job_path(str(job["job_id"])).write_text(
        json.dumps(_serializable_job(job), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _update_job(job_id: str, **updates: Any) -> dict[str, Any]:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is None:
            job = _load_job(job_id)
            JOBS[job_id] = job
        job.update(updates)
        job["updated_at"] = _utc_now()
        _persist_job(job)
        return _serializable_job(job)


def _load_job(job_id: str) -> dict[str, Any]:
    path = _job_path(job_id)
    if not path.exists():
        raise FileNotFoundError(job_id)
    return json.loads(path.read_text(encoding="utf-8"))


def _get_job(job_id: str) -> dict[str, Any]:
    with JOB_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            return _serializable_job(job)
    return _load_job(job_id)


def _job_cancellation_requested(job_id: str) -> bool:
    try:
        return bool(_get_job(job_id).get("cancellation_requested", False))
    except FileNotFoundError:
        return False


def _submit_job(
    *,
    kind: str,
    session_id: str = "",
    checkpoint_id: str = "",
    target: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    job_id = str(uuid4())
    job = {
        "job_id": job_id,
        "kind": kind,
        "session_id": session_id,
        "checkpoint_id": checkpoint_id,
        "status": "queued",
        "stage": "queued",
        "result": None,
        "error": None,
        "cancellation_requested": False,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
    }
    with JOB_LOCK:
        JOBS[job_id] = job
        _persist_job(job)

    def runner() -> None:
        _update_job(job_id, status="running", stage="running")
        try:
            result = target()
            canceled = _job_cancellation_requested(job_id) or result.get("stage") == "stopped"
            _update_job(
                job_id,
                status="canceled" if canceled else "completed",
                stage=str(result.get("stage") or ("canceled" if canceled else "completed")),
                result=result,
                error=None,
            )
        except Exception as exc:
            error = classify_error(exc)
            debug_event("job_failed", job_id=job_id, kind=kind, error=error)
            _update_job(job_id, status="failed", stage="failed", error=error)

    future = JOB_EXECUTOR.submit(runner)
    with JOB_LOCK:
        JOBS[job_id]["future"] = future
    return _serializable_job(JOBS[job_id])


def _request_stop_for_jobs(*, session_id: str = "", checkpoint_id: str = "") -> bool:
    matched = False
    with JOB_LOCK:
        candidates = list(JOBS.values())
    for job in candidates:
        if session_id and job.get("session_id") != session_id:
            continue
        if checkpoint_id and job.get("checkpoint_id") != checkpoint_id:
            continue
        if job.get("status") not in {"queued", "running"}:
            continue
        matched = True
        future = job.get("future")
        canceled_queued = isinstance(future, Future) and future.cancel()
        _update_job(
            str(job["job_id"]),
            cancellation_requested=True,
            status="canceled" if canceled_queued else job.get("status", "running"),
            stage="canceled" if canceled_queued else job.get("stage", "running"),
        )
    return matched


def _resolve_manifest_file(item: dict, batch_dir: Path) -> Path | None:
    source = Path(str(item.get("saved_path", "")))
    if not source.exists() or not source.is_file():
        return None
    try:
        resolved_source = source.resolve()
        resolved_batch_dir = batch_dir.resolve()
    except OSError:
        return None
    if not resolved_source.is_relative_to(resolved_batch_dir):
        return None
    return resolved_source


def _validated_staged_files(upload_batch_id: str, staged_files: list[dict]) -> list[dict]:
    if not upload_batch_id:
        return []
    safe_batch_id = _safe_identifier(upload_batch_id)
    batch_dir = _staging_batch_dir(safe_batch_id)
    manifest_payload = _read_upload_manifest(safe_batch_id)
    manifest_files = manifest_payload.get("uploaded_files", [])
    if not isinstance(manifest_files, list):
        return []

    requested_names = {
        str(item.get("name") or "").strip()
        for item in staged_files
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    }
    validated: list[dict] = []
    for item in manifest_files:
        if not isinstance(item, dict):
            continue
        if requested_names and str(item.get("name") or "") not in requested_names:
            continue
        source = _resolve_manifest_file(item, batch_dir)
        if source is None:
            continue
        validated.append(
            {
                "name": Path(str(item.get("name") or source.name)).name,
                "content_type": item.get("content_type", "application/octet-stream"),
                "size_bytes": source.stat().st_size,
                "saved_path": str(source),
            }
        )
    return validated


def _prepare_session_files(
    *,
    session_id: str,
    files: list[UploadFile],
    staged_files_json: str,
    upload_batch_id: str,
) -> tuple[Path, list[dict]]:
    session_dir = CONFIG.upload_dir / session_id
    uploaded_files = _save_uploaded_files(files, session_dir)
    try:
        staged_files = json.loads(staged_files_json or "[]")
    except json.JSONDecodeError:
        staged_files = []
    staged_items = staged_files if isinstance(staged_files, list) else []
    for item in _validated_staged_files(upload_batch_id, staged_items):
        source = Path(str(item["saved_path"]))
        target = session_dir / Path(str(item.get("name") or source.name)).name
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        uploaded_files.append(
            {
                "name": target.name,
                "content_type": item.get("content_type", "application/octet-stream"),
                "size_bytes": target.stat().st_size,
                "saved_path": str(target),
            }
        )
    return session_dir, uploaded_files


def _workflow_result_to_response(result: WorkflowResult, uploaded_files: list[dict]) -> dict:
    return {
        "session_id": result.session_id,
        "stage": result.stage,
        "checkpoint_id": result.checkpoint_id,
        "payload_final": result.payload_final,
        "workflow_state": result.workflow_state,
        "document_context_summary": result.document_context_summary,
        "selected_skill": result.selected_skill,
        "uploaded_files": uploaded_files,
        "confirm_required": bool(result.checkpoint_id),
    }


def _run_prompt_pipeline_response(
    *,
    session_id: str,
    user_input: str,
    session_dir: Path,
    uploaded_files: list[dict],
) -> dict:
    document_context = INGESTION.ingest_uploaded_files(
        uploaded_files=uploaded_files,
        artifact_dir=session_dir,
    )
    result = RUNTIME.start_prompt_pipeline(
        session_id=session_id,
        user_input=user_input,
        document_context=document_context,
    )
    return _workflow_result_to_response(result, uploaded_files)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ACTIVE_FRONTEND_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/agents")
def list_agents() -> dict:
    return {"agents": RUNTIME.describe_agents()}


@app.get("/skills")
def list_skills() -> dict:
    return {"skills": RUNTIME.describe_skills()}


@app.get("/workflow")
def describe_workflow() -> dict:
    return RUNTIME.describe_workflow()


@app.post("/session/upload-files-form", response_class=HTMLResponse)
async def upload_files_form(
    files: list[UploadFile] = File(default_factory=list),
    upload_batch_id: str | None = Form(default=None),
) -> HTMLResponse:
    upload_batch_id = _safe_identifier(upload_batch_id or str(uuid4()))
    batch_dir = _staging_batch_dir(upload_batch_id)
    uploaded_files = _save_uploaded_files(files, batch_dir)
    _write_upload_manifest(upload_batch_id, uploaded_files)
    payload = json.dumps(
        {
            "type": "drawagent-upload-result",
            "ok": True,
            "payload": {
                "upload_batch_id": upload_batch_id,
                "uploaded_files": uploaded_files,
            },
        },
        ensure_ascii=False,
    )
    return HTMLResponse(
        f"""
<!DOCTYPE html>
<html lang="zh-CN">
  <body>
    <script>
      window.parent.postMessage({payload}, "*");
    </script>
  </body>
</html>
"""
    )


@app.post("/session/upload-files")
async def upload_files(
    files: list[UploadFile] = File(default_factory=list),
    upload_batch_id: str | None = Form(default=None),
) -> dict:
    upload_batch_id = _safe_identifier(upload_batch_id or str(uuid4()))
    batch_dir = _staging_batch_dir(upload_batch_id)
    uploaded_files = _save_uploaded_files(files, batch_dir)
    _write_upload_manifest(upload_batch_id, uploaded_files)
    return {
        "ok": True,
        "upload_batch_id": upload_batch_id,
        "uploaded_files": uploaded_files,
    }


@app.get("/session/upload-status/{upload_batch_id}")
def upload_status(upload_batch_id: str) -> dict:
    upload_batch_id = _safe_identifier(upload_batch_id)
    payload = _read_upload_manifest(upload_batch_id)
    return {
        "ok": True,
        **payload,
    }


@app.post("/session/start")
async def start_session(
    user_input: str = Form(""),
    files: list[UploadFile] = File(default_factory=list),
    staged_files_json: str = Form("[]"),
    upload_batch_id: str = Form(""),
    session_id: str = Form(""),
) -> dict:
    session_id = _safe_identifier(session_id or str(uuid4()))
    session_dir, uploaded_files = _prepare_session_files(
        session_id=session_id,
        files=files,
        staged_files_json=staged_files_json,
        upload_batch_id=upload_batch_id,
    )
    return _run_prompt_pipeline_response(
        session_id=session_id,
        user_input=user_input,
        session_dir=session_dir,
        uploaded_files=uploaded_files,
    )


@app.post("/session/start-job")
async def start_session_job(
    user_input: str = Form(""),
    files: list[UploadFile] = File(default_factory=list),
    staged_files_json: str = Form("[]"),
    upload_batch_id: str = Form(""),
    session_id: str = Form(""),
) -> dict:
    session_id = _safe_identifier(session_id or str(uuid4()))
    session_dir, uploaded_files = _prepare_session_files(
        session_id=session_id,
        files=files,
        staged_files_json=staged_files_json,
        upload_batch_id=upload_batch_id,
    )
    job = _submit_job(
        kind="prompt",
        session_id=session_id,
        target=lambda: _run_prompt_pipeline_response(
            session_id=session_id,
            user_input=user_input,
            session_dir=session_dir,
            uploaded_files=uploaded_files,
        ),
    )
    return {
        "job_id": job["job_id"],
        "session_id": session_id,
        "status": job["status"],
        "stage": job["stage"],
        "uploaded_files": uploaded_files,
    }


@app.get("/session/jobs/{job_id}")
def get_session_job(job_id: str) -> dict:
    try:
        return _get_job(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="job not found") from exc


@app.get("/session/{session_id}/tasks")
def list_session_tasks(session_id: str) -> dict:
    normalized = _normalize_identifier(session_id)
    if not normalized:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "ok": True,
        "session_id": normalized,
        "tasks": RUNTIME.list_task_records(normalized),
    }


@app.get("/session/{session_id}/events")
def list_session_events(session_id: str) -> dict:
    normalized = _normalize_identifier(session_id)
    if not normalized:
        raise HTTPException(status_code=404, detail="session not found")
    return {
        "ok": True,
        "session_id": normalized,
        "events": RUNTIME.read_run_events(normalized),
    }


@app.post("/session/confirm-image-generation")
def confirm_image_generation(request: ConfirmGenerationRequest) -> dict:
    try:
        return RUNTIME.confirm_image_generation(request.checkpoint_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="checkpoint not found") from exc


@app.post("/session/confirm-orchestration")
def confirm_orchestration(request: ConfirmOrchestrationRequest) -> dict:
    try:
        session_id = _normalize_identifier(request.session_id)
        if not session_id:
            raise ValueError("invalid session id")
        return RUNTIME.confirm_orchestration(session_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="session not awaiting orchestration confirmation") from exc


@app.post("/session/confirm-orchestration-job")
def confirm_orchestration_job(request: ConfirmOrchestrationRequest) -> dict:
    session_id = _normalize_identifier(request.session_id)
    if not session_id:
        raise HTTPException(status_code=404, detail="session not found")
    state = RUNTIME.load_session_state(session_id)
    if not state or state.get("stage") != "awaiting_orchestration_confirmation":
        raise HTTPException(status_code=404, detail="session not awaiting orchestration confirmation")
    job = _submit_job(
        kind="orchestration",
        session_id=session_id,
        target=lambda: RUNTIME.confirm_orchestration(session_id),
    )
    return {
        "job_id": job["job_id"],
        "session_id": session_id,
        "status": job["status"],
        "stage": job["stage"],
    }


@app.post("/session/confirm-image-generation-job")
def confirm_image_generation_job(request: ConfirmGenerationRequest) -> dict:
    try:
        checkpoint = RUNTIME.checkpoint_store.load(request.checkpoint_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="checkpoint not found") from exc
    session_id = str(checkpoint.get("workflow_state", {}).get("session_id") or "")
    job = _submit_job(
        kind="image_generation",
        session_id=session_id,
        checkpoint_id=request.checkpoint_id,
        target=lambda: RUNTIME.confirm_image_generation(request.checkpoint_id),
    )
    return {
        "job_id": job["job_id"],
        "session_id": session_id,
        "checkpoint_id": request.checkpoint_id,
        "status": job["status"],
        "stage": job["stage"],
    }


@app.post("/session/revise-prompt")
def revise_prompt(request: RevisePromptRequest) -> dict:
    try:
        return RUNTIME.revise_prompt_checkpoint(
            checkpoint_id=request.checkpoint_id,
            revision_instruction=request.revision_instruction,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="checkpoint not found") from exc


@app.post("/session/revise-prompt-job")
def revise_prompt_job(request: RevisePromptRequest) -> dict:
    try:
        checkpoint = RUNTIME.checkpoint_store.load(request.checkpoint_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="checkpoint not found") from exc
    session_id = str(checkpoint.get("workflow_state", {}).get("session_id") or "")
    job = _submit_job(
        kind="prompt_revision",
        session_id=session_id,
        checkpoint_id=request.checkpoint_id,
        target=lambda: RUNTIME.revise_prompt_checkpoint(
            checkpoint_id=request.checkpoint_id,
            revision_instruction=request.revision_instruction,
        ),
    )
    return {
        "job_id": job["job_id"],
        "session_id": session_id,
        "checkpoint_id": request.checkpoint_id,
        "status": job["status"],
        "stage": job["stage"],
    }


@app.post("/session/stop")
def stop_session(request: StopSessionRequest) -> dict:
    stopped = False
    stopped_jobs = False
    if request.session_id:
        session_id = _normalize_identifier(request.session_id)
        stopped = RUNTIME.request_stop(session_id)
        stopped_jobs = _request_stop_for_jobs(session_id=session_id)
    elif request.checkpoint_id:
        try:
            stopped = RUNTIME.request_stop_for_checkpoint(request.checkpoint_id)
            stopped_jobs = _request_stop_for_jobs(checkpoint_id=request.checkpoint_id)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="checkpoint not found") from exc
    return {
        "ok": stopped or stopped_jobs,
        "stage": "stop_requested" if (stopped or stopped_jobs) else "no_active_session",
    }
