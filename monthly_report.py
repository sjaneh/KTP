import os
import io
import csv
import calendar
from datetime import datetime, date

from one_drive import download_file
from graph_mail import send_results_email


DRIVE_ID = os.environ["DRIVE_ID"]
MONTHLY_SUMMARY_LOG_PATH = os.environ.get(
    "MONTHLY_SUMMARY_LOG_PATH",
    "NBFKTPAPP/Admin/monthly_summary_log.csv",
)
ORGANISER_EMAIL = os.environ["ORGANISER_EMAIL"]


def load_summary_rows(drive_id: str, log_path: str) -> list[dict]:
    data = download_file(drive_id, log_path)
    if not data:
        return []

    text = data.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def previous_month_range(today: date | None = None) -> tuple[int, int]:
    today = today or date.today()

    if today.month == 1:
        return today.year - 1, 12

    return today.year, today.month - 1


def rows_for_month(rows: list[dict], year: int, month: int) -> list[dict]:
    filtered = []

    for row in rows:
        ts = (row.get("timestamp") or "").strip()

        try:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue

        if dt.year == year and dt.month == month:
            filtered.append(row)

    return filtered


def build_monthly_summary(rows: list[dict]) -> dict:
    uploads = len(rows)
    total_samples = 0
    green_count = 0
    amber_count = 0
    red_count = 0

    rd_sample_count = 0
    regular_sample_count = 0

    rd_green_count = 0
    rd_amber_count = 0
    rd_red_count = 0

    regular_green_count = 0
    regular_amber_count = 0
    regular_red_count = 0

    for row in rows:
        total_samples += int(row.get("sample_count", 0) or 0)
        green_count += int(row.get("green_count", 0) or 0)
        amber_count += int(row.get("amber_count", 0) or 0)
        red_count += int(row.get("red_count", 0) or 0)
        rd_sample_count += int(row.get("rd_sample_count", 0) or 0)
        regular_sample_count += int(row.get("regular_sample_count", 0) or 0)

        rd_green_count += int(row.get("rd_green_count", 0) or 0)
        rd_amber_count += int(row.get("rd_amber_count", 0) or 0)
        rd_red_count += int(row.get("rd_red_count", 0) or 0)

        regular_green_count += int(row.get("regular_green_count", 0) or 0)
        regular_amber_count += int(row.get("regular_amber_count", 0) or 0)
        regular_red_count += int(row.get("regular_red_count", 0) or 0)

    avg_samples_per_upload = round(total_samples / uploads, 1) if uploads else 0.0

    total_categorised = green_count + amber_count + red_count
    rd_total_categorised = rd_green_count + rd_amber_count + rd_red_count
    regular_total_categorised = regular_green_count + regular_amber_count + regular_red_count

    def pct(value: int, total: int) -> float:
        if total == 0:
            return 0.0
        return round((value / total) * 100, 1)

    return {
        "uploads": uploads,
        "total_samples": total_samples,
        "avg_samples_per_upload": avg_samples_per_upload,

        "green_count": green_count,
        "amber_count": amber_count,
        "red_count": red_count,
        "green_pct": pct(green_count, total_categorised),
        "amber_pct": pct(amber_count, total_categorised),
        "red_pct": pct(red_count, total_categorised),

        "rd_sample_count": rd_sample_count,
        "regular_sample_count": regular_sample_count,

        "rd_green_count": rd_green_count,
        "rd_amber_count": rd_amber_count,
        "rd_red_count": rd_red_count,
        "rd_green_pct": pct(rd_green_count, rd_total_categorised),
        "rd_amber_pct": pct(rd_amber_count, rd_total_categorised),
        "rd_red_pct": pct(rd_red_count, rd_total_categorised),

        "regular_green_count": regular_green_count,
        "regular_amber_count": regular_amber_count,
        "regular_red_count": regular_red_count,
        "regular_green_pct": pct(regular_green_count, regular_total_categorised),
        "regular_amber_pct": pct(regular_amber_count, regular_total_categorised),
        "regular_red_pct": pct(regular_red_count, regular_total_categorised),
    }


def build_email_body(year: int, month: int, summary: dict) -> str:
    month_name = calendar.month_name[month]

    return (
        f"Monthly upload summary for {month_name} {year}\n\n"
        f"Number of uploads: {summary['uploads']}\n"
        f"Number of samples: {summary['total_samples']}\n"
        f"Average samples per upload: {summary['avg_samples_per_upload']}\n\n"

        f"Overall result category overview:\n"
        f"- Good (Green): {summary['green_count']} ({summary['green_pct']}%)\n"
        f"- Unsatisfactory (Amber): {summary['amber_count']} ({summary['amber_pct']}%)\n"
        f"- Cause for Concern (Red): {summary['red_count']} ({summary['red_pct']}%)\n\n"

        f"Sample type split:\n"
        f"- Research and Development samples: {summary['rd_sample_count']}\n"
        f"- Regular Test Schedule samples: {summary['regular_sample_count']}\n\n"

        f"Research and Development result breakdown:\n"
        f"- Good (Green): {summary['rd_green_count']} ({summary['rd_green_pct']}%)\n"
        f"- Unsatisfactory (Amber): {summary['rd_amber_count']} ({summary['rd_amber_pct']}%)\n"
        f"- Cause for Concern (Red): {summary['rd_red_count']} ({summary['rd_red_pct']}%)\n\n"

        f"Regular Test Schedule result breakdown:\n"
        f"- Good (Green): {summary['regular_green_count']} ({summary['regular_green_pct']}%)\n"
        f"- Unsatisfactory (Amber): {summary['regular_amber_count']} ({summary['regular_amber_pct']}%)\n"
        f"- Cause for Concern (Red): {summary['regular_red_count']} ({summary['regular_red_pct']}%)\n"
    )


def send_previous_month_report() -> None:
    year, month = previous_month_range()


    all_rows = load_summary_rows(DRIVE_ID, MONTHLY_SUMMARY_LOG_PATH)
    month_rows = rows_for_month(all_rows, year, month)
    summary = build_monthly_summary(month_rows)


    subject = f"Monthly upload summary - {calendar.month_name[month]} {year}"
    body = build_email_body(year, month, summary)

    send_results_email(
        to_email=ORGANISER_EMAIL,
        subject=subject,
        body_text=body,
        attachments=None,
    )


if __name__ == "__main__":
    send_previous_month_report()