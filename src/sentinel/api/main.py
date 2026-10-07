# src/sentinel/api/main.py
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Any, Dict, Iterator
from fastapi import FastAPI, Depends, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sentinel.config.settings import BASE_DIR, settings
from sentinel.database.db import SessionLocal, get_db, init_db
from sentinel.database.crud import (
    create_investigation,
    finalize_investigation,
    fetch_all_investigations,
    fetch_investigation
)
from sentinel.domain.tooling import ToolAuditTrail
from sentinel.tools.registry import get_registry
from sentinel.graph.workflow import CHECKPOINT_BACKEND, app as graph_app
from sentinel.llm import get_gateway
from sentinel.services.report import extract_score, filename_for, render_pdf

# --- Reference asset directories (documentation & datasets browser) ---
ASSET_DIRECTORIES = {
    "doc": BASE_DIR / "docs" / "corpus",
    "dataset": BASE_DIR / "datasets",
}

@asynccontextmanager
async def lifespan(_: FastAPI):
    """Owns the process-wide resources: schema on the way in, pools on the way out."""
    init_db()
    yield
    # The checkpoint pool holds worker threads; releasing it here keeps shutdown
    # clean instead of leaving the interpreter to finalise them.
    CHECKPOINT_BACKEND.close()


app = FastAPI(
    title="SENTINEL AI Gateway API",
    description="Enterprise Multi-Agent Compliance & Intelligence API Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

# The UI's .env points VITE_API_URL straight at this server (not through Vite's
# /api proxy), so browser requests are cross-origin and need CORS + preflight support.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Data Models ---
class StartInvestigationRequest(BaseModel):
    subject_name: str
    ticker: Optional[str] = ""

class ApprovalRequest(BaseModel):
    approved: bool

class InvestigationResponse(BaseModel):
    id: str
    subject_name: str
    ticker: Optional[str] = ""
    status: str
    # Which specialists the supervisor engaged, and the reason for each skip.
    plan: Optional[Dict[str, Any]] = None
    risk_level: Optional[str] = "UNKNOWN"
    # Full scored verdict: composite score, band, per-dimension breakdown,
    # drivers and mitigants. The UI renders its panel from this.
    risk_assessment: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = None
    human_approved: Optional[bool] = None
    supervisor_reasoning: Optional[str] = ""
    final_report: Optional[str] = ""
    research_data: Optional[List[Dict[str, Any]]] = []
    financial_data: Optional[Dict[str, Any]] = {}
    news_data: Optional[List[Dict[str, Any]]] = []
    # Synthesised obligations plus the passages they were drawn from.
    compliance_data: Optional[Dict[str, Any]] = None
    sanctions_data: Optional[Dict[str, Any]] = None
    # SEC EDGAR registrant profile and supervisory filing flags.
    edgar_data: Optional[Dict[str, Any]] = None
    logs: Optional[List[str]] = []
    # M-04: every tool call made during the run - arguments, duration, outcome -
    # plus the evidence ids each one produced.
    tool_audit: Optional[Dict[str, Any]] = None
    # The critic's reading of the verdict: what it could not support, and why.
    critic_review: Optional[Dict[str, Any]] = None
    # Prior reviews of the same entity.
    memory_data: Optional[Dict[str, Any]] = None
    # Entities connected to the subject, and how.
    graph_data: Optional[Dict[str, Any]] = None
    # Fraud indicators and their convergence patterns.
    fraud_data: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = ""


# --- API Endpoints ---

@app.get("/system")
def system_status_endpoint():
    """Reports the durability guarantees the running instance actually provides.

    The console surfaces this: a reviewer should be able to see at a glance
    whether an interrupted investigation would survive a restart, rather than
    having to trust that it would.
    """
    gateway = get_gateway()

    return {
        "checkpointing": {
            "backend": CHECKPOINT_BACKEND.name,
            "durable": CHECKPOINT_BACKEND.durable,
            "detail": CHECKPOINT_BACKEND.detail,
        },
        "database": {
            "engine": "postgresql" if str(settings.DATABASE_URL).startswith("postgres") else "sqlite",
        },
        "llm": {
            "providers": gateway.providers,
            "cache_enabled": gateway.cache_enabled,
            # Cumulative since this process started, not per investigation —
            # labelled as such so the figure is not read as a single run's cost.
            "usage_since_startup": gateway.ledger.snapshot(),
        },
    }


@app.post("/investigations", response_model=InvestigationResponse, status_code=status.HTTP_201_CREATED)
def start_investigation_endpoint(req: StartInvestigationRequest, db: Session = Depends(get_db)):
    """Triggers multi-agent analysis graph and saves initial record in SQL DB."""
    # 1. Save entry to SQL DB
    db_record = create_investigation(
        subject_name=req.subject_name,
        ticker=req.ticker,
        db=db
    )
    record_id = str(db_record.id)
    config = {"configurable": {"thread_id": record_id}}

    initial_input = {
        "investigation_id": record_id,
        "ticker": req.ticker,
        "subject_name": req.subject_name,
    }

    # 2. Run graph until the human approval interrupt.
    #
    # `interrupt_before` does not raise — LangGraph returns the state at the
    # breakpoint — so nothing here is an "expected pause". Anything caught below
    # is a genuine failure: an unreachable provider, a node defect, a dropped
    # database connection. Swallowing it returned 201 with an empty verdict and
    # left the record RUNNING for ever, which is the silent degradation this
    # system exists to prevent. The run is stopped and the reason is reported.
    try:
        graph_app.invoke(initial_input, config)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"Investigation {record_id} failed while executing the graph: "
                f"{type(exc).__name__}: {exc}"
            ),
        ) from exc

    state = graph_app.get_state(config)
    values = state.values if state else {}

    return InvestigationResponse(
        id=record_id,
        subject_name=req.subject_name,
        ticker=req.ticker,
        status=db_record.status.value if hasattr(db_record.status, "value") else str(db_record.status),
        plan=values.get("plan"),
        risk_level=values.get("risk_level", "UNKNOWN"),
        risk_assessment=values.get("risk_assessment"),
        tool_audit=_tool_audit(values),
        critic_review=values.get("critic_review"),
        memory_data=values.get("memory_data"),
        graph_data=values.get("graph_data"),
        fraud_data=values.get("fraud_data"),
        confidence=values.get("confidence"),
        human_approved=values.get("human_approved"),
        supervisor_reasoning=values.get("supervisor_reasoning", ""),
        final_report=values.get("final_report", ""),
        research_data=values.get("research_data", []),
        financial_data=values.get("financial_data", {}),
        news_data=values.get("news_data", []),
        compliance_data=values.get("compliance_data"),
        sanctions_data=values.get("sanctions_data"),
        edgar_data=values.get("edgar_data"),
        logs=values.get("logs", []),
        created_at=db_record.created_at.isoformat() if db_record.created_at else ""
    )


