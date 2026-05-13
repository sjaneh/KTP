from dotenv import load_dotenv
load_dotenv(override=False)

# one_drive.py
import os
import io
import csv
import json
import requests
from msal import ConfidentialClientApplication




# Microsoft Entra (Azure AD) / Microsoft Graph configuration from environment variables
AUTHORITY = f"https://login.microsoftonline.com/{os.environ['TENANT_ID']}"
SCOPES = ["https://graph.microsoft.com/.default"]  # Use application permissions granted to your app

def acquire_token() -> str:
    """
    Get an access token using client credentials (Client ID + Client Secret).
    Your app registration must have Graph application permissions and admin consent.
    """
    app = ConfidentialClientApplication(
        client_id=os.environ["CLIENT_ID"],
        client_credential=os.environ["CLIENT_SECRET"],
        authority=AUTHORITY,
    )
    result = app.acquire_token_for_client(scopes=SCOPES)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "MSAL token error"))
    return result["access_token"]




# ---------- Basic file operations ----------

def upload_small_file(drive_id: str, dest_path: str, local_path: str) -> dict:
    
    #Upload a (small) file to OneDrive/SharePoint using PUT .../content.
    
    token = acquire_token()
    with open(local_path, "rb") as f:
        r = requests.put(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{dest_path}:/content",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
            data=f.read(), timeout=120
        )
    r.raise_for_status()
    return r.json()



def upload_bytes(drive_id: str, dest_path: str, content: bytes, content_type: str = "text/csv") -> dict:
    """
    Upload bytes to OneDrive/SharePoint to a given path.
    Good for generated CSV content.
    """
    token = acquire_token()
    r = requests.put(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{dest_path}:/content",
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        data=content,
        timeout=120,
    )
    r.raise_for_status()
    return r.json()
       

def download_file(drive_id: str, path: str) -> bytes | None:
    #Download raw file content from OneDrive/SharePoint.
    token = acquire_token()
    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{path}:/content"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content

def ensure_folder(drive_id: str, folder_path: str) -> dict:
    
    #Ensure nested folders exist by creating each segment if missing.
    
    token = acquire_token()
    parts = [p for p in folder_path.strip("/").split("/") if p]
    parent_path = ""
    for i, part in enumerate(parts):
        current = "/".join(parts[: i + 1])
        # Check existence
        r = requests.get(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{current}",
            headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
        if r.status_code == 200:
            parent_path = current
            continue
        elif r.status_code != 404:
            r.raise_for_status()
        # Create under parent
        children_url = (
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{parent_path}:/children"
            if parent_path else f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children"
        )
        payload = {"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}
        r = requests.post(
            children_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=payload, timeout=30
        )
        r.raise_for_status()
        parent_path = current
    # Return final folder metadata
    r = requests.get(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder_path}",
        headers={"Authorization": f"Bearer {token}"}, timeout=30
    )
    r.raise_for_status()
    return r.json()

def list_children(drive_id: str, folder_path: str) -> list[dict]:
    #List items within a folder (files & subfolders).
    token = acquire_token()
    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{folder_path}:/children"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json().get("value", [])

def create_view_link(drive_id: str, item_id: str, scope: str = "organization") -> str:
    
    #Create a view-only sharing link. Use scope='organization' so that only tenant users can view
    
    token = acquire_token()
    r = requests.post(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/createLink",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"type": "view", "scope": scope}, timeout=30
    )
    r.raise_for_status()
    return r.json()["link"]["webUrl"]

# ---------- Audit log (CSV) ----------

def append_audit_log_csv(drive_id: str, log_path: str, entry: dict) -> None:
    
    #Append a row to audit.csv; creates the file with headers if missing.
    #We re-upload the whole CSV (download -> append -> PUT).

    existing = download_file(drive_id, log_path)
    headers = ["timestamp", "user_id", "filename", "rows", "columns", "sha256", "drive_path", "result"]
    rows = []

    if existing is None:
        rows.append(headers)
    else:
        text = existing.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        existing_rows = list(reader)
        rows.extend(existing_rows if existing_rows else [headers])

    rows.append([
        entry.get("timestamp", ""), entry.get("user_id", ""), entry.get("filename", ""),
        str(entry.get("rows", "")), str(entry.get("columns", "")), entry.get("sha256", ""),
        entry.get("drive_path", ""), entry.get("result", "")
    ])


    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows(rows)
    csv_bytes = out.getvalue().encode("utf-8")

    token = acquire_token()
    r = requests.put(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{log_path}:/content",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/csv"},
        data=csv_bytes, timeout=120
    )
    r.raise_for_status()

# ---------- Product keys ----------

def load_product_keys(drive_id: str, keys_path: str) -> list[dict]:
    #Read CSV with columns: product_key, used_by, used_at.
    data = download_file(drive_id, keys_path)
    if data is None:
        return []
    text = data.decode("utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)

def update_product_key(drive_id: str, keys_path: str, product_key: str, email: str, timestamp: str) -> bool:
    
    #Mark a key as used by this email. Returns True if key existed & was unused.
    
    data = download_file(drive_id, keys_path)
    rows = []
    if data:
        text = data.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    if not rows:
        # Initialize with a single row if file was missing/empty
        rows = [{"product_key": product_key, "used_by": "", "used_at": ""}]

    updated = False
    for r in rows:
        if r.get("product_key") == product_key and (r.get("used_by") in [None, "", ""]):
            r["used_by"] = email
            r["used_at"] = timestamp
            updated = True
            break
    if not updated:
        return False

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["product_key", "used_by", "used_at"])
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = out.getvalue().encode("utf-8")

    token = acquire_token()
    r = requests.put(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{keys_path}:/content",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/csv"},
        data=csv_bytes, timeout=120
    )
    r.raise_for_status()
    return True

# ---------- Read JSON (e.g., training_videos.json) ----------

def read_json(drive_id: str, json_path: str) -> list[dict] | dict | None:
    b = download_file(drive_id, json_path)
    if b is None:
        return None
    return json.loads(b.decode("utf-8", errors="replace"))

# ---------- Build monthly summary log ----------

def append_monthly_summary_log_csv(drive_id: str, log_path: str, entry: dict) -> None:
    """
    Append one anonymous upload-summary row to a CSV in OneDrive.
    Creates the file with headers if it doesn't exist.
    """
    existing = download_file(drive_id, log_path)

    headers = [
        "timestamp",
        "upload_filename",
        "sample_count",
        "green_count",
        "amber_count",
        "red_count",
    ]
    rows = []

    if existing is None:
        rows.append(headers)
    else:
        text = existing.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        existing_rows = list(reader)
        rows.extend(existing_rows if existing_rows else [headers])

    rows.append([
        entry.get("timestamp", ""),
        entry.get("upload_filename", ""),
        str(entry.get("sample_count", 0)),
        str(entry.get("green_count", 0)),
        str(entry.get("amber_count", 0)),
        str(entry.get("red_count", 0)),
    ])

    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows(rows)
    csv_bytes = out.getvalue().encode("utf-8")

    token = acquire_token()
    r = requests.put(
        f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{log_path}:/content",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "text/csv"},
        data=csv_bytes,
        timeout=120,
    )
    r.raise_for_status()