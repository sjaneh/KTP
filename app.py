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
    #ui.update_nav_panel("main_nav", target="Upload", method="hide")
    ui.update_nav_panel("main_nav", target="Decision Tool", method="hide")
    ui.update_nav_panel("main_nav", target="My results", method="hide")
    ui.update_navset("main_nav", selected="Activation")

def _unlock_tabs_and_go(default="Training"):
    ui.update_nav_panel("main_nav", target="Training", method="show")
    #ui.update_nav_panel("main_nav", target="Upload", method="show")
    ui.update_nav_panel("main_nav", target="Decision Tool", method="show")
    ui.update_nav_panel("main_nav", target="My results", method="show")
    ui.update_nav_panel("main_nav", target="Activation", method="hide")
    ui.update_navset("main_nav", selected=default)

@reactive.effect
def _on_session_start():
    _protect_tabs_initial()



# ---------------- PAGE LAYOUT ----------------
with ui.navset_bar(title="'New Name of Test Here' App", id="main_nav"):

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

                #debug attempt
                #print("DEBUG NEW ACCOUNT PASSWORD:", repr(pwd), "len=", len(pwd))

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

                #debug attempt
                print("DEBUG LOGIN PASSWORD:", repr(pwd), "len=", len(pwd))

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


    # ===== UPLOAD =====
    #with ui.nav_panel("Upload", value="Upload"):
        #ui.h3("Test Results Upload")

        #ui.input_file("tbl", "Upload CSV (Document must be in CSV format and contain 6 columns)", accept=[".csv"], multiple=False)
        

        #@render.download(filename=lambda: f"template_{time.strftime('%Y%m%d')}.csv", label="Download CSV Template")
        #def dl_template():
            #try:
                #b = download_file(DRIVE_ID, "NBFKTPAPP/Admin/upload_template.csv")
                #if not b:
                    #fallback to generated template if OneDrive read fails
                    #cols = EXPECTED_COLUMNS or [f"col{i}" for i in range(1, 14)]
                    #df = pd.DataFrame(columns=cols)
                    #s = io.StringIO()
                    #df.to_csv(s, index=False)
                    #yield s.getvalue().encode("utf-8")
                    #return
                #yield b
            #except Exception as ex:
                #print("TEMPLATE DOWNLOAD ERROR:", ex)
                #fallback to generated template
                #cols = EXPECTED_COLUMNS or [f"col{i}" for i in range(1, 14)]
                #df = pd.DataFrame(columns=cols)
                #s = io.StringIO()
                #df.to_csv(s, index=False)
                #yield s.getvalue().encode("utf-8")

        #@reactive.calc
        #def parsed_df():
            #f = input.tbl()
            #if not f:
                #return pd.DataFrame()
            #try:
                #return pd.read_csv(f[0]["datapath"])
            #except:
                #return pd.DataFrame()

        #@render.text
        #def validation_msg():
            #email = get_user_email()
            #if not email:
                #return "⚠️ Please sign in first."
            #df = parsed_df()
            #if df.empty:
                #return "Awaiting upload…"
            #if df.shape[1] != 6:
                #return f"❌ Expected 6 columns, got {df.shape[1]}."
            #if df.shape[0] < 1:
                #return "❌ No data rows."
            #return f"✅ {df.shape[0]} rows × 6 columns."

        #@render.data_frame
        #def preview_df():
            #df = parsed_df()
            #return df.head(25) if not df.empty else pd.DataFrame({"Status": ["No preview available"]})

        #ui.input_action_button("confirm", "Confirm Upload")

        #@render.text
        #@reactive.event(input.confirm)
        #def upload_status():
           # email = get_user_email()
           # if not email:
               # return "You must sign in first."

           # f = input.tbl()
           # df = parsed_df()
           # if not f or df.empty:
                #return "Upload not attempted."

            #if df.shape[1] != 6:
               # return "Invalid column count."

            #ts = time.strftime("%Y%m%d_%H%M%S")
            #filename = f"upload_{ts}.csv"
            #dest_dir = user_upload_dir(email)
            #dest_path = f"{dest_dir}/{filename}"

            #sha256 = _sha256_file(f[0]["datapath"])

            #try:
                #ensure_folder(DRIVE_ID, dest_dir)
                #upload_small_file(DRIVE_ID, dest_path, f[0]["datapath"])

                #ensure_folder(DRIVE_ID, user_log_dir(email))
                #append_audit_log_csv(
                    #DRIVE_ID, f"{user_log_dir(email)}/audit.csv",
                   # {
                  #      "timestamp": ts, "user_id": email, "filename": filename,
                  #      "rows": df.shape[0], "columns": 6,
                  #      "sha256": sha256, "drive_path": dest_path,
                  #      "result": "success"
