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

    # Pick/format columns (safe if some missing)
    desired_cols = ["material_name", "test_date", "EB", "YM", "RAC", "decision_result"]
    cols = [c for c in desired_cols if c in results_df.columns]
    df = results_df[cols].copy()

    # Human-friendly column headers
    header_map = {
        "material_name": "Material",
        "test_date": "Test date",
        "EB": "EB",
        "YM": "YM",
        "RAC": "RAC",
        "decision_result": "Result",
    }
    headers = [header_map.get(c, c) for c in cols]

    # Convert to list-of-lists for ReportLab Table
    data = [headers]
    for _, row in df.iterrows():
        data.append([("" if pd.isna(row[c]) else str(row[c])) for c in cols])

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
            # Fit into header bar area
            logo_h = 18 * mm
            logo_w = 50 * mm
            c.drawImage(img, 12 * mm, height - 23 * mm, width=logo_w, height=logo_h, mask="auto")
        except Exception:
            # If logo fails, ignore (don't break certificate generation)
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
    y -= 10 * mm

    # Accent line
    c.setStrokeColor(accent)
    c.setLineWidth(2)
    c.line(12 * mm, y, width - 12 * mm, y)
    y -= 8 * mm

    # --- Results table ---
    # Table width and column widths
    table_w = width - 24 * mm
    # allocate widths: material wider, date medium, others small
    col_widths = []
    for col in cols:
        if col == "material_name":
            col_widths.append(table_w * 0.30)
        elif col == "test_date":
            col_widths.append(table_w * 0.16)
        elif col == "decision_result":
            col_widths.append(table_w * 0.14)
        else:
            col_widths.append(table_w * 0.10)

    # Normalize if widths don't sum perfectly
    scale = table_w / sum(col_widths) if col_widths else 1.0
    col_widths = [w * scale for w in col_widths]

    t = Table(data, colWidths=col_widths, repeatRows=1)

    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), primary),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    # Draw the table at (x, y_table_bottom)
    # Estimate table height (simple heuristic): row count * row height
    row_h = 8 * mm
    table_h = len(data) * row_h
    y_table_top = y
    y_table_bottom = y_table_top - table_h

    # If table would go off page, shrink font a bit (simple single-page safety)
    min_bottom = 18 * mm
    if y_table_bottom < min_bottom:
        # Reduce font size to fit (still single-page)
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7)]))
        row_h = 6.5 * mm
        table_h = len(data) * row_h
        y_table_bottom = y_table_top - table_h

    t.wrapOn(c, table_w, table_h)
    t.drawOn(c, 12 * mm, y_table_bottom)

    # --- Footer ---
    if footer_text:
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.grey)
        c.drawString(12 * mm, 10 * mm, footer_text)

    c.showPage()
    c.save()
    return buf.getvalue()