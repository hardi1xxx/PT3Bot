"""
rilis_order_db_service.py

Akses data untuk halaman /rilis-order: upload data rilis order (No, TIF
Area, Regional, REGION, Witel, STO, Nama Proyek, iHLD LoP ID, ODP Plan,
Port Plan, Total BOQ, Batch, CPP), riwayat upload, dan pencocokan tiap
baris terhadap data IHLD (database "Upload IHLD", tabel lop_regional)
berdasarkan iHLD LoP ID.

Dua database TERPISAH dipakai di sini:
  - RILIS_ORDER_DATABASE_URL -- database "Rilis Order" sendiri (tabel
    rilis_order, rilis_order_uploads, rilis_order_ihld_match).
  - DATABASE_URL -- database "Upload IHLD" (tabel lop_regional), HANYA
    dibaca (read-only) untuk pencocokan -- pakai ulang koneksi dari
    ihld_db_service supaya tidak dobel logic.

PENTING soal duplikat: TIDAK ada upsert/skip di sini seperti tabel
IHLD -- setiap baris yang diupload SELALU disimpan apa adanya (lihat
catatan di rilis_order_schema.sql). "Duplikat" (ihld_lop_id yang sama
muncul lebih dari sekali) dideteksi saat list_rilis_order() dipanggil,
lewat window function SQL -- baris PERTAMA untuk satu ihld_lop_id
dianggap normal, baris ke-2/3/dst untuk ihld_lop_id yang sama ditandai
is_duplicate=True (baru ditandai merah di UI, datanya tetap disimpan
semua, tidak dihapus).
"""

import csv
import math
import os

import openpyxl
import psycopg2
import psycopg2.extras

import ihld_db_service  # reuse koneksi & konstanta ke database IHLD


TABLE = "rilis_order"
UPLOADS_TABLE = "rilis_order_uploads"
MATCH_TABLE = "rilis_order_ihld_match"

IMPORT_COLUMNS = [
    "tif_area", "regional", "region", "witel", "sto", "nama_proyek",
    "ihld_lop_id", "odp_plan", "port_plan", "total_boq", "batch", "cpp",
]

NUMERIC_COLUMNS = {"odp_plan", "port_plan", "total_boq", "cpp"}

IMPORT_BATCH_SIZE = 5000
MAX_CONSECUTIVE_EMPTY = 30


def get_connection():
    database_url = os.environ.get("RILIS_ORDER_DATABASE_URL") or os.environ.get("DATABASE_URL_rilis_order")
    if not database_url:
        raise RuntimeError(
            "Environment variable RILIS_ORDER_DATABASE_URL atau DATABASE_URL_rilis_order tidak ditemukan. "
            "Cek tab Variables di service 'web' pada project Railway -- "
            "kalau nama env var untuk database Rilis Order kamu berbeda, "
            "sesuaikan baris ini."
        )
    return psycopg2.connect(database_url)


# ── Parsing file upload (sama pola dengan ihld_db_service) ─────────────

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
        raise ValueError(
            "Header kolom di file tidak ada yang cocok dengan format Rilis Order "
            "(TIF Area, Regional, REGION, Witel, STO, Nama Proyek, iHLD LoP ID, "
            "ODP Plan, Port Plan, Total BOQ, Batch, CPP)."
        )
    return col_index


def _row_to_record(col_index, raw_row):
    record = {}
    for col, idx in col_index.items():
        record[col] = _clean_value(col, raw_row[idx] if idx < len(raw_row) else None)
    return record if any(v is not None for v in record.values()) else None


def iter_records_from_worksheet(ws):
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


def iter_records_from_csv_path(path):
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


def iter_records_from_xlsx_path(path):
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        yield from iter_records_from_worksheet(wb.active)
    finally:
        wb.close()


# ── Tabel 2: riwayat / status upload ────────────────────────────────────

def create_upload(filename):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO {UPLOADS_TABLE} (filename, status) VALUES (%s, 'queued') RETURNING id",
            (filename,),
        )
        upload_id = cur.fetchone()[0]
        conn.commit()
    finally:
        conn.close()
    return upload_id


