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
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/update/<int:row_num>", methods=["GET"])
def update_form(row_num):
    try:
        snapshot = sheets_service.get_row_snapshot(row_num)
    except Exception as e:
        flash(f"Gagal mengambil data baris: {e}", "error")
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
        flash(f"Gagal update: {e}", "error")

    return redirect(url_for("update_form", row_num=row_num))


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
