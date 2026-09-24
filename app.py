import json
import traceback
import datetime
import threading
import functools
import logging
from io import BytesIO

import openpyxl
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, Response, session, send_file
from werkzeug.utils import secure_filename
from googleapiclient.errors import HttpError

import config
import sheets_service
import auth_service
import hem_db_service
import master_db_service
import ihld_db_service

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY

logger = logging.getLogger(__name__)


def _prewarm_mitra_options():
    """Pemanasan cache daftar Nama Mitra (MASTER DATA kolom H) di background
    begitu proses app ini nyala -- supaya user PERTAMA yang buka panel
    Update Status/update.html setelah deploy/restart tidak ikut kena
    latensi fetch pertama ke Google Sheets ("Memuat daftar mitra..." lama).
    Dijalankan di thread terpisah biar TIDAK menunda startup Flask/gunicorn
    kalau kredensial/Sheets API lagi lambat atau belum siap; gagal pun tidak
    apa-apa -- endpoint /api/mitra-options tetap fetch normal saat dipanggil."""
    try:
        sheets_service.get_mitra_options()
    except Exception:
        logger.exception("Prewarm mitra options gagal (non-fatal, akan di-fetch ulang saat dibutuhkan)")


threading.Thread(target=_prewarm_mitra_options, daemon=True).start()


# ── Definisi project untuk halaman "preview all project" (index.html) ──
# status "active" -> ada halamannya beneran (route sudah ada di app.py ini
# sejak sebelumnya). status "soon" -> belum ada fitur/halamannya sama
# sekali, kartu tampil abu-abu/disabled di landing page.
PROJECT_MENUS = [
    {"key": "PT3", "label": "PT3", "desc": "Dashboard order, port & LOP per batch -- status fisik terkini.", "url": "/pt3", "status": "active"},
    {"key": "PT2", "label": "PT2", "desc": "Dashboard PT2.", "url": "/pt2", "status": "active"},
    {"key": "FBB", "label": "FBB", "desc": "Monitoring & laporan FBB.", "url": "/fbb", "status": "active"},
    {"key": "MBB", "label": "MBB", "desc": "Monitoring All Node B.", "url": "/mbb-olo", "status": "active"},
    {"key": "OLO", "label": "OLO", "desc": "Monitoring OLO (satu halaman sama dengan MBB).", "url": "/mbb-olo", "status": "active"},
    {"key": "HEM", "label": "HEM", "desc": "Dashboard & input data Semesta (data_semesta, Postgres).", "url": "/hem/dashboard", "status": "active"},
    {"key": "QE", "label": "QE", "desc": "Segera hadir.", "url": None, "status": "soon"},
]


@app.before_request
def require_login():
    """Gerbang login global -- semua route butuh login KECUALI yang
    dikecualikan di bawah (halaman login sendiri, file static, health
    check buat Railway, dan proxy konten file yang dipanggil lewat <img>/
    <iframe> src langsung dari browser -- itu tetap perlu bisa diakses
    tanpa cookie session ikut kebawa kalau dibuka di tab baru, TAPI karena
    semuanya same-origin & session cookie otomatis ikut kebawa oleh
    browser di request biasa, jadi tetap aman -- pengecualian di sini
    murni untuk endpoint yang secara desain publik/tanpa login)."""
    exempt_paths = ("/login", "/healthz", "/static/")
    if request.path.startswith(exempt_paths):
        return None
    if not session.get("user"):
        # Endpoint /api/* dipanggil lewat fetch() dari JS (bukan navigasi
        # browser) -- kalau di-redirect ke /login (HTML), fetch tetap
        # "berhasil" (ikut redirect, status 200) tapi body-nya HTML, bukan
        # JSON -- bikin frontend gagal parse JSON dengan pesan membingungkan
        # ("Unexpected token '<' ... is not valid JSON"). Untuk /api/*,
        # balas 401 JSON supaya frontend bisa kasih tahu user secara jelas
        # ("sesi habis, reload & login ulang") alih-alih error parse mentah.
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Sesi login sudah habis. Reload halaman ini lalu login ulang, kemudian coba lagi."}), 401
        return redirect(url_for("login", next=request.path))
    return None


def is_viewer():
    """True kalau user yang lagi login role-nya 'Telkomsel' (view only --
    akun Tsel). Dipakai di setiap endpoint yang MENGUBAH data
    (update status, upload dokumen/KML/BOQ, import massal, insert HEM)
    supaya tetap ditolak di server walau request-nya tidak lewat
    form/tombol di browser (curl/Postman dsb). Endpoint yang murni baca
    data (termasuk download/export) TIDAK dipasangi guard ini."""
    user = session.get("user") or {}
    return user.get("role") == "Telkomsel"


def viewer_blocked_json():
    return jsonify({"ok": False, "error": "Akun Anda hanya memiliki akses lihat saja (view only)."}), 403


def is_developer():
    """True kalau user yang lagi login role-nya 'developer'. Dipakai buat
    membatasi halaman /master-data dan semua endpoint /api/master-data/*
    -- HANYA developer yang boleh lihat/ubah data referensi (Mitra,
    Status, Project, WOK, Users)."""
    user = session.get("user") or {}
    return user.get("role") == "developer"


def _require_developer_json():
    if not is_developer():
        return jsonify({"ok": False, "error": "Khusus role developer"}), 403
    return None


def project_feature_allowed(project_code, feature_key):
    """True kalau fitur ini boleh dipakai untuk project_code tsb.
    DEFAULT AMAN: kalau project_code belum ada row-nya sama sekali di
    tabel `projects` (belum pernah diisi lewat tab Project di
    /master-data), dianggap BOLEH -- supaya fitur ini tidak mendadak
    mengunci semua orang sebelum tabel projects diisi lengkap. Begitu
    row-nya sudah ada, nilai centang yang menentukan. Kalau DB MASTER
    lagi bermasalah, juga dianggap BOLEH (jangan sampai fitur utama ikut
    mati gara-gara DB referensi down)."""
    try:
        flags = master_db_service.get_project_flags(project_code)
    except Exception:
        logger.exception("Gagal cek project_feature_allowed untuk %s/%s", project_code, feature_key)
        return True
    if flags is None:
        return True
    return bool(flags.get(feature_key))


