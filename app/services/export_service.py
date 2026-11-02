"""Export service for cricket match toss analysis in CSV and PDF formats."""

import io
import csv
from datetime import datetime
from typing import Dict, Any, List, Optional
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfgen import canvas

from app.models import Match
from app.db import format_ist
from sqlalchemy import inspect
from sqlalchemy.orm.base import NO_VALUE


def _safe_get_predictions(match: Match) -> list:
    """Retrieve predictions relationship without triggering async lazy-load error."""
    try:
        insp = inspect(match)
        loaded = insp.attrs.predictions.loaded_value
        if loaded is NO_VALUE:
            return []
        return loaded or []
    except Exception:
        return []


def _safe_get_toss_result(match: Match) -> Optional[Any]:
    """Retrieve toss_result relationship without triggering async lazy-load error."""
    try:
        insp = inspect(match)
        loaded = insp.attrs.toss_result.loaded_value
        if loaded is NO_VALUE:
            return None
        return loaded
    except Exception:
        return None


def generate_match_csv(analysis: Dict[str, Any], match: Match) -> str:
    """Generate comprehensive CSV report for a cricket match fixture."""
    output = io.StringIO()
    writer = csv.writer(output)

    # 1. Match Header
    writer.writerow(["CRICKET TOSS ANALYTICS - AUDITED MATCH DOSSIER"])
    writer.writerow(["Match ID", match.id])
    writer.writerow(["Teams", f"{analysis.get('team_a_name')} vs {analysis.get('team_b_name')}"])
    writer.writerow(["Tournament / League", analysis.get("league_name")])
    writer.writerow(["Venue", analysis.get("venue_name")])
    writer.writerow(["Start Time (UTC)", match.start_time_utc.isoformat() if match.start_time_utc else ""])
    writer.writerow(["Start Time (IST)", format_ist(match.start_time_utc) if match.start_time_utc else ""])
    writer.writerow(["Match Status", match.status])
    writer.writerow([])

    # 2. Toss Ground Truth & Prediction
    writer.writerow(["PREDICTION & OFFICIAL RESULT"])
    preds = _safe_get_predictions(match)
    if preds:
        p = preds[-1]
        writer.writerow(["Pre-Toss Call", p.lean])
        writer.writerow(["Estimated Probability", f"{(p.probability * 100):.1f}%" if p.probability else "50.0%"])
        writer.writerow(["95% Confidence Interval", f"[{(p.ci_low * 100):.1f}% - {(p.ci_high * 100):.1f}%]" if p.ci_low else "N/A"])
        writer.writerow(["SHA-256 Cryptographic Seal", p.hash])
        writer.writerow(["Locked At (IST)", format_ist(p.locked_at_utc)])
    else:
        writer.writerow(["Pre-Toss Call", "None"])

    tr = _safe_get_toss_result(match)
    if tr:
        w_team = str(tr.winner_team_id or "Winner")
        try:
            insp = inspect(tr)
            if insp.attrs.winner_team.loaded_value is not NO_VALUE and tr.winner_team:
                w_team = tr.winner_team.name
        except Exception:
            pass
        writer.writerow(["Confirmed Toss Winner", w_team])
        writer.writerow(["Toss Decision", tr.decision])
        writer.writerow(["Verification Source", tr.source])
        if preds and preds[-1].outcome:
            writer.writerow(["Evaluation Outcome", preds[-1].outcome.result])
    else:
        writer.writerow(["Confirmed Toss Winner", "Toss Pending"])
    writer.writerow([])

    # Table Header Helper
    stat_header = ["Category / Entity", "Sample Size (n)", "Toss Wins", "Toss Losses", "Win %", "95% Wilson CI", "Data Sufficiency", "Baseline 50% Test"]

    def write_stat_row(category: str, stat: Dict[str, Any]):
        writer.writerow([
            category,
            stat.get("n", 0),
            stat.get("wins", 0),
            stat.get("losses", 0),
            f"{stat.get('win_pct', 50.0):.1f}%",
            stat.get("ci_display", "N/A"),
            "INSUFFICIENT (n < 30)" if stat.get("is_insufficient") else "Sufficient",
            stat.get("baseline_note", "")
        ])

    # 3. Team A Toss Statistics
    team_a = analysis.get("team_a_name", "Team A")
    writer.writerow([f"TOSS STATISTICS: {team_a.upper()}"])
    writer.writerow(stat_header)
    stats_a = analysis.get("stats_a", {})
    write_stat_row("Overall", stats_a.get("overall", {}))
    for fmt, s in stats_a.get("by_format", {}).items():
        write_stat_row(f"Format: {fmt}", s)
    for lg, s in stats_a.get("by_league", {}).items():
        write_stat_row(f"League: {lg}", s)
    home_away_a = stats_a.get("home_away", {})
    write_stat_row("Home Matches", home_away_a.get("home", {}))
    write_stat_row("Away Matches", home_away_a.get("away", {}))
    recent_a = stats_a.get("recent_form", {})
    write_stat_row("Recent: Last 5", recent_a.get("last_5", {}))
    write_stat_row("Recent: Last 10", recent_a.get("last_10", {}))
    write_stat_row("Recent: Last 20", recent_a.get("last_20", {}))
    for cap, s in stats_a.get("captains", {}).items():
        write_stat_row(f"Captain: {cap}", s)
    writer.writerow([])

    # 4. Team B Toss Statistics
    team_b = analysis.get("team_b_name", "Team B")
    writer.writerow([f"TOSS STATISTICS: {team_b.upper()}"])
    writer.writerow(stat_header)
    stats_b = analysis.get("stats_b", {})
    write_stat_row("Overall", stats_b.get("overall", {}))
    for fmt, s in stats_b.get("by_format", {}).items():
        write_stat_row(f"Format: {fmt}", s)
    for lg, s in stats_b.get("by_league", {}).items():
        write_stat_row(f"League: {lg}", s)
    home_away_b = stats_b.get("home_away", {})
    write_stat_row("Home Matches", home_away_b.get("home", {}))
    write_stat_row("Away Matches", home_away_b.get("away", {}))
    recent_b = stats_b.get("recent_form", {})
    write_stat_row("Recent: Last 5", recent_b.get("last_5", {}))
    write_stat_row("Recent: Last 10", recent_b.get("last_10", {}))
    write_stat_row("Recent: Last 20", recent_b.get("last_20", {}))
    for cap, s in stats_b.get("captains", {}).items():
        write_stat_row(f"Captain: {cap}", s)
    writer.writerow([])

    # 5. Head to Head Toss Statistics
    writer.writerow([f"HEAD-TO-HEAD TOSS HISTORY: {team_a.upper()} VS {team_b.upper()}"])
    writer.writerow(stat_header)
    h2h = analysis.get("h2h_stats", {})
    write_stat_row(f"{team_a} Wins", h2h.get("stat_a", {}))
    write_stat_row(f"{team_b} Wins", h2h.get("stat_b", {}))
    writer.writerow([])

    # 6. Venue Tendencies
    venue_name = analysis.get("venue_name", "Venue")
    writer.writerow([f"VENUE TENDENCIES: {venue_name.upper()}"])
    writer.writerow(stat_header)
    v_stats = analysis.get("venue_stats", {})
    write_stat_row("Toss Winner Chose Bat First", v_stats.get("bat_first", {}))
    write_stat_row("Toss Winner Chose Field First", v_stats.get("field_first", {}))
    write_stat_row("Toss Winner Won The Match", v_stats.get("toss_match_correlation", {}))
    writer.writerow([])

    # 7. Permanent Disclaimer
    writer.writerow(["MANDATORY SCIENTIFIC DISCLAIMER"])
    writer.writerow([analysis.get("disclaimer", "Toss is a random event. Past stats do not guarantee results. Play responsibly.")])

    return output.getvalue()