def update_upload(upload_id, **fields):
    if not fields:
        return
    set_sql = ", ".join(f"{k} = %s" for k in fields)
    values = list(fields.values()) + [upload_id]
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"UPDATE {UPLOADS_TABLE} SET {set_sql} WHERE id = %s", values)
        conn.commit()
    finally:
        conn.close()


def get_recent_uploads(limit=5):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""
            SELECT id, filename, status, total_rows, processed_rows,
                   matched_rows, error_message, uploaded_at, updated_at
            FROM {UPLOADS_TABLE}
            ORDER BY id DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()
    finally:
        conn.close()


# ── Tabel 1: insert data rilis order (SELALU insert, tidak upsert) ─────

def bulk_insert_rilis_order(rows, upload_id, page_size=1000):
    """Insert semua baris apa adanya (duplikat ihld_lop_id TETAP masuk
    semua) -- return list of id baris yang baru dibuat, dipakai untuk
    langsung dicocokkan ke IHLD lewat match_against_ihld()."""
    if not rows:
        return []

    cols = IMPORT_COLUMNS
    col_list_sql = ", ".join(cols + ["upload_id"])
    insert_sql = f"INSERT INTO {TABLE} ({col_list_sql}) VALUES %s RETURNING id"
    values = [tuple(r.get(c) for c in cols) + (upload_id,) for r in rows]

    conn = get_connection()
    try:
        cur = conn.cursor()
        result_rows = psycopg2.extras.execute_values(
            cur, insert_sql, values, page_size=page_size, fetch=True
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return [r[0] for r in result_rows]


# ── Tabel 3: pencocokan ke database IHLD ────────────────────────────────

def match_against_ihld(rilis_order_ids):
    """Untuk sekumpulan id baris rilis_order yang baru diinsert: ambil
    ihld_lop_id-nya, cari padanannya di database IHLD (lop_regional),
    lalu simpan/upsert hasilnya ke rilis_order_ihld_match. Return jumlah
    baris yang ketemu (found=True)."""
    if not rilis_order_ids:
        return 0

    # 1) Ambil ihld_lop_id untuk id-id ini dari database Rilis Order.
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, ihld_lop_id FROM {TABLE} WHERE id = ANY(%s)",
            (rilis_order_ids,),
        )
        id_to_lop = dict(cur.fetchall())
    finally:
        conn.close()

    lop_ids = sorted({v for v in id_to_lop.values() if v})

    # 2) Cari padanannya di database IHLD (read-only).
    ihld_by_lop = {}
    if lop_ids:
        ihld_conn = ihld_db_service.get_connection()
        try:
            ihld_table = ihld_db_service.get_table_name(ihld_conn)
            icur = ihld_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            icur.execute(
                f"""
                SELECT ihld_lop_id, nama_proyek, regional, witel,
                       status_order, status_proyek, tahun_program
                FROM {ihld_table}
                WHERE ihld_lop_id = ANY(%s)
                """,
                (lop_ids,),
            )
            for row in icur.fetchall():
                ihld_by_lop[row["ihld_lop_id"]] = row
        finally:
            ihld_conn.close()

    # 3) Susun baris untuk upsert ke rilis_order_ihld_match.
    match_rows = []
    for rid, lop_id in id_to_lop.items():
        ihld = ihld_by_lop.get(lop_id) if lop_id else None
        match_rows.append((
            rid,
            lop_id,
            bool(ihld),
            ihld["nama_proyek"] if ihld else None,
            ihld["regional"] if ihld else None,
            ihld["witel"] if ihld else None,
            ihld["status_order"] if ihld else None,
            ihld["status_proyek"] if ihld else None,
            ihld["tahun_program"] if ihld else None,
        ))

    conn = get_connection()
    try:
        cur = conn.cursor()
        psycopg2.extras.execute_values(
            cur,
            f"""
            INSERT INTO {MATCH_TABLE}
                (rilis_order_id, ihld_lop_id, found, ihld_nama_proyek,
                 ihld_regional, ihld_witel, ihld_status_order,
                 ihld_status_proyek, ihld_tahun_program)
            VALUES %s
            ON CONFLICT (rilis_order_id) DO UPDATE SET
                ihld_lop_id = EXCLUDED.ihld_lop_id,
                found = EXCLUDED.found,
                ihld_nama_proyek = EXCLUDED.ihld_nama_proyek,
                ihld_regional = EXCLUDED.ihld_regional,
                ihld_witel = EXCLUDED.ihld_witel,
                ihld_status_order = EXCLUDED.ihld_status_order,
                ihld_status_proyek = EXCLUDED.ihld_status_proyek,
                ihld_tahun_program = EXCLUDED.ihld_tahun_program,
                checked_at = now()
            """,
            match_rows,
            page_size=1000,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return sum(1 for r in match_rows if r[2])


# ── Job background: upload -> insert -> cocokkan ke IHLD ───────────────

def run_import_job(upload_id, file_path, ext, batch_size=IMPORT_BATCH_SIZE):
    update_upload(upload_id, status="processing")
    total = matched = 0
    try:
        record_iter = (
            iter_records_from_csv_path(file_path)
            if ext == "csv"
            else iter_records_from_xlsx_path(file_path)
        )

        batch = []
        for record in record_iter:
            batch.append(record)
            if len(batch) >= batch_size:
                new_ids = bulk_insert_rilis_order(batch, upload_id)
                matched += match_against_ihld(new_ids)
                total += len(new_ids)
                batch = []
                update_upload(upload_id, processed_rows=total, matched_rows=matched)

        if batch:
            new_ids = bulk_insert_rilis_order(batch, upload_id)
            matched += match_against_ihld(new_ids)
            total += len(new_ids)

        update_upload(
            upload_id, status="done", total_rows=total,
            processed_rows=total, matched_rows=matched,
        )
    except Exception as e:
        update_upload(upload_id, status="error", error_message=f"{type(e).__name__}: {e}")
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass


# ── List + search + pagination (dengan flag duplikat & info IHLD) ──────

def list_rilis_order(search="", page=1, per_page=10):
    """is_duplicate = True untuk baris ke-2/3/dst yang ihld_lop_id-nya
    sama dengan baris lain (baris paling lama/id terkecil tetap normal).
    ditemukan_di_ihld & info IHLD diambil dari rilis_order_ihld_match."""
    search = (search or "").strip()
    page = max(int(page or 1), 1)

    where_sql = ""
    params = []
    if search:
        where_sql = """
            WHERE r.nama_proyek ILIKE %s
               OR r.ihld_lop_id ILIKE %s
               OR r.regional ILIKE %s
               OR r.witel ILIKE %s
               OR r.sto ILIKE %s
        """
        like_q = f"%{search}%"
        params = [like_q, like_q, like_q, like_q, like_q]

    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        cur.execute(f"SELECT COUNT(*) AS total FROM {TABLE} r {where_sql}", params)
        total_count = cur.fetchone()["total"]
        total_pages = max(math.ceil(total_count / per_page), 1)
        page = min(page, total_pages)
        offset = (page - 1) * per_page

        cur.execute(
            f"""
            SELECT
                r.id, r.tif_area, r.regional, r.region, r.witel, r.sto,
                r.nama_proyek, r.ihld_lop_id, r.odp_plan, r.port_plan,
                r.total_boq, r.batch, r.cpp, r.created_at,
                (ROW_NUMBER() OVER (
                    PARTITION BY r.ihld_lop_id
                    ORDER BY r.id
                ) > 1 AND r.ihld_lop_id IS NOT NULL) AS is_duplicate,
                COALESCE(m.found, false) AS found_in_ihld,
                m.ihld_nama_proyek, m.ihld_regional, m.ihld_witel,
                m.ihld_status_order, m.ihld_status_proyek, m.ihld_tahun_program
            FROM {TABLE} r
            LEFT JOIN {MATCH_TABLE} m ON m.rilis_order_id = r.id
            {where_sql}
            ORDER BY r.id DESC
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


def get_rilis_order_detail(row_id):
    conn = get_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            f"""
            SELECT r.*, COALESCE(m.found, false) AS found_in_ihld,
                   m.ihld_nama_proyek, m.ihld_regional, m.ihld_witel,
                   m.ihld_status_order, m.ihld_status_proyek,
                   m.ihld_tahun_program, m.checked_at
            FROM {TABLE} r
            LEFT JOIN {MATCH_TABLE} m ON m.rilis_order_id = r.id
            WHERE r.id = %s
            """,
            (row_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None
