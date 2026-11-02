"""Web application routes, HTMX partials, and administrative endpoints."""

import os
from datetime import datetime, date
from typing import Optional, List
from fastapi import APIRouter, Request, Depends, HTTPException, status, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, distinct, desc
from sqlalchemy.orm import selectinload, joinedload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db, format_ist, utc_to_ist
from app.config import settings
from app.models import (
    Match, Team, League, Tournament, Venue, TossResult,
    Prediction, PredictionOutcome, ExternalSource, MarketEntry,
    TelegramReview, CollectorHealth, AuditLog
)
from app.schemas import PredictionCreate, TossResultCreate
from app.auth.security import (
    authenticate_admin_user,
    create_signed_session_token,
    verify_admin_credentials
)
from app.auth.dependencies import (
    get_current_admin_optional,
    get_current_admin,
    RequiresLoginException
)
from app.auth.csrf import generate_csrf_token, validate_csrf_token, verify_csrf
from app.auth.rate_limiter import login_rate_limiter
from app.services.audit_service import log_audit_event, get_audit_logs, get_client_ip
from app.services.match_service import get_matches, get_match_by_id
from app.services.toss_service import record_toss_result, get_toss_history, export_toss_history_csv
from app.services.prediction_service import (
    lock_prediction,
    verify_prediction_integrity,
    generate_lean_from_analysis,
    lock_match_prediction
)
from app.services.accuracy_service import (
    get_accuracy_metrics_for_source,
    get_accuracy_dashboard_data
)
from app.services.totals_service import (
    get_totals_overview,
    get_totals_by_league,
    get_totals_by_team,
    get_totals_by_timeframe
)
from app.services.analysis_service import (
    get_team_toss_stats,
    get_h2h_toss_stats,
    get_venue_toss_stats,
    get_comprehensive_match_analysis
)
from app.services.market_service import (
    get_market_entries_for_match,
    record_manual_market_entry
)
from app.services.export_service import generate_match_csv, generate_match_pdf
from app.services.source_service import (
    get_source_leaderboard,
    get_source_accuracy_leaderboard,
    get_all_sources,
    get_source_by_id,
    create_external_source,
    update_external_source,
    toggle_source_enabled,
    record_manual_source_snapshot,
    get_source_comparison_for_match,
    get_consensus_historical_accuracy,
    evaluate_combined_panel_lean
)
from app.jobs.tasks import sync_live_matches_task, verify_toss_results_task

router = APIRouter()

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def template_ctx(request: Request, **kwargs) -> dict:
    """Inject common template context including CSRF tokens and admin identity."""
    ctx = {
        "request": request,
        "current_admin": get_current_admin_optional(request),
        "app_timezone": settings.app_timezone,
        "csrf_token": generate_csrf_token(),
    }
    ctx.update(kwargs)
    return ctx


# --------------------------------------------------------------------------
# 1. Dashboard View (Protected)
# --------------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
async def dashboard_view(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Admin dashboard overview."""
    # 1. Total counts
    matches_count_res = await session.execute(select(func.count(Match.id)))
    total_matches = matches_count_res.scalar_one()

    upcoming_count_res = await session.execute(
        select(func.count(Match.id)).where(Match.status.in_(("upcoming", "UPCOMING", "toss_pending")))
    )
    upcoming_count = upcoming_count_res.scalar_one()

    toss_count_res = await session.execute(select(func.count(TossResult.match_id)))
    verified_tosses = toss_count_res.scalar_one()

    # 2. Upcoming matches
    upcoming_matches = await get_matches(session, status="upcoming", limit=8)
    if not upcoming_matches:
        upcoming_matches = await get_matches(session, limit=8)

    for m in upcoming_matches:
        m.scheduled_start_ist = format_ist(m.scheduled_start_utc)

    # 3. Completed matches with toss results
    completed_matches = await get_matches(session, status="toss_done", limit=8)
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
# 2. Match Centre Views & HTMX Partials (Protected)
# --------------------------------------------------------------------------
@router.get("/matches", response_class=HTMLResponse)
async def matches_view(
    request: Request,
    tab: Optional[str] = "all",
    status: Optional[str] = None,
    tournament_id: Optional[str] = None,
    search: Optional[str] = None,
    venue_search: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Match centre list page with lifecycle tabs and advanced filters."""
    matches = await get_matches(
        session,
        status=status or None,
        tab=tab or "all",
        tournament_id=tournament_id or None,
        search=search or None,
        venue_search=venue_search or None,
        outcome_filter=outcome_filter or None,
        limit=50
    )
    for m in matches:
        m.scheduled_start_ist = format_ist(m.scheduled_start_utc)

    tourn_res = await session.execute(select(League).order_by(League.name))
    tournaments = tourn_res.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="matches.html",
        context=template_ctx(
            request,
            active_page="matches",
            matches=matches,
            tournaments=tournaments,
            current_tab=tab or "all",
            current_status=status or "",
            current_tournament=tournament_id or "",
            search=search or "",
            venue_search=venue_search or "",
            outcome_filter=outcome_filter or ""
        )
    )


