# app.py — extended with "My results" row-name filtering
from dotenv import load_dotenv
_=load_dotenv(override=False)

# app.py (patched for JSON password storage and Shiny Express)
import os
import time
import hashlib
import pandas as pd
import io
import matplotlib.pyplot as plt
import datetime as dt
import tempfile

from shiny.express import ui, render, input
from shiny import reactive, App
from shiny import ui as U
from graph_mail import send_results_email
from certificate_pdf import make_certificate_pdf_bytes
from crypto_store import encrypt_for_user, decrypt_for_user

from accounts import (
    create_account,
    verify_login,
    set_activated,
    record_login
)

from activation_context import set_user_email, get_user_email
from one_drive import (
    ensure_folder, upload_small_file, append_audit_log_csv,
    list_children, create_view_link, update_product_key, read_json,
    download_file, 
    upload_bytes,  
)

import decision_logic

print("Loaded app.py at", time.strftime("%H:%M:%S"))

# ---------- CONFIG ----------
DRIVE_ID = os.environ["DRIVE_ID"]

ADMIN_KEYS_PATH      = "NBFKTPAPP/Admin/product_keys.csv"
TRAINING_FOLDER      = "NBFKTPAPP/Training"
TRAINING_VIDEOS_JSON = "NBFKTPAPP/Admin/training_videos.json"
NATURAL_FIBRES_RULES_JSON   = "NBFKTPAPP/Admin/natural_fibres_decision_rules.json"
SYNTHETIC_FIBRES_RULES_JSON = "NBFKTPAPP/Admin/synthetic_fibres_decision_rules.json"
CERT_LOGO_PATH = "NBFKTPAPP/Admin/brand/logo.png"          
CERT_THEME_JSON = "NBFKTPAPP/Admin/brand/cert_theme.json"

EXPECTED_COLUMNS = None

def user_upload_dir(email: str) -> str:
    return f"NBFKTPAPP/Users/{email}/uploads"

def user_log_dir(email: str) -> str:
    return f"NBFKTPAPP/Users/{email}/logs"

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- Load decision rules (optional JSON config) ----------
@reactive.calc
def decision_rules():
    # Default to natural if missing (e.g., before UI initializes)
    choice = input.material_type() if hasattr(input, "material_type") else "natural"

    if choice == "synthetic":
        path = SYNTHETIC_FIBRES_RULES_JSON
    else:
        path = NATURAL_FIBRES_RULES_JSON

    cfg = read_json(DRIVE_ID, path)
    return cfg or {}


# ---------- NAVIGATION & SECURITY ----------
def _protect_tabs_initial():
    ui.update_nav_panel("main_nav", target="Training", method="hide")
    ui.update_nav_panel("main_nav", target="Decision Tool", method="hide")
    ui.update_nav_panel("main_nav", target="My results", method="hide")
    ui.update_navset("main_nav", selected="Activation")

def _unlock_tabs_and_go(default="Training"):
    ui.update_nav_panel("main_nav", target="Training", method="show")
    ui.update_nav_panel("main_nav", target="Decision Tool", method="show")
    ui.update_nav_panel("main_nav", target="My results", method="show")
    ui.update_nav_panel("main_nav", target="Activation", method="hide")
    ui.update_navset("main_nav", selected=default)

@reactive.effect
def _on_session_start():
    _protect_tabs_initial()