@app.get("/investigations", response_model=List[Dict[str, Any]])
def list_investigations_endpoint(db: Session = Depends(get_db)):
    """Fetches full SQL audit trail history."""
    records = fetch_all_investigations(db=db)
    result = []
    for r in records:
        result.append({
            "Database ID": str(r.id),
            "Created At": r.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if r.created_at else "",
            "Company": r.subject_name,
            "Ticker": r.ticker or "N/A",
            "Status": r.status.value if hasattr(r.status, "value") else str(r.status),
            "Risk Level": r.risk_level or "N/A",
            "Approved": "✅ Yes" if r.human_approved else ("❌ No" if r.human_approved is False else "Pending"),
            # Lets the ledger offer a download only where a report was stored.
            "Has Report": bool(r.final_report),
        })
    return result


def _record_id(investigation_id: str) -> uuid.UUID:
    """Parses a path identifier, or answers 404 rather than 500.

    `uuid.UUID` raises `ValueError` on anything malformed, and an unhandled
    ValueError in a handler is a server error — which says the server broke when
    in fact the caller asked for something that cannot exist.
    """
    try:
        return uuid.UUID(investigation_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=404,
            detail=f"No investigation with identifier {investigation_id!r}.",
        ) from None


@app.get("/investigations/{investigation_id}", response_model=InvestigationResponse)
def get_investigation_endpoint(investigation_id: str, db: Session = Depends(get_db)):
    """Retrieves specific investigation snapshot from state and database."""
    record = fetch_investigation(_record_id(investigation_id), db=db)
    if not record:
        raise HTTPException(status_code=404, detail="Investigation record not found")

    config = {"configurable": {"thread_id": investigation_id}}
    state = graph_app.get_state(config)
    values = state.values if state else {}

    return InvestigationResponse(
        id=investigation_id,
        subject_name=record.subject_name,
        ticker=record.ticker,
        status=record.status.value if hasattr(record.status, "value") else str(record.status),
        plan=values.get("plan"),
        risk_level=values.get("risk_level") or record.risk_level or "UNKNOWN",
        risk_assessment=values.get("risk_assessment"),
        tool_audit=_tool_audit(values),
        critic_review=values.get("critic_review"),
        memory_data=values.get("memory_data"),
        graph_data=values.get("graph_data"),
        fraud_data=values.get("fraud_data"),
        confidence=values.get("confidence"),
        human_approved=values.get("human_approved") if values.get("human_approved") is not None else record.human_approved,
        supervisor_reasoning=values.get("supervisor_reasoning") or record.supervisor_reasoning or "",
        final_report=values.get("final_report") or record.final_report or "",
        research_data=values.get("research_data", []),
        financial_data=values.get("financial_data", {}),
        news_data=values.get("news_data", []),
        compliance_data=values.get("compliance_data"),
        sanctions_data=values.get("sanctions_data"),
        edgar_data=values.get("edgar_data"),
        logs=values.get("logs", []),
        created_at=record.created_at.isoformat() if record.created_at else ""
    )