@router.get("/partials/matches-table", response_class=HTMLResponse)
async def matches_table_partial(
    request: Request,
    tab: Optional[str] = "all",
    status: Optional[str] = None,
    tournament_id: Optional[str] = None,
    search: Optional[str] = None,
    venue_search: Optional[str] = None,
    outcome_filter: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """HTMX partial for filtered matches table."""
    matches = await get_matches(
        session,
        status=status or None,
        tab=tab or "all",
        tournament_id=tournament_id or None,
        search=search or None,
        venue_search=venue_search or None,
        outcome_filter=outcome_filter or None,
        limit=50
    )
    for m in matches:
        m.scheduled_start_ist = format_ist(m.scheduled_start_utc)

    return templates.TemplateResponse(
        request=request,
        name="partials/matches_table.html",
        context=template_ctx(
            request,
            matches=matches,
            current_tab=tab or "all",
            current_status=status or "",
            current_tournament=tournament_id or "",
            search=search or "",
            venue_search=venue_search or "",
            outcome_filter=outcome_filter or ""
        )
    )


@router.get("/matches/{match_id}", response_class=HTMLResponse)
async def match_detail_view(
    request: Request,
    match_id: str,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Match detail page with full longitudinal analysis and market intelligence."""
    match = await get_match_by_id(session, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match fixture not found.")

    match.scheduled_start_ist = format_ist(match.scheduled_start_utc)
    for p in match.predictions:
        p.locked_at_ist = format_ist(p.locked_at_utc)

    # Full analysis report
    analysis = await get_comprehensive_match_analysis(session, match)
    market_entries = await get_market_entries_for_match(session, match.id)
    for me in market_entries:
        me.fetched_at_ist = format_ist(me.fetched_at_utc)

    # Phase 6: Multi-Source Comparison, Consensus, and Combined Panel Lean
    model_lean_info = generate_lean_from_analysis(analysis)
    model_lean_str = model_lean_info.get("lean")
    source_comparison_data = await get_source_comparison_for_match(session, match.id, model_lean_str)
    consensus_historical = await get_consensus_historical_accuracy(session)
    combined_panel_lean = evaluate_combined_panel_lean(
        model_lean_info=model_lean_info,
        consensus_info=source_comparison_data["consensus"],
        consensus_historical=consensus_historical
    )
    configured_sources = await get_all_sources(session)

    return templates.TemplateResponse(
        request=request,
        name="match_detail.html",
        context=template_ctx(
            request,
            active_page="matches",
            match=match,
            analysis=analysis,
            stats_a=analysis["stats_a"],
            stats_b=analysis["stats_b"],
            h2h_stats=analysis["h2h_stats"],
            venue_stats=analysis["venue_stats"],
            market_entries=market_entries,
            source_snapshots=source_comparison_data["snapshots"],
            source_comparison=source_comparison_data,
            consensus_info=source_comparison_data["consensus"],
            consensus_historical=consensus_historical,
            combined_panel_lean=combined_panel_lean,
            model_lean_info=model_lean_info,
            configured_sources=configured_sources
        )
    )


@router.get("/matches/{match_id}/export/csv")
async def export_match_csv(
    match_id: str,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Export complete match analysis dossier as structured CSV."""
    match = await get_match_by_id(session, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found.")

    analysis = await get_comprehensive_match_analysis(session, match)
    csv_content = generate_match_csv(analysis, match)

    filename = f"match_{match_id}_toss_analysis.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.get("/matches/{match_id}/export/pdf")
async def export_match_pdf(
    match_id: str,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Export complete match analysis report as a formatted PDF."""
    match = await get_match_by_id(session, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found.")

    analysis = await get_comprehensive_match_analysis(session, match)
    pdf_bytes = generate_match_pdf(analysis, match)

    filename = f"match_{match_id}_toss_analysis.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/admin/matches/{match_id}/market", response_class=HTMLResponse)
async def admin_record_market_entry(
    request: Request,
    match_id: int,
    source: str = Form("Manual Exchange"),
    team_lean: Optional[str] = Form(None),
    load_market: Optional[str] = Form(None),
    odds_a: Optional[float] = Form(None),
    odds_b: Optional[float] = Form(None),
    notes: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf)
):
    """Record verified human-entered market intelligence (immutable append-only)."""
    client_ip = get_client_ip(request)
    try:
        entry = await record_manual_market_entry(
            session=session,
            match_id=match_id,
            source=source,
            team_lean=team_lean,
            load_market=load_market,
            odds_a=odds_a,
            odds_b=odds_b,
            notes=notes,
            admin_user=admin.get("username", "admin"),
            client_ip=client_ip
        )
        entry_ist = format_ist(entry.fetched_at_utc)
        return HTMLResponse(
            f"<tr class='bg-emerald-950/40 text-xs font-mono transition-colors'>"
            f"<td class='py-2 px-3 text-slate-300 font-sans font-medium'>{entry.source}</td>"
            f"<td class='py-2 px-3 text-emerald-400 font-bold'>{entry.team_lean or '-'}</td>"
            f"<td class='py-2 px-3 text-slate-300'>{entry.load_market or '-'}</td>"
            f"<td class='py-2 px-3 text-slate-300'>{entry.odds_a or '-'} / {entry.odds_b or '-'}</td>"
            f"<td class='py-2 px-3 text-slate-400 text-xs font-sans'>{entry.notes or '-'}</td>"
            f"<td class='py-2 px-3 text-slate-400'>{entry_ist}</td>"
            f"</tr>"
        )
    except Exception as e:
        return HTMLResponse(f"<div class='text-xs text-rose-400 p-2'>Error: {str(e)}</div>")


# --------------------------------------------------------------------------
# 3. Toss History View & CSV Export (Protected)
# --------------------------------------------------------------------------
@router.get("/toss-history", response_class=HTMLResponse)
async def toss_history_view(
    request: Request,
    league_id: Optional[str] = None,
    team_id: Optional[str] = None,
    venue_id: Optional[str] = None,
    outcome: Optional[str] = None,
    source: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = 1,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Confirmed ground truth toss outcomes with rich filters, pre-toss calls, and evaluations."""
    # Parse filter types
    parsed_league_id = int(league_id) if league_id and league_id.isdigit() else None
    parsed_team_id = int(team_id) if team_id and team_id.isdigit() else None
    parsed_venue_id = int(venue_id) if venue_id and venue_id.isdigit() else None

    d_from = None
    if date_from:
        try:
            d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        except Exception:
            pass

    d_to = None
    if date_to:
        try:
            d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        except Exception:
            pass

    # Fetch filter lists
    leagues_res = await session.execute(select(League).order_by(League.name))
    leagues = list(leagues_res.scalars().all())

    teams_res = await session.execute(select(Team).order_by(Team.name))
    teams = list(teams_res.scalars().all())

    # Get history
    history_data = await get_toss_history(
        session=session,
        league_id=parsed_league_id,
        team_id=parsed_team_id,
        venue_id=parsed_venue_id,
        outcome_filter=outcome,
        source_filter=source,
        date_from=d_from,
        date_to=d_to,
        page=page,
        page_size=30
    )

    return templates.TemplateResponse(
        request=request,
        name="toss_history.html",
        context=template_ctx(
            request,
            active_page="toss_history",
            items=history_data["items"],
            tosses=history_data["items"],
            total_count=history_data["total_count"],
            page=history_data["page"],
            total_pages=history_data["total_pages"],
            leagues=leagues,
            teams=teams,
            current_league_id=league_id or "",
            current_team_id=team_id or "",
            current_outcome=outcome or "ALL",
            current_source=source or "",
            current_date_from=date_from or "",
            current_date_to=date_to or ""
        )
    )


@router.get("/toss-history/export/csv")
async def toss_history_export_csv_view(
    league_id: Optional[str] = None,
    team_id: Optional[str] = None,
    venue_id: Optional[str] = None,
    outcome: Optional[str] = None,
    source: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Export filtered toss history as audited CSV dossier."""
    parsed_league_id = int(league_id) if league_id and league_id.isdigit() else None
    parsed_team_id = int(team_id) if team_id and team_id.isdigit() else None
    parsed_venue_id = int(venue_id) if venue_id and venue_id.isdigit() else None

    d_from = None
    if date_from:
        try:
            d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        except Exception:
            pass

    d_to = None
    if date_to:
        try:
            d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        except Exception:
            pass

    history_data = await get_toss_history(
        session=session,
        league_id=parsed_league_id,
        team_id=parsed_team_id,
        venue_id=parsed_venue_id,
        outcome_filter=outcome,
        source_filter=source,
        date_from=d_from,
        date_to=d_to,
        page=1,
        page_size=10000
    )

    csv_content = export_toss_history_csv(history_data["items"])
    filename = f"toss_history_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-cache"
        }
    )


# --------------------------------------------------------------------------
# 4. Prediction Ledger & Hash Verification (Protected)
# --------------------------------------------------------------------------
@router.get("/predictions", response_class=HTMLResponse)
async def predictions_ledger_view(
    request: Request,
    source_channel: Optional[str] = None,
    action_type: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Append-only prediction ledger."""
    query = (
        select(Prediction)
        .options(
            joinedload(Prediction.match).joinedload(Match.team_a),
            joinedload(Prediction.match).joinedload(Match.team_b),
            joinedload(Prediction.predicted_team),
            joinedload(Prediction.outcome),
        )
        .order_by(Prediction.locked_at_utc.desc())
    )

    if source_channel:
        query = query.where(Prediction.source == source_channel)
    if action_type:
        if action_type == "SKIP":
            query = query.where(Prediction.lean == "NO_EDGE")
        else:
            query = query.where(Prediction.lean != "NO_EDGE")

    result = await session.execute(query.limit(100))
    predictions = result.unique().scalars().all()

    for p in predictions:
        p.locked_at_ist = format_ist(p.locked_at_utc)

    # Distinct source channels for filter
    src_res = await session.execute(
        select(Prediction.source).distinct().where(Prediction.source.isnot(None))
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
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
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
# 5. Statistical Validation & Accuracy Dashboard (Protected)
# --------------------------------------------------------------------------
@router.get("/accuracy", response_class=HTMLResponse)
@router.get("/analytics", response_class=HTMLResponse)
async def accuracy_dashboard_view(
    request: Request,
    source_channel: Optional[str] = None,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Accuracy and Mathematical Truth Dashboard with 95% Wilson CI, Brier score, and calibration."""
    data = await get_accuracy_dashboard_data(session, source_channel=source_channel)
    return templates.TemplateResponse(
        request=request,
        name="accuracy.html",
        context=template_ctx(
            request,
            active_page="accuracy",
            metrics=data["metrics"],
            diff_from_baseline=data["diff_from_baseline"],
            calibration_table=data["calibration_table"],
            streaks=data["streaks"],
            rolling_data=data["rolling_data"],
            is_indistinguishable_from_50=data["is_indistinguishable_from_50"],
            banner_message=data["banner_message"],
            calls_taken=data["calls_taken"],
            no_edge_skipped=data["no_edge_skipped"],
            disclaimer=data["disclaimer"]
        )
    )


# --------------------------------------------------------------------------
# 5b. Macro Totals & Trends Dashboard (Protected)
# --------------------------------------------------------------------------
@router.get("/totals", response_class=HTMLResponse)
async def totals_dashboard_view(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Macro totals and longitudinal trends dashboard."""
    overview = await get_totals_overview(session)
    league_totals = await get_totals_by_league(session)
    team_totals = await get_totals_by_team(session, limit=30)
    monthly_series = await get_totals_by_timeframe(session, interval="month")

    return templates.TemplateResponse(
        request=request,
        name="totals.html",
        context=template_ctx(
            request,
            active_page="totals",
            overview=overview,
            league_totals=league_totals,
            team_totals=team_totals,
            monthly_series=monthly_series
        )
    )


# --------------------------------------------------------------------------
# 5c. Manual Prediction Locking Route (Protected)
# --------------------------------------------------------------------------
@router.post("/admin/matches/{match_id}/prediction")
async def admin_lock_prediction_route(
    match_id: int,
    request: Request,
    csrf_token: str = Form(...),
    lean: Optional[str] = Form(None),
    predicted_team_id: Optional[str] = Form(None),
    probability: Optional[float] = Form(None),
    rationale: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Admin route to manually or automatically lock a pre-toss prediction before match start."""
    if not validate_csrf_token(csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")

    p_team_id = int(predicted_team_id) if predicted_team_id and predicted_team_id.isdigit() else None
    client_ip = get_client_ip(request)

    try:
        pred = await lock_match_prediction(
            session=session,
            match_id=match_id,
            lean=lean,
            predicted_team_id=p_team_id,
            probability=probability,
            source="manual",
            admin_user=admin.get("username", "admin"),
            client_ip=client_ip,
            punter_rationale=rationale,
            strict=True
        )
        if "HX-Request" in request.headers:
            return HTMLResponse(
                f"<div class='p-2 rounded bg-emerald-500/10 text-emerald-400 text-xs font-semibold'>"
                f"Prediction locked: {pred.lean} (SHA-256: {pred.hash[:12]}...)"
                f"</div>"
            )
        return RedirectResponse(url=f"/matches/{match_id}", status_code=303)
    except Exception as e:
        if "HX-Request" in request.headers:
            return HTMLResponse(
                f"<div class='p-2 rounded bg-rose-500/10 text-rose-400 text-xs font-semibold'>"
                f"Error: {str(e)}"
                f"</div>",
                status_code=400
            )
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/sources", response_class=HTMLResponse)
async def sources_view(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Audited leaderboard of tracked sources and channels, plus dynamic source management."""
    leaderboard = await get_source_accuracy_leaderboard(session)
    sources = await get_all_sources(session)
    consensus_accuracy = await get_consensus_historical_accuracy(session)
    return templates.TemplateResponse(
        request=request,
        name="sources.html",
        context=template_ctx(
            request,
            active_page="sources",
            leaderboard=leaderboard,
            sources=sources,
            consensus_accuracy=consensus_accuracy
        )
    )


@router.post("/admin/sources/create")
async def admin_create_source_route(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    url: Optional[str] = Form(None),
    parse_rules: Optional[str] = Form(None),
    poll_interval: int = Form(300),
    enabled: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Dynamically register a new external data source or tipper channel."""
    await verify_csrf(request, csrf_token)
    client_ip = get_client_ip(request)
    admin_user = admin.get("username", "admin")
    is_enabled = bool(enabled and enabled.lower() in ("true", "1", "on", "yes"))

    try:
        await create_external_source(
            session=session,
            name=name,
            source_type=type,
            url=url,
            parse_rules=parse_rules,
            poll_interval=poll_interval,
            enabled=is_enabled,
            admin_user=admin_user,
            client_ip=client_ip
        )
        return RedirectResponse(url="/sources", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        if "HX-Request" in request.headers:
            return HTMLResponse(f"<div class='p-2 rounded bg-rose-500/10 text-rose-400 text-xs font-semibold'>Error: {str(e)}</div>", status_code=400)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/sources/{source_id}/edit")
async def admin_edit_source_route(
    request: Request,
    source_id: int,
    name: str = Form(...),
    type: str = Form(...),
    url: Optional[str] = Form(None),
    parse_rules: Optional[str] = Form(None),
    poll_interval: int = Form(300),
    enabled: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Update configuration of an existing external data source."""
    await verify_csrf(request, csrf_token)
    client_ip = get_client_ip(request)
    admin_user = admin.get("username", "admin")
    is_enabled = bool(enabled and enabled.lower() in ("true", "1", "on", "yes"))

    try:
        await update_external_source(
            session=session,
            source_id=source_id,
            name=name,
            source_type=type,
            url=url,
            parse_rules=parse_rules,
            poll_interval=poll_interval,
            enabled=is_enabled,
            admin_user=admin_user,
            client_ip=client_ip
        )
        return RedirectResponse(url="/sources", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        if "HX-Request" in request.headers:
            return HTMLResponse(f"<div class='p-2 rounded bg-rose-500/10 text-rose-400 text-xs font-semibold'>Error: {str(e)}</div>", status_code=400)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/sources/{source_id}/toggle")
async def admin_toggle_source_route(
    request: Request,
    source_id: int,
    csrf_token: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Toggle source enabled/disabled status."""
    await verify_csrf(request, csrf_token)
    client_ip = get_client_ip(request)
    admin_user = admin.get("username", "admin")

    try:
        await toggle_source_enabled(
            session=session,
            source_id=source_id,
            admin_user=admin_user,
            client_ip=client_ip
        )
        return RedirectResponse(url="/sources", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/admin/sources/{source_id}/sync")
async def admin_sync_source_route(
    request: Request,
    source_id: int,
    csrf_token: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Trigger immediate manual sync / fetch for an external source."""
    await verify_csrf(request, csrf_token)
    source = await session.get(ExternalSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found.")

    from app.collectors.external_sources import poll_external_source
    snaps = await poll_external_source(session, source)

    if "HX-Request" in request.headers:
        return HTMLResponse(
            f"<div class='p-2 rounded bg-emerald-500/10 text-emerald-400 text-xs font-semibold'>"
            f"Synced {source.name}: captured {len(snaps)} snapshot(s)."
            f"</div>"
        )
    return RedirectResponse(url="/sources", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/matches/{match_id}/source-snapshot")
async def admin_record_source_snapshot_route(
    request: Request,
    match_id: str,
    source_id: int = Form(...),
    lean: str = Form(...),
    market: Optional[str] = Form(None),
    session_notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Record a verified manual snapshot for a fixture from an external source."""
    await verify_csrf(request, csrf_token)
    client_ip = get_client_ip(request)
    admin_user = admin.get("username", "admin")

    m_id = int(match_id) if match_id.isdigit() else None
    if not m_id:
        match = await get_match_by_id(session, match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found.")
        m_id = match.id

    try:
        await record_manual_source_snapshot(
            session=session,
            match_id=m_id,
            source_id=source_id,
            lean=lean,
            market=market,
            session_notes=session_notes,
            admin_user=admin_user,
            client_ip=client_ip
        )
        return RedirectResponse(url=f"/matches/{match_id}", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        if "HX-Request" in request.headers:
            return HTMLResponse(f"<div class='p-2 rounded bg-rose-500/10 text-rose-400 text-xs font-semibold'>Error: {str(e)}</div>", status_code=400)
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------
# 6. Leagues & Teams View (Protected)
# --------------------------------------------------------------------------
@router.get("/leagues-teams", response_class=HTMLResponse)
async def leagues_teams_view(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Tournament leagues and normalized cricket teams registry."""
    leagues_res = await session.execute(select(League).order_by(League.name))
    leagues = list(leagues_res.scalars().all())

    teams_res = await session.execute(select(Team).order_by(Team.name))
    teams = list(teams_res.scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="leagues_teams.html",
        context=template_ctx(request, active_page="leagues_teams", leagues=leagues, teams=teams)
    )


# --------------------------------------------------------------------------
# 7. Telegram Review Queue View (Protected)
# --------------------------------------------------------------------------
@router.get("/telegram-reviews", response_class=HTMLResponse)
async def telegram_reviews_view(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Incoming Telegram messages requiring manual or automated review."""
    reviews_res = await session.execute(
        select(TelegramReview).order_by(desc(TelegramReview.created_at_utc)).limit(50)
    )
    reviews = list(reviews_res.scalars().all())
    for r in reviews:
        r.created_at_ist = format_ist(r.created_at_utc)

    # Active matches that admin can map to
    matches_res = await session.execute(
        select(Match)
        .options(joinedload(Match.team_a), joinedload(Match.team_b))
        .where(Match.status.in_(("upcoming", "live", "UPCOMING", "LIVE", "toss_pending", "TOSS_PENDING")))
        .order_by(Match.start_time_utc.asc())
    )
    active_matches = list(matches_res.unique().scalars().all())

    return templates.TemplateResponse(
        request=request,
        name="telegram_reviews.html",
        context=template_ctx(request, active_page="telegram_reviews", reviews=reviews, active_matches=active_matches)
    )


# --------------------------------------------------------------------------
# 8. Collector Health & Scraper Status View (Protected)
# --------------------------------------------------------------------------
@router.get("/collector-health", response_class=HTMLResponse)
async def collector_health_view(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Real-time health status, latencies, and collector logs."""
    health_res = await session.execute(
        select(CollectorHealth).order_by(desc(CollectorHealth.last_run_utc))
    )
    health_records = list(health_res.scalars().all())
    for h in health_records:
        h.last_run_ist = format_ist(h.last_run_utc)
        h.last_success_ist = format_ist(h.last_success_utc) if h.last_success_utc else "-"
        h.last_error_ist = format_ist(h.last_error_utc) if h.last_error_utc else "-"

    return templates.TemplateResponse(
        request=request,
        name="collector_health.html",
        context=template_ctx(request, active_page="collector_health", health_records=health_records)
    )


# --------------------------------------------------------------------------
# 9. System Settings View (Protected)
# --------------------------------------------------------------------------
@router.get("/settings", response_class=HTMLResponse)
async def settings_view(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Application thresholds, parameters, and security policies."""
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context=template_ctx(request, active_page="settings", config=settings)
    )


# --------------------------------------------------------------------------
# 10. Audit Logs View (Protected)
# --------------------------------------------------------------------------
@router.get("/logs", response_class=HTMLResponse)
@router.get("/audit-logs", response_class=HTMLResponse)
async def logs_view(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin)
):
    """Immutable administrative audit trail."""
    logs = await get_audit_logs(session, limit=100)
    for l in logs:
        l.timestamp_ist = format_ist(l.timestamp_utc)

    return templates.TemplateResponse(
        request=request,
        name="logs.html",
        context=template_ctx(request, active_page="logs", logs=logs)
    )


@router.get("/audit", response_class=HTMLResponse)
async def audit_tool_view(
    request: Request,
    admin: dict = Depends(get_current_admin)
):
    """Cryptographic hash audit tool."""
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context=template_ctx(request, active_page="audit")
    )


# --------------------------------------------------------------------------
# 11. Admin Authentication Views (Public Login / Logout)
# --------------------------------------------------------------------------
@router.get("/auth/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    next: Optional[str] = "/"
):
    """Admin login page."""
    client_ip = get_client_ip(request)
    is_locked, secs = login_rate_limiter.is_locked_out(client_ip)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context=template_ctx(
            request,
            next_url=next or "/",
            error=None,
            is_locked_out=is_locked,
            lockout_seconds=secs
        )
    )


@router.post("/auth/login", response_class=HTMLResponse)
async def login_action(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: Optional[str] = Form(None),
    next: Optional[str] = Form("/"),
    session: AsyncSession = Depends(get_db)
):
    """
    Handle admin credentials, CSRF verification, rate limiting,
    and issue secure signed session cookie.
    """
    client_ip = get_client_ip(request)
    rate_key = f"{username.strip()}:{client_ip}"

    # 1. CSRF Protection Check
    if not validate_csrf_token(csrf_token):
        await log_audit_event(
            session=session,
            action="CSRF_REJECTION",
            entity="Auth",
            user=username.strip(),
            ip_address=client_ip,
            details="Rejected POST /auth/login due to missing or invalid CSRF token"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed. Request rejected."
        )

    # 2. Rate Limit & Lockout Check
    is_locked, remaining_seconds = login_rate_limiter.is_locked_out(rate_key)
    if not is_locked:
        # Also check by IP alone
        is_locked, remaining_seconds = login_rate_limiter.is_locked_out(client_ip)

    if is_locked:
        await log_audit_event(
            session=session,
            action="LOGIN_LOCKED_OUT",
            entity="AdminUser",
            user=username.strip(),
            ip_address=client_ip,
            details=f"Login attempt rejected due to active lockout ({remaining_seconds}s remaining)"
        )
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            context=template_ctx(
                request,
                next_url=next,
                is_locked_out=True,
                lockout_seconds=remaining_seconds,
                error="Account temporarily locked out due to excessive failed attempts. Please wait."
            )
        )

    # 3. Authenticate Credentials (Bcrypt Database or Fallback Settings)
    user = await authenticate_admin_user(session, username, password)

    if user:
        # Reset failed attempts
        login_rate_limiter.reset(rate_key)
        login_rate_limiter.reset(client_ip)

        token = create_signed_session_token({"username": user.username})

        await log_audit_event(
            session=session,
            action="ADMIN_LOGIN_SUCCESS",
            entity="AdminUser",
            entity_id=str(user.id),
            user=user.username,
            ip_address=client_ip,
            details="Admin session authenticated successfully"
        )

        response = RedirectResponse(url=next or "/", status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            key=settings.session_cookie_name,
            value=token,
            httponly=True,
            secure=settings.session_cookie_secure,
            samesite="lax",
            max_age=settings.session_expire_minutes * 60,
        )
        return response

    # 4. Failed Login: record attempt and check if lockout triggered
    attempts, is_now_locked, lockout_secs = login_rate_limiter.record_failed_attempt(rate_key)
    # Also record against IP
    login_rate_limiter.record_failed_attempt(client_ip)

    await log_audit_event(
        session=session,
        action="ADMIN_LOGIN_FAILED",
        entity="AdminUser",
        user=username.strip(),
        ip_address=client_ip,
        details=f"Invalid password (attempt {attempts} of {settings.login_max_failed_attempts})"
    )

    if is_now_locked:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            context=template_ctx(
                request,
                next_url=next,
                is_locked_out=True,
                lockout_seconds=lockout_secs,
                error=f"Account locked out for {lockout_secs // 60} minutes due to repeated failed attempts."
            )
        )

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        status_code=status.HTTP_200_OK,
        context=template_ctx(
            request,
            next_url=next,
            is_locked_out=False,
            error=f"Invalid username or password. Attempt {attempts} of {settings.login_max_failed_attempts}."
        )
    )


@router.get("/auth/logout")
async def logout_action(
    request: Request,
    session: AsyncSession = Depends(get_db)
):
    """Clear admin session cookie and record audit logout event."""
    admin = get_current_admin_optional(request)
    client_ip = get_client_ip(request)
    if admin:
        await log_audit_event(
            session=session,
            action="ADMIN_LOGOUT",
            entity="AdminUser",
            user=admin.get("username", "admin"),
            ip_address=client_ip,
            details="Admin session logged out"
        )
    response = RedirectResponse(url="/auth/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(settings.session_cookie_name)
    return response


# --------------------------------------------------------------------------
# 12. Admin Actions (Protected & Audited & CSRF Guarded)
# --------------------------------------------------------------------------
@router.post("/admin/sync-now", response_class=HTMLResponse)
async def admin_sync_now(
    request: Request,
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf)
):
    """Trigger immediate ingestion and synchronization tasks."""
    client_ip = get_client_ip(request)
    try:
        await sync_live_matches_task()
        await verify_toss_results_task()

        await log_audit_event(
            session=session,
            action="TRIGGER_MANUAL_SYNC",
            entity="System",
            user=admin.get("username", "admin"),
            ip_address=client_ip,
            details="Manual data sync completed successfully"
        )
        return HTMLResponse("<span class='text-emerald-400 font-medium'>✓ Sync completed successfully!</span>")
    except Exception as e:
        await log_audit_event(
            session=session,
            action="SYNC_ERROR",
            entity="System",
            user=admin.get("username", "admin"),
            ip_address=client_ip,
            details=f"Sync error: {str(e)}"
        )
        return HTMLResponse(f"<span class='text-rose-400 font-medium'>Sync error: {str(e)}</span>")


@router.post("/admin/matches/{match_id}/toss", response_class=HTMLResponse)
async def admin_record_toss(
    request: Request,
    match_id: str,
    toss_winner_id: str = Form(...),
    toss_decision: str = Form("BAT"),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf)
):
    """Manually record official ground truth toss result."""
    client_ip = get_client_ip(request)
    try:
        toss_data = TossResultCreate(
            match_id=match_id,
            toss_winner_id=toss_winner_id,
            toss_decision=toss_decision,
            notes=f"Recorded manually by admin {admin.get('username')}"
        )
        await record_toss_result(session, toss_data, source_provider_id="ADMIN_MANUAL")

        await log_audit_event(
            session=session,
            action="MANUAL_RECORD_TOSS",
            entity="Match",
            entity_id=str(match_id),
            user=admin.get("username", "admin"),
            ip_address=client_ip,
            details=f"Recorded toss winner: {toss_winner_id} ({toss_decision})"
        )
        return HTMLResponse(
            f"<div class='p-2 bg-emerald-950/60 text-emerald-300 border border-emerald-800 rounded-lg text-xs'>"
            f"✓ Confirmed toss for {match_id}: Winner {toss_winner_id} ({toss_decision}). Refresh to view updated evaluations.</div>"
        )
    except Exception as e:
        return HTMLResponse(f"<div class='text-xs text-rose-400'>Failed: {str(e)}</div>")


@router.post("/admin/telegram-reviews/{review_id}/resolve", response_class=HTMLResponse)
async def admin_resolve_telegram_review(
    request: Request,
    review_id: int,
    action: str = Form(...),
    match_id: Optional[str] = Form(None),
    toss_winner_id: Optional[str] = Form(None),
    toss_decision: Optional[str] = Form("BAT"),
    session: AsyncSession = Depends(get_db),
    admin: dict = Depends(get_current_admin),
    _csrf: None = Depends(verify_csrf)
):
    """Manually map or reject an unparsable/ambiguous Telegram message from the review queue."""
    client_ip = get_client_ip(request)
    stmt = select(TelegramReview).where(TelegramReview.id == review_id)
    res = await session.execute(stmt)
    review = res.scalar_one_or_none()
    if not review:
        raise HTTPException(status_code=404, detail="Review item not found.")

    if action == "reject":
        review.status = "REJECTED"
        await session.commit()
        await log_audit_event(
            session=session,
            action="TELEGRAM_REVIEW_REJECTED",
            entity="TelegramReview",
            entity_id=str(review_id),
            user=admin.get("username", "admin"),
            ip_address=client_ip,
            details=f"Rejected Telegram msg #{review.message_id}"
        )
        return HTMLResponse(
            f"<span class='text-xs text-slate-400 font-sans'>✓ Rejected msg #{review.message_id}</span>"
        )

    if action == "map":
        if not match_id or not toss_winner_id:
            return HTMLResponse("<span class='text-xs text-rose-400 font-sans'>Match and toss winner are required.</span>")

        toss_data = TossResultCreate(
            match_id=match_id,
            toss_winner_id=toss_winner_id,
            toss_decision=toss_decision or "BAT",
            notes=f"Manually mapped from Telegram msg #{review.message_id} by admin {admin.get('username')}"
        )
        toss_obj = await record_toss_result(session, toss_data, source_provider_id="TELEGRAM_MANUAL")
        toss_obj.telegram_message_id = review.message_id
        review.status = "APPROVED"
        await session.commit()

        await log_audit_event(
            session=session,
            action="TELEGRAM_REVIEW_MAPPED",
            entity="TelegramReview",
            entity_id=str(review_id),
            user=admin.get("username", "admin"),
            ip_address=client_ip,
            details=f"Mapped msg #{review.message_id} to Match #{match_id}: Winner {toss_winner_id} ({toss_decision})"
        )
        return HTMLResponse(
            f"<span class='text-xs text-emerald-400 font-sans font-medium'>✓ Mapped to Match #{match_id} (Winner: {toss_winner_id})!</span>"
        )

    return HTMLResponse("<span class='text-xs text-rose-400 font-sans'>Invalid action.</span>")


# --------------------------------------------------------------------------
# 13. JSON API Endpoints (Bearer Token or Session Protected)
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
