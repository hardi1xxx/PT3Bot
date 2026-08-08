"""
Core logic for reading/writing the "Detail PT3" Google Sheet.
Used by both the Flask web app and the Telegram bot so the update
logic (routing + prepend + date-overwrite) lives in exactly one place.
"""
import json
import re
import datetime
import threading

import gspread
from dateutil import parser as date_parser
from google.oauth2.service_account import Credentials

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_client_lock = threading.Lock()
_gspread_client = None
_worksheet = None


def _col_to_index(col_letters: str) -> int:
    """'A' -> 1, 'Z' -> 26, 'AA' -> 27, etc."""
    result = 0
    for ch in col_letters:
        result = result * 26 + (ord(ch.upper()) - ord("A") + 1)
    return result


def get_client():
    global _gspread_client
    with _client_lock:
        if _gspread_client is None:
            if not config.GOOGLE_SERVICE_ACCOUNT_JSON:
                raise RuntimeError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON env var is not set. "
                    "Paste the full service-account JSON key content as this variable "
                    "in Railway's environment settings."
                )
            info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
            _gspread_client = gspread.authorize(creds)
        return _gspread_client


def get_worksheet():
    global _worksheet
    if _worksheet is None:
        client = get_client()
        sh = client.open_by_key(config.SPREADSHEET_ID)
        _worksheet = sh.worksheet(config.SHEET_NAME)
    return _worksheet


_semesta_worksheet = None


def get_semesta_worksheet():
    """Sheet 'Semesta' (gabungan PT2+PT3) — tab lain, spreadsheet yang sama."""
    global _semesta_worksheet
    if _semesta_worksheet is None:
        client = get_client()
        sh = client.open_by_key(config.SPREADSHEET_ID)
        _semesta_worksheet = sh.worksheet(config.SHEET_NAME_SEMESTA)
    return _semesta_worksheet


def find_row(ihld: str, lokasi: str):
    """
    Find the sheet row matching IHLD (col I) + LOKASI IHLD (col J).
    Returns the 1-indexed row number, or None if not found.
    Matching is case-insensitive and trims whitespace.
    """
    ws = get_worksheet()
    ihld_col_idx = _col_to_index(config.COL_IHLD)
    lokasi_col_idx = _col_to_index(config.COL_LOKASI)

    ihld_values = ws.col_values(ihld_col_idx)
    lokasi_values = ws.col_values(lokasi_col_idx)

    target_ihld = (ihld or "").strip().lower()
    target_lokasi = (lokasi or "").strip().lower()

    max_len = max(len(ihld_values), len(lokasi_values))
    for i in range(config.DATA_START_ROW - 1, max_len):  # 0-indexed list, rows start at DATA_START_ROW
        row_num = i + 1
        v_ihld = ihld_values[i].strip().lower() if i < len(ihld_values) else ""
        v_lokasi = lokasi_values[i].strip().lower() if i < len(lokasi_values) else ""
        if v_ihld == target_ihld and (not target_lokasi or v_lokasi == target_lokasi):
            return row_num
    return None


def search_rows(query: str, limit: int = 15):
    """
    Loose search across IHLD + LOKASI IHLD for autocomplete-style lookup
    (used when the user isn't sure of the exact IHLD code).
    Returns a list of dicts: {row, ihld, lokasi, status_z, status_aa}
    """
    ws = get_worksheet()
    ihld_col_idx = _col_to_index(config.COL_IHLD)
    lokasi_col_idx = _col_to_index(config.COL_LOKASI)
    z_col_idx = _col_to_index(config.COL_STATUS_Z)
    aa_col_idx = _col_to_index(config.COL_STATUS_AA)

    ihld_values = ws.col_values(ihld_col_idx)
    lokasi_values = ws.col_values(lokasi_col_idx)
    z_values = ws.col_values(z_col_idx)
    aa_values = ws.col_values(aa_col_idx)

    q = (query or "").strip().lower()
    results = []
    max_len = max(len(ihld_values), len(lokasi_values))
    for i in range(config.DATA_START_ROW - 1, max_len):
        row_num = i + 1
        v_ihld = ihld_values[i].strip() if i < len(ihld_values) else ""
        v_lokasi = lokasi_values[i].strip() if i < len(lokasi_values) else ""
        if not v_ihld and not v_lokasi:
            continue
        if q and q not in v_ihld.lower() and q not in v_lokasi.lower():
            continue
        results.append({
            "row": row_num,
            "ihld": v_ihld,
            "lokasi": v_lokasi,
            "status_z": z_values[i].strip() if i < len(z_values) else "",
            "status_aa": aa_values[i].strip() if i < len(aa_values) else "",
        })
        if len(results) >= limit:
            break
    return results


