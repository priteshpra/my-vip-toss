"""Web application routes, HTMX partials, and administrative endpoints."""

import os
from typing import Optional, List
from fastapi import APIRouter, Request, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, distinct
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, format_ist, utc_to_ist
from app.config import settings
from app.models import Match, Team, Tournament, Venue, TossResult, PredictionSnapshot, PredictionEvaluation
from app.schemas import PredictionCreate, TossResultCreate
from app.auth.security import verify_admin_credentials, create_signed_session_token
from app.auth.dependencies import get_current_admin_optional, get_current_admin
from app.services.match_service import get_matches, get_match_by_id
from app.services.toss_service import record_toss_result
from app.services.prediction_service import lock_prediction, verify_prediction_integrity
from app.services.accuracy_service import get_accuracy_metrics_for_source
from app.services.analysis_service import get_team_toss_stats, get_h2h_toss_stats
from app.services.source_service import get_source_leaderboard
from app.jobs.tasks import sync_live_matches_task, verify_toss_results_task

router = APIRouter()

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


# Helper to inject common template context
def template_ctx(request: Request, **kwargs) -> dict:
    ctx = {
        "request": request,
        "current_admin": get_current_admin_optional(request),
        "app_timezone": settings.app_timezone,
    }
    ctx.update(kwargs)
    return ctx


# --------------------------------------------------------------------------
# 1. Dashboard View
# --------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    session: AsyncSession = Depends(get_db)
):
    """Admin dashboard overview."""
    # 1. Total counts
    matches_count_res = await session.execute(select(func.count(Match.id)))
    total_matches = matches_count_res.scalar_one()

    upcoming_count_res = await session.execute(
        select(func.count(Match.id)).where(Match.status.in_(("UPCOMING", "TOSS_PENDING")))
    )
    upcoming_count = upcoming_count_res.scalar_one()

    toss_count_res = await session.execute(select(func.count(TossResult.match_id)))
    verified_tosses = toss_count_res.scalar_one()

    # 2. Upcoming matches
    upcoming_matches = await get_matches(session, status="UPCOMING", limit=8)
    if not upcoming_matches:
        # Check any pending
        upcoming_matches = await get_matches(session, limit=8)

    # Hydrate IST display strings
    for m in upcoming_matches:
        m.scheduled_start_ist = format_ist(m.scheduled_start_utc)

    # 3. Completed matches with toss results
    completed_matches = await get_matches(session, status="TOSS_COMPLETED", limit=8)
    for m in completed_matches:
        m.scheduled_start_ist = format_ist(m.scheduled_start_utc)

    # 4. Overall accuracy metrics & leaderboard
    overall_metrics = await get_accuracy_metrics_for_source(session)
    leaderboard = await get_source_leaderboard(session)

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context=template_ctx(
            request,
            active_page="dashboard",
            stats={
                "total_matches": total_matches,
                "upcoming_matches": upcoming_count,
                "verified_tosses": verified_tosses,
            },
            upcoming_matches=upcoming_matches,
            completed_matches=completed_matches,
            overall_metrics=overall_metrics,
            leaderboard=leaderboard
        )
    )


# --------------------------------------------------------------------------
# 2. Match Centre Views & HTMX Partials
# --------------------------------------------------------------------------
@router.get("/matches", response_class=HTMLResponse)
async def matches_view(
    request: Request,
    status: Optional[str] = None,
    tournament_id: Optional[str] = None,
    search: Optional[str] = None,
    session: AsyncSession = Depends(get_db)
):
    """Match centre list page."""
    matches = await get_matches(
        session, status=status or None, tournament_id=tournament_id or None, search=search or None, limit=50
    )
    for m in matches:
        m.scheduled_start_ist = format_ist(m.scheduled_start_utc)

    # Tournaments for filter dropdown
    tourn_res = await session.execute(select(Tournament).order_by(Tournament.name))
    tournaments = tourn_res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="matches.html",
        context=template_ctx(
            request,
            active_page="matches",
            matches=matches,
            tournaments=tournaments,
            current_status=status or "",
            current_tournament=tournament_id or "",
            search=search or ""
        )
    )


