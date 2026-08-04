from flask import Flask, render_template, request, jsonify, redirect, url_for, flash

import config
import sheets_service

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "")
    try:
        results = sheets_service.search_rows(q)
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/update/<int:row_num>", methods=["GET"])
def update_form(row_num):
    try:
        snapshot = sheets_service.get_row_snapshot(row_num)
    except Exception as e:
        flash(f"Gagal mengambil data baris: {type(e).__name__}: {e}", "error")
        return redirect(url_for("index"))
    return render_template(
        "update.html",
        row=row_num,
        snapshot=snapshot,
        z_options=config.Z_OPTIONS,
        aa_options=config.AA_OPTIONS,
    )


@app.route("/update/<int:row_num>", methods=["POST"])
def do_update(row_num):
    z_value = request.form.get("status_z", "").strip()
    aa_value = request.form.get("status_aa", "").strip()
    note_text = request.form.get("note_text", "").strip()

    if not z_value:
        flash("Status (kolom Z) wajib dipilih.", "error")
        return redirect(url_for("update_form", row_num=row_num))
    if not note_text:
        flash("Keterangan tidak boleh kosong.", "error")
        return redirect(url_for("update_form", row_num=row_num))

    try:
        date_col, note_col = sheets_service.update_status(row_num, z_value, aa_value, note_text)
        flash(f"Berhasil diupdate ke kolom {note_col} (tanggal di {date_col}).", "success")
    except Exception as e:
        flash(f"Gagal update: {type(e).__name__}: {e}", "error")

    return redirect(url_for("update_form", row_num=row_num))


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


@app.route("/debug/sheet-check")
def debug_sheet_check():
    """
    Diagnostic endpoint: walks through each step of connecting to the
    sheet and reports exactly where it fails, instead of a blank error.
    Safe to leave in place (read-only, no secrets exposed).
    """
    import json as _json
    report = {}

    # Step 1: env vars present?
    report["has_GOOGLE_SERVICE_ACCOUNT_JSON"] = bool(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    report["SPREADSHEET_ID"] = config.SPREADSHEET_ID
    report["SHEET_NAME"] = config.SHEET_NAME

    # Step 2: JSON parses?
    try:
        info = _json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
        report["json_parses"] = True
        report["client_email"] = info.get("client_email")
        report["project_id"] = info.get("project_id")
    except Exception as e:
        report["json_parses"] = False
        report["json_parse_error"] = f"{type(e).__name__}: {e}"
        return jsonify(report), 500

    # Step 3: can we build a client?
    try:
        client = sheets_service.get_client()
        report["client_built"] = True
    except Exception as e:
        report["client_built"] = False
        report["client_error"] = f"{type(e).__name__}: {e}"
        return jsonify(report), 500

    # Step 4: can we open the spreadsheet by ID?
    try:
        sh = client.open_by_key(config.SPREADSHEET_ID)
        report["spreadsheet_opened"] = True
        report["spreadsheet_title"] = sh.title
        report["available_worksheets"] = [ws.title for ws in sh.worksheets()]
    except Exception as e:
        report["spreadsheet_opened"] = False
        report["spreadsheet_error"] = f"{type(e).__name__}: {e}"
        return jsonify(report), 500

    # Step 5: does the target worksheet name match exactly?
    try:
        ws = sh.worksheet(config.SHEET_NAME)
        report["worksheet_found"] = True
        report["row_count"] = ws.row_count
        report["col_count"] = ws.col_count
        report["header_row_preview"] = ws.row_values(config.HEADER_ROW)[:10]
    except Exception as e:
        report["worksheet_found"] = False
        report["worksheet_error"] = f"{type(e).__name__}: {e}"
        return jsonify(report), 500

    report["all_ok"] = True
    return jsonify(report)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