def get_row_label(row_num: int):
    """Return {ihld, lokasi, batch} for the row header shown in the update panel."""
    ws = get_worksheet()
    ihld = ws.acell(f"{config.COL_IHLD}{row_num}").value or ""
    lokasi = ws.acell(f"{config.COL_LOKASI}{row_num}").value or ""
    batch = ws.acell(f"{config.COL_BATCH}{row_num}").value or ""
    return {"ihld": ihld.strip(), "lokasi": lokasi.strip(), "batch": batch.strip()}


def _to_number(raw: str) -> float:
    """Parse a sheet cell into a number. Tolerates '' , '12', '12.5', and the
    Indonesian '1.234,56' thousands/decimal style. Anything unparseable -> 0."""
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return 0.0


# Normalized (trimmed + uppercased) lookup so that status values entered in the
# sheet with different case or stray whitespace (e.g. "01. perijinan ") still
# match a DASHBOARD_STATUSES entry instead of being silently dropped from the
# pivot tables / "lokasi sedang berjalan" count.
_STATUS_LOOKUP = {s.strip().upper(): s for s in config.DASHBOARD_STATUSES}


def _match_status(raw: str):
    """Return the canonical DASHBOARD_STATUSES value matching `raw`, or None."""
    return _STATUS_LOOKUP.get((raw or "").strip().upper())


# Reverse lookup: raw Z value (normalized) -> pivot group label ("GOLIVE"/"DROP").
# Kolom ke-6/ke-7 di tabel Rekap Port & LOP — lihat config.PIVOT_STATUS_GROUPS.
_PIVOT_GROUP_LOOKUP = {}
for _group_label, _raw_values in config.PIVOT_STATUS_GROUPS.items():
    for _raw in _raw_values:
        _PIVOT_GROUP_LOOKUP[_raw.strip().upper()] = _group_label


def _match_pivot_group(raw: str):
    """Return 'GOLIVE'/'DROP' if `raw` belongs to one of PIVOT_STATUS_GROUPS, else None."""
    return _PIVOT_GROUP_LOOKUP.get((raw or "").strip().upper())


