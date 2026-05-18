import io
import datetime as dt
from typing import Any

import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet
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
    Generate a single-page branded PDF report (A4) with TWO tables:
      1) Replicate results (EB_1..3, YM_1..3, RAC_1..3)
      2) Averages + outcome (EB/YM/RAC rounded to 2dp + decision_result)

    Designed to be reliable on hosted environments (pure Python ReportLab).
    """
    theme = theme or {}
    title = theme.get("title", "Test Report")
    subtitle = theme.get("subtitle", "Decision Tool Results")
    brand_name = theme.get("brand_name", "")
    primary = _hex_to_color(theme.get("primary_hex", "#193159"))
    accent = _hex_to_color(theme.get("accent_hex", "#F4B400"))
    footer_text = theme.get("footer_text", "")
    styles = getSampleStyleSheet()

    cell_style = styles["BodyText"].clone("cell_style")
    cell_style.fontName = "Helvetica"
    cell_style.fontSize = 7
    cell_style.leading = 8
    cell_style.spaceBefore = 0
    cell_style.spaceAfter = 0

    meta_style = styles["BodyText"].clone("meta_style")
    meta_style.fontName = "Helvetica"
    meta_style.fontSize = 10
    meta_style.leading = 12
    meta_style.spaceBefore = 0
    meta_style.spaceAfter = 4

    section_style = styles["Heading2"].clone("section_style")
    section_style.fontName = "Helvetica-Bold"
    section_style.fontSize = 10
    section_style.leading = 12
    section_style.textColor = colors.black
    section_style.spaceBefore = 0
    section_style.spaceAfter = 4

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

    def _build_table_data(df_in: pd.DataFrame, cols: list[str], header_map: dict[str, str]) -> list[list]:
        headers = [header_map.get(cn, cn) for cn in cols]
        data = [headers]

        for _, row in df_in.iterrows():
            data.append([
                Paragraph(_format_cell(cn, row.get(cn)), cell_style)
                for cn in cols
            ])

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
                widths.append(table_w * (0.22 if mode == "replicates" else 0.30))
            elif cn == "material_type":
                widths.append(table_w * (0.16 if mode == "replicates" else 0.22))
            elif cn == "test_date":
                widths.append(table_w * (0.12 if mode == "replicates" else 0.18))
            elif cn == "decision_result":
                widths.append(table_w * 0.18)
            elif cn == "sample_type":
                widths.append(table_w * (0.12 if mode == "replicates" else 0.16))
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
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
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
        "material_name", "material_type", "sample_type", "test_date",
        "EB_1", "EB_2", "EB_3",
        "YM_1", "YM_2", "YM_3",
        "RAC_1", "RAC_2", "RAC_3",
    ]
    summary_cols_all = [
        "material_name", "material_type", "sample_type", "test_date",
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
        "sample_type": "Sample Type",

        "EB_1": "EB r1", "EB_2": "EB r2", "EB_3": "EB r3",
        "YM_1": "YM r1", "YM_2": "YM r2", "YM_3": "YM r3",
        "RAC_1": "RAC r1", "RAC_2": "RAC r2", "RAC_3": "RAC r3",

        "EB": "EB avg", "YM": "YM avg", "RAC": "RAC avg",
        "decision_result": "Result",
    }

    def _draw_page_chrome(canvas, doc):
        page_width, page_height = A4

        # Header bar
        canvas.setFillColor(primary)
        canvas.rect(0, page_height - 28 * mm, page_width, 28 * mm, fill=1, stroke=0)

        # Logo
        if logo_png_bytes:
            try:
                img = ImageReader(io.BytesIO(logo_png_bytes))
                target_h = 22 * mm
                iw, ih = img.getSize()
                target_w = target_h * (iw / ih)

                canvas.drawImage(
                    img,
                    12 * mm,
                    page_height - 25 * mm,
                    width=target_w,
                    height=target_h,
                    mask="auto",
                    preserveAspectRatio=True,
                    anchor="sw",
                )
            except Exception:
                pass

        # Title / subtitle
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawRightString(page_width - 12 * mm, page_height - 11 * mm, title)

        canvas.setFont("Helvetica", 10)
        if subtitle:
            canvas.drawRightString(page_width - 12 * mm, page_height - 18 * mm, subtitle)

        # Accent line
        canvas.setStrokeColor(accent)
        canvas.setLineWidth(2)
        canvas.line(12 * mm, page_height - 42 * mm, page_width - 12 * mm, page_height - 42 * mm)

        # Footer text
        if footer_text:
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.grey)
            canvas.drawString(12 * mm, 10 * mm, footer_text)

        # Page number
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawRightString(page_width - 12 * mm, 10 * mm, f"Page {doc.page}")
    
    buf = io.BytesIO()
    width, height = A4

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=45 * mm,
        bottomMargin=18 * mm,
    )

    story = []

    if brand_name:
        story.append(Paragraph(brand_name, meta_style))
        story.append(Spacer(1, 3 * mm))

    story.append(Paragraph(f"Issued to: {user_email}", meta_style))
    story.append(Paragraph(f"Issued on: {issued_on.isoformat()}", meta_style))
    story.append(Spacer(1, 5 * mm))

    story.append(Paragraph("Replicate results", section_style))
    story.append(Spacer(1, 2 * mm))

    if not df_rep.empty:
        data_rep = _build_table_data(df_rep, replicates_cols, header_map)
        t_rep = _styled_table(
            data_rep,
            _col_widths(doc.width, replicates_cols, mode="replicates"),
            font_size=7,
        )
        story.append(t_rep)
        story.append(Spacer(1, 6 * mm))
    else:
        story.append(Paragraph("No replicate columns found in results.", meta_style))
        story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Averages and outcome", section_style))
    story.append(Spacer(1, 2 * mm))

    if not df_sum.empty:
        data_sum = _build_table_data(df_sum, summary_cols, header_map)
        t_sum = _styled_table(
            data_sum,
            _col_widths(doc.width, summary_cols, mode="summary"),
            font_size=8,
        )
        story.append(t_sum)
    else:
        story.append(Paragraph("No summary columns found in results.", meta_style))

    doc.build(
        story,
        onFirstPage=_draw_page_chrome,
        onLaterPages=_draw_page_chrome,
    )

    return buf.getvalue()