@router.get("/partials/matches-table", response_class=HTMLResponse)
async def matches_table_partial(
    request: Request,
    status: Optional[str] = None,
    tournament_id: Optional[str] = None,
    search: Optional[str] = None,
    session: AsyncSession = Depends(get_db)
):
    """HTMX partial for filtered matches table."""
    matches = await get_matches(
        session, status=status or None, tournament_id=tournament_id or None, search=search or None, limit=50
    )
    for m in matches:
        m.scheduled_start_ist = format_ist(m.scheduled_start_utc)

    return templates.TemplateResponse(
        request=request,
        name="partials/matches_table.html",
        context=template_ctx(request, matches=matches)
    )


@router.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail_view(
    request: Request,
    match_id: str,
    session: AsyncSession = Depends(get_db)
):
    """Match detail page with longitudinal statistics and locked pre-toss calls."""
    match = await get_match_by_id(session, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match fixture not found.")

    match.scheduled_start_ist = format_ist(match.scheduled_start_utc)
    for p in match.predictions:
        p.locked_at_ist = format_ist(p.locked_at_utc)

    team_a_name = match.team_a.name if match.team_a else match.team_a_id
    team_b_name = match.team_b.name if match.team_b else match.team_b_id

    stats_a = await get_team_toss_stats(session, team_a_name)
    stats_b = await get_team_toss_stats(session, team_b_name)
    h2h_stats = await get_h2h_toss_stats(session, team_a_name, team_b_name)

    return templates.TemplateResponse(
        request=request,
        name="match_detail.html",
        context=template_ctx(
            request,
            active_page="matches",
            match=match,
            stats_a=stats_a,
            stats_b=stats_b,
            h2h_stats=h2h_stats
        )
    )


# --------------------------------------------------------------------------
# 3. Prediction Ledger & Hash Verification
# --------------------------------------------------------------------------
@router.get("/predictions", response_class=HTMLResponse)
async def predictions_ledger_view(
    request: Request,
    source_channel: Optional[str] = None,
    action_type: Optional[str] = None,
    session: AsyncSession = Depends(get_db)
):
    """Append-only prediction ledger."""
    query = (
        select(PredictionSnapshot)
        .options(
            joinedload(PredictionSnapshot.match),
            joinedload(PredictionSnapshot.predicted_team),
            joinedload(PredictionSnapshot.evaluation),
        )
        .order_by(PredictionSnapshot.locked_at_utc.desc())
    )

    if source_channel:
        query = query.where(PredictionSnapshot.source_channel == source_channel)
    if action_type:
        query = query.where(PredictionSnapshot.action_type == action_type)

    result = await session.execute(query.limit(100))
    predictions = result.unique().scalars().all()

    for p in predictions:
        p.locked_at_ist = format_ist(p.locked_at_utc)

    # Distinct source channels for filter
    src_res = await session.execute(
        select(PredictionSnapshot.source_channel).distinct().where(PredictionSnapshot.source_channel.isnot(None))
    )
    sources = [s for s in src_res.scalars().all() if s]

    return templates.TemplateResponse(
        request=request,
        name="predictions.html",
        context=template_ctx(
            request,
            active_page="predictions",
            predictions=predictions,
            sources=sources,
            current_source=source_channel or "",
            current_action=action_type or ""
        )
    )


@router.get("/partials/verify-prediction/{prediction_id}", response_class=HTMLResponse)
async def verify_prediction_partial(
    request: Request,
    prediction_id: int,
    session: AsyncSession = Depends(get_db)
):
    """HTMX partial to verify cryptographic SHA-256 seal."""
    try:
        audit = await verify_prediction_integrity(session, prediction_id)
        return templates.TemplateResponse(
            request=request,
            name="partials/audit_result.html",
            context=template_ctx(request, audit=audit)
        )
    except Exception as e:
        return HTMLResponse(f"<div class='text-xs text-rose-400'>Error: {str(e)}</div>")


# --------------------------------------------------------------------------
# 4. Statistical Validation & Tracked Sources
# --------------------------------------------------------------------------
@router.get("/analytics", response_class=HTMLResponse)
async def analytics_view(
    request: Request,
    session: AsyncSession = Depends(get_db)
):
    """Statistical validation page with Wilson score interval and baseline null test."""
    metrics = await get_accuracy_metrics_for_source(session)
    return templates.TemplateResponse(
        request=request,
        name="analytics.html",
        context=template_ctx(request, active_page="analytics", metrics=metrics)
    )


@router.get("/sources", response_class=HTMLResponse)
async def sources_view(
    request: Request,
    session: AsyncSession = Depends(get_db)
):
    """Audited leaderboard of tracked sources and channels."""
    leaderboard = await get_source_leaderboard(session)
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context=template_ctx(request, active_page="sources", leaderboard=leaderboard)
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_tool_view(
    request: Request
):
    """Cryptographic hash audit tool."""
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context=template_ctx(request, active_page="audit")
    )