def get_dashboard_data():
    """
    One-shot read of the whole sheet, returned as a flat list of per-row
    records (plus the status/batch/branch option lists). Kept intentionally
    "raw" (no pre-aggregation) so the dashboard can recompute every KPI,
    the chart, the pivot tables, and the Kategori Drop counts from the same
    branch-filtered set on the client — otherwise the branch filter would
    only affect the pivot table like before.
    """
    ws = get_worksheet()
    all_values = ws.get_all_values()

    idx = {
        "order": _col_to_index(config.COL_ORDER) - 1,
        "batch": _col_to_index(config.COL_BATCH) - 1,
        "status_z": _col_to_index(config.COL_STATUS_Z) - 1,
        "status_aa": _col_to_index(config.COL_STATUS_AA) - 1,
        "port": _col_to_index(config.COL_PORT) - 1,
        "bh": _col_to_index(config.COL_BH) - 1,
        "branch": _col_to_index(config.COL_BRANCH) - 1,
        "ihld": _col_to_index(config.COL_IHLD) - 1,
        "lokasi": _col_to_index(config.COL_LOKASI) - 1,
        "mitra": _col_to_index(config.COL_MITRA) - 1,
        "odp_l": _col_to_index(config.COL_ODP_L) - 1,
        "port_m": _col_to_index(config.COL_PORT_M) - 1,
        "boq_n": _col_to_index(config.COL_BOQ_N) - 1,
        "cpp_o": _col_to_index(config.COL_CPP_O) - 1,
    }

    data_rows = all_values[config.DATA_START_ROW - 1:]

    rows = []
    batch_order = []
    seen_batches = set()
    branch_set = set()

    for offset, row in enumerate(data_rows):
        row_num = config.DATA_START_ROW + offset

        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""

        order_val = cell("order")
        batch_val = cell("batch") or "(Tanpa Batch)"
        status_raw = cell("status_z")
        status_val = _match_status(status_raw)  # canonical DASHBOARD_STATUSES value or None
        pivot_group_val = status_val or _match_pivot_group(status_raw)  # + GOLIVE/DROP untuk tabel
        ihld_val = cell("ihld")

        if not order_val and not ihld_val and not cell("batch"):
            continue  # fully empty row, skip

        branch_val = cell("branch") or "(Tanpa Branch)"
        branch_set.add(branch_val)
        if batch_val not in seen_batches:
            seen_batches.add(batch_val)
            batch_order.append(batch_val)

        rows.append({
            "row": row_num,
            "has_order": bool(order_val),
            "branch": branch_val,
            "batch": batch_val,
            "status": status_val,          # canonical value, or null if not one of the 5
            "pivot_group": pivot_group_val,  # status_val, atau "GOLIVE"/"DROP", atau null
            "status_raw": status_raw,
            "status_aa": cell("status_aa"),
            "port": _to_number(cell("port")),
            "ihld": ihld_val,
            "lokasi": cell("lokasi"),
            "mitra": cell("mitra"),
            "odp_l": cell("odp_l"),
            "port_m": cell("port_m"),
            "boq_n": cell("boq_n"),
            "cpp_o": cell("cpp_o"),
            "bh": cell("bh") if cell("bh").strip().upper() not in {v.strip().upper() for v in config.BH_EXCLUDE_VALUES} else "",
        })

    return {
        "statuses": config.DASHBOARD_STATUSES,
        "pivot_columns": config.DASHBOARD_STATUSES + list(config.PIVOT_STATUS_GROUPS.keys()),
        "batches": batch_order,
        "branches": sorted(branch_set, key=lambda b: b.lower()),
        "rows": rows,
    }


def get_row_snapshot(row_num: int):
    """Return current Z, AA and the keterangan cell content for the row's current Z status.
    `last_note` is just the topmost single entry (the most recent one, since
    entries are prepended newest-first) — used by the update panel to show
    "Keterangan sebelumnya" without the whole history.
    Also returns current BL-BQ values (extra_fields) so the update panel can
    show what's already saved without forcing the user to re-enter it."""
    ws = get_worksheet()
    z_val = ws.acell(f"{config.COL_STATUS_Z}{row_num}").value or ""
    aa_val = ws.acell(f"{config.COL_STATUS_AA}{row_num}").value or ""
    note_preview = ""
    if z_val in config.STATUS_COLUMN_MAP:
        note_col = config.STATUS_COLUMN_MAP[z_val]["note_col"]
        note_preview = ws.acell(f"{note_col}{row_num}").value or ""
    last_note = note_preview.split("\n")[0].strip() if note_preview.strip() else ""

    extra_fields = {}
    for key, col in config.EXTRA_FIELD_COLUMNS.items():
        extra_fields[key] = ws.acell(f"{col}{row_num}").value or ""

    return {
        "row": row_num,
        "status_z": z_val,
        "status_aa": aa_val,
        "note_preview": note_preview,
        "last_note": last_note,
        "extra_fields": extra_fields,
    }