class NumberedCanvas(canvas.Canvas):
    """Canvas that adds page numbers and permanent disclaimer footer to every page."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_number(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count: int):
        self.saveState()
        self.setFont("Helvetica", 7)
        self.setFillColor(colors.HexColor("#64748b"))

        # Footer divider line
        self.setStrokeColor(colors.HexColor("#cbd5e1"))
        self.setLineWidth(0.5)
        self.line(36, 32, 576, 32)

        # Permanent Disclaimer
        disclaimer = "DISCLAIMER: Toss is a random event. Past stats do not guarantee results. Play responsibly."
        self.drawString(36, 22, disclaimer)

        # Page numbering
        page_text = f"Page {self._pageNumber} of {page_count}"
        self.drawRightString(576, 22, page_text)
        self.restoreState()


def generate_match_pdf(analysis: Dict[str, Any], match: Match) -> bytes:
    """Generate high-quality PDF analysis report for a cricket match fixture."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=45
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#475569"),
        spaceAfter=12
    )
    h2_style = ParagraphStyle(
        "DocH2",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=10,
        spaceAfter=4
    )
    body_style = ParagraphStyle(
        "DocBody",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#334155")
    )
    cell_style = ParagraphStyle(
        "TableCell",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#1e293b")
    )
    cell_bold_style = ParagraphStyle(
        "TableCellBold",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#0f172a")
    )

    elements: List[Any] = []

    # Title & Subtitle
    team_a = analysis.get("team_a_name", "Team A")
    team_b = analysis.get("team_b_name", "Team B")
    elements.append(Paragraph(f"Toss Analytics: {team_a} vs {team_b}", title_style))
    elements.append(Paragraph(
        f"Match Fixture #{match.id} &bull; {analysis.get('league_name')} &bull; "
        f"Venue: {analysis.get('venue_name')} &bull; "
        f"Scheduled: {format_ist(match.start_time_utc)}",
        subtitle_style
    ))

    # Match Summary & Pre-Toss Box
    pred_text = "None"
    prob_text = "50.0%"
    ci_text = "N/A"
    hash_text = "N/A"
    preds = _safe_get_predictions(match)
    if preds:
        p = preds[-1]
        pred_text = p.lean
        prob_text = f"{(p.probability * 100):.1f}%" if p.probability else "50.0%"
        ci_text = f"[{(p.ci_low * 100):.1f}% - {(p.ci_high * 100):.1f}%]" if p.ci_low else "N/A"
        hash_text = p.hash

    toss_result_text = "Awaiting Toss"
    tr = _safe_get_toss_result(match)
    if tr:
        w_team = str(tr.winner_team_id or "Winner")
        try:
            insp = inspect(tr)
            if insp.attrs.winner_team.loaded_value is not NO_VALUE and tr.winner_team:
                w_team = tr.winner_team.name
        except Exception:
            pass
        toss_result_text = f"{w_team} won toss ({tr.decision.upper()})"

    summary_data = [
        [
            Paragraph("<b>Match ID:</b>", cell_style), Paragraph(str(match.id), cell_style),
            Paragraph("<b>Match Status:</b>", cell_style), Paragraph(match.status.upper(), cell_style),
        ],
        [
            Paragraph("<b>Pre-Toss Call:</b>", cell_style), Paragraph(pred_text, cell_bold_style),
            Paragraph("<b>Win Probability:</b>", cell_style), Paragraph(f"{prob_text} {ci_text}", cell_style),
        ],
        [
            Paragraph("<b>SHA-256 Hash:</b>", cell_style), Paragraph(hash_text[:32] + "...", cell_style),
            Paragraph("<b>Confirmed Toss:</b>", cell_style), Paragraph(toss_result_text, cell_bold_style),
        ]
    ]
    summary_table = Table(summary_data, colWidths=[90, 180, 90, 180])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 8))

    # Helper function to generate clean table for stats
    def build_stat_table(rows_data: List[List[str]]) -> Table:
        formatted_rows = []
        for i, row in enumerate(rows_data):
            row_elements = []
            for j, val in enumerate(row):
                st = cell_bold_style if (i == 0 or j == 0) else cell_style
                row_elements.append(Paragraph(str(val), st))
            formatted_rows.append(row_elements)

        t = Table(formatted_rows, colWidths=[140, 50, 45, 45, 50, 80, 130])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#f1f5f9")),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        return t

    headers = ["Category", "n", "Wins", "Losses", "Win %", "95% Wilson CI", "Evaluation Note"]

    # 1. Team A Statistics
    elements.append(Paragraph(f"Toss Analysis: {team_a}", h2_style))
    rows_a = [headers]
    stats_a = analysis.get("stats_a", {})
    ov_a = stats_a.get("overall", {})
    rows_a.append(["Overall", str(ov_a.get("n", 0)), str(ov_a.get("wins", 0)), str(ov_a.get("losses", 0)), f"{ov_a.get('win_pct', 50):.1f}%", ov_a.get("ci_display", "N/A"), ov_a.get("insufficient_warning") or ov_a.get("baseline_note", "")])
    for fmt, s in stats_a.get("by_format", {}).items():
        rows_a.append([f"Format: {fmt}", str(s.get("n", 0)), str(s.get("wins", 0)), str(s.get("losses", 0)), f"{s.get('win_pct', 50):.1f}%", s.get("ci_display", "N/A"), s.get("baseline_note", "")])
    ha_a = stats_a.get("home_away", {})
    h_a = ha_a.get("home", {})
    rows_a.append(["Home Matches", str(h_a.get("n", 0)), str(h_a.get("wins", 0)), str(h_a.get("losses", 0)), f"{h_a.get('win_pct', 50):.1f}%", h_a.get("ci_display", "N/A"), h_a.get("baseline_note", "")])
    elements.append(build_stat_table(rows_a))
    elements.append(Spacer(1, 6))

    # 2. Team B Statistics
    elements.append(Paragraph(f"Toss Analysis: {team_b}", h2_style))
    rows_b = [headers]
    stats_b = analysis.get("stats_b", {})
    ov_b = stats_b.get("overall", {})
    rows_b.append(["Overall", str(ov_b.get("n", 0)), str(ov_b.get("wins", 0)), str(ov_b.get("losses", 0)), f"{ov_b.get('win_pct', 50):.1f}%", ov_b.get("ci_display", "N/A"), ov_b.get("insufficient_warning") or ov_b.get("baseline_note", "")])
    for fmt, s in stats_b.get("by_format", {}).items():
        rows_b.append([f"Format: {fmt}", str(s.get("n", 0)), str(s.get("wins", 0)), str(s.get("losses", 0)), f"{s.get('win_pct', 50):.1f}%", s.get("ci_display", "N/A"), s.get("baseline_note", "")])
    ha_b = stats_b.get("home_away", {})
    h_b = ha_b.get("home", {})
    rows_b.append(["Home Matches", str(h_b.get("n", 0)), str(h_b.get("wins", 0)), str(h_b.get("losses", 0)), f"{h_b.get('win_pct', 50):.1f}%", h_b.get("ci_display", "N/A"), h_b.get("baseline_note", "")])
    elements.append(build_stat_table(rows_b))
    elements.append(Spacer(1, 6))

    # 3. Head to Head
    elements.append(Paragraph(f"Head-to-Head Toss History: {team_a} vs {team_b}", h2_style))
    rows_h2h = [headers]
    h2h = analysis.get("h2h_stats", {})
    s_a = h2h.get("stat_a", {})
    s_b = h2h.get("stat_b", {})
    rows_h2h.append([f"{team_a} Wins", str(s_a.get("n", 0)), str(s_a.get("wins", 0)), str(s_a.get("losses", 0)), f"{s_a.get('win_pct', 50):.1f}%", s_a.get("ci_display", "N/A"), s_a.get("insufficient_warning") or s_a.get("baseline_note", "")])
    rows_h2h.append([f"{team_b} Wins", str(s_b.get("n", 0)), str(s_b.get("wins", 0)), str(s_b.get("losses", 0)), f"{s_b.get('win_pct', 50):.1f}%", s_b.get("ci_display", "N/A"), s_b.get("insufficient_warning") or s_b.get("baseline_note", "")])
    elements.append(build_stat_table(rows_h2h))
    elements.append(Spacer(1, 6))

    # 4. Venue Tendencies
    venue_name = analysis.get("venue_name", "Venue")
    elements.append(Paragraph(f"Venue Tendencies: {venue_name}", h2_style))
    rows_v = [headers]
    v_stats = analysis.get("venue_stats", {})
    bf = v_stats.get("bat_first", {})
    ff = v_stats.get("field_first", {})
    corr = v_stats.get("toss_match_correlation", {})
    rows_v.append(["Toss: Chose Bat First", str(bf.get("n", 0)), str(bf.get("wins", 0)), str(bf.get("losses", 0)), f"{bf.get('win_pct', 50):.1f}%", bf.get("ci_display", "N/A"), bf.get("baseline_note", "")])
    rows_v.append(["Toss: Chose Field First", str(ff.get("n", 0)), str(ff.get("wins", 0)), str(ff.get("losses", 0)), f"{ff.get('win_pct', 50):.1f}%", ff.get("ci_display", "N/A"), ff.get("baseline_note", "")])
    rows_v.append(["Toss Winner Won Match", str(corr.get("n", 0)), str(corr.get("wins", 0)), str(corr.get("losses", 0)), f"{corr.get('win_pct', 50):.1f}%", corr.get("ci_display", "N/A"), corr.get("baseline_note", "")])
    elements.append(build_stat_table(rows_v))

    doc.build(elements, canvasmaker=NumberedCanvas)
    return buffer.getvalue()