def require_project_feature(project_code, feature_key, feature_label):
    """Kalau fitur belum diaktifkan, return response 403 (JSON kalau
    request-nya ke /api/..., teks biasa kalau bukan) -- panggil ini di
    baris pertama view function, kalau hasilnya bukan None langsung
    `return` hasilnya."""
    if not project_feature_allowed(project_code, feature_key):
        msg = f"Fitur '{feature_label}' untuk project {project_code} belum diaktifkan developer (lihat tab Project di /master-data)."
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": msg}), 403
        return msg, 403
    return None


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    """Jaring pengaman terakhir: kalau ADA route /api/* yang lolos tanpa
    try/except sendiri dan meledak dengan error tak terduga, Flask secara
    default membalas HALAMAN HTML generik ("Internal Server Error") --
    itu penyebab pesan "Server mengembalikan respons tidak terduga" di
    frontend (fetch gagal parse HTML sebagai JSON). Untuk /api/*, selalu
    balas JSON supaya frontend selalu bisa menampilkan pesan yang jelas.
    Route non-/api/* (halaman biasa) tetap pakai halaman error default
    Flask/Werkzeug seperti sebelumnya -- PENTING: untuk itu handler ini
    harus me-*return* HTTPException apa adanya (mis. 404/403), BUKAN
    me-raise ulang -- raise di dalam errorhandler membuat Werkzeug gagal
    render halaman error aslinya dan malah jatuh ke 500 generik, jadi
    404 murni (route belum ada) pun tampak seperti "Internal Server
    Error" yang membingungkan."""
    from werkzeug.exceptions import HTTPException
    if request.path.startswith("/api/"):
        status = e.code if isinstance(e, HTTPException) else 500
        logger.exception("Unhandled error on %s", request.path)
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), status
    if isinstance(e, HTTPException):
        return e
    logger.exception("Unhandled error on %s", request.path)
    raise e


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", error=None)

    password = request.form.get("password", "")
    user = auth_service.verify_login(password)
    if not user:
        return render_template("login.html", error="Password salah."), 401

    session["user"] = user
    next_url = request.args.get("next") or url_for("index")
    return redirect(next_url)


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


@app.route("/")
def index():
    """Halaman 'preview all project' -- landing page setelah login, tempat
    user pilih mau buka project yang mana."""
    user = session.get("user")
    menus = [m for m in PROJECT_MENUS if auth_service.can_access_menu(user, m["key"])]
    return render_template("index.html", user=user, menus=menus, role_label=auth_service.ROLE_LABELS.get(user["role"], user["role"]))


@app.route("/master-data")
def master_data_page():
    """Halaman kelola data referensi (Mitra, Status Progress, Project,
    WOK, Users) -- khusus role developer. Sumber datanya Postgres
    (service 'DB MASTER' di Railway, lihat master_db_service.py), BUKAN
    lagi sheet Google atau users.xlsx."""
    user = session.get("user")
    if not is_developer():
        return "Halaman ini khusus role developer.", 403
    return render_template(
        "master_data.html",
        user=user,
        role_label=auth_service.ROLE_LABELS.get(user["role"], user["role"]),
    )


UPLOAD_IHLD_PER_PAGE = 10
UPLOAD_IHLD_ALLOWED_EXT = {"xlsx", "csv"}
UPLOAD_IHLD_MAX_BYTES = 10 * 1024 * 1024


@app.route("/upload-ihld")
def upload_ihld_page():
    """Halaman list + search + upload data IHLD (batch 10 baris/halaman).
    Sumber data: tabel lop_regional di Postgres (lihat ihld_db_service.py,
    service Postgres terpisah "Upload IHLD" -- env var DATABASE_URL)."""
    q = (request.args.get("q") or "").strip()
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1

    try:
        result = ihld_db_service.list_ihld(search=q, page=page, per_page=UPLOAD_IHLD_PER_PAGE)
    except Exception:
        logger.exception("Gagal mengambil data IHLD (halaman /upload-ihld)")
        flash("Gagal memuat data IHLD dari database. Coba lagi sebentar lagi.", "error")
        result = {"items": [], "page": 1, "total_pages": 1, "total_count": 0}

    return render_template(
        "upload_ihld.html",
        items=result["items"],
        search_query=q,
        page=result["page"],
        per_page=UPLOAD_IHLD_PER_PAGE,
        total_pages=result["total_pages"],
        total_count=result["total_count"],
    )


@app.route("/upload-ihld/import", methods=["POST"])
def upload_ihld_import():
    try:
        if is_viewer():
            flash("Akun Anda hanya memiliki akses lihat saja (view only).", "error")
            return redirect(url_for("upload_ihld_page"))

        file = request.files.get("file")
        if not file or not file.filename:
            flash("Tidak ada file yang dipilih.", "error")
            return redirect(url_for("upload_ihld_page"))

        filename = secure_filename(file.filename)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in UPLOAD_IHLD_ALLOWED_EXT:
            flash("Format file tidak didukung. Gunakan .xlsx atau .csv.", "error")
            return redirect(url_for("upload_ihld_page"))

        # Guard ukuran file (maks 10MB) supaya file yang kebesaran gagal
        # cepat dengan pesan jelas, bukan menggantung lama lalu crash
        # (request timeout) -- lihat catatan di ihld_db_service.py.
        if request.content_length and request.content_length > UPLOAD_IHLD_MAX_BYTES:
            flash("Ukuran file melebihi 10MB.", "error")
            return redirect(url_for("upload_ihld_page"))

        try:
            if ext == "csv":
                rows = ihld_db_service.parse_ihld_csv(file.stream)
            else:
                wb = openpyxl.load_workbook(BytesIO(file.read()), data_only=True, read_only=True)
                rows = ihld_db_service.parse_ihld_worksheet(wb.active)
                wb.close()
        except Exception as e:
            logger.exception("Gagal membaca file upload IHLD")
            flash(f"File tidak valid / gagal dibaca: {e}", "error")
            return redirect(url_for("upload_ihld_page"))

        if not rows:
            flash("Tidak ada baris data yang bisa diimpor dari file ini.", "error")
            return redirect(url_for("upload_ihld_page"))

        try:
            result = ihld_db_service.bulk_upsert_ihld(rows)
        except Exception as e:
            logger.exception("Gagal menyimpan data IHLD ke database")
            flash(f"Gagal menyimpan data ke database: {e}", "error")
            return redirect(url_for("upload_ihld_page"))

        msg = f"Selesai: {result['written']} baris disimpan (baru/diperbarui)"
        if result["skipped_same"]:
            msg += f", {result['skipped_same']} baris dilewati (data sama persis)"
        msg += f" dari total {result['total']} baris di file."
        flash(msg, "success")
        return redirect(url_for("upload_ihld_page"))

    except Exception:
        # Jaring pengaman terakhir: apa pun yang lolos dari try/except di
        # atas (bug tak terduga) tetap berakhir sebagai pesan flash yang
        # rapi, bukan halaman 500 polos.
        logger.exception("Unexpected error saat upload IHLD")
        flash("Terjadi kesalahan tak terduga saat memproses upload. Coba lagi.", "error")
        return redirect(url_for("upload_ihld_page"))


# ── MITRA ──
@app.route("/api/master-data/mitra")
def api_md_mitra_list():
    err = _require_developer_json()
    if err: return err
    return jsonify({"ok": True, "items": master_db_service.list_mitra()})


@app.route("/api/master-data/mitra", methods=["POST"])
def api_md_mitra_add():
    err = _require_developer_json()
    if err: return err
    try:
        new_id = master_db_service.add_mitra((request.get_json(silent=True) or {}).get("nama_mitra"))
        return jsonify({"ok": True, "id": new_id})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menambah (mungkin sudah ada): {e}"}), 409