@app.post("/investigations/{investigation_id}/approve", response_model=InvestigationResponse)
def approve_investigation_endpoint(investigation_id: str, req: ApprovalRequest, db: Session = Depends(get_db)):
    """Submits human decision, resumes workflow to completion, and persists final report."""
    # Validated before the graph is touched: writing an approval into a thread
    # named by an unusable identifier would leave a decision recorded against an
    # investigation that can never be finalised.
    record_id = _record_id(investigation_id)
    config = {"configurable": {"thread_id": investigation_id}}

    # 1. Inject human verdict into graph
    graph_app.update_state(config, {"human_approved": req.approved})

    # 2. Resume graph to final node
    for event in graph_app.stream(None, config):
        pass

    final_state = graph_app.get_state(config)
    final_values = final_state.values if final_state else {}

    risk_lvl = final_values.get("risk_level", "UNKNOWN")

    # 3. Finalize in SQL Database
    record = finalize_investigation(
        record_id=record_id,
        plan=final_values.get("plan"),
        risk_level=risk_lvl,
        human_approved=req.approved,
        supervisor_reasoning=final_values.get("supervisor_reasoning", ""),
        final_report=final_values.get("final_report", ""),
        db=db
    )

    return InvestigationResponse(
        id=investigation_id,
        subject_name=record.subject_name if record else "",
        ticker=record.ticker if record else "",
        status=record.status.value if hasattr(record.status, "value") else "completed",
        plan=final_values.get("plan"),
        risk_level=risk_lvl,
        risk_assessment=final_values.get("risk_assessment"),
        tool_audit=_tool_audit(final_values),
        critic_review=final_values.get("critic_review"),
        memory_data=final_values.get("memory_data"),
        graph_data=final_values.get("graph_data"),
        fraud_data=final_values.get("fraud_data"),
        confidence=final_values.get("confidence"),
        human_approved=req.approved,
        supervisor_reasoning=final_values.get("supervisor_reasoning", ""),
        final_report=final_values.get("final_report", ""),
        research_data=final_values.get("research_data", []),
        financial_data=final_values.get("financial_data", {}),
        news_data=final_values.get("news_data", []),
        compliance_data=final_values.get("compliance_data"),
        sanctions_data=final_values.get("sanctions_data"),
        edgar_data=final_values.get("edgar_data"),
        logs=final_values.get("logs", []),
        created_at=record.created_at.isoformat() if record and record.created_at else ""
    )