# --- Global branding ---
ui.tags.head(
    ui.tags.style("""
.app-banner {
    display: grid;
    grid-template-columns: auto 1fr auto;   /* logo | title | right spacer */
    align-items: center;
    gap: 12px;
    padding: 10px 16px;
    background: #193159;
    color: white;
    border-bottom: 4px solid #C83E2F;
}

/* Logo: responsive, but not tiny */
.app-banner img {
    height: auto;
    width: clamp(110px, 22vw, 200px);       /* min 110px, scales, max 200px */
}

/* Title always centered in the middle column, no overlap possible */
.app-banner .title-wrap {
    text-align: center;
    min-width: 0;                           /* IMPORTANT: allows text to wrap instead of overflow */
}

/* Make title scale with viewport width, within reasonable bounds */
.app-banner .title {
    font-weight: 700;
    line-height: 1.1;
    margin: 0;
    font-size: clamp(1.05rem, 2.2vw, 1.55rem);
    overflow-wrap: anywhere;                /* break long words if needed */
}

.app-banner .subtitle {
    margin: 0;
    opacity: 0.9;
    line-height: 1.1;
    font-size: clamp(0.85rem, 1.4vw, 1.0rem);
    overflow-wrap: anywhere;
}

/* Right spacer: keeps the title truly centered overall (balances the logo column) */
.app-banner::after {
    content: "";
}

/* On very narrow screens, stack naturally */
@media (max-width: 420px) {
    .app-banner {
        grid-template-columns: 1fr;
        justify-items: center;
        text-align: center;
    }
    .app-banner img {
        width: clamp(120px, 45vw, 180px);
    }

/* Add spacing between radio button options */
#material_type .shiny-options-group .form-check {
    margin-bottom: 0.5rem;   /* increase/decrease to taste */
}
}
    """)
)

ui.div(
    ui.img(src="logo.png", alt="Logo"),
    ui.div(
        ui.div("Cleanliness of Post-Consumer Material Test", class_="title"),
        class_="title-wrap"
    ),
    class_="app-banner",
)

