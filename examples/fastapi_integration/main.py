"""Example: Daedalus as a FastAPI service.

Exposes the agent and proposals over HTTP.

Prerequisites:
  pip install daedalus-agent fastapi uvicorn

Run:
  uvicorn examples.fastapi_integration.main:app --reload

Endpoints:
  POST /run              — run the agent
  GET  /proposals        — list pending proposals
  POST /proposals/{id}/approve — approve a proposal
  POST /proposals/{id}/reject  — reject a proposal
"""
import sys
sys.path.insert(0, "../..")

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from daedalus import Agent
from daedalus.agent import AgentConfig
from daedalus.proposals import ProposalStore

app = FastAPI(title="Daedalus Agent API", version="0.1.0")

_config = AgentConfig.from_env()
_agent = Agent(_config)
_proposals = _agent.proposals


class RunRequest(BaseModel):
    message: str
    context: str = ""


class RunResponse(BaseModel):
    ok: bool
    response: str
    iterations: int
    elapsed_ms: int
    tool_calls: list[dict]
    error: str = ""


class RejectRequest(BaseModel):
    reason: str = ""


@app.post("/run", response_model=RunResponse)
async def run_agent(req: RunRequest):
    result = await _agent.run(req.message, context=req.context)
    return RunResponse(
        ok=result.ok,
        response=result.response,
        iterations=result.iterations,
        elapsed_ms=result.elapsed_ms,
        tool_calls=result.tool_calls,
        error=result.error,
    )


@app.get("/proposals")
def list_proposals():
    return [p.to_dict() for p in _proposals.list_pending()]


@app.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: str):
    result = _proposals.approve(proposal_id)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: str, req: RejectRequest):
    result = _proposals.reject(proposal_id, reason=req.reason)
    if not result["ok"]:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}