@app.get("/investigations/{investigation_id}/report")
def download_report_endpoint(
    investigation_id: str,
    format: str = Query("md", pattern="^(md|pdf)$"),
    db: Session = Depends(get_db),
):
    """Serves a stored executive report as Markdown or as a typeset PDF.

    Reports are read from the ledger rather than regenerated, so a document
    pulled months later is the same one the officer approved.
    """
    try:
        record_id = uuid.UUID(investigation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Malformed investigation id")

    record = fetch_investigation(record_id, db=db)
    if not record:
        raise HTTPException(status_code=404, detail="Investigation record not found")

    markdown = record.final_report or ""
    if not markdown.strip():
        raise HTTPException(status_code=404, detail="No report has been generated for this investigation")

    meta = {
        "id": str(record.id),
        "subject_name": record.subject_name,
        "ticker": record.ticker,
        "risk_level": record.risk_level,
        "score": extract_score(markdown),
        "human_approved": record.human_approved,
        "created_at": record.created_at.isoformat() if record.created_at else "",
    }

    if format == "pdf":
        body = render_pdf(markdown, meta)
        media_type = "application/pdf"
    else:
        body = markdown.encode("utf-8")
        media_type = "text/markdown; charset=utf-8"

    name = filename_for(meta, format)
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            # The browser fetches this cross-origin, so the name must be readable.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


# --- Live agent telemetry (Server-Sent Events) ---

# Node names the UI renders on the topology graph. The graph runs the four
# specialists in a single parallel superstep, so LangGraph's "tasks" stream is
# what lets the UI show them genuinely running at the same time rather than
# faking a sequence.
def _graph_nodes() -> frozenset:
    """Node names taken from the compiled graph rather than a hand-kept list.

    A literal list silently drops telemetry for any node added later, which is
    how the memory, fraud, network and critic agents came to report as idle in
    the console while running normally.
    """
    try:
        return frozenset(
            name for name in graph_app.get_graph().nodes if not name.startswith("__")
        )
    except Exception:
        return frozenset()


GRAPH_NODES = _graph_nodes()


def _sse(event: str, payload: Dict[str, Any]) -> str:
    """Serialises one Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _first_log(result: Any) -> str:
    """Pulls the human-readable log line a node wrote, if it wrote one."""
    if not isinstance(result, dict):
        return ""
    logs = result.get("logs")
    if isinstance(logs, dict):  # channel received multiple writes
        logs = logs.get("$writes")
    if isinstance(logs, list) and logs:
        return str(logs[0])
    return str(logs) if logs else ""


def _tool_audit(values: Dict[str, Any]) -> Dict[str, Any]:
    """Rebuilds the audit trail from state so the API returns it summarised.

    The raw rows are kept too, but the summary is what makes the panel useful at
    a glance: it says how many calls returned data, how many found nothing, and
    which ones never ran.
    """
    return ToolAuditTrail.from_dicts(values.get("tool_calls") or []).to_dict()


def _build_response(
    *,
    investigation_id: str,
    subject_name: str,
    ticker: Optional[str],
    status_value: str,
    values: Dict[str, Any],
    created_at: str,
) -> InvestigationResponse:
    """Shapes graph state plus record metadata into the API response model."""
    return InvestigationResponse(
        id=investigation_id,
        subject_name=subject_name,
        ticker=ticker or "",
        status=status_value,
        plan=values.get("plan"),
        risk_level=values.get("risk_level", "UNKNOWN"),
        risk_assessment=values.get("risk_assessment"),
        tool_audit=_tool_audit(values),
        critic_review=values.get("critic_review"),
        memory_data=values.get("memory_data"),
        graph_data=values.get("graph_data"),
        fraud_data=values.get("fraud_data"),
        confidence=values.get("confidence"),
        human_approved=values.get("human_approved"),
        supervisor_reasoning=values.get("supervisor_reasoning", ""),
        final_report=values.get("final_report", ""),
        research_data=values.get("research_data", []),
        financial_data=values.get("financial_data", {}),
        news_data=values.get("news_data", []),
        compliance_data=values.get("compliance_data"),
        sanctions_data=values.get("sanctions_data"),
        edgar_data=values.get("edgar_data"),
        logs=values.get("logs", []),
        created_at=created_at,
    )


def _stream_graph(config: Dict[str, Any], graph_input: Optional[Dict[str, Any]]) -> Iterator[str]:
    """Runs the graph, yielding an SSE frame each time a node starts or settles.

    LangGraph emits a task payload carrying `input`/`triggers` when a node is
    dispatched and one carrying `result`/`error` when it settles, so the two are
    told apart by which keys are present.
    """
    origin = time.perf_counter()
    started_at: Dict[str, int] = {}

    def elapsed() -> int:
        return int((time.perf_counter() - origin) * 1000)

    try:
        for chunk in graph_app.stream(graph_input, config, stream_mode="tasks"):
            if not isinstance(chunk, dict):
                continue
            node = str(chunk.get("name") or "")
            if node not in GRAPH_NODES:
                continue

            if "result" in chunk or "error" in chunk:
                start = started_at.pop(node, None)
                result = chunk.get("result")
                result = result if isinstance(result, dict) else {}
                now = elapsed()
                yield _sse(
                    "node.end",
                    {
                        "node": node,
                        "at": now,
                        "duration_ms": (now - start) if start is not None else None,
                        "log": _first_log(result),
                        "channels": [key for key in result if key != "logs"],
                        "risk_level": result.get("risk_level"),
                        "error": str(chunk["error"]) if chunk.get("error") else None,
                    },
                )
            else:
                started_at[node] = elapsed()
                yield _sse("node.start", {"node": node, "at": started_at[node]})
    except Exception as exc:  # keep the stream alive so the UI can settle its nodes
        yield _sse("run.error", {"message": str(exc), "at": elapsed()})


@app.post("/investigations/stream")
def start_investigation_stream_endpoint(req: StartInvestigationRequest, db: Session = Depends(get_db)):
    """Same work as POST /investigations, but streams per-agent progress live."""
    db_record = create_investigation(subject_name=req.subject_name, ticker=req.ticker, db=db)

    # Read everything off the record up front: the request-scoped session is
    # already closed by the time the streaming generator runs.
    record_id = str(db_record.id)
    record_status = db_record.status.value if hasattr(db_record.status, "value") else str(db_record.status)
    created_at = db_record.created_at.isoformat() if db_record.created_at else ""

    def event_stream() -> Iterator[str]:
        config = {"configurable": {"thread_id": record_id}}
        yield _sse(
            "run.start",
            {
                "investigation_id": record_id,
                "subject_name": req.subject_name,
                "ticker": req.ticker or "",
                "created_at": created_at,
                "nodes": list(GRAPH_NODES),
            },
        )

        yield from _stream_graph(
            config,
            {
                "investigation_id": record_id,
                "ticker": req.ticker,
                "subject_name": req.subject_name,
            },
        )

        state = graph_app.get_state(config)
        yield _sse(
            "run.complete",
            _build_response(
                investigation_id=record_id,
                subject_name=req.subject_name,
                ticker=req.ticker,
                status_value=record_status,
                values=state.values if state else {},
                created_at=created_at,
            ).model_dump(),
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/investigations/{investigation_id}/approve/stream")
def approve_investigation_stream_endpoint(investigation_id: str, req: ApprovalRequest):
    """Resumes the graph past the human checkpoint, streaming the remaining nodes."""
    # Rejected here rather than inside the generator: once streaming has begun
    # the status code is already sent, and the caller would receive a 200 whose
    # body happens to contain an error.
    record_id = _record_id(investigation_id)
    config = {"configurable": {"thread_id": investigation_id}}
    graph_app.update_state(config, {"human_approved": req.approved})

    def event_stream() -> Iterator[str]:
        yield _sse("run.start", {"investigation_id": investigation_id, "resumed": True})

        yield from _stream_graph(config, None)

        state = graph_app.get_state(config)
        final_values = (state.values if state else {}) or {}

        # The generator outlives the request-scoped session, so open its own.
        db = SessionLocal()
        try:
            record = finalize_investigation(
                record_id=record_id,
                risk_level=final_values.get("risk_level", "UNKNOWN"),
                human_approved=req.approved,
                supervisor_reasoning=final_values.get("supervisor_reasoning", ""),
                final_report=final_values.get("final_report", ""),
                db=db,
            )
            payload = _build_response(
                investigation_id=investigation_id,
                subject_name=record.subject_name if record else "",
                ticker=record.ticker if record else "",
                status_value=(
                    record.status.value
                    if record is not None and hasattr(record.status, "value")
                    else "completed"
                ),
                values={**final_values, "human_approved": req.approved},
                created_at=record.created_at.isoformat() if record and record.created_at else "",
            ).model_dump()
        finally:
            db.close()

        yield _sse("run.complete", payload)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- Reference asset library (documentation & datasets browser) ---

def _list_asset_dir(directory: Path, category: str) -> List[Dict[str, Any]]:
    if not directory.is_dir():
        return []
    items = []
    for entry in sorted(directory.iterdir()):
        if not entry.is_file():
            continue
        stat = entry.stat()
        items.append({
            "id": f"{category}:{entry.name}",
            "name": entry.stem,
            "filename": entry.name,
            "type": category,
            "ext": entry.suffix.lstrip(".").lower(),
            "size": stat.st_size,
            "path": f"/assets/{category}/{entry.name}",
            "uploaded_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        })
    return items


@app.get("/tools", response_model=List[Dict[str, Any]])
def list_tools_endpoint():
    """The tool catalogue, with each tool's live availability.

    Deployment problems here are invisible from the verdict alone: an
    investigation run without a search key or without an indexed corpus still
    produces a confident-looking result, just on thinner evidence. This endpoint
    lets the operator see which capabilities are actually online before trusting
    one.
    """
    return get_registry().describe()


@app.get("/assets/files", response_model=List[Dict[str, Any]])
def list_asset_files_endpoint():
    """Lists downloadable reference documentation and dataset files."""
    files: List[Dict[str, Any]] = []
    for category, directory in ASSET_DIRECTORIES.items():
        files.extend(_list_asset_dir(directory, category))
    return files


@app.get("/assets/{category}/{filename}")
def download_asset_file_endpoint(category: str, filename: str):
    """Streams a single reference file for download."""
    directory = ASSET_DIRECTORIES.get(category)
    if directory is None:
        raise HTTPException(status_code=404, detail="Unknown asset category")

    # os.path.basename strips any directory components to prevent path traversal.
    safe_name = os.path.basename(filename)
    file_path = directory / safe_name
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(file_path, filename=safe_name)