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
    issued_at: dt.datetime,
    results_df: pd.DataFrame,
    logo_png_bytes: bytes | None,
    theme: dict[str, Any] | None = None,
) -> bytes:
    """
    Generate a single-page branded PDF certificate (A4) with TWO tables:
      1) Replicate results (EB_1..3, YM_1..3, RAC_1..3)
      2) Averages + outcome (EB/YM/RAC rounded to 2dp + decision_result)

    Designed to be reliable on hosted environments (pure Python ReportLab).
    """
    theme = theme or {}
    title = theme.get("title", "Test Certificate")
    subtitle = theme.get("subtitle", "Decision Tool Results")
    brand_name = theme.get("brand_name", "")
    primary = _hex_to_color(theme.get("primary_hex", "#193159"))
    accent = _hex_to_color(theme.get("accent_hex", "#F4B400"))
    footer_text = theme.get("footer_text", "")

    def _format_cell(col_name: str, value) -> str:
        if pd.isna(value):
            return ""

        if col_name == "decision_result":
            key = str(value or "").strip().title()
            return {
                "Green": "Good",
                "Amber": "Unsatisfactory",
                "Red": "Cause for Concern",
            }.get(key, str(value))

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
        widths: list[float] = []
        for cn in cols:
            if cn == "material_name":
                widths.append(table_w * (0.22 if mode == "replicates" else 0.35))
            elif cn == "test_date":
                widths.append(table_w * (0.12 if mode == "replicates" else 0.18))
            elif cn == "decision_result":
                widths.append(table_w * 0.18)
            else:
                widths.append(table_w * (0.06 if mode == "replicates" else 0.10))

        scale = table_w / sum(widths) if widths else 1.0
        return [w * scale for w in widths]

    def _styled_table(data: list[list[str]], col_widths: list[float], font_size: int) -> Table:
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
    # Columns and header labels
    # --------------------------
    replicates_cols_all = [
        "material_name", "material_type", "test_date",
        "EB_1", "EB_2", "EB_3",
        "YM_1", "YM_2", "YM_3",
        "RAC_1", "RAC_2", "RAC_3",
    ]
    summary_cols_all = [
        "material_name", "material_type", "test_date",
        "EB", "YM", "RAC",
        "decision_result",
    ]

    replicates_cols = [c for c in replicates_cols_all if c in results_df.columns]
    summary_cols = [c for c in summary_cols_all if c in results_df.columns]

    df_rep = results_df[replicates_cols].copy() if replicates_cols else pd.DataFrame()
    df_sum = results_df[summary_cols].copy() if summary_cols else pd.DataFrame()

    header_map = {
        "material_name": "Material",
        "material_type": "Category",
        "test_date": "Test date",

        "EB_1": "EB r1", "EB_2": "EB r2", "EB_3": "EB r3",
        "YM_1": "YM r1", "YM_2": "YM r2", "YM_3": "YM r3",
        "RAC_1": "RAC r1", "RAC_2": "RAC r2", "RAC_3": "RAC r3",

        "EB": "EB avg", "YM": "YM avg", "RAC": "RAC avg",
        "decision_result": "Result",
    }

    # --------------------------
    # PDF canvas + header
    # --------------------------
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    width, height = A4

    # --- Header bar ---
    c.setFillColor(primary)
    c.rect(0, height - 28 * mm, width, 28 * mm, fill=1, stroke=0)

    # Logo (if present)
    if logo_png_bytes:
        try:
            img = ImageReader(io.BytesIO(logo_png_bytes))
            target_h = 22 * mm  # increase this to make it taller
            iw, ih = img.getSize()
            target_w = target_h * (iw / ih)

            c.drawImage(
                img, 
                12 * mm, 
                height - 25 * mm,   # adjust Y if needed after changing height
                width=target_w,
                height=target_h,
                mask="auto",
                preserveAspectRatio=True,
                anchor="sw",
            )
        except Exception:
            pass

    # Title text on header
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 16)
    c.drawRightString(width - 12 * mm, height - 12 * mm, title)

    c.setFont("Helvetica", 10)
    if brand_name:
        c.drawRightString(width - 12 * mm, height - 19 * mm, brand_name)

    # --- Body meta info ---
    y = height - 40 * mm
    c.setFillColor(colors.black)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(12 * mm, y, subtitle)
    y -= 8 * mm

    c.setFont("Helvetica", 10)
    c.drawString(12 * mm, y, f"Issued to: {user_email}")
    y -= 6 * mm
    c.drawString(12 * mm, y, f"Issued on: {issued_on.isoformat()}")
    y -= 6 * mm
    c.drawString(12 * mm, y, f"Issued at: {issued_at.strftime('%H:%M:%S')}")
    y -= 10 * mm

    # Accent line
    c.setStrokeColor(accent)
    c.setLineWidth(2)
    c.line(12 * mm, y, width - 12 * mm, y)
    y -= 8 * mm

    # --------------------------
    # Two stacked tables
    # --------------------------
    table_w = width - 24 * mm
    x_left = 12 * mm

    # Replicates section title
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(colors.black)
    c.drawString(x_left, y, "Replicate results")
    y -= 6 * mm

    if not df_rep.empty:
        data_rep = _build_table_data(df_rep, replicates_cols, header_map)
        t_rep = _styled_table(
            data_rep,
            _col_widths(table_w, replicates_cols, mode="replicates"),
            font_size=7,
        )
        w_rep, h_rep = t_rep.wrap(table_w, height)
        y_rep_bottom = y - h_rep
        t_rep.drawOn(c, x_left, y_rep_bottom)
        y = y_rep_bottom - 8 * mm
    else:
        c.setFont("Helvetica", 9)
        c.drawString(x_left, y, "No replicate columns found in results.")
        y -= 10 * mm

    # Summary section title
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

    # --- Footer ---
    if footer_text:
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.grey)
        c.drawString(12 * mm, 10 * mm, footer_text)

    c.showPage()
    c.save()
    return buf.getvalue()