@app.route("/api/master-data/mitra/<int:mitra_id>", methods=["PUT"])
def api_md_mitra_update(mitra_id):
    err = _require_developer_json()
    if err: return err
    try:
        master_db_service.update_mitra(mitra_id, (request.get_json(silent=True) or {}).get("nama_mitra"))
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menyimpan: {e}"}), 409


@app.route("/api/master-data/mitra/<int:mitra_id>", methods=["DELETE"])
def api_md_mitra_delete(mitra_id):
    err = _require_developer_json()
    if err: return err
    master_db_service.delete_mitra(mitra_id)
    return jsonify({"ok": True})


@app.route("/api/master-data/mitra/template")
def api_md_mitra_template():
    err = _require_developer_json()
    if err: return err
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Mitra"
    ws.append(["nama_mitra"])
    ws.append(["PT. CONTOH JAYA"])
    ws.append(["PT. CONTOH LAIN"])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="template_mitra.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/master-data/mitra/bulk", methods=["POST"])
def api_md_mitra_bulk():
    """Import banyak nama mitra sekaligus dari file .xlsx yang di-upload
    (kolom pertama = nama mitra, baris pertama boleh header atau langsung data)."""
    err = _require_developer_json()
    if err: return err
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "File tidak ditemukan"}), 400
    try:
        wb = openpyxl.load_workbook(BytesIO(file.read()), data_only=True)
        ws = wb.active
    except Exception as e:
        return jsonify({"ok": False, "error": f"File .xlsx tidak valid: {e}"}), 400
    names = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if not row:
            continue
        first_col = str(row[0]).strip() if row[0] is not None else ""
        if i == 0 and first_col.lower() in ("nama_mitra", "mitra", "nama mitra"):
            continue  # baris header, skip
        if first_col:
            names.append(first_col)
    try:
        result = master_db_service.bulk_add_mitra(names)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ── STATUS KATEGORI ──
@app.route("/api/master-data/status-kategori")
def api_md_status_kategori_list():
    err = _require_developer_json()
    if err: return err
    return jsonify({"ok": True, "items": master_db_service.list_status_kategori()})


@app.route("/api/master-data/status-kategori", methods=["POST"])
def api_md_status_kategori_add():
    err = _require_developer_json()
    if err: return err
    try:
        new_id = master_db_service.add_status_kategori((request.get_json(silent=True) or {}).get("kode"))
        return jsonify({"ok": True, "id": new_id})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menambah (mungkin sudah ada): {e}"}), 409


@app.route("/api/master-data/status-kategori/<int:kategori_id>", methods=["PUT"])
def api_md_status_kategori_update(kategori_id):
    err = _require_developer_json()
    if err: return err
    try:
        master_db_service.update_status_kategori(kategori_id, (request.get_json(silent=True) or {}).get("kode"))
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menyimpan: {e}"}), 409


@app.route("/api/master-data/status-kategori/<int:kategori_id>", methods=["DELETE"])
def api_md_status_kategori_delete(kategori_id):
    err = _require_developer_json()
    if err: return err
    master_db_service.delete_status_kategori(kategori_id)
    return jsonify({"ok": True})


# ── STATUS PEKERJAAN (sub-status) ──
@app.route("/api/master-data/status-pekerjaan")
def api_md_status_pekerjaan_list():
    err = _require_developer_json()
    if err: return err
    return jsonify({"ok": True, "items": master_db_service.list_status_pekerjaan()})


@app.route("/api/master-data/status-pekerjaan", methods=["POST"])
def api_md_status_pekerjaan_add():
    err = _require_developer_json()
    if err: return err
    body = request.get_json(silent=True) or {}
    try:
        new_id = master_db_service.add_status_pekerjaan(body.get("kategori_id"), body.get("nama_status"))
        return jsonify({"ok": True, "id": new_id})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409


@app.route("/api/master-data/status-pekerjaan/<int:status_id>", methods=["PUT"])
def api_md_status_pekerjaan_update(status_id):
    err = _require_developer_json()
    if err: return err
    body = request.get_json(silent=True) or {}
    try:
        master_db_service.update_status_pekerjaan(status_id, body.get("kategori_id"), body.get("nama_status"))
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409


@app.route("/api/master-data/status-pekerjaan/<int:status_id>", methods=["DELETE"])
def api_md_status_pekerjaan_delete(status_id):
    err = _require_developer_json()
    if err: return err
    master_db_service.delete_status_pekerjaan(status_id)
    return jsonify({"ok": True})


# ── PROJECT ──
@app.route("/api/master-data/projects")
def api_md_projects_list():
    err = _require_developer_json()
    if err: return err
    return jsonify({"ok": True, "items": master_db_service.list_projects()})


@app.route("/api/master-data/projects", methods=["POST"])
def api_md_projects_add():
    err = _require_developer_json()
    if err: return err
    body = request.get_json(silent=True) or {}
    try:
        master_db_service.add_project(body.get("project_code"), body)
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menambah (mungkin sudah ada): {e}"}), 409


@app.route("/api/master-data/projects/<project_code>", methods=["PUT"])
def api_md_projects_update(project_code):
    err = _require_developer_json()
    if err: return err
    body = request.get_json(silent=True) or {}
    try:
        master_db_service.update_project(project_code, body)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 409


@app.route("/api/master-data/projects/<project_code>", methods=["DELETE"])
def api_md_projects_delete(project_code):
    err = _require_developer_json()
    if err: return err
    master_db_service.delete_project(project_code)
    return jsonify({"ok": True})


# ── WOK ──
@app.route("/api/master-data/wok")
def api_md_wok_list():
    err = _require_developer_json()
    if err: return err
    return jsonify({"ok": True, "items": master_db_service.list_wok()})


@app.route("/api/master-data/wok", methods=["POST"])
def api_md_wok_add():
    err = _require_developer_json()
    if err: return err
    try:
        new_id = master_db_service.add_wok(request.get_json(silent=True) or {})
        return jsonify({"ok": True, "id": new_id})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menambah (mungkin sudah ada): {e}"}), 409


@app.route("/api/master-data/wok/<int:wok_id>", methods=["PUT"])
def api_md_wok_update(wok_id):
    err = _require_developer_json()
    if err: return err
    try:
        master_db_service.update_wok(wok_id, request.get_json(silent=True) or {})
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menyimpan: {e}"}), 409


@app.route("/api/master-data/wok/<int:wok_id>", methods=["DELETE"])
def api_md_wok_delete(wok_id):
    err = _require_developer_json()
    if err: return err
    master_db_service.delete_wok(wok_id)
    return jsonify({"ok": True})


