"""Koneksi & CRUD untuk data referensi "MASTER DATA" yang dikelola role
developer dari 1 halaman (/master-data): Mitra, Project, Status
Pekerjaan (kategori + sub-status/progress), WOK/Wilayah, dan Users
(login aplikasi ini).

PENTING: ini connect ke service Postgres "DB MASTER" di Railway --
service Postgres TERPISAH dari yang dipakai hem_db_service.py. Makanya
dipakai env var sendiri (MASTER_DATABASE_URL), bukan DATABASE_URL yang
dipakai HEM, supaya dua-duanya bisa jalan bersamaan tanpa bentrok.
"""
import os
import threading
import time

import psycopg2
from werkzeug.security import generate_password_hash

MASTER_DATABASE_URL = os.environ.get("MASTER_DATABASE_URL")

# Role yang berlaku di aplikasi ini. developer/admin/manager selalu bebas
# akses semua menu (lihat auth_service.can_access_menu); waspang & TIF
# dibatasi sesuai kolom project (sama seperti 'user' versi lama);
# Telkomsel = view-only (sama seperti 'viewer' versi lama, lihat
# is_viewer() di app.py).
VALID_ROLES = ["developer", "admin", "manager", "waspang", "TIF", "Telkomsel"]


def get_connection():
    if not MASTER_DATABASE_URL:
        raise RuntimeError(
            "MASTER_DATABASE_URL belum di-set di service 'web'. Ambil connection "
            "string dari service Postgres 'DB MASTER' (tab Variables, field "
            "DATABASE_URL), lalu tambahkan sebagai variable baru bernama "
            "MASTER_DATABASE_URL di service 'web'."
        )
    return psycopg2.connect(MASTER_DATABASE_URL)


# =============================================================================
# MITRA
# =============================================================================

MITRA_TABLE = "mitra"

_mitra_options_cache = {"ts": 0.0, "data": []}
_mitra_options_lock = threading.Lock()
_MITRA_OPTIONS_CACHE_TTL_SECONDS = 600


def get_mitra_options():
    now = time.time()
    with _mitra_options_lock:
        cached = _mitra_options_cache["data"]
        if cached and (now - _mitra_options_cache["ts"]) < _MITRA_OPTIONS_CACHE_TTL_SECONDS:
            return cached

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT nama_mitra FROM {MITRA_TABLE} ORDER BY nama_mitra")
            rows = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    seen = set()
    options = []
    for raw in rows:
        raw = (raw or "").strip()
        if not raw:
            continue
        key = raw.upper()
        if key in seen:
            continue
        seen.add(key)
        options.append(raw)
    options.sort(key=lambda s: s.lower())

    with _mitra_options_lock:
        _mitra_options_cache["ts"] = now
        _mitra_options_cache["data"] = options
    return options


def _invalidate_mitra_cache():
    with _mitra_options_lock:
        _mitra_options_cache["ts"] = 0.0
        _mitra_options_cache["data"] = []