# ---------------- PAGE LAYOUT ----------------
with ui.navset_bar(title="Menu", id="main_nav"):

    # ===== ACTIVATION =====
    with ui.nav_panel("Activation", value="Activation"):

        ui.h3("Welcome")
        ui.p("Create a new account or sign in to continue.")

        # --- Sign-up ---
        ui.h4("Create account + Activate")
        ui.input_text("reg_email", "Email")
        ui.input_password("reg_password", "Password")
        ui.input_text("reg_key", "Product key")
        ui.input_action_button("reg_btn", "Create & Activate")

        ui.hr()

        # --- Sign-in ---
        ui.h4("Existing users: Sign in")
        ui.input_text("login_email", "Email")
        ui.input_password("login_password", "Password")
        ui.input_action_button("login_btn", "Sign in")

        # ------ Handlers ------

        @render.text
        @reactive.event(input.reg_btn)
        def reg_status():
            try:
                email = (input.reg_email() or "").strip().lower()
                pwd   = (input.reg_password() or "")
                key   = (input.reg_key() or "").strip()

                if not email or not pwd or not key:
                    return "Please fill all fields."

                # Create non-activated account
                ok = create_account(DRIVE_ID, email, pwd,
                                    product_key="", activated=False)
                if not ok:
                    return "❌ Account already exists. Please sign in."

                # Validate product key
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                if not update_product_key(DRIVE_ID, ADMIN_KEYS_PATH, key, email, ts):
                    return "❌ Invalid product key. Account created but not activated."

                set_activated(DRIVE_ID, email, key)

                # Prepare user folders
                ensure_folder(DRIVE_ID, user_upload_dir(email))
                ensure_folder(DRIVE_ID, user_log_dir(email))

                # Bind session
                set_user_email(email)
                _unlock_tabs_and_go("Training")

                return f"✅ Account created and activated for {email}."
            except Exception as ex:
                print("REG ERROR:", ex)
                return "⚠️ Something went wrong."
            

        @render.text
        @reactive.event(input.login_btn)
        def login_status():
            try:
                email = (input.login_email() or "").strip().lower()
                pwd   = (input.login_password() or "")

                if not email or not pwd:
                    return "Please enter both fields."

                # JSONL backend returns (ok: bool, activated: bool, product_key)
                ok, activated, product_key = verify_login(DRIVE_ID, email, pwd)
                print("DEBUG verify_login:", ok, activated, product_key)

                if not ok:
                    return "❌ Incorrect email or password."

                if not activated:
                    return "⚠️ Account exists but is NOT activated."

                record_login(DRIVE_ID, email)
                set_user_email(email)
                _unlock_tabs_and_go("Training")

                return f"✅ Signed in as {email}."
            except Exception as ex:
                print("LOGIN ERROR:", ex)
                return "⚠️ Login failed."



    # ===== TRAINING =====
    with ui.nav_panel("Training", value="Training"):
        ui.h3("Training Documents and Instructional Videos")

        @render.ui
        def pdf_list():
            items = list_children(DRIVE_ID, TRAINING_FOLDER)
            links = []
            for it in items:
                if "file" in it:
                    try:
                        web_url = create_view_link(DRIVE_ID, it["id"], "organization")
                        links.append(ui.div(ui.a(it["name"], href=web_url, target="_blank")))
                    except:
                        links.append(ui.div(it["name"]))
            return ui.div(*links) if links else ui.div("No Documents Found.")

        @render.ui
        def video_embeds():
            cfg = read_json(DRIVE_ID, TRAINING_VIDEOS_JSON)
            if not cfg:
                return ui.div("No Video Files Found.")
            videos = cfg if isinstance(cfg, list) else cfg.get("videos", [])
            blocks = []
            for v in videos:
                title = v.get("title", "Video")
                iframe = v.get("embed_iframe", "")
                blocks.append(ui.div(ui.h4(title), ui.HTML(iframe)))
            return ui.div(*blocks)


   
    # ===== DECISION TOOL =====
    with ui.nav_panel("Result Calculator", value="Decision Tool"):
        ui.h3("Result Calculator and Certificate Generator")
        ui.hr()
        ui.h4("1. Input each result in the spaces provided below. ")
        ui.p("Enter a number or 'TNTC'")
        ui.h4("2. Press 'Enter' after each sample")
        ui.h4("3. Once all results are entered press 'Results Complete' to recieve your automatically generated certificate.")

        ui.input_text("material_name", "Material name (Case sensitive)", placeholder="e.g. Sample A / Product XYZ")
        ui.input_date("test_date", "Date of test", value=dt.date.today())

        ui.h4("Select Material Category")

        ui.input_radio_buttons(
            "material_type",
            "",
            choices={
                "Natural or Mixed": "Natural or Mixed Fibre Materials",
                "Synthetic": "Synthetic Fibre and Foam Materials",
            },
            selected="natural",
        )

        ui.h5("Enterobacteriaceae (EB) replicates")
        ui.input_text("eb_1", "EB replicate 1", value="0")
        ui.input_text("eb_2", "EB replicate 2", value="0")
        ui.input_text("eb_3", "EB replicate 3", value="0")

        ui.h5("Yeast & Mould (YM) replicates")
        ui.input_text("ym_1", "YM replicate 1", value="0")
        ui.input_text("ym_2", "YM replicate 2", value="0")
        ui.input_text("ym_3", "YM replicate 3", value="0")

        ui.h5("Rapid Aerobic Count (RAC) replicates")
        ui.input_text("rac_1", "RAC replicate 1", value="0")
        ui.input_text("rac_2", "RAC replicate 2", value="0")
        ui.input_text("rac_3", "RAC replicate 3", value="0")

        TNTC_SENTINEL = "TNTC"

        def _is_tntc(val) -> bool:
            return str(val or "").strip().upper() == TNTC_SENTINEL

        def _parse_number(val) -> float:
            # allow commas like "1,000"
            s = str(val or "").strip().replace(",", "")
            if not s:
                raise ValueError("Empty value")
            return float(s)

        def _avg3_or_tntc(a, b, c):
            # If any replicate is TNTC => average shown as TNTC
            if _is_tntc(a) or _is_tntc(b) or _is_tntc(c):
                return TNTC_SENTINEL
            avg = (_parse_number(a) + _parse_number(b) + _parse_number(c)) / 3.0
            return round(avg, 2)

        def _fmt_avg(x):
            return x if x == TNTC_SENTINEL else f"{float(x):.2f}"

        def _any_tntc_in_replicates() -> bool:
            vals = [
                input.eb_1(), input.eb_2(), input.eb_3(),
                input.ym_1(), input.ym_2(), input.ym_3(),
                input.rac_1(), input.rac_2(), input.rac_3(),
            ]
            return any(_is_tntc(v) for v in vals)

        RESULT_LABELS = {
            "Green": "Good",
            "Amber": "Unsatisfactory",
            "Red": "Cause for Concern",
        }

        def _display_label(result_value: str) -> str:
            # Keep stored values as Green/Amber/Red, but display nicer labels
            key = str(result_value or "").strip().title()  # "green" -> "Green"
            return RESULT_LABELS.get(key, str(result_value))

        @render.ui
        def decision_result():
            try:
                eb_avg = _avg3_or_tntc(input.eb_1(), input.eb_2(), input.eb_3())
                ym_avg = _avg3_or_tntc(input.ym_1(), input.ym_2(), input.ym_3())
                rac_avg = _avg3_or_tntc(input.rac_1(), input.rac_2(), input.rac_3())
            except Exception:
                return ui.div("Please enter valid numbers for all replicates (or TNTC).", style="color: #b00020;")

            # If ANY box is TNTC, skip decision tree entirely
            if _any_tntc_in_replicates():
                result = "Red"
                explanation = "TNTC was entered for at least one replicate."
            else:
                rules = decision_rules()
                result, explanation = decision_logic.evaluate_triplet(
                    [float(eb_avg), float(ym_avg), float(rac_avg)],
                    rules,
                )

            color_map = {
                "Green": "#2e7d32",
                "Amber": "#ff8f00",
                "Red": "#c62828",
            }
            color = color_map.get(str(result), "#333333")
            display_result = _display_label(result)

            return ui.div(
                ui.div(
                    f"Averages → EB: {_fmt_avg(eb_avg)}, YM: {_fmt_avg(ym_avg)}, RAC: {_fmt_avg(rac_avg)}",
                    style="margin-bottom: 0.5rem; color: #333333;",
                ),
                ui.div(display_result, style=f"color: {color}; font-weight: 700; font-size: 1.2rem;"),
                ui.div(str(explanation), style="margin-top: 0.25rem; color: #333333;"),
                style="margin-top: 1rem;",
            )


        ui.input_action_button("enter_result", "Enter")



        ui.hr()
        ui.h4("Results entered this session")

        @render.data_frame
        def entered_results_table():
            df = entered_results.get()
            if df.empty:
                return pd.DataFrame({"Status": ["No rows entered yet."]})
            # show a friendly subset first
            cols = ["material_name", "test_date", "material_type", "EB", "YM", "RAC", "decision_result"]
            cols = [c for c in cols if c in df.columns]

            out = df[cols].copy()
            if "decision_result" in out.columns:
                out["decision_result"] = out["decision_result"].map(_display_label)

            return out
        

        ui.input_action_button("results_completed", "Results completed")
        

        entered_results = reactive.Value(pd.DataFrame(columns=[
            "material_name",
            "test_date",
            "material_type",
            "EB_1", "EB_2", "EB_3",
            "YM_1", "YM_2", "YM_3",
            "RAC_1", "RAC_2", "RAC_3",
            "EB", "YM", "RAC",
            "decision_result",
            "decision_explanation",
            "entered_at",
        ]))

        

        @reactive.effect
        @reactive.event(input.enter_result)
        def _on_enter_result():
            email = get_user_email()
            if not email:
                ui.notification_show("Please sign in first.", type="error")
                return

            name = (input.material_name() or "").strip()
            if not name:
                ui.notification_show("Please enter a material name.", type="error")
                return

            # date comes back as datetime.date
            test_date = input.test_date()
            if not test_date:
                ui.notification_show("Please select the test date.", type="error")
                return

            try:
                eb_avg = _avg3_or_tntc(input.eb_1(), input.eb_2(), input.eb_3())
                ym_avg = _avg3_or_tntc(input.ym_1(), input.ym_2(), input.ym_3())
                rac_avg = _avg3_or_tntc(input.rac_1(), input.rac_2(), input.rac_3())
            except Exception:
                ui.notification_show("Please enter valid numbers for all replicates (or TNTC).", type="error")
                return

            if _any_tntc_in_replicates():
                result = "Red"
                explanation = "TNTC was entered for at least one replicate."
            else:
                rules = decision_rules()
                result, explanation = decision_logic.evaluate_triplet(
                    [float(eb_avg), float(ym_avg), float(rac_avg)],
                    rules,
                )

            df = entered_results.get()
            new_row = pd.DataFrame([{
                "material_name": name,
                "test_date": str(test_date),
                "material_type": input.material_type(),

                # Replicates (stored exactly as user typed, trimmed)
                "EB_1": str(input.eb_1() or "").strip(),
                "EB_2": str(input.eb_2() or "").strip(),
                "EB_3": str(input.eb_3() or "").strip(),
                "YM_1": str(input.ym_1() or "").strip(),
                "YM_2": str(input.ym_2() or "").strip(),
                "YM_3": str(input.ym_3() or "").strip(),
                "RAC_1": str(input.rac_1() or "").strip(),
                "RAC_2": str(input.rac_2() or "").strip(),
                "RAC_3": str(input.rac_3() or "").strip(),

                # Averages (rounded already by _avg3_or_tntc); blank if TNTC
                "EB": None if eb_avg == TNTC_SENTINEL else float(eb_avg),
                "YM": None if ym_avg == TNTC_SENTINEL else float(ym_avg),
                "RAC": None if rac_avg == TNTC_SENTINEL else float(rac_avg),

                "decision_result": str(result),
                "decision_explanation": str(explanation),
                "entered_at": dt.datetime.now().isoformat(timespec="seconds"),
            }])

            entered_results.set(pd.concat([df, new_row], ignore_index=True))

            ui.update_text("material_name", value="")
            ui.update_text("eb_1", value="0")
            ui.update_text("eb_2", value="0")
            ui.update_text("eb_3", value="0")
            ui.update_text("ym_1", value="0")
            ui.update_text("ym_2", value="0")
            ui.update_text("ym_3", value="0")
            ui.update_text("rac_1", value="0")
            ui.update_text("rac_2", value="0")
            ui.update_text("rac_3", value="0")

        

        @reactive.effect
        @reactive.event(input.results_completed)
        def _on_results_completed():
            email = get_user_email()
            if not email:
                ui.notification_show("Please sign in first.", type="error")
                return

            df = entered_results.get()
            if df.empty:
                ui.notification_show("No results to submit.", type="error")
                return

            # --- Upload to OneDrive (so My results page continues to work) ---
            ts = time.strftime("%Y%m%d_%H%M%S")
            filename = f"decisiontool_{ts}.csv"
            enc_filename = filename + ".enc"
            dest_dir = user_upload_dir(email)
            dest_path = f"{dest_dir}/{enc_filename}"
            csv_bytes = df.to_csv(index=False).encode("utf-8")
            enc_bytes = encrypt_for_user(email, csv_bytes)

            try:
                ensure_folder(DRIVE_ID, dest_dir)
                upload_bytes(DRIVE_ID, dest_path, enc_bytes, content_type="application/octet-stream")
            except Exception as ex:
                ui.notification_show(f"Upload to OneDrive failed: {ex}", type="error")
                return
            
            logo_bytes = None
            theme = {}

            try:
                logo_bytes = download_file(DRIVE_ID, CERT_LOGO_PATH)
            except Exception as ex:
                print("CERT: logo download failed:", ex)

            try:
                theme_obj = read_json(DRIVE_ID, CERT_THEME_JSON)
                if isinstance(theme_obj, dict):
                    theme = theme_obj
            except Exception as ex:
                print("CERT: theme json read failed:", ex)

            pdf_bytes = make_certificate_pdf_bytes(
                user_email=email,
                issued_on=dt.date.today(),
                results_df=df,
                logo_png_bytes=logo_bytes,
                theme=theme,
            )



            # --- Email summary via Graph ---
            try:
                # You can switch this to HTML template later; for now simple summary
                subject = "Your test results summary"
                df_email = df.copy()
                if "decision_result" in df_email.columns:
                    df_email["decision_result"] = df_email["decision_result"].map(_display_label)

                body_text = (
                    f"Hello,\n\n"
                    f"Your results have been submitted.\n\n"
                    f"Please see attached certificate.\n\n"
                    f"Uploaded file: {filename}\n"
                    f"Number of rows: {len(df)}\n\n"
                    f"Summary:\n"
                    f"{df_email[['material_name','test_date','decision_result']].head(50).to_string(index=False)}\n"
                )

                send_results_email(
                    to_email=email,
                    subject=subject,
                    body_text=body_text,
                    attachments=[
                        ("Cleanliness of Post-Consumer Material Test Certificate.pdf", "application/pdf", pdf_bytes),
                        (filename, "text/csv", csv_bytes),
                    ],
                )

            except Exception as ex:
                ui.notification_show(f"Email failed: {ex}", type="error")
                return

            entered_results.set(entered_results.get().iloc[0:0].copy())

            ui.notification_show("Results submitted: uploaded and emailed.", type="message")

    # ===== MY RESULTS =====
    with ui.nav_panel("My results", value="My results"):
        ui.h3("My uploaded results and trends")
        ui.p("View your data and trends over time.")

        # Reactive: load & combine the user's uploaded CSVs from OneDrive
        @reactive.calc
        def my_uploads_df():
            email = get_user_email()
            if not email:
                print("MY RESULTS: no user logged in")
                return pd.DataFrame()

            folder = user_upload_dir(email)
            try:
                items = list_children(DRIVE_ID, folder) or []
            except Exception as ex:
                print("MY RESULTS: list_children failed:", ex)
                return pd.DataFrame()

            dfs = []
            for it in items:
                if "file" not in it:
                    continue
                name = it.get("name", "")
                path = f"{folder}/{name}"
                try:
                    b = download_file(DRIVE_ID, path)
                    if not b:
                        print("MY RESULTS: download_file returned empty for", path)
                        continue

                    try:
                        plain = decrypt_for_user(email, b)
                    except Exception as ex:
                        print("MY RESULTS: decrypt failed for", path, ex)
                        continue

                    try:
                        df = pd.read_csv(io.BytesIO(plain))
                    except Exception:
                        s = plain.decode("utf-8", errors="replace")
                        df = pd.read_csv(io.StringIO(s))

                    df["_uploaded_filename"] = name
                    ts = None
                    # filenames structure upload_YYYYMMDD_HHMMSS.csv

                    name_for_ts = name[:-4] if name.endswith(".enc") else name  
                    prefixes = ("upload_", "decisiontool_")
                    ts_iso = ""

                    if name_for_ts.startswith(prefixes):
                        try:
                            ts_raw = name_for_ts.split("_", 1)[1].split(".")[0]
                            ts = time.strptime(ts_raw, "%Y%m%d_%H%M%S")
                            ts_iso = time.strftime("%Y-%m-%d %H:%M:%S", ts)
                        except Exception:
                            ts_iso = ""
                    df["_upload_time"] = ts_iso
                    dfs.append(df)
                except Exception as ex:
                    print("MY RESULTS: failed to load", path, ex)
                    continue

            if not dfs:
                return pd.DataFrame()
            try:
                big = pd.concat(dfs, ignore_index=True, sort=False)
            except Exception as ex:
                print("MY RESULTS: concat failed", ex)
                return pd.DataFrame()

            try:
                big["_upload_time_dt"] = pd.to_datetime(big["_upload_time"], errors="coerce")
            except Exception:
                big["_upload_time_dt"] = pd.NaT

            if "test_date" in big.columns:
                big["test_date_dt"] = pd.to_datetime(big["test_date"], errors="coerce")
            else:
                big["test_date_dt"] = pd.NaT

            return big


        
        @render.ui
        def metric_select():
            df = my_uploads_df()
            if df.empty:
                return ui.input_checkbox_group(
                    "metrics",
                    "Select metrics to plot",
                    choices={"(no data)": "(no data)"},
                    selected=[],
                )

            avg_cols = ["EB", "YM", "RAC"]
            choices = [c for c in avg_cols if c in df.columns and pd.api.types.is_numeric_dtype(df[c])]

            if not choices:
                return ui.input_checkbox_group(
                    "metrics",
                    "Select metrics to plot",
                    choices={"(no numeric columns)": "(no numeric columns)"},
                    selected=[],
                )

            return ui.input_checkbox_group(
                "metrics",
                "Select metrics to plot",
                choices=choices,
                selected=choices,
            )

        

        @render.ui
        def rowval_select():
            df = my_uploads_df()
            if df.empty:
                return ui.input_select("rowval", "Sample Identifier", choices=["(no values)"])
    
            exclude = {"_uploaded_filename", "_upload_time", "_upload_time_dt"}
            id_cols = [c for c in df.columns if c not in exclude and not pd.api.types.is_numeric_dtype(df[c])]
    
            if not id_cols:
                return ui.input_select("rowval", "Sample Identifier", choices=["(no values)"])
    
            rid = id_cols[0]
            try:
                vals = df[rid].dropna().astype(str).unique().tolist()
                vals = sorted(vals)
            except Exception as ex:
                print("MY RESULTS: failed to compute row values for", rid, ex)
                vals = []
    
            if not vals:
                vals = ["(no values)"]
            return ui.input_select("rowval", "Sample Identifier", choices=vals, selected=vals[0] if vals and vals[0] != "(no values)" else "(no values)")
       

        @render.text
        def results_msg():
            df = my_uploads_df()
            if df.empty:
                return "No uploads found for your account."
            return f"Choose a metric and a Sample Identifier to plot."

        @render.plot
        def results_plot():
            df = my_uploads_df()
            if df.empty:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No data to plot", ha="center", va="center")
                ax.set_axis_off()
                return fig

            rowval = input.rowval() or ""

            exclude = {"_uploaded_filename", "_upload_time", "_upload_time_dt"}
            id_cols = [c for c in df.columns if c not in exclude and not pd.api.types.is_numeric_dtype(df[c])]

            if not id_cols:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No identifier columns found", ha="center", va="center")
                ax.set_axis_off()
                return fig

            rid = id_cols[0] 

            metrics = list(input.metrics() or []) 
            plot_cols = [m for m in metrics if m in {"EB", "YM", "RAC"}]

            if not plot_cols:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "Select one or more metrics (EB, YM, RAC) to plot.", ha="center", va="center")
                ax.set_axis_off()
                return fig

            if not plot_cols:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No numeric columns to plot", ha="center", va="center")
                ax.set_axis_off()
                return fig

            df_plot = df
            if rid and rid in df.columns and rowval and rowval != "(no values)":
  
                df_plot = df_plot[df_plot[rid].astype(str) == str(rowval)]

            if df_plot.empty:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No matching rows for selected Sample Identifier", ha="center", va="center")
                ax.set_axis_off()
                return fig

            if "test_date_dt" in df_plot.columns and df_plot["test_date_dt"].notna().any():
                time_col = "test_date_dt"
                x_label = "Test date"
            else:
                time_col = "_upload_time_dt"
                x_label = "Upload time"

            fig, ax = plt.subplots(figsize=(8, 4 + 0.6 * len(plot_cols)))

            for col in plot_cols:
                sub = df_plot[[time_col, col]].copy()
                sub = sub.dropna(subset=[col, time_col])
                if sub.empty:
                    continue
                sub = sub.sort_values(by=time_col)
                color_map = {
                "EB": "#d81b60",   # magenta
                "YM": "#5bb450",   # green
                "RAC": "#ffda03",  # mustard yellow
            }
                ax.plot(
                    sub[time_col],
                    sub[col],
                    marker="o",
                    label=col,
                    color=color_map.get(col, None),
                )

            ax.legend()
            ax.set_xlabel(x_label)
            ax.set_ylabel(", ".join(plot_cols))
            ax.tick_params(axis="x", rotation=30)
            fig.tight_layout()
            return fig

        @render.data_frame
        def results_table():
            df = my_uploads_df()
            if df.empty:
                return pd.DataFrame({"Status": ["No uploads found"]})
    
            rowval = input.rowval() or ""

            exclude = {"_uploaded_filename", "_upload_time", "_upload_time_dt"}
            id_cols = [c for c in df.columns if c not in exclude and not pd.api.types.is_numeric_dtype(df[c])]
    
            if not id_cols:
                return df
    
            rid = id_cols[0]
            df_show = df
            if rid and rid in df.columns and rowval and rowval != "(no values)":
                df_show = df_show[df_show[rid].astype(str) == str(rowval)]
    
    # show a few columns plus metadata
            cols = [c for c in df_show.columns if not c.startswith("_")][:10]
            meta = ["_upload_time"]
            display_cols = cols + [c for c in meta if c in df_show.columns]
            return df_show[display_cols].head(200)