@app.route("/api/master-data/wok/template")
def api_md_wok_template():
    err = _require_developer_json()
    if err: return err
    headers = ["sto", "sto_conf_bu", "duplicate_flag", "nama_sto", "witel", "datel",
               "kab_telkom", "kab_tsel", "cluster", "branch_old", "branch_new",
               "regional", "area", "region_sap"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "WOK"
    ws.append(headers)
    ws.append(["BBL", "BBL", "N", "BABELAN", "BEKASI", "BEKASI", "BEKASI", "BEKASI",
                "BEKASI", "KARAWANG", "KARAWANG", "EASTERN JABOTABEK", "AREA 2", "Eastern Jabotabek"])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name="template_wok.xlsx",
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/api/master-data/wok/bulk", methods=["POST"])
def api_md_wok_bulk():
    """Import banyak baris WOK sekaligus dari file .xlsx yang di-upload.
    Kolom (harus ada baris header, urutan bebas): sto,sto_conf_bu,
    duplicate_flag,nama_sto,witel,datel,kab_telkom,kab_tsel,cluster,
    branch_old,branch_new,regional,area,region_sap"""
    err = _require_developer_json()
    if err: return err
    file = request.files.get("file")
    if not file:
        return jsonify({"ok": False, "error": "File tidak ditemukan"}), 400
    try:
        wb = openpyxl.load_workbook(BytesIO(file.read()), data_only=True)
        ws = wb.active
    except Exception as e:
        return jsonify({"ok": False, "error": f"File .xlsx tidak valid: {e}"}), 400
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = [str(h).strip().lower() if h is not None else "" for h in next(rows_iter)]
    except StopIteration:
        return jsonify({"ok": True, "added": 0, "skipped": 0})
    rows = []
    for values in rows_iter:
        if values is None or all(v is None for v in values):
            continue
        row = {header[i]: (str(values[i]).strip() if i < len(values) and values[i] is not None else "")
               for i in range(len(header))}
        rows.append(row)
    try:
        result = master_db_service.bulk_add_wok(rows)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


# ── USERS ──
@app.route("/api/master-data/users")
def api_md_users_list():
    err = _require_developer_json()
    if err: return err
    return jsonify({"ok": True, "items": master_db_service.list_users()})


@app.route("/api/master-data/users", methods=["POST"])
def api_md_users_add():
    err = _require_developer_json()
    if err: return err
    body = request.get_json(silent=True) or {}
    try:
        master_db_service.add_user(body.get("nik"), body.get("name"), body.get("password"), body.get("role"), body.get("project"))
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"Gagal menambah (NIK mungkin sudah ada): {e}"}), 409


@app.route("/api/master-data/users/<nik>", methods=["DELETE"])
def api_md_users_delete(nik):
    err = _require_developer_json()
    if err: return err
    master_db_service.delete_user(nik)
    return jsonify({"ok": True})


@app.route("/pt3")
def pt3_dashboard():
    err = require_project_feature("PT3", "dashboard_enabled", "Dashboard")
    if err: return err
    return render_template("PT3.html")


@app.route("/api/search")
def api_search():
    err = require_project_feature("PT3", "search_enabled", "Search")
    if err: return err
    q = request.args.get("q", "")
    try:
        results = sheets_service.search_rows(q)
        return jsonify({"ok": True, "results": results})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/dashboard")
def api_dashboard():
    try:
        data = sheets_service.get_dashboard_data()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/mitra-options")
def api_mitra_options():
    """Daftar Nama Mitra (sheet MASTER DATA kolom H, ~500 baris) buat
    dropdown pencarian kolom Y di update.html. Di-cache 10 menit di
    sheets_service, jadi endpoint ini ringan dipanggil berkali-kali."""
    try:
        options = sheets_service.get_mitra_options()
        return jsonify({"ok": True, "data": options})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/row/<int:row_num>")