def get_row_detail(row_num: int):
    """
    Return header/value pairs for every column from I to AB (inclusive) of
    the given row, used by the "Kategori Drop" list -> detail view.
    Headers come from HEADER_ROW; falls back to "Kolom N" if a header cell
    is blank.
    """
    ws = get_worksheet()
    start_idx = _col_to_index("I")
    end_idx = _col_to_index("AB")
    header_range = ws.get(f"I{config.HEADER_ROW}:AB{config.HEADER_ROW}")
    value_range = ws.get(f"I{row_num}:AB{row_num}")
    headers = header_range[0] if header_range else []
    values = value_range[0] if value_range else []

    fields = []
    for i in range(end_idx - start_idx + 1):
        h = headers[i].strip() if i < len(headers) and headers[i].strip() else f"Kolom {i + 1}"
        v = values[i] if i < len(values) else ""
        fields.append({"header": h, "value": v})
    return fields


def _clean_number(raw: str) -> str:
    """Strip thousands separators (. or ,) and whitespace from a numeric
    string, e.g. 'Rp 1.500.000' -> '1500000'. Returns '' if nothing left."""
    digits = re.sub(r"[^\d]", "", raw or "")
    return digits


def validate_extra_field(key: str, raw: str):
    """
    Validate one BL-BQ field's raw input. Returns (ok: bool, message: str).
    Empty string is always valid (field is optional — means "don't touch").
    """
    raw = (raw or "").strip()
    if not raw:
        return True, ""

    if key in ("nilai_perijinan", "nilai_boq"):
        if not _clean_number(raw):
            return False, "Harus berupa angka (rupiah)."
        return True, ""

    if key in ("jumlah_odp", "jumlah_port"):
        if not raw.isdigit():
            return False, "Harus berupa angka saja."
        return True, ""

    if key == "idsw":
        if "#" not in raw:
            return False, "Format IDSW harus mengandung '#', contoh: 9671760#9671766"
        return True, ""

    if key == "odp_golive":
        if "-" not in raw or "/" not in raw:
            return False, "Format ODP Golive harus mengandung '-' dan '/', contoh: FBE/D08/068 - FBE/D08/071"
        return True, ""

    return True, ""


def update_status(row_num: int, z_value: str, aa_value: str, note_text: str,
                   extra_fields: dict = None, when: datetime.date = None):
    """
    Apply one update to a row:
      1. Write Z and AA dropdown values.
      2. Resolve the (date_col, note_col) pair from Z.
      3. Prepend "DD/MM/YY : note_text" above whatever is already in note_col.
      4. Overwrite date_col with today's date (or `when` if given).
    Returns the (date_col, note_col) pair used.
    """
    if z_value not in config.STATUS_COLUMN_MAP:
        raise ValueError(f"Unknown status Z value: {z_value!r}")

    when = when or datetime.date.today()
    date_str = when.strftime("%d/%m/%y")

    ws = get_worksheet()
    mapping = config.STATUS_COLUMN_MAP[z_value]
    date_col, note_col = mapping["date_col"], mapping["note_col"]

    # 1. Status dropdowns
    ws.update_acell(f"{config.COL_STATUS_Z}{row_num}", z_value)
    if aa_value:
        ws.update_acell(f"{config.COL_STATUS_AA}{row_num}", aa_value)

    # 2. Prepend note (newest on top)
    existing_note = ws.acell(f"{note_col}{row_num}").value or ""
    new_entry = f"{date_str} : {note_text.strip()}"
    if existing_note.strip():
        merged_note = new_entry + "\n" + existing_note
    else:
        merged_note = new_entry
    ws.update_acell(f"{note_col}{row_num}", merged_note)

    # 3. Overwrite the paired date cell — KECUALI kolom ini ada di
    #    DATE_COLS_WRITE_ONCE (mis. AZ) dan sudah pernah terisi: kolom itu
    #    mencatat tanggal MULAI masuk status ini, jadi cukup ditulis sekali.
    write_date = True
    if date_col in config.DATE_COLS_WRITE_ONCE:
        existing_date = ws.acell(f"{date_col}{row_num}").value or ""
        if existing_date.strip():
            write_date = False
    if write_date:
        ws.update_acell(f"{date_col}{row_num}", date_str)

    # 4. Field tambahan BL-BQ — OPSIONAL: cuma ditulis kalau ada isinya.
    #    Kosong = tidak diubah, nilai lama di sheet tetap dipertahankan.
    if extra_fields:
        for key, raw_value in extra_fields.items():
            raw_value = (raw_value or "").strip()
            if not raw_value:
                continue
            col = config.EXTRA_FIELD_COLUMNS.get(key)
            if not col:
                continue
            if key in ("nilai_perijinan", "nilai_boq"):
                raw_value = _clean_number(raw_value)  # simpan angka polos, tanpa "Rp"/titik ribuan
                if not raw_value:
                    continue
            ws.update_acell(f"{col}{row_num}", raw_value)

    return date_col, note_col