def list_mitra():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, nama_mitra FROM {MITRA_TABLE} ORDER BY nama_mitra")
            return [{"id": r[0], "nama_mitra": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def add_mitra(nama_mitra):
    nama_mitra = (nama_mitra or "").strip()
    if not nama_mitra:
        raise ValueError("Nama mitra tidak boleh kosong")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {MITRA_TABLE} (nama_mitra) VALUES (%s) RETURNING id",
                    (nama_mitra,),
                )
                new_id = cur.fetchone()[0]
    finally:
        conn.close()
    _invalidate_mitra_cache()
    return new_id


def update_mitra(mitra_id, nama_mitra):
    nama_mitra = (nama_mitra or "").strip()
    if not nama_mitra:
        raise ValueError("Nama mitra tidak boleh kosong")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE {MITRA_TABLE} SET nama_mitra=%s WHERE id=%s", (nama_mitra, mitra_id))
    finally:
        conn.close()
    _invalidate_mitra_cache()


def delete_mitra(mitra_id):
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(f"DELETE FROM {MITRA_TABLE} WHERE id = %s", (mitra_id,))
    finally:
        conn.close()
    _invalidate_mitra_cache()


def bulk_add_mitra(names):
    """Import banyak nama mitra sekaligus (dari upload CSV). Nama yang
    sudah ada (duplikat) otomatis di-skip, bukan bikin gagal semuanya."""
    added, skipped = 0, 0
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for raw in names:
                    nama = (raw or "").strip()
                    if not nama:
                        continue
                    try:
                        cur.execute(f"INSERT INTO {MITRA_TABLE} (nama_mitra) VALUES (%s)", (nama,))
                        added += 1
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        skipped += 1
    finally:
        conn.close()
    _invalidate_mitra_cache()
    return {"added": added, "skipped": skipped}


# =============================================================================
# PROJECT
# =============================================================================

_PROJECT_FLAG_KEYS = [
    "dashboard_enabled", "search_enabled", "create_enabled",
    "update_enabled", "delete_enabled", "priority_enabled",
]


def list_projects():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT project_code, " + ", ".join(_PROJECT_FLAG_KEYS) +
                " FROM projects ORDER BY project_code"
            )
            cols = ["project_code"] + _PROJECT_FLAG_KEYS
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def get_project_flags(project_code):
    """Ambil flag Dashboard/Search/Create/Update/Delete/Priority untuk 1
    project_code. Return None kalau project_code belum terdaftar sama
    sekali di tabel projects -- dipakai app.py buat benar-benar mengunci
    fitur (bukan cuma checklist), lihat require_project_feature()."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT " + ", ".join(_PROJECT_FLAG_KEYS) +
                " FROM projects WHERE project_code = %s",
                (project_code,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip(_PROJECT_FLAG_KEYS, row))
    finally:
        conn.close()


def add_project(project_code, flags):
    project_code = (project_code or "").strip().upper()
    if not project_code:
        raise ValueError("Kode project tidak boleh kosong")
    values = [bool((flags or {}).get(k)) for k in _PROJECT_FLAG_KEYS]
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cols_sql = ", ".join(_PROJECT_FLAG_KEYS)
                placeholders = ", ".join(["%s"] * len(_PROJECT_FLAG_KEYS))
                cur.execute(
                    f"INSERT INTO projects (project_code, {cols_sql}) VALUES (%s, {placeholders})",
                    [project_code] + values,
                )
    finally:
        conn.close()


def get_project_flags(project_code):
    """Flag untuk 1 project_code, atau None kalau project itu belum ada
    row-nya sama sekali di tabel projects (dipakai app.py buat mutuskan
    default aman: belum diisi = jangan diblokir)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT " + ", ".join(_PROJECT_FLAG_KEYS) + " FROM projects WHERE project_code=%s",
                (project_code,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return dict(zip(_PROJECT_FLAG_KEYS, row))
    finally:
        conn.close()


def update_project(project_code, flags):
    values = [bool((flags or {}).get(k)) for k in _PROJECT_FLAG_KEYS]
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                set_sql = ", ".join(f"{k}=%s" for k in _PROJECT_FLAG_KEYS)
                cur.execute(
                    f"UPDATE projects SET {set_sql} WHERE project_code=%s",
                    values + [project_code],
                )
    finally:
        conn.close()


def delete_project(project_code):
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM projects WHERE project_code = %s", (project_code,))
    finally:
        conn.close()


# =============================================================================
# STATUS KATEGORI + STATUS PEKERJAAN (progress)
# =============================================================================

def list_status_kategori():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, kode FROM status_kategori ORDER BY kode")
            return [{"id": r[0], "kode": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def add_status_kategori(kode):
    kode = (kode or "").strip()
    if not kode:
        raise ValueError("Nama kategori tidak boleh kosong")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO status_kategori (kode) VALUES (%s) RETURNING id", (kode,))
                return cur.fetchone()[0]
    finally:
        conn.close()


def update_status_kategori(kategori_id, kode):
    kode = (kode or "").strip()
    if not kode:
        raise ValueError("Nama kategori tidak boleh kosong")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE status_kategori SET kode=%s WHERE id=%s", (kode, kategori_id))
    finally:
        conn.close()


def delete_status_kategori(kategori_id):
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM status_kategori WHERE id = %s", (kategori_id,))
    finally:
        conn.close()


def list_status_pekerjaan():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT sp.id, sp.kategori_id, sk.kode, sp.nama_status "
                "FROM status_pekerjaan sp JOIN status_kategori sk ON sp.kategori_id = sk.id "
                "ORDER BY sk.kode, sp.nama_status"
            )
            return [
                {"id": r[0], "kategori_id": r[1], "kategori_kode": r[2], "nama_status": r[3]}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def add_status_pekerjaan(kategori_id, nama_status):
    nama_status = (nama_status or "").strip()
    if not nama_status:
        raise ValueError("Nama sub-status tidak boleh kosong")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO status_pekerjaan (kategori_id, nama_status) VALUES (%s,%s) RETURNING id",
                    (kategori_id, nama_status),
                )
                return cur.fetchone()[0]
    finally:
        conn.close()


def update_status_pekerjaan(status_id, kategori_id, nama_status):
    nama_status = (nama_status or "").strip()
    if not nama_status:
        raise ValueError("Nama sub-status tidak boleh kosong")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE status_pekerjaan SET kategori_id=%s, nama_status=%s WHERE id=%s",
                    (kategori_id, nama_status, status_id),
                )
    finally:
        conn.close()


def delete_status_pekerjaan(status_id):
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM status_pekerjaan WHERE id = %s", (status_id,))
    finally:
        conn.close()


# =============================================================================
# WOK / WILAYAH
# =============================================================================

# =============================================================================
# WOK / WILAYAH -- kolom lengkap, sama dengan format sheet asli.
# =============================================================================

_WOK_COLS = [
    "sto", "sto_conf_bu", "duplicate_flag", "nama_sto", "witel", "datel",
    "kab_telkom", "kab_tsel", "cluster", "branch_old", "branch_new",
    "regional", "area", "region_sap",
]


def list_wok():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT id, {', '.join(_WOK_COLS)} FROM wok ORDER BY sto")
            cols = ["id"] + _WOK_COLS
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def add_wok(data):
    data = data or {}
    sto = (data.get("sto") or "").strip().upper()
    if not sto:
        raise ValueError("Kode STO tidak boleh kosong")
    values = [sto] + [(data.get(c) or "").strip() or None for c in _WOK_COLS[1:]]
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cols_sql = ", ".join(_WOK_COLS)
                placeholders = ", ".join(["%s"] * len(_WOK_COLS))
                cur.execute(f"INSERT INTO wok ({cols_sql}) VALUES ({placeholders}) RETURNING id", values)
                return cur.fetchone()[0]
    finally:
        conn.close()


def update_wok(wok_id, data):
    data = data or {}
    sto = (data.get("sto") or "").strip().upper()
    if not sto:
        raise ValueError("Kode STO tidak boleh kosong")
    values = [sto] + [(data.get(c) or "").strip() or None for c in _WOK_COLS[1:]]
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                set_sql = ", ".join(f"{c}=%s" for c in _WOK_COLS)
                cur.execute(f"UPDATE wok SET {set_sql} WHERE id=%s", values + [wok_id])
    finally:
        conn.close()


def delete_wok(wok_id):
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM wok WHERE id = %s", (wok_id,))
    finally:
        conn.close()


def bulk_add_wok(rows):
    """Import banyak baris WOK sekaligus (dari upload CSV). rows = list of
    dict dengan key sto/nama_sto/witel/regional/area. STO yang sudah ada
    di-skip, bukan bikin gagal semuanya."""
    added, skipped = 0, 0
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for row in rows:
                    sto = (row.get("sto") or "").strip().upper()
                    if not sto:
                        continue
                    values = [sto] + [(row.get(c) or "").strip() or None for c in _WOK_COLS[1:]]
                    try:
                        cols_sql = ", ".join(_WOK_COLS)
                        placeholders = ", ".join(["%s"] * len(_WOK_COLS))
                        cur.execute(f"INSERT INTO wok ({cols_sql}) VALUES ({placeholders})", values)
                        added += 1
                    except psycopg2.errors.UniqueViolation:
                        conn.rollback()
                        skipped += 1
    finally:
        conn.close()
    return {"added": added, "skipped": skipped}


# =============================================================================
# USERS -- login aplikasi ini.
# =============================================================================

def list_users():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nik, name, role, project FROM users ORDER BY name")
            return [{"nik": r[0], "name": r[1], "role": r[2], "project": r[3]} for r in cur.fetchall()]
    finally:
        conn.close()


def list_users_with_hash():
    """Khusus dipakai auth_service.py buat proses login."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT nik, name, password_hash, role, project FROM users")
            cols = ["nik", "name", "password_hash", "role", "project"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def add_user(nik, name, password, role, project):
    nik = (nik or "").strip()
    name = (name or "").strip()
    role = (role or "").strip()
    project = (project or "ALL").strip().upper() or "ALL"
    if not nik or not name:
        raise ValueError("NIK dan nama wajib diisi")
    if not password:
        raise ValueError("Password wajib diisi untuk user baru")
    if role not in VALID_ROLES:
        raise ValueError(f"Role tidak valid, harus salah satu dari: {', '.join(VALID_ROLES)}")
    password_hash = generate_password_hash(password, method="scrypt")
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (nik, name, password_hash, role, project) VALUES (%s,%s,%s,%s,%s)",
                    (nik, name, password_hash, role, project),
                )
    finally:
        conn.close()


def delete_user(nik):
    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE nik = %s", (nik,))
    finally:
        conn.close()