def api_row(row_num):
    try:
        snapshot = sheets_service.get_row_snapshot(row_num)
        label = sheets_service.get_row_label(row_num)
        return jsonify({
            "ok": True,
            "snapshot": snapshot,
            "label": label,
            "z_options": config.Z_OPTIONS,
            "aa_options": config.AA_OPTIONS,
            "status_aa_groups": config.STATUS_AA_GROUPS,
            "progress_drop_statuses": config.PROGRESS_DROP_STATUSES,
            "kategori_drop_options": config.KATEGORI_DROP_OPTIONS,
            "extra_fields_by_status": config.EXTRA_FIELDS_BY_STATUS,
            "extra_field_meta": config.EXTRA_FIELD_META,
            "pre_finish_install_statuses": config.PRE_FINISH_INSTALL_STATUSES,
            "document_types_by_status": config.DOCUMENT_TYPES_BY_STATUS,
            "progress_stages": config.PROGRESS_STAGES,
            "document_keys_hidden": config.DOCUMENT_KEYS_HIDDEN_ON_PT3_PAGE,
            "document_upload_required": config.DOCUMENT_UPLOAD_REQUIRED,
            "kml_visible_statuses": config.KML_VISIBLE_STATUSES,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/row/<int:row_num>/detail")
def api_row_detail(row_num):
    """Kolom I s/d AB untuk satu baris — dipakai oleh panel 'Kategori Drop'."""
    try:
        fields = sheets_service.get_row_detail(row_num)
        return jsonify({"ok": True, "fields": fields})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/row/<int:row_num>/update", methods=["POST"])
def api_row_update(row_num):
    if is_viewer():
        return viewer_blocked_json()
    err = require_project_feature("PT3", "update_enabled", "Update")
    if err: return err
    payload = request.get_json(silent=True) or {}
    z_value = (payload.get("status_z") or "").strip()
    aa_value = (payload.get("status_aa") or "").strip()
    note_text = (payload.get("note_text") or "").strip()
    extra_fields = payload.get("extra_fields") or {}
    target_fi = (payload.get("target_fi") or "").strip()
    kategori_drop = (payload.get("kategori_drop") or "").strip()
    mitra_value = (payload.get("nama_mitra") or "").strip()

    if not z_value:
        return jsonify({"ok": False, "error": "Status (kolom Z) wajib dipilih."}), 400
    if not note_text:
        return jsonify({"ok": False, "error": "Keterangan tidak boleh kosong."}), 400

    # Validasi field tambahan (BL-BQ) — kosong selalu valid (opsional).
    for key, raw_value in extra_fields.items():
        ok, message = sheets_service.validate_extra_field(key, raw_value)
        if not ok:
            label = config.EXTRA_FIELD_META.get(key, {}).get("label", key)
            return jsonify({"ok": False, "error": f"{label}: {message}"}), 400

    # Target Finish Instalasi (kolom AK) — wajib terisi (baru atau lama)
    # untuk status sebelum Finish Instalasi.
    ok, message = sheets_service.validate_target_fi(row_num, z_value, target_fi)
    if not ok:
        return jsonify({"ok": False, "error": message}), 400

    # Kategori Drop (kolom BH) — wajib dipilih untuk status Drop.
    ok, message = sheets_service.validate_kategori_drop(z_value, kategori_drop)
    if not ok:
        return jsonify({"ok": False, "error": message}), 400

    # Dokumen wajib (BAST/Foto Instalasi/Berita Acara) — harus sudah
    # diupload (sesi ini atau sebelumnya) untuk status yang dituju.
    ok, message = sheets_service.validate_documents_for_status(row_num, z_value)
    if not ok:
        return jsonify({"ok": False, "error": message}), 400

    try:
        date_col, note_col = sheets_service.update_status(
            row_num, z_value, aa_value, note_text, extra_fields=extra_fields,
            target_fi=target_fi, kategori_drop=kategori_drop, mitra_value=mitra_value,
        )
        return jsonify({"ok": True, "date_col": date_col, "note_col": note_col})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/row/<int:row_num>/document/<doc_key>", methods=["POST"])
def api_row_document_upload(row_num, doc_key):
    """Upload/revisi 1 dokumen (BAST / Foto Instalasi / Berita Acara
    Perijinan) untuk 1 LOP. multipart/form-data: file=<file>, note=<catatan
    revisi opsional -- wajib diisi kalau ini revisi (sudah ada file lama),
    validasinya di sisi JS supaya user dikasih tahu sebelum upload jalan."""
    if is_viewer():
        return viewer_blocked_json()
    err = require_project_feature("PT3", "update_enabled", "Update")
    if err: return err
    if doc_key not in config.DOCUMENT_TYPES:
        return jsonify({"ok": False, "error": "Jenis dokumen tidak dikenal."}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "File belum dipilih."}), 400

    note = (request.form.get("note") or "").strip()
    filename = secure_filename(file.filename) or "dokumen"
    mimetype = file.mimetype or "application/octet-stream"

    try:
        url = sheets_service.upload_row_document(
            row_num, doc_key, filename, file.stream, mimetype, revision_note=note
        )
        return jsonify({"ok": True, "url": url})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/boq-content/<file_id>")
def boq_content(file_id):
    """Proxy isi mentah 1 file BOQ dari Drive ke browser -- sama pola
    dengan /kml-content/<file_id> (same-origin, tidak kena CORS, file
    tidak perlu publik). Mimetype respons ditentukan dari query param
    ?name=<filename asli> (dikirim frontend dari daftar file), supaya PDF
    dirender iframe browser sebagai PDF, sementara xlsx/xls cukup dikirim
    apa adanya -- diparse di sisi browser pakai SheetJS (client-side,
    gratis, jadi TIDAK perlu buka Google Sheets/Office Online yang berat)."""
    import drive_service
    name = (request.args.get("name") or "").lower()
    if name.endswith(".pdf"):
        mimetype = "application/pdf"
    elif name.endswith(".xlsx"):
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif name.endswith(".xls"):
        mimetype = "application/vnd.ms-excel"
    else:
        mimetype = "application/octet-stream"
    try:
        content = drive_service.get_file_content(file_id)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return Response(content, mimetype=mimetype)


@app.route("/kml-content/<file_id>")
def kml_content(file_id):
    """Proxy isi mentah 1 file KML dari Drive ke browser. Dipakai oleh
    peta Leaflet di index.html (ganti iframe preview Drive yang tidak
    bisa render KML sebagai peta). Server yang fetch dari Drive (pakai
    kredensial OAuth yang sama dengan upload), browser cuma fetch ke
    domain kita sendiri -- jadi tidak kena CORS dan file TIDAK perlu
    publik di Drive."""
    import drive_service
    try:
        content = drive_service.get_file_content(file_id)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return Response(content, mimetype="application/vnd.google-earth.kml+xml")


@app.route("/api/row/<int:row_num>/kml")
def api_row_kml_list(row_num):
    """List file KML yang sudah diupload untuk 1 LOP. SENGAJA route
    TERPISAH dari /api/row/<row_num> (bukan digabung) -- frontend manggil
    ini belakangan/lazy setelah panel utama sudah tampil, supaya buka
    panel tidak ikut nunggu request ke Drive (opsional, kadang lambat)."""
    try:
        files = sheets_service.get_row_kml_files(row_num)
        return jsonify({"ok": True, "files": files})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/row/<int:row_num>/kml", methods=["POST"])
def api_row_kml_upload(row_num):
    """Upload 1 file KML untuk 1 LOP. Opsional -- TIDAK ada validasi wajib
    di /api/row/<row_num>/update terkait ini. Boleh upload lebih dari 1
    file per LOP (tidak menimpa yang lama)."""
    if is_viewer():
        return viewer_blocked_json()
    err = require_project_feature("PT3", "update_enabled", "Update")
    if err: return err
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "File belum dipilih."}), 400
    if not file.filename.lower().endswith((".kml", ".kmz")):
        return jsonify({"ok": False, "error": "File harus berformat .kml atau .kmz."}), 400

    filename = secure_filename(file.filename) or "lokasi.kml"

    # Mimetype yang dikirim browser SERING salah untuk .kml/.kmz (banyak
    # OS/browser -- terutama Windows -- tidak kenal ekstensi ini di database
    # MIME-nya, jadi kirim "application/xml" atau "application/octet-stream").
    # Google Drive menentukan tampilan preview (peta interaktif vs. teks XML
    # mentah) dari mimeType yang TERSIMPAN di Drive, bukan dari nama file --
    # jadi mimetype salah = KML kebuka sebagai teks, bukan peta. Karena
    # ekstensi sudah divalidasi di atas, paksa mimetype yang benar di sini,
    # jangan percaya file.mimetype dari browser sama sekali.
    if filename.lower().endswith(".kmz"):
        mimetype = "application/vnd.google-earth.kmz"
    else:
        mimetype = "application/vnd.google-earth.kml+xml"

    try:
        result = sheets_service.upload_row_kml(row_num, filename, file.stream, mimetype)
        return jsonify({"ok": True, "file": result})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/row/<int:row_num>/boq")
def api_row_boq_list(row_num):
    """List file BOQ yang sudah diupload untuk 1 LOP -- sama pola dengan
    /api/row/<row_num>/kml (route terpisah, dipanggil lazy oleh frontend)."""
    try:
        files = sheets_service.get_row_boq_files(row_num)
        return jsonify({"ok": True, "files": files})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/row/<int:row_num>/boq", methods=["POST"])
def api_row_boq_upload(row_num):
    """Upload 1 file BOQ (.pdf/.xlsx/.xls) untuk 1 LOP. Opsional, tidak
    memblokir simpan status. Boleh lebih dari 1 file per LOP (tidak
    menimpa yang lama)."""
    if is_viewer():
        return viewer_blocked_json()
    err = require_project_feature("PT3", "update_enabled", "Update")
    if err: return err
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "File belum dipilih."}), 400

    filename = secure_filename(file.filename) or "boq"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in config.BOQ_ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": "File harus berformat .pdf, .xlsx, atau .xls."}), 400

    # Sama seperti KML: paksa mimetype dari ekstensi, jangan percaya
    # file.mimetype dari browser (sering salah/generik untuk xlsx/xls).
    mimetype = config.BOQ_MIMETYPES[ext]

    try:
        result = sheets_service.upload_row_boq(row_num, filename, file.stream, mimetype)
        return jsonify({"ok": True, "file": result})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/pt3-export", methods=["POST"])