def _parse_date(raw: str):
    """Best-effort parse of a sheet cell into a date. Kolom AP (Tanggal NDE)
    is a pre-existing column not written by this app, so its format isn't
    guaranteed. Tries unambiguous explicit formats first (including ISO
    YYYY-MM-DD) — dateutil's dayfirst=True fallback mis-parses ISO dates
    (e.g. "2026-06-01" -> 6 Jan instead of 1 Jun), so it's only used as a
    last resort for formats like "1 Jun 2026"."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    try:
        return date_parser.parse(raw, dayfirst=True).date()
    except (ValueError, OverflowError):
        return None


def get_aging_data():
    """
    Per-row aging: (end_date - start_date) in days.
      start_date = kolom AP (Tanggal NDE)
      end_date   = HARI INI secara default, KECUALI status Z ada di
                   config.AGING_FIXED_END_COLUMNS (Drop/BAST2025 -> BF;
                   Golive/UT/Rekon/BAST -> BD), dalam hal itu end_date =
                   nilai kolom tsb — jatuh balik ke hari ini kalau kosong
                   / tidak valid.
    aging_days bernilai None kalau AP kosong/tidak bisa di-parse.
    """
    ws = get_worksheet()
    all_values = ws.get_all_values()

    # Only the fixed-end columns actually referenced in config need reading.
    fixed_end_cols = sorted(set(config.AGING_FIXED_END_COLUMNS.values()))

    idx = {
        "ap": _col_to_index(config.COL_TANGGAL_NDE) - 1,
        "order": _col_to_index(config.COL_ORDER) - 1,
        "batch": _col_to_index(config.COL_BATCH) - 1,
        "branch": _col_to_index(config.COL_BRANCH) - 1,
        "status_z": _col_to_index(config.COL_STATUS_Z) - 1,
        "status_aa": _col_to_index(config.COL_STATUS_AA) - 1,
        "ihld": _col_to_index(config.COL_IHLD) - 1,
        "lokasi": _col_to_index(config.COL_LOKASI) - 1,
        "mitra": _col_to_index(config.COL_MITRA) - 1,
    }
    for col in fixed_end_cols:
        idx[f"fixed_{col}"] = _col_to_index(col) - 1

    data_rows = all_values[config.DATA_START_ROW - 1:]
    today = datetime.date.today()
    rows = []
    branch_set = set()

    for offset, row in enumerate(data_rows):
        row_num = config.DATA_START_ROW + offset

        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""

        order_val = cell("order")
        ihld_val = cell("ihld")
        if not order_val and not ihld_val and not cell("batch"):
            continue  # fully empty row, skip

        branch_val = cell("branch") or "(Tanpa Branch)"
        branch_set.add(branch_val)

        status_raw = cell("status_z")
        start_date = _parse_date(cell("ap"))

        fixed_col = config.AGING_FIXED_END_COLUMNS.get(status_raw)
        if fixed_col:
            end_date = _parse_date(cell(f"fixed_{fixed_col}")) or today
        else:
            end_date = today

        aging_days = (end_date - start_date).days if start_date else None

        rows.append({
            "row": row_num,
            "ihld": ihld_val,
            "lokasi": cell("lokasi"),
            "batch": cell("batch") or "(Tanpa Batch)",
            "branch": branch_val,
            "mitra": cell("mitra"),
            "status_z": status_raw,
            "status_aa": cell("status_aa"),
            "aging_days": aging_days,
            "fixed_end_column": fixed_col,
        })

    return {
        "branches": sorted(branch_set, key=lambda b: b.lower()),
        "warning_days": config.AGING_WARNING_DAYS,
        "critical_days": config.AGING_CRITICAL_DAYS,
        "rows": rows,
    }


def get_pending_updates():
    """
    LOP yang statusnya (kolom Z) ada di config.NOTIFY_STATUS_DATE_MAP dan
    BELUM di-update hari ini — TERMASUK kalau belum pernah diisi sama
    sekali (ditandai "Belum pernah diisi").

    Untuk status yang date_col-nya masuk config.DATE_COLS_WRITE_ONCE
    (04. INSTALASI -> AZ, yang cuma mencatat tanggal MULAI masuk status,
    bukan update terakhir), sumber tanggalnya BUKAN kolom AZ, melainkan
    tanggal di baris PALING ATAS kolom keterangannya (BA) — karena itu
    yang benar-benar berubah tiap ada update.

    Perbandingan tanggal pakai string exact-match ke format "%d/%m/%y",
    karena kolom-kolom ini SELALU ditulis oleh update_status() di app ini
    dengan format itu persis — jadi tidak perlu parsing tanggal yang berat.
    """
    ws = get_worksheet()
    all_values = ws.get_all_values()
    today_str = datetime.date.today().strftime("%d/%m/%y")

    date_cols = sorted(set(config.NOTIFY_STATUS_DATE_MAP.values()))
    # Kolom keterangan (note_col) juga perlu dibaca untuk status yang
    # date_col-nya write-once.
    note_cols_needed = sorted({
        config.STATUS_COLUMN_MAP[status]["note_col"]
        for status, date_col in config.NOTIFY_STATUS_DATE_MAP.items()
        if date_col in config.DATE_COLS_WRITE_ONCE and status in config.STATUS_COLUMN_MAP
    })

    idx = {
        "status_z": _col_to_index(config.COL_STATUS_Z) - 1,
        "status_aa": _col_to_index(config.COL_STATUS_AA) - 1,
        "ihld": _col_to_index(config.COL_IHLD) - 1,
        "lokasi": _col_to_index(config.COL_LOKASI) - 1,
        "batch": _col_to_index(config.COL_BATCH) - 1,
        "branch": _col_to_index(config.COL_BRANCH) - 1,
        "mitra": _col_to_index(config.COL_MITRA) - 1,
    }
    for col in date_cols:
        idx[f"date_{col}"] = _col_to_index(col) - 1
    for col in note_cols_needed:
        idx[f"note_{col}"] = _col_to_index(col) - 1

    data_rows = all_values[config.DATA_START_ROW - 1:]
    pending = []

    for offset, row in enumerate(data_rows):
        row_num = config.DATA_START_ROW + offset

        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""

        status_raw = cell("status_z")
        date_col = config.NOTIFY_STATUS_DATE_MAP.get(status_raw)
        if not date_col:
            continue

        if date_col in config.DATE_COLS_WRITE_ONCE:
            note_col = config.STATUS_COLUMN_MAP[status_raw]["note_col"]
            note_text = cell(f"note_{note_col}")
            first_line = note_text.split("\n")[0].strip() if note_text else ""
            date_val = first_line.split(" : ")[0].strip() if " : " in first_line else ""
            source_col = note_col  # dilaporkan sebagai "kolom dipantau"
        else:
            date_val = cell(f"date_{date_col}")
            source_col = date_col

        if date_val == today_str:
            continue  # sudah update hari ini

        pending.append({
            "row": row_num,
            "ihld": cell("ihld"),
            "lokasi": cell("lokasi"),
            "batch": cell("batch") or "(Tanpa Batch)",
            "branch": cell("branch") or "(Tanpa Branch)",
            "mitra": cell("mitra"),
            "status_z": status_raw,
            "status_aa": cell("status_aa"),
            "date_col": source_col,
            "last_date": date_val,  # bisa string kosong = belum pernah diisi
        })

    pending.sort(key=lambda r: (r["branch"], r["batch"]))
    return pending


def get_fbb_data():
    """
    Tahap 1 halaman FBB (gabungan PT2+PT3 dari sheet 'Semesta'): baca semua
    baris mentah, tidak ada agregasi berat di sini — supaya filter
    Program/Regional/Branch bisa dihitung ulang bebas di client, sama
    seperti pola dashboard PT3. Target/ACH/GAP/Outlook belum ada di sini,
    menyusul setelah rumusnya dikonfirmasi.
    """
    ws = get_semesta_worksheet()
    all_values = ws.get_all_values()

    idx = {
        "tanggal_nde": _col_to_index(config.COL_SEMESTA_TANGGAL_NDE) - 1,
        "program": _col_to_index(config.COL_SEMESTA_PROGRAM) - 1,
        "ihld": _col_to_index(config.COL_SEMESTA_ID_IHLD) - 1,
        "lokasi": _col_to_index(config.COL_SEMESTA_NAMA_LOKASI) - 1,
        "sto": _col_to_index(config.COL_SEMESTA_STO) - 1,
        "batch": _col_to_index(config.COL_SEMESTA_BATCH) - 1,
        "branch": _col_to_index(config.COL_SEMESTA_BRANCH) - 1,
        "regional": _col_to_index(config.COL_SEMESTA_REGIONAL) - 1,
        "status_lop": _col_to_index(config.COL_SEMESTA_STATUS_LOP) - 1,
        "final_port": _col_to_index(config.COL_SEMESTA_FINAL_PORT) - 1,
        "tgl_fi": _col_to_index(config.COL_SEMESTA_TGL_FI) - 1,
        "tgl_golive": _col_to_index(config.COL_SEMESTA_TGL_GOLIVE) - 1,
        "umur": _col_to_index(config.COL_SEMESTA_UMUR) - 1,
        "odp_golive": _col_to_index(config.COL_SEMESTA_ODP_GOLIVE) - 1,
        "keterangan": _col_to_index(config.COL_SEMESTA_KETERANGAN) - 1,
    }

    data_rows = all_values[config.DATA_START_ROW_SEMESTA - 1:]
    rows = []
    programs = set()
    regionals = set()
    branches = set()

    for offset, row in enumerate(data_rows):
        row_num = config.DATA_START_ROW_SEMESTA + offset

        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""

        ihld_val = cell("ihld")
        lokasi_val = cell("lokasi")
        if not ihld_val and not lokasi_val:
            continue  # baris kosong total, skip

        program_val = cell("program") or "(Tanpa Program)"
        regional_val = cell("regional") or "(Tanpa Regional)"
        branch_val = cell("branch") or "(Tanpa Branch)"
        programs.add(program_val)
        regionals.add(regional_val)
        branches.add(branch_val)

        rows.append({
            "row": row_num,
            "tanggal_nde": cell("tanggal_nde"),
            "program": program_val,
            "ihld": ihld_val,
            "lokasi": lokasi_val,
            "sto": cell("sto"),
            "batch": cell("batch") or "(Tanpa Batch)",
            "branch": branch_val,
            "regional": regional_val,
            "status_lop": cell("status_lop") or "(Tanpa Status)",
            "final_port": _to_number(cell("final_port")),
            "tgl_fi": cell("tgl_fi"),
            "tgl_golive": cell("tgl_golive"),
            "umur": cell("umur"),
            "odp_golive": cell("odp_golive"),
            "keterangan": cell("keterangan"),
        })

    return {
        "programs": sorted(programs, key=lambda p: p.lower()),
        "regionals": sorted(regionals, key=lambda r: r.lower()),
        "branches": sorted(branches, key=lambda b: b.lower()),
        "rows": rows,
    }
