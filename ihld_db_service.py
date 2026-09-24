"""
ihld_db_service.py

Modul akses data untuk halaman /upload-ihld (list + search + pagination
+ import xlsx/csv), mengikuti pola yang sama dengan master_db_service.py
/ hem_db_service.py di project ini: app.py cukup memanggil fungsi di
modul ini dan membungkusnya dengan try/except sendiri.

Koneksi database: pakai env var DATABASE_URL, yang di Railway sudah
otomatis mengarah ke service Postgres terpisah "Upload IHLD" (lihat
project Railway kamu -- beda dari MASTER_DATABASE_URL yang dipakai
master_db_service.py). Dibaca langsung dari os.environ (bukan lewat
config.py) supaya tidak tergantung nama atribut di config.py.
"""

import csv
import io
import math
import os

import psycopg2
import psycopg2.extras


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
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "Environment variable DATABASE_URL tidak ditemukan. "
            "Cek tab Variables di service 'web' pada project Railway."
        )
    return psycopg2.connect(database_url)


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
    & dicocokkan ke IMPORT_COLUMNS. Baris pertama dianggap header.

    Berhenti otomatis setelah menemukan banyak baris kosong berturut-turut
    (MAX_CONSECUTIVE_EMPTY) -- ini jaga-jaga terhadap file Excel yang
    punya "phantom rows" (baris kosong tapi ter-format sampai ratusan
    ribu baris), yang kalau tidak dibatasi bisa membuat upload jadi
    sangat lambat / timeout."""
    MAX_CONSECUTIVE_EMPTY = 30
    MAX_ROWS = 50_000  # batas wajar jumlah baris data per upload

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
    consecutive_empty = 0
    for raw_row in rows_iter:
        if raw_row is None or all(v in (None, "") for v in raw_row):
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                break
            continue
        consecutive_empty = 0

        record = {}
        for col, idx in col_index.items():
            record[col] = _clean_value(col, raw_row[idx] if idx < len(raw_row) else None)
        if any(v is not None for v in record.values()):
            results.append(record)
            if len(results) >= MAX_ROWS:
                break
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
            if len(results) >= 50_000:
                break
    return results


def bulk_upsert_ihld(rows, page_size=1000):
    """rows: list of dict (key = nama kolom di IMPORT_COLUMNS).

    Upsert cepat berdasar ihld_lop_id (butuh unique partial index --
    lihat migration_unique_ihld_lop_id.sql):
      - ihld_lop_id BELUM ada di tabel  -> insert baris baru
      - ihld_lop_id SUDAH ada, data BEDA -> baris lama diganti (UPDATE)
      - ihld_lop_id SUDAH ada, data SAMA PERSIS -> dilewati, tidak disentuh
      - ihld_lop_id kosong/NULL -> selalu insert sebagai baris baru
        (tidak ada ID buat dibandingkan)

    Pakai execute_values (bukan execute_batch satu-satu) supaya ribuan
    baris tetap terkirim dalam beberapa statement besar saja -- jauh
    lebih cepat dan tidak gampang kena request timeout.

    Return dict: {"total", "written", "skipped_same"}.
    """
    if not rows:
        return {"total": 0, "written": 0, "skipped_same": 0}

    cols = sorted({c for r in rows for c in r.keys()})
    if "ihld_lop_id" not in cols:
        cols.append("ihld_lop_id")
        cols.sort()

    update_cols = [c for c in cols if c != "ihld_lop_id"]
    col_list_sql = ", ".join(cols)

    if update_cols:
        set_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        old_tuple_sql = ", ".join(f"{TABLE_NAME}.{c}" for c in update_cols)
        new_tuple_sql = ", ".join(f"EXCLUDED.{c}" for c in update_cols)
        conflict_action_sql = (
            f"DO UPDATE SET {set_sql} "
            f"WHERE ({old_tuple_sql}) IS DISTINCT FROM ({new_tuple_sql})"
        )
    else:
        # Cuma ada kolom ihld_lop_id, tidak ada kolom lain untuk dibandingkan.
        conflict_action_sql = "DO NOTHING"

    insert_sql = f"""
        INSERT INTO {TABLE_NAME} ({col_list_sql})
        VALUES %s
        ON CONFLICT (ihld_lop_id) WHERE ihld_lop_id IS NOT NULL
        {conflict_action_sql}
        RETURNING 1
    """
    values = [tuple(r.get(c) for c in cols) for r in rows]

    conn = get_connection()
    try:
        cur = conn.cursor()
        written_rows = psycopg2.extras.execute_values(
            cur, insert_sql, values, page_size=page_size, fetch=True
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    total = len(values)
    written = len(written_rows)
    return {"total": total, "written": written, "skipped_same": total - written}


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