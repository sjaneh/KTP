import io
import datetime as dt
from typing import Any

import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle
from reportlab.lib.utils import ImageReader


def _hex_to_color(hex_str: str, fallback=colors.HexColor("#0B3D91")):
    try:
        if not hex_str:
            return fallback
        return colors.HexColor(hex_str)
    except Exception:
        return fallback


def make_certificate_pdf_bytes(
    *,
    user_email: str,
    issued_on: dt.date,
    results_df: pd.DataFrame,
    logo_png_bytes: bytes | None,
    theme: dict[str, Any] | None = None,
) -> bytes:
    """
    Generate a single-page branded PDF certificate (A4) with a table of results.
    Designed to be reliable on hosted environments (pure Python ReportLab).
    """
    theme = theme or {}
    title = theme.get("title", "Test Certificate")
    subtitle = theme.get("subtitle", "Decision Tool Results")
    brand_name = theme.get("brand_name", "")
    primary = _hex_to_color(theme.get("primary_hex", "#0B3D91"))
    accent = _hex_to_color(theme.get("accent_hex", "#F4B400"))
    footer_text = theme.get("footer_text", "")

    def _format_cell(col_name: str, value) -> str:
        """Format cell values for PDF display."""
        if pd.isna(value):
            return ""
        # force averages to 2dp
        if col_name in ("EB", "YM", "RAC"):
            try:
                return f"{float(value):.2f}"
            except Exception:
                return str(value)
        return str(value)

    def _build_table_data(df_in: pd.DataFrame, cols: list[str], header_map: dict[str, str]) -> list[list[str]]:
        headers = [header_map.get(cn, cn) for cn in cols]
        data = [headers]
        for _, row in df_in.iterrows():
            data.append([_format_cell(cn, row.get(cn)) for cn in cols])
        return data

    def _col_widths(table_w: float, cols: list[str], mode: str) -> list[float]:
        """
        mode:
          - "replicates": lots of narrow columns
          - "summary": fewer columns, give Material more space
        """
        widths = []
        for cn in cols:
            if cn == "material_name":
                widths.append(table_w * (0.22 if mode == "replicates" else 0.35))
            elif cn == "test_date":
                widths.append(table_w * (0.12 if mode == "replicates" else 0.18))
            elif cn == "decision_result":
                widths.append(table_w * 0.18)
            else:
                # replicate columns or avg columns
                widths.append(table_w * (0.06 if mode == "replicates" else 0.10))

        # normalize
        scale = table_w / sum(widths) if widths else 1.0
        return [w * scale for w in widths]

    def _styled_table(data: list[list[str]], col_widths: list[float], font_size: int = 8) -> Table:
        t = Table(data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), primary),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), font_size),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        return t

    # --------------------------
    # Two-table certificate layout
    # --------------------------
    # Define columns (only include columns that exist)
    replicates_cols_all = [
        "material_name", "test_date",
        "EB_1", "EB_2", "EB_3",
        "YM_1", "YM_2", "YM_3",
        "RAC_1", "RAC_2", "RAC_3",
    ]
    summary_cols_all = [
        "material_name", "test_date",
        "EB", "YM", "RAC",
        "decision_result",
    ]

    replicates_cols = [c for c in replicates_cols_all if c in results_df.columns]
    summary_cols = [c for c in summary_cols_all if c in results_df.columns]

    df_rep = results_df[replicates_cols].copy() if replicates_cols else pd.DataFrame()
    df_sum = results_df[summary_cols].copy() if summary_cols else pd.DataFrame()

    # Headers
    header_map = {
        "material_name": "Material",
        "test_date": "Test date",

        "EB_1": "EB r1", "EB_2": "EB r2", "EB_3": "EB r3",
        "YM_1": "YM r1", "YM_2": "YM r2", "YM_3": "YM r3",
        "RAC_1": "RAC r1", "RAC_2": "RAC r2", "RAC_3": "RAC r3",

        "EB": "EB avg", "YM": "YM avg", "RAC": "RAC avg",
        "decision_result": "Result",
    }
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # --- Results tables ---
    table_w = width - 24 * mm
    x_left = 12 * mm

    # Table title: Replicates
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.black)
    c.drawString(x_left, y, "Replicate results")
    y -= 6 * mm

    if not df_rep.empty:
        data_rep = _build_table_data(df_rep, replicates_cols, header_map)
        t_rep = _styled_table(
            data_rep,
            _col_widths(table_w, replicates_cols, mode="replicates"),
            font_size=7,  # replicates table is dense
        )

        # Wrap to get actual height
        w_rep, h_rep = t_rep.wrap(table_w, height)
        y_rep_bottom = y - h_rep
        t_rep.drawOn(c, x_left, y_rep_bottom)
        y = y_rep_bottom - 8 * mm
    else:
        c.setFont("Helvetica", 9)
        c.drawString(x_left, y, "No replicate columns found in results.")
        y -= 10 * mm

    # Table title: Summary
    c.setFont("Helvetica-Bold", 10)
    c.drawString(x_left, y, "Averages and outcome")
    y -= 6 * mm

    if not df_sum.empty:
        data_sum = _build_table_data(df_sum, summary_cols, header_map)
        t_sum = _styled_table(
            data_sum,
            _col_widths(table_w, summary_cols, mode="summary"),
            font_size=8,
        )

        w_sum, h_sum = t_sum.wrap(table_w, height)
        y_sum_bottom = y - h_sum
        t_sum.drawOn(c, x_left, y_sum_bottom)
        y = y_sum_bottom - 4 * mm
    else:
        c.setFont("Helvetica", 9)
        c.drawString(x_left, y, "No summary columns found in results.")
        y -= 10 * mm

    

    