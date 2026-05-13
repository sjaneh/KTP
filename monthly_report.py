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

    for row in rows:
        total_samples += int(row.get("sample_count", 0) or 0)
        green_count += int(row.get("green_count", 0) or 0)
        amber_count += int(row.get("amber_count", 0) or 0)
        red_count += int(row.get("red_count", 0) or 0)

    total_categorised = green_count + amber_count + red_count

    def pct(value: int) -> float:
        if total_categorised == 0:
            return 0.0
        return round((value / total_categorised) * 100, 1)

    return {
        "uploads": uploads,
        "total_samples": total_samples,
        "green_count": green_count,
        "amber_count": amber_count,
        "red_count": red_count,
        "green_pct": pct(green_count),
        "amber_pct": pct(amber_count),
        "red_pct": pct(red_count),
    }


def build_email_body(year: int, month: int, summary: dict) -> str:
    month_name = calendar.month_name[month]

    return (
        f"Monthly upload summary for {month_name} {year}\n\n"
        f"Number of uploads: {summary['uploads']}\n"
        f"Number of samples: {summary['total_samples']}\n\n"
        f"Result category overview:\n"
        f"- Good (Green): {summary['green_count']} ({summary['green_pct']}%)\n"
        f"- Unsatisfactory (Amber): {summary['amber_count']} ({summary['amber_pct']}%)\n"
        f"- Cause for Concern (Red): {summary['red_count']} ({summary['red_pct']}%)\n"
    )


def send_previous_month_report() -> None:
    year, month = 2026, 5 # previous_month_range()


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