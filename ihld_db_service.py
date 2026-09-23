"""
ihld_db_service.py

Modul akses data untuk halaman /upload-ihld (list + search + pagination
+ import xlsx/csv), mengikuti pola yang sama dengan master_db_service.py
/ hem_db_service.py di project ini: app.py cukup memanggil fungsi di
modul ini dan membungkusnya dengan try/except sendiri.

PENTING -- sesuaikan get_connection() di bawah ini:
Saya belum punya isi master_db_service.py / hem_db_service.py kamu,
jadi fungsi get_connection() di sini pakai psycopg2 + env var
DATABASE_URL (konvensi umum Railway Postgres). Kalau master_db_service.py
kamu punya cara koneksi sendiri (mis. connection pool, nama env var
beda, atau pakai SQLAlchemy), ganti isi get_connection() supaya SATU
sumber koneksi yang dipakai semua modul -- idealnya import & pakai
langsung helper koneksi yang sudah ada di master_db_service.py,
misalnya:

    from master_db_service import get_connection

lalu hapus fungsi get_connection() versi di bawah ini.
"""

import csv
import io
import math

import psycopg2
import psycopg2.extras

import config

# Tabel sumber data -- ini tabel yang dibuat lewat lop_regional.sql
# sebelumnya. Ganti nama tabelnya di sini kalau nama aslinya berbeda.
TABLE_NAME = "lop_regional"

# Kolom yang diterima dari file upload (header di file akan dinormalisasi
# lalu dicocokkan ke daftar ini -- kolom lain di file akan diabaikan).
IMPORT_COLUMNS = [
    "status_order", "tipe_desain", "nama_proyek", "ihld_lop_id",
    "smart_planning_polygon_id", "eproposal_lop_id", "eproposal_lop_parent_id",
    "kode_program", "revenue_plan", "nama_cfu", "kategori", "jenis_program",
    "batch_program", "regional", "witel", "witel_lama", "datel", "sto", "wok",
    "telkomsel_area", "telkomsel_regional", "telkomsel_branch", "telkomsel_cluster",
    "durasi_desain", "total_boq", "capex_per_port", "tahun_program",
    "odp_plan", "odp_real", "alpro_mtel", "jenis_kebutuhan_olt",
    "jenis_kebutuhan_otn", "site_bts_csf", "total_port", "no_pr", "no_po",
    "nilai_po", "no_gr", "nilai_gr", "no_ir", "nilai_ir", "status_eproposal",
    "status_tomps", "status_tomps_last_activity", "status_sap", "status_proyek",
    "estimasi_go_live", "kategori_mitra", "nama_mitra", "odp_go_live",
    "star_click_id", "dibuat_oleh", "username_nik_pembuat",
]

# Kolom yang boleh diisi angka (dibersihkan dari "-" / pemisah ribuan)
NUMERIC_COLUMNS = {
    "total_boq", "capex_per_port", "tahun_program", "odp_plan", "odp_real",
    "total_port", "nilai_po", "nilai_gr", "nilai_ir",
}


def get_connection():
    # TODO: samakan dengan cara koneksi yang dipakai master_db_service.py
    # kalau berbeda dari ini (lihat catatan di docstring atas file).
    return psycopg2.connect(config.DATABASE_URL)


def _normalize_header(name):
    return str(name or "").strip().lower().replace(" ", "_").replace("/", "_").replace("-", "_")


def _clean_value(col, value):
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-"):
        return None
    if col in NUMERIC_COLUMNS:
        cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
        if cleaned in ("", "-"):
            return None
        try:
            num = float(cleaned)
        except ValueError:
            return None
        return int(num) if num.is_integer() else num
    return text


def parse_ihld_worksheet(ws):
    """Baca sheet openpyxl aktif -> list of dict, kolom sudah dinormalisasi
    & dicocokkan ke IMPORT_COLUMNS. Baris pertama dianggap header."""
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return []

    headers = [_normalize_header(h) for h in header_row]
    col_index = {h: i for i, h in enumerate(headers) if h in IMPORT_COLUMNS}
    if not col_index:
        raise ValueError("Header kolom di file tidak ada yang cocok dengan format IHLD yang diharapkan.")

    results = []
    for raw_row in rows_iter:
        if raw_row is None or all(v in (None, "") for v in raw_row):
            continue
        record = {}
        for col, idx in col_index.items():
            record[col] = _clean_value(col, raw_row[idx] if idx < len(raw_row) else None)
        if any(v is not None for v in record.values()):
            results.append(record)
    return results


def parse_ihld_csv(file_stream):
    """Baca file .csv (delimiter otomatis dideteksi antara ',' dan ';')
    -> list of dict, sama seperti parse_ihld_worksheet()."""
    raw = file_stream.read()
    text = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw

    sample = text[:2048]
    delimiter = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    try:
        header_row = next(reader)
    except StopIteration:
        return []

    headers = [_normalize_header(h) for h in header_row]
    col_index = {h: i for i, h in enumerate(headers) if h in IMPORT_COLUMNS}
    if not col_index:
        raise ValueError("Header kolom di file tidak ada yang cocok dengan format IHLD yang diharapkan.")

    results = []
    for raw_row in reader:
        if not raw_row or all((v or "").strip() == "" for v in raw_row):
            continue
        record = {}
        for col, idx in col_index.items():
            record[col] = _clean_value(col, raw_row[idx] if idx < len(raw_row) else None)
        if any(v is not None for v in record.values()):
            results.append(record)
    return results


def bulk_insert_ihld(rows):
    """rows: list of dict (key = nama kolom di IMPORT_COLUMNS). Insert
    semua baris sekaligus, kolom yang tidak diisi jadi NULL."""
    if not rows:
        return 0

    cols = sorted({c for r in rows for c in r.keys()})
    cols_sql = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    insert_sql = f"INSERT INTO {TABLE_NAME} ({cols_sql}) VALUES ({placeholders})"
    values = [tuple(r.get(c) for c in cols) for r in rows]

    conn = get_connection()
    try:
        cur = conn.cursor()
        psycopg2.extras.execute_batch(cur, insert_sql, values)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(values)


def list_ihld(search="", page=1, per_page=10):
    """Ambil data IHLD dengan pencarian (nama_proyek / ihld_lop_id /
    regional / witel) dan pagination. Return dict siap dipakai template."""
    search = (search or "").strip()
    page = max(int(page or 1), 1)

    where_sql = ""
    params = []
    if search:
        where_sql = """
            WHERE nama_proyek ILIKE %s
               OR ihld_lop_id ILIKE %s
               OR regional ILIKE %s
               OR witel ILIKE %s
        """
        like_q = f"%{search}%"
        params = [like_q, like_q, like_q, like_q]

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"SELECT COUNT(*) AS total FROM {TABLE_NAME} {where_sql}", params)
        total_count = cur.fetchone()["total"]
        total_pages = max(math.ceil(total_count / per_page), 1)
        page = min(page, total_pages)
        offset = (page - 1) * per_page

        cur.execute(
            f"""
            SELECT nama_proyek, ihld_lop_id, regional, witel,
                   status_order, status_proyek, tahun_program,
                   diperbarui_pada
            FROM {TABLE_NAME}
            {where_sql}
            ORDER BY diperbarui_pada DESC NULLS LAST
            LIMIT %s OFFSET %s
            """,
            params + [per_page, offset],
        )
        items = cur.fetchall()
    finally:
        conn.close()

    return {
        "items": items,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