def api_pt3_export():
    """Export "Data A-AP" -- tombol di sebelah "Grouping (TSEL)" pada
    dashboard PT3. Body JSON: {"rows": [<row_num>, ...]} = baris yang lagi
    lolos filter Branch/Batch/Priority di dashboard (dikirim frontend);
    kalau `rows` kosong/tidak dikirim, export SEMUA baris data."""
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or None
    try:
        buf = sheets_service.build_export_workbook_a_ap(rows)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    filename = f"PT3-Data-A-AP-{datetime.date.today().isoformat()}.xlsx"
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/pt3-update-template", methods=["POST"])
def api_pt3_update_template():
    """Download "Format Update" -- template Excel siap-isi (ID IHLD, Nama
    Mitra, Status Fisik, Sub Status Fisik, Keterangan) + sheet referensi
    Status/Sub Status. Body JSON: {"rows": [<row_num>, ...]} = baris yang
    lagi tampil di tabel "Detail Lokasi Sedang Berjalan"; kosong/tidak
    dikirim -> semua baris yang punya ID IHLD."""
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or None
    try:
        buf = sheets_service.build_update_template_workbook(rows)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    filename = f"PT3-Format-Update-{datetime.date.today().isoformat()}.xlsx"
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/pt3-import", methods=["POST"])
def api_pt3_import():
    """Upload file "Format Update" (hasil isian dari /api/pt3-update-template)
    untuk update banyak lokasi sekaligus -- tiap baris diterapkan lewat
    sheets_service.update_status() (logika sama persis dengan update
    1-per-1 lewat panel Update Status). Baris yang gagal tidak menghentikan
    baris lainnya -- lihat hasil per baris di response `results`."""
    if is_viewer():
        return viewer_blocked_json()
    err = require_project_feature("PT3", "update_enabled", "Update")
    if err: return err
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "File belum dipilih."}), 400
    if not file.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify({"ok": False, "error": "File harus berformat .xlsx (hasil download Format Update)."}), 400
    try:
        summary = sheets_service.apply_bulk_update_from_excel(file.stream)
        return jsonify({"ok": True, **summary})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/hem")
def hem_input_page():
    """Halaman "Input Data Semesta" (form manual / upload Excel-CSV /
    generate SQL) -- lihat templates/hem.html. Datanya masuk ke tabel
    Postgres data_semesta lewat /api/hem/insert di bawah."""
    err = require_project_feature("HEM", "create_enabled", "Create")
    if err: return err
    return render_template("hem.html")


@app.route("/api/hem/insert", methods=["POST"])
def api_hem_insert():
    """Dipanggil tombol "Simpan ke Database" di hem.html (baik dari tab
    Upload file maupun Input manual) -- body JSON: {"rows": [{field_key:
    value, ...}, ...]}. Response HARUS {"ok", "inserted", "errors"} --
    itu kontrak yang sudah dipakai JS di hem.html (lihat
    saveRowsDirectly())."""
    if is_viewer():
        return viewer_blocked_json()
    err = require_project_feature("HEM", "create_enabled", "Create")
    if err: return err
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or []
    try:
        inserted, errors = hem_db_service.insert_rows(rows)
        return jsonify({"ok": True, "inserted": inserted, "errors": errors})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/hem/insert-ihld", methods=["POST"])
def api_hem_insert_ihld():
    """Dipanggil tombol "Simpan ke Database" di panel "Upload IHLD"
    hem.html (lihat saveRowsDirectlyIhld()) -- body JSON: {"rows": [...]},
    key tiap dict = HEADER ASLI file LOP/IHLD (mis. "iHLD LoP ID"), bukan
    nama kolom Postgres (lihat IHLD_FIELDS di hem_db_service.py).

    Upsert ke tabel ihld berdasar id_ihld (= "iHLD LoP ID"). Setelah ini,
    baris data_semesta yang ihld_lop_id-nya cocok (lewat /api/hem/insert
    di atas) akan otomatis tertaut & tersebar ke data_area/data_material/
    data_progress/proses_dokumen/dokumen_file -- lihat insert_rows() di
    hem_db_service.py."""
    if is_viewer():
        return viewer_blocked_json()
    err = require_project_feature("HEM", "create_enabled", "Create")
    if err: return err
    payload = request.get_json(silent=True) or {}
    rows = payload.get("rows") or []
    try:
        inserted, errors = hem_db_service.insert_ihld_rows(rows)
        return jsonify({"ok": True, "inserted": inserted, "errors": errors})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/debug/hem-schema")
def debug_hem_schema():
    """Tampilkan DDL 'CREATE TABLE IF NOT EXISTS data_semesta (...)' buat
    di-copy-paste manual ke tab Query Railway KALAU tabelnya belum ada --
    TIDAK dieksekusi otomatis oleh endpoint ini. Skema produksi
    sebenarnya (data_semesta + ihld + anak2-nya, FK, trigger) ada di
    schema_ihld.sql terpisah -- lihat catatan di
    hem_db_service.build_create_table_sql()."""
    return Response(hem_db_service.build_create_table_sql(), mimetype="text/plain")


@app.route("/hem/dashboard")
def hem_dashboard_page():
    """Dashboard "Data Semesta" (data_semesta, Postgres) -- lihat
    templates/hem_dashboard.html. Data & filter diambil lewat
    /api/hem-dashboard, export lewat /api/hem-export."""
    err = require_project_feature("HEM", "dashboard_enabled", "Dashboard")
    if err: return err
    return render_template("hem_dashboard.html")