# --------------------------------------------------------------------------
# 5. Admin Authentication Views
# --------------------------------------------------------------------------
@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: Optional[str] = "/"
):
    """Admin login page."""
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=template_ctx(request, next_url=next, error=None)
    )


@router.post("/auth/login", response_class=HTMLResponse)
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: Optional[str] = Form("/")
):
    """Handle admin credentials and issue signed session cookie."""
    if verify_admin_credentials(username, password):
        token = create_signed_session_token({"username": username})
        response = RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            key=settings.session_cookie_name,
            value=token,
            httponly=True,
            samesite="lax",
            max_age=86400 * 2,
        )
        return response

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=template_ctx(request, next_url=next, error="Invalid username or password.")
    )


@router.get("/auth/logout")
async def logout_action():
    """Clear admin session cookie."""
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.session_cookie_name)
    return response


# --------------------------------------------------------------------------
# 6. Admin Actions (Protected)
# --------------------------------------------------------------------------
@router.post("/admin/sync-now", response_class=HTMLResponse)
async def admin_sync_now(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Trigger immediate sync tasks."""
    try:
        await sync_live_matches_task()
        await verify_toss_results_task()
        return HTMLResponse("<span class='text-emerald-400'>✓ Sync completed successfully!</span>")
    except Exception as e:
        return HTMLResponse(f"<span class='text-rose-400'>Sync error: {str(e)}</span>")


@router.post("/admin/matches/{match_id}/toss", response_class=HTMLResponse)
async def admin_record_toss(
    request: Request,
    match_id: str,
    toss_winner_id: str = Form(...),
    toss_decision: str = Form("BAT"),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Manually record official ground truth toss result."""
    try:
        toss_data = TossResultCreate(
            match_id=match_id,
            toss_winner_id=toss_winner_id,
            toss_decision=toss_decision,
            notes=f"Recorded manually by admin {admin.get('username')}"
        )
        await record_toss_result(session, toss_data, source_provider_id="ADMIN_MANUAL")
        return HTMLResponse(
            f"<div class='p-2 bg-emerald-950/60 text-emerald-300 border border-emerald-800 rounded-lg text-xs'>"
            f"✓ Confirmed toss for {match_id}: Winner {toss_winner_id} ({toss_decision}). Refresh to view updated evaluations.</div>"
        )
    except Exception as e:
        return HTMLResponse(f"<div class='text-xs text-rose-400'>Failed: {str(e)}</div>")


# --------------------------------------------------------------------------
# 7. JSON API Endpoints
# --------------------------------------------------------------------------
@router.get("/api/predictions/audit")
async def api_prediction_audit(
    prediction_id: int,
    session: AsyncSession = Depends(get_db)
):
    """API endpoint for verifying cryptographic hash of a prediction."""
    try:
        audit = await verify_prediction_integrity(session, prediction_id)
        return JSONResponse(audit.model_dump(mode="json"))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/api/predictions/lock", status_code=status.HTTP_201_CREATED)
async def api_lock_prediction(
    data: PredictionCreate,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Lock a pre-toss prediction cryptographically."""
    try:
        snapshot = await lock_prediction(session, data)
        return {
            "success": True,
            "prediction_id": snapshot.id,
            "match_id": snapshot.match_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "locked_at_utc": snapshot.locked_at_utc.isoformat(),
            "is_pre_toss": snapshot.is_pre_toss,
            "message": "Prediction cryptographically locked. Append-only."
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/toss-results", status_code=status.HTTP_201_CREATED)
async def api_record_toss(
    data: TossResultCreate,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Record verified ground truth toss result."""
    try:
        toss = await record_toss_result(session, data, source_provider_id="API")
        return {
            "success": True,
            "match_id": toss.match_id,
            "toss_winner_id": toss.toss_winner_id,
            "toss_decision": toss.toss_decision,
            "verified_at_utc": toss.verified_at_utc.isoformat()
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
