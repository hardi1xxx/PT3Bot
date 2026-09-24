"""
ihld_db_service.py

Modul akses data untuk halaman /upload-ihld: list + search + pagination,
dan import xlsx/csv untuk dataset BESAR (ratusan ribu baris, akan terus
bertambah).

Desain import (PENTING -- ini yang menghindari server "tidak kuat"):
  1. app.py HANYA menyimpan file upload ke disk (streaming, cepat, tidak
     menahan banyak RAM) lalu langsung balas ke browser.
  2. Proses baca + simpan ke database jalan di THREAD BACKGROUND terpisah
     (lihat run_import_job()), dibaca & di-upsert per BATCH (default 5000
     baris) -- jadi RAM yang dipakai selalu kecil & konstan, tidak peduli
     filenya 50 ribu atau 5 juta baris.
  3. Progres tiap job dicatat di tabel ihld_import_jobs (lihat
     migration_import_jobs_table.sql) supaya halaman /upload-ihld bisa
     menampilkan status (menunggu/diproses/selesai/gagal) tanpa perlu
     browser menunggu di request yang sama.

Koneksi database: pakai env var DATABASE_URL (Postgres "Upload IHLD" di
Railway, terpisah dari MASTER_DATABASE_URL yang dipakai master_db_service.py).
"""

import csv
import math
import os

import openpyxl
import psycopg2
import psycopg2.extras


# Tabel sumber data -- dibuat lewat lop_regional.sql.
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

IMPORT_BATCH_SIZE = 5000

# Berhenti membaca setelah baris kosong berturut-turut sebanyak ini --
# jaga-jaga file Excel dengan "phantom rows" (baris kosong ter-format
# sampai jutaan baris) supaya tidak membaca selamanya.
MAX_CONSECUTIVE_EMPTY = 30


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


def _build_col_index(header_row):
    headers = [_normalize_header(h) for h in header_row]
    col_index = {h: i for i, h in enumerate(headers) if h in IMPORT_COLUMNS}
    if not col_index:
        raise ValueError("Header kolom di file tidak ada yang cocok dengan format IHLD yang diharapkan.")
    return col_index


def _row_to_record(col_index, raw_row):
    record = {}
    for col, idx in col_index.items():
        record[col] = _clean_value(col, raw_row[idx] if idx < len(raw_row) else None)
    return record if any(v is not None for v in record.values()) else None


def iter_ihld_records_from_worksheet(ws):
    """Generator -- baca sheet openpyxl (read_only) baris demi baris,
    TIDAK menumpuk semuanya di memori sekaligus."""
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return
    col_index = _build_col_index(header_row)

    consecutive_empty = 0
    for raw_row in rows_iter:
        if raw_row is None or all(v in (None, "") for v in raw_row):
            consecutive_empty += 1
            if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                break
            continue
        consecutive_empty = 0
        record = _row_to_record(col_index, raw_row)
        if record is not None:
            yield record


def iter_ihld_records_from_csv_path(path):
    """Generator -- baca file .csv langsung dari path baris demi baris
    (delimiter otomatis dideteksi antara ',' dan ';'), tanpa memuat
    seluruh isi file ke memori sekaligus."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delimiter = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.reader(f, delimiter=delimiter)

        try:
            header_row = next(reader)
        except StopIteration:
            return
        col_index = _build_col_index(header_row)

        for raw_row in reader:
            if not raw_row or all((v or "").strip() == "" for v in raw_row):
                continue
            record = _row_to_record(col_index, raw_row)
            if record is not None:
                yield record


def iter_ihld_records_from_xlsx_path(path):
    """Generator -- buka file .xlsx dari path (mode read_only, hemat
    memori) dan yield record baris demi baris."""
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        yield from iter_ihld_records_from_worksheet(wb.active)
    finally:
        wb.close()


def bulk_upsert_ihld(rows, page_size=1000):
    """rows: list of dict (key = nama kolom di IMPORT_COLUMNS).

    Upsert berdasar ihld_lop_id (butuh unique partial index -- lihat
    migration_unique_ihld_lop_id.sql):
      - ihld_lop_id BELUM ada di tabel   -> insert baris baru
      - ihld_lop_id SUDAH ada, data BEDA -> baris lama diganti (UPDATE)
      - ihld_lop_id SUDAH ada, SAMA PERSIS -> dilewati, tidak disentuh
      - ihld_lop_id kosong/NULL -> selalu insert sebagai baris baru

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


# ── Job tracking (tabel ihld_import_jobs) ──────────────────────────────

def create_import_job(filename):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO ihld_import_jobs (filename, status) VALUES (%s, 'queued') RETURNING id",
            (filename,),
        )
        job_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return job_id


def update_import_job(job_id, **fields):
    if not fields:
        return
    set_sql = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [job_id]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE ihld_import_jobs SET {set_sql} WHERE id = %s", values)
        conn.commit()
    finally:
        conn.close()


def get_recent_import_jobs(limit=5):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT id, filename, status, total_rows, processed_rows,
                   written_rows, skipped_rows, error_message,
                   created_at, updated_at
            FROM ihld_import_jobs
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()


def run_import_job(job_id, file_path, ext, batch_size=IMPORT_BATCH_SIZE):
    """Dipanggil di THREAD BACKGROUND (lihat app.py) -- baca file dari
    disk baris demi baris, upsert per batch, update progres job setelah
    tiap batch. File sumber dihapus di akhir (berhasil maupun gagal)."""
    update_import_job(job_id, status="processing")
    total = written = skipped = 0
    try:
        record_iter = (
            iter_ihld_records_from_csv_path(file_path)
            if ext == "csv"
            else iter_ihld_records_from_xlsx_path(file_path)
        )

        batch = []
        for record in record_iter:
            batch.append(record)
            if len(batch) >= batch_size:
                result = bulk_upsert_ihld(batch)
                total += result["total"]
                written += result["written"]
                skipped += result["skipped_same"]
                batch = []
                update_import_job(
                    job_id, processed_rows=total, written_rows=written, skipped_rows=skipped,
                )

        if batch:
            result = bulk_upsert_ihld(batch)
            total += result["total"]
            written += result["written"]
            skipped += result["skipped_same"]

        update_import_job(
            job_id,
            status="done",
            total_rows=total,
            processed_rows=total,
            written_rows=written,
            skipped_rows=skipped,
        )
    except Exception as e:
        update_import_job(job_id, status="error", error_message=f"{type(e).__name__}: {e}")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass


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
            SELECT id, nama_proyek, ihld_lop_id, regional, witel,
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


def get_ihld_detail(row_id):
    """Ambil SEMUA kolom untuk satu baris (dipakai panel detail saat
    baris di klik di halaman /upload-ihld). Return dict, atau None kalau
    id tidak ditemukan."""
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(f"SELECT * FROM {TABLE_NAME} WHERE id = %s", (row_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None