@app.route("/api/hem-dashboard")
def api_hem_dashboard():
    """Data untuk dashboard HEM: rows mentah (sudah difilter kalau ada
    query param filter), ringkasan KPI + rekap Region x Status, dan
    opsi filter (nilai unik per kolom filter). Query param multi-value,
    mis. ?new_region_ta=Jabo&new_region_ta=Jabar&status_ihld=Approved."""
    filters = {
        col: request.args.getlist(col) for col in hem_db_service.DASHBOARD_FILTER_COLS
    }
    filters = {k: v for k, v in filters.items() if v}
    try:
        rows = hem_db_service.get_dashboard_rows(filters or None)
        summary = hem_db_service.get_dashboard_summary(rows)
        options = hem_db_service.get_filter_options()
        return jsonify({
            "ok": True,
            "rows": rows,
            "summary": summary,
            "options": options,
            "field_labels": hem_db_service.HEM_FIELD_LABELS,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/hem-export", methods=["POST"])
def api_hem_export():
    """Export SEMUA kolom data_semesta (sesuai filter yang lagi aktif di
    dashboard) sebagai .xlsx. Body JSON: {"filters": {col: [nilai, ...]}}."""
    payload = request.get_json(silent=True) or {}
    filters = payload.get("filters") or {}
    filters = {k: v for k, v in filters.items() if k in hem_db_service.DASHBOARD_FILTER_COLS and v}
    try:
        buf = hem_db_service.build_export_workbook(filters or None)
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    filename = f"Data-Semesta-{datetime.date.today().isoformat()}.xlsx"
    return Response(
        buf.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/pending-updates")
def api_pending_updates():
    try:
        items = sheets_service.get_pending_updates()
        return jsonify({"ok": True, "count": len(items), "items": items})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/pt2")
def pt2_page():
    err = require_project_feature("PT2", "dashboard_enabled", "Dashboard")
    if err: return err
    return render_template("pt2.html")


@app.route("/api/pt2-dashboard")
def api_pt2_dashboard():
    try:
        data = sheets_service.get_pt2_dashboard_data()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/fbb")
def fbb_page():
    err = require_project_feature("FBB", "dashboard_enabled", "Dashboard")
    if err: return err
    return render_template("fbb.html")


@app.route("/fbb/laporan")
def fbb_laporan_page():
    """Laporan eksekutif FBB — tidak ada di menu sidebar, hanya diakses lewat
    tombol 'Buat Laporan' di halaman FBB. Semua data diambil lewat
    /api/fbb-data dan /api/fbb-summary yang sudah ada, jadi tidak perlu
    fungsi baru di sheets_service.py."""
    err = require_project_feature("FBB", "dashboard_enabled", "Dashboard")
    if err: return err
    return render_template("Laporan fbb.html")


@app.route("/api/fbb-data")
def api_fbb_data():
    try:
        data = sheets_service.get_fbb_data()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/fbb-summary")
def api_fbb_summary():
    date_param = request.args.get("date") or None
    try:
        data = sheets_service.get_fbb_summary(date_param)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/aging")
def aging_page():
    err = require_project_feature("FBB", "dashboard_enabled", "Dashboard")
    if err: return err
    return render_template("aging.html")


@app.route("/mbb-olo")
def mbb_olo_page():
    """Monitoring MBB (All Node B) & OLO -- extend base.html (sidebar &
    topbar sama seperti halaman lain). Spreadsheet-nya TERPISAH dari
    'Detail PT3' dan tetap PRIVATE -- dibaca lewat service account yang
    sama (lihat get_mbb_rows/get_olo_rows di sheets_service.py), bukan
    lagi CSV export publik dari browser. Tampilan ditentukan lewat
    ?view=... dari link menu MBB/OLO di sidebar (mis. /mbb-olo?view=mbb-newinfra)."""
    view = (request.args.get("view") or "").lower()
    project_code = "OLO" if view.startswith("olo") else "MBB"
    err = require_project_feature(project_code, "dashboard_enabled", "Dashboard")
    if err: return err
    return render_template("mbb-olo.html")


@app.route("/api/mbb-data")
def api_mbb_data():
    # Refresh manual (tombol "Update Data" di halaman) -- lewati cache
    # 5 menit dan paksa baca ulang dari Google.
    if request.args.get("force") == "1":
        sheets_service.invalidate_sheet_cache(sheets_service.get_mbb_worksheet().title)
    try:
        rows = sheets_service.get_mbb_rows()
        return jsonify({"ok": True, "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/olo-data")
def api_olo_data():
    if request.args.get("force") == "1":
        sheets_service.invalidate_sheet_cache(sheets_service.get_olo_worksheet().title)
    try:
        rows = sheets_service.get_olo_rows()
        return jsonify({"ok": True, "rows": rows})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/aging-data")
def api_aging_data():
    try:
        data = sheets_service.get_aging_data()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/aging-export")
def api_aging_export():
    # Murni baca/download data (kolom A-H, Q-U, Y-AL, AP-BC) -- bukan aksi
    # ubah data, jadi tidak perlu digate is_viewer() (sama seperti export
    # lain di PT2/PT3/FBB, viewer tetap boleh download).
    try:
        data = sheets_service.get_aging_export_rows()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@app.route("/update/<int:row_num>", methods=["GET"])
def update_form(row_num):
    try:
        snapshot = sheets_service.get_row_snapshot(row_num)
        label = sheets_service.get_row_label(row_num)
    except Exception as e:
        flash(f"Gagal mengambil data baris: {type(e).__name__}: {e}", "error")
        return redirect(url_for("index"))
    return render_template(
        "update.html",
        row=row_num,
        label=label,
        snapshot=snapshot,
        z_options=config.Z_OPTIONS,
        aa_options=config.AA_OPTIONS,
        status_aa_groups=config.STATUS_AA_GROUPS,
        pre_finish_install_statuses=config.PRE_FINISH_INSTALL_STATUSES,
        progress_drop_statuses=config.PROGRESS_DROP_STATUSES,
        kategori_drop_options=config.KATEGORI_DROP_OPTIONS,
        document_types=config.DOCUMENT_TYPES,
        document_modes=sheets_service.get_document_ui_modes(snapshot["status_z"]),
        document_upload_required=config.DOCUMENT_UPLOAD_REQUIRED,
        today_iso=datetime.date.today().isoformat(),
    )


@app.route("/update/<int:row_num>", methods=["POST"])
def do_update(row_num):
    # Role 'viewer' cuma boleh lihat data -- form-nya memang sudah tidak
    # dirender untuk role ini (lihat update.html), tapi itu murni UI.
    # Blok juga di server supaya POST langsung (curl/Postman) ke endpoint
    # ini tetap ditolak walau request-nya tidak lewat form di browser.
    if is_viewer():
        flash("Akun Anda hanya memiliki akses lihat saja (view only) -- tidak bisa melakukan update.", "error")
        return redirect(url_for("update_form", row_num=row_num))
    if not project_feature_allowed("PT3", "update_enabled"):
        flash("Fitur Update untuk project PT3 belum diaktifkan developer (lihat tab Project di /master-data).", "error")
        return redirect(url_for("update_form", row_num=row_num))

    z_value = request.form.get("status_z", "").strip()
    aa_value = request.form.get("status_aa", "").strip()
    note_text = request.form.get("note_text", "").strip()
    target_fi = request.form.get("target_fi", "").strip()
    kategori_drop = request.form.get("kategori_drop", "").strip()
    mitra_value = request.form.get("nama_mitra", "").strip()

    if not z_value:
        flash("Status (kolom Z) wajib dipilih.", "error")
        return redirect(url_for("update_form", row_num=row_num))
    if not note_text:
        flash("Keterangan tidak boleh kosong.", "error")
        return redirect(url_for("update_form", row_num=row_num))

    ok, message = sheets_service.validate_target_fi(row_num, z_value, target_fi)
    if not ok:
        flash(message, "error")
        return redirect(url_for("update_form", row_num=row_num))

    ok, message = sheets_service.validate_kategori_drop(z_value, kategori_drop)
    if not ok:
        flash(message, "error")
        return redirect(url_for("update_form", row_num=row_num))

    # Dokumen (BAST/Foto Instalasi/Berita Acara) -- form ini tidak pakai JS
    # terpisah seperti dashboard utama, jadi file yang dipilih diupload
    # langsung di sini, SEBELUM validasi wajib-dokumen, supaya upload di
    # submission yang sama ikut dihitung.
    for doc_key, meta in config.DOCUMENT_TYPES.items():
        if doc_key in config.DOCUMENT_KEYS_HIDDEN_ON_PT3_PAGE:
            continue
        file = request.files.get(f"doc_{doc_key}")
        if file and file.filename:
            note = request.form.get(f"doc_note_{doc_key}", "").strip()
            filename = secure_filename(file.filename) or "dokumen"
            try:
                sheets_service.upload_row_document(
                    row_num, doc_key, filename, file.stream, file.mimetype or "application/octet-stream",
                    revision_note=note,
                )
            except Exception as e:
                flash(f"Gagal upload {meta['label']}: {type(e).__name__}: {e}", "error")
                return redirect(url_for("update_form", row_num=row_num))

    ok, message = sheets_service.validate_documents_for_status(row_num, z_value)
    if not ok:
        flash(message, "error")
        return redirect(url_for("update_form", row_num=row_num))

    try:
        date_col, note_col = sheets_service.update_status(
            row_num, z_value, aa_value, note_text, target_fi=target_fi,
            kategori_drop=kategori_drop, mitra_value=mitra_value,
        )
        flash(f"Berhasil diupdate ke kolom {note_col} (tanggal di {date_col}).", "success")
    except Exception as e:
        flash(f"Gagal update: {type(e).__name__}: {e}", "error")
        return redirect(url_for("update_form", row_num=row_num))

    # Sukses -> balik ke dashboard PT3, bukan render ulang form update yang
    # sama (yang sebelumnya rawan gagal kalau get_row_snapshot() kena
    # error/rate-limit tepat setelah proses tulis ke Google Sheets).
    return redirect(url_for("pt3_dashboard"))


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
    report = {}

    # Step 1: env vars present?
    report["has_GOOGLE_SERVICE_ACCOUNT_JSON"] = bool(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    report["SPREADSHEET_ID"] = config.SPREADSHEET_ID
    report["SHEET_NAME"] = config.SHEET_NAME

    # Step 2: JSON parses?
    try:
        info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
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
        report["traceback"] = traceback.format_exc()
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
        report["traceback"] = traceback.format_exc()
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
        report["traceback"] = traceback.format_exc()
        return jsonify(report), 500

    report["all_ok"] = True
    return jsonify(report)


@app.route("/debug/drive-check")
def debug_drive_check():
    """Diagnostic khusus Drive (OAuth pribadi). Nunjukkin: akun mana yang
    sedang login lewat refresh_token, dan apakah DRIVE_FOLDER_ID /
    KML_FOLDER_ID yang di-set di Railway BENAR-BENAR bisa diakses akun itu
    -- tanpa perlu upload beneran & tanpa expose secret apapun. Aman
    ditinggal (read-only)."""
    import drive_service

    report = {}
    report["DRIVE_FOLDER_ID"] = config.DRIVE_FOLDER_ID or "(kosong)"
    report["KML_FOLDER_ID"] = config.KML_FOLDER_ID or "(kosong)"

    # Step 1: kredensial OAuth bisa dibangun & refresh_token masih valid?
    try:
        drive = drive_service.get_drive_client()
        report["oauth_client_built"] = True
    except Exception as e:
        report["oauth_client_built"] = False
        report["oauth_error"] = f"{type(e).__name__}: {e}"
        report["traceback"] = traceback.format_exc()
        return jsonify(report), 500

    # Step 2: akun Drive mana yang sebenarnya sedang login?
    try:
        about = drive.about().get(fields="user(emailAddress,displayName)").execute()
        report["logged_in_as"] = about.get("user", {})
    except Exception as e:
        report["about_error"] = f"{type(e).__name__}: {e}"

    # Step 3: DRIVE_FOLDER_ID -- ID-nya valid & bisa diakses akun ini?
    if config.DRIVE_FOLDER_ID:
        try:
            f = drive.files().get(
                fileId=config.DRIVE_FOLDER_ID,
                fields="id, name, mimeType, driveId, capabilities(canAddChildren)",
                supportsAllDrives=True,
            ).execute()
            report["drive_folder_id_check"] = {"ok": True, **f}
        except HttpError as e:
            report["drive_folder_id_check"] = {"ok": False, "error": drive_service._describe_http_error(e)}
    else:
        report["drive_folder_id_check"] = {"ok": False, "error": "DRIVE_FOLDER_ID kosong di env var"}

    # Step 4: sama, untuk KML_FOLDER_ID.
    if config.KML_FOLDER_ID:
        try:
            f = drive.files().get(
                fileId=config.KML_FOLDER_ID,
                fields="id, name, mimeType, driveId, capabilities(canAddChildren)",
                supportsAllDrives=True,
            ).execute()
            report["kml_folder_id_check"] = {"ok": True, **f}
        except HttpError as e:
            report["kml_folder_id_check"] = {"ok": False, "error": drive_service._describe_http_error(e)}
    else:
        report["kml_folder_id_check"] = {"ok": False, "error": "KML_FOLDER_ID kosong di env var"}

    return jsonify(report)


@app.route("/debug/mbb-olo-check")
def debug_mbb_olo_check():
    """Diagnostic khusus spreadsheet MBB/OLO (terpisah dari 'Detail PT3').
    Nunjukkin: daftar semua tab + gid-nya (buat cocokin config.MBB_SHEET_GID
    itu beneran tab 'All Node B' apa bukan), dan preview 20 baris pertama
    kolom A (MBB) / kolom F (OLO) apa adanya -- biar ketahuan header
    'TAHUN'/'SUB SISTEM' itu di baris ke berapa sebenarnya, tanpa nebak-nebak."""
    report = {}
    try:
        client = sheets_service.get_client()
    except Exception as e:
        report["client_error"] = f"{type(e).__name__}: {e}"
        return jsonify(report), 500

    try:
        sh = client.open_by_key(config.MBB_OLO_SPREADSHEET_ID)
        report["spreadsheet_opened"] = True
        report["spreadsheet_title"] = sh.title
        report["all_tabs"] = [{"title": ws.title, "gid": str(ws.id)} for ws in sh.worksheets()]
    except Exception as e:
        report["spreadsheet_opened"] = False
        report["spreadsheet_error"] = f"{type(e).__name__}: {e}"
        report["traceback"] = traceback.format_exc()
        return jsonify(report), 500

    report["configured_mbb_gid"] = str(config.MBB_SHEET_GID)
    report["configured_olo_gid"] = str(config.OLO_SHEET_GID)

    try:
        mbb_ws = sheets_service.get_mbb_worksheet()
        report["mbb_tab_title_matched"] = mbb_ws.title
        report["mbb_col_A_first_20_rows"] = mbb_ws.col_values(1)[:20]
    except Exception as e:
        report["mbb_error"] = f"{type(e).__name__}: {e}"

    try:
        olo_ws = sheets_service.get_olo_worksheet()
        report["olo_tab_title_matched"] = olo_ws.title
        report["olo_col_F_first_20_rows"] = olo_ws.col_values(6)[:20]
    except Exception as e:
        report["olo_error"] = f"{type(e).__name__}: {e}"

    return jsonify(report)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)