#}
              #  )

             #   return f"✅ Uploaded: {filename}"

          #  except Exception as ex:
           #     return f"Upload failed: {ex}"


    # ===== DECISION TOOL =====
    with ui.nav_panel("Decision Tool", value="Decision Tool"):
        ui.h3("Decision Tree Calculator")

        ui.hr()
        ui.h4("Enter and collect results")

        ui.input_text("material_name", "Material name", placeholder="e.g. Sample A / Product XYZ")

        # Shiny for Python date input:
        ui.input_date("test_date", "Date of test", value=dt.date.today())

        ui.input_action_button("enter_result", "Enter")
        ui.input_action_button("results_completed", "Results completed")
       # ui.output_ui("entry_status")

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
            return df[cols]
        

        ui.input_radio_buttons(
            "material_type",
            "Select material type",
            choices={
                "natural": "Natural or Mixed Fibre Materials",
                "synthetic": "Synthetic Fibre and Foam Materials",
            },
            selected="natural",
        )

        ui.input_numeric("num_eb", "Enterobacteriaceae (EB)", value=0)
        ui.input_numeric("num_ym", "Yeast & Mould (YM)", value=0)
        ui.input_numeric("num_rac", "Rapid Aerobic Count (RAC)", value=0)

        @render.ui
        def decision_result():
            try:
                eb = float(input.num_eb())
                ym = float(input.num_ym())
                rac = float(input.num_rac())
            except Exception:
                return ui.div("Please enter valid numbers.", style="color: #b00020;")

            rules = decision_rules()
            result, explanation = decision_logic.evaluate_triplet([eb, ym, rac], rules)

            color_map = {
                "Green": "#2e7d32",
                "Amber": "#ff8f00",
                "Red": "#c62828",
            }
            color = color_map.get(str(result), "#333333")

            return ui.div(
                ui.div(str(result), style=f"color: {color}; font-weight: 700; font-size: 1.2rem;"),
                ui.div(str(explanation), style="margin-top: 0.25rem; color: #333333;"),
                style="margin-top: 1rem;",
            )

        # Put this inside the Decision Tool panel code (same scope as other reactive funcs)

        entered_results = reactive.Value(pd.DataFrame(columns=[
            "material_name",
            "test_date",
            "material_type",
            "EB",
            "YM",
            "RAC",
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
                eb = float(input.num_eb())
                ym = float(input.num_ym())
                rac = float(input.num_rac())
            except Exception:
                ui.notification_show("Please enter valid numbers for EB/YM/RAC.", type="error")
                return

            rules = decision_rules()
            result, explanation = decision_logic.evaluate_triplet([eb, ym, rac], rules)

            df = entered_results.get()
            new_row = pd.DataFrame([{
                "material_name": name,
                "test_date": str(test_date),               # store as ISO string for CSV consistency
                "material_type": input.material_type(),
                "EB": eb,
                "YM": ym,
                "RAC": rac,
                "decision_result": str(result),            # Red/Amber/Green
                "decision_explanation": str(explanation),  # optional but useful
                "entered_at": dt.datetime.now().isoformat(timespec="seconds"),
            }])

            entered_results.set(pd.concat([df, new_row], ignore_index=True))

            # Optional: reset inputs after entry
            ui.update_text("material_name", value="")
            ui.update_numeric("num_eb", value=0)
            ui.update_numeric("num_ym", value=0)
            ui.update_numeric("num_rac", value=0)

        

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
            dest_dir = user_upload_dir(email)
            dest_path = f"{dest_dir}/{filename}"

            # Convert to CSV bytes
            csv_bytes = df.to_csv(index=False).encode("utf-8")

            try:
                ensure_folder(DRIVE_ID, dest_dir)
                upload_bytes(DRIVE_ID, dest_path, csv_bytes, content_type="text/csv")
            except Exception as ex:
                ui.notification_show(f"Upload to OneDrive failed: {ex}", type="error")
                return

            # --- Email summary via Graph ---
            try:
                # You can switch this to HTML template later; for now simple summary
                subject = "Your test results summary"
                body_text = (
                    f"Hello,\n\n"
                    f"Your results have been submitted.\n\n"
                    f"Uploaded file: {filename}\n"
                    f"Number of rows: {len(df)}\n\n"
                    f"Summary (first 50 rows):\n"
                    f"{df[['material_name','test_date','decision_result']].head(50).to_string(index=False)}\n"
                )

                send_results_email(
                    to_email=email,
                    subject=subject,
                    body_text=body_text,
                    # Optional: attach the CSV they submitted
                    attachments=[(filename, "text/csv", csv_bytes)],
                )
            except Exception as ex:
                ui.notification_show(f"Email failed: {ex}", type="error")
                # Decide: you might still want to clear table or not. You asked to clear after sending/email+upload,
                # so only clear if email succeeded. Since it failed, return without clearing.
                return

            # --- Clear the session table after success ---
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
                # skip folders, only handle files
                if "file" not in it:
                    continue
                name = it.get("name", "")
                # build path and download
                path = f"{folder}/{name}"
                try:
                    b = download_file(DRIVE_ID, path)
                    if not b:
                        print("MY RESULTS: download_file returned empty for", path)
                        continue
                    # read into pandas - handle bytes
                    try:
                        df = pd.read_csv(io.BytesIO(b))
                    except Exception:
                        # try decode as text
                        s = b.decode("utf-8", errors="replace")
                        df = pd.read_csv(io.StringIO(s))
                    # attach metadata: source filename and attempt to get timestamp from filename
                    df["_uploaded_filename"] = name
                    ts = None
                    # expect filenames like upload_YYYYMMDD_HHMMSS.csv
                    if name.startswith("upload_"):
                        try:
                            ts_raw = name[len("upload_"):].split(".")[0]
                            ts = time.strptime(ts_raw, "%Y%m%d_%H%M%S")
                            ts_iso = time.strftime("%Y-%m-%d %H:%M:%S", ts)
                        except Exception:
                            ts_iso = ""
                    else:
                        ts_iso = ""
                    df["_upload_time"] = ts_iso
                    dfs.append(df)
                except Exception as ex:
                    print("MY RESULTS: failed to load", path, ex)
                    continue

            if not dfs:
                return pd.DataFrame()
            # concatenate with union of columns
            try:
                big = pd.concat(dfs, ignore_index=True, sort=False)
            except Exception as ex:
                print("MY RESULTS: concat failed", ex)
                return pd.DataFrame()

            # Try to coerce upload_time to datetime and make a column we can use
            try:
                big["_upload_time_dt"] = pd.to_datetime(big["_upload_time"], errors="coerce")
            except Exception:
                big["_upload_time_dt"] = pd.NaT

            return big


        # Server-rendered selects (so they update based on uploaded CSVs)
        @render.ui
        def metric_select():
            df = my_uploads_df()
            if df.empty:
                choices = ["(no numeric columns)"]
            else:
                exclude = {"_uploaded_filename", "_upload_time", "_upload_time_dt"}
                numeric_cols = [c for c in df.columns if c not in exclude and pd.api.types.is_numeric_dtype(df[c])]
                choices = numeric_cols or ["(no numeric columns)"]
            return ui.input_select("metric", "Select metric to plot", choices=choices)

        

        @render.ui
        def rowval_select():
             # Get unique values from the first non-numeric column found
            df = my_uploads_df()
            if df.empty:
                return ui.input_select("rowval", "Sample Identifier", choices=["(no values)"])
    
            # Find the first non-numeric column to use as identifier
            exclude = {"_uploaded_filename", "_upload_time", "_upload_time_dt"}
            id_cols = [c for c in df.columns if c not in exclude and not pd.api.types.is_numeric_dtype(df[c])]
    
            if not id_cols:
                return ui.input_select("rowval", "Sample Identifier", choices=["(no values)"])
    
            # Use the first identifier column
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
            return f"Loaded {len(df)} rows from your uploads. Choose a metric and a Sample Identifier to plot."

        @render.plot
        def results_plot():
            df = my_uploads_df()
            if df.empty:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No data to plot", ha="center", va="center")
                ax.set_axis_off()
                return fig

            metric = input.metric()
            rowval = input.rowval() or ""

    # Find the first non-numeric column to use as identifier
            exclude = {"_uploaded_filename", "_upload_time", "_upload_time_dt"}
            id_cols = [c for c in df.columns if c not in exclude and not pd.api.types.is_numeric_dtype(df[c])]
    
            if not id_cols:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No identifier columns found", ha="center", va="center")
                ax.set_axis_off()
                return fig
    
            rid = id_cols[0]

    # prepare plotting columns
            if not metric or metric == "(no numeric columns)":
                numeric_cols = [c for c in df.columns if c not in {"_uploaded_filename", "_upload_time", "_upload_time_dt"} and pd.api.types.is_numeric_dtype(df[c])]
                plot_cols = numeric_cols
            else:
                plot_cols = [metric]

            if not plot_cols:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No numeric columns to plot", ha="center", va="center")
                ax.set_axis_off()
                return fig

    # apply row-name filtering if both selected and valid
            df_plot = df
            if rid and rid in df.columns and rowval and rowval != "(no values)":
        # compare as strings to avoid dtype issues
                df_plot = df_plot[df_plot[rid].astype(str) == str(rowval)]

            if df_plot.empty:
                fig, ax = plt.subplots()
                ax.text(0.5, 0.5, "No matching rows for selected Sample Identifier", ha="center", va="center")
                ax.set_axis_off()
                return fig

    # choose time axis: prefer Date column if it's datetime-like, else use upload time
            if 'Date' in df_plot.columns and pd.api.types.is_datetime64_any_dtype(df_plot['Date']):
                time_col = 'Date'
                times = pd.to_datetime(df_plot[time_col], errors="coerce")
            else:
                time_col = "_upload_time_dt"
                times = df_plot["_upload_time_dt"]

            fig, ax = plt.subplots(figsize=(8, 4 + 0.6 * len(plot_cols)))
            for col in plot_cols:
                sub = df_plot[[col]].copy()
                sub[time_col] = times
                sub = sub.dropna(subset=[col, time_col])
                if sub.empty:
                    continue
                ax.plot(sub[time_col], sub[col], marker="o", label=col)
            ax.legend()
            ax.set_xlabel("Time")
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
    
    # Find the first non-numeric column to use as identifier
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
            meta = ["_uploaded_filename", "_upload_time"]
            display_cols = cols + [c for c in meta if c in df_show.columns]
            return df_show[display_cols].head(200)