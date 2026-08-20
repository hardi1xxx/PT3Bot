"""
Core logic for reading/writing the "Detail PT3" Google Sheet.
Used by both the Flask web app and the Telegram bot so the update
logic (routing + prepend + date-overwrite) lives in exactly one place.
"""
import json
import re
import datetime
import calendar
import threading
import time

import gspread
from dateutil import parser as date_parser
from google.oauth2.service_account import Credentials

import config
import drive_service

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    # Full Drive access (bukan drive.readonly lagi) -- fitur upload/revisi
    # dokumen LOP butuh bikin folder, upload, dan hapus file lama.
    "https://www.googleapis.com/auth/drive",
]

_client_lock = threading.Lock()
_gspread_client = None
_worksheet = None

# Status fisik (kolom Z) yang dianggap "sudah golive ke atas" -- dipakai
# kalender Target FI/Golive di dashboard (get_dashboard_data) untuk
# menentukan tanggal mana yang dipakai (Golive kolom BD, atau Target FI
# kolom AK kalau belum sampai tahap ini). Dicocokkan lewat _normalize_status
# (uppercase + tanpa spasi), jadi variasi format tetap kena.
GOLIVE_STAGE_STATUSES = {"06.GOLIVE", "07.UT", "09.REKON", "10.BAST"}


def _col_to_index(col_letters: str) -> int:
    """'A' -> 1, 'Z' -> 26, 'AA' -> 27, etc."""
    result = 0
    for ch in col_letters:
        result = result * 26 + (ord(ch.upper()) - ord("A") + 1)
    return result


def _get_credentials():
    """Satu tempat untuk build Credentials dari GOOGLE_SERVICE_ACCOUNT_JSON --
    dipakai gspread (get_client() di bawah) MAUPUN drive_service.py, supaya
    keduanya konsisten pakai service account & scope yang sama."""
    if not config.GOOGLE_SERVICE_ACCOUNT_JSON:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON env var is not set. "
            "Paste the full service-account JSON key content as this variable "
            "in Railway's environment settings."
        )
    info = json.loads(config.GOOGLE_SERVICE_ACCOUNT_JSON)
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def get_client():
    global _gspread_client
    with _client_lock:
        if _gspread_client is None:
            _gspread_client = gspread.authorize(_get_credentials())
        return _gspread_client


def _required_min_col_index():
    """Kolom paling kanan yang dipakai konfigurasi saat ini (target_fi +
    semua link_col/log_col dokumen) -- dipakai buat mastiin grid sheet
    cukup lebar, biar nggak kena 'Range exceeds grid limits' pas nulis ke
    kolom yang belum ada di sheet (mis. BT-BW yang lebih kanan dari BS)."""
    cols = [config.COL_TARGET_FI]
    for meta in config.DOCUMENT_TYPES.values():
        cols.append(meta["link_col"])
        cols.append(meta["log_col"])
    return max(_col_to_index(c) for c in cols)


def _ensure_min_cols(ws, min_col_index: int):
    """Auto-expand grid worksheet kalau lebar sheet saat ini kurang dari
    yang dibutuhkan konfigurasi -- sekali per proses (dicek tiap
    get_worksheet() pertama kali buka worksheet)."""
    if ws.col_count < min_col_index:
        ws.add_cols(min_col_index - ws.col_count)


def get_worksheet():
    global _worksheet
    if _worksheet is None:
        client = get_client()
        sh = client.open_by_key(config.SPREADSHEET_ID)
        _worksheet = sh.worksheet(config.SHEET_NAME)
        _ensure_min_cols(_worksheet, _required_min_col_index())
    return _worksheet


_semesta_worksheet = None


# ── Cache singkat untuk get_all_values() ────────────────────────────────
# Beberapa endpoint (mis. /api/fbb-data dan /api/fbb-summary) baca sheet
# yang SAMA (Semesta) hampir bersamaan tiap kali halaman FBB dibuka --
# tanpa cache, itu jadi 2x baca penuh 18rb+ baris dari Google Sheets API,
# itulah penyebab jeda ~2 detik sebelum tabel muncul. TTL pendek (bukan
# selamanya) supaya data tetap terasa segar, tapi permintaan yang datang
# berdekatan cukup 1x round-trip ke Google.
#
# PENTING: kalau 2 request itu datang BENAR-BENAR bersamaan (frontend
# fbb.html memang manggil loadFbb() dan loadFbbSummary() balik-balikan
# tanpa nunggu satu selesai), dua-duanya bisa sama-sama cek cache di
# saat masih kosong -> dua-duanya sama-sama nembak Google Sheets API,
# cache-nya nggak kepakai sama sekali. Makanya perlu lock PER SHEET:
# request kedua yang datang selagi request pertama masih fetch harus
# NUNGGU hasil yang pertama, bukan ikut fetch sendiri.
_values_cache = {}
_values_cache_lock = threading.Lock()
_values_fetch_locks = {}
_VALUES_CACHE_TTL_SECONDS = 20


def _get_sheet_fetch_lock(key):
    with _values_cache_lock:
        lock = _values_fetch_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _values_fetch_locks[key] = lock
        return lock


def _cached_get_all_values(ws):
    key = ws.title

    def _fresh_entry():
        entry = _values_cache.get(key)
        if entry and (time.time() - entry[0]) < _VALUES_CACHE_TTL_SECONDS:
            return entry[1]
        return None

    with _values_cache_lock:
        hit = _fresh_entry()
    if hit is not None:
        return hit

    # Cache kosong/basi -- ambil lock khusus sheet ini. Kalau ada request lain
    # yang lebih dulu masuk sini duluan, kita nunggu dia selesai lalu pakai
    # hasilnya (bukan ikut fetch sendiri ke Google).
    fetch_lock = _get_sheet_fetch_lock(key)
    with fetch_lock:
        with _values_cache_lock:
            hit = _fresh_entry()
        if hit is not None:
            return hit
        values = ws.get_all_values()
        with _values_cache_lock:
            _values_cache[key] = (time.time(), values)
        return values


def invalidate_sheet_cache(sheet_title=None):
    """Panggil ini dari route 'Update Data' kalau butuh paksa baca ulang dari
    Google (skip cache), bukan nunggu TTL habis. Tanpa argumen -> hapus semua."""
    with _values_cache_lock:
        if sheet_title:
            _values_cache.pop(sheet_title, None)
        else:
            _values_cache.clear()


def get_semesta_worksheet():
    """Sheet 'Semesta' (gabungan PT2+PT3) — tab lain, spreadsheet yang sama."""
    global _semesta_worksheet
    if _semesta_worksheet is None:
        client = get_client()
        sh = client.open_by_key(config.SPREADSHEET_ID)
        _semesta_worksheet = sh.worksheet(config.SHEET_NAME_SEMESTA)
    return _semesta_worksheet


_pt2_worksheet = None


def get_pt2_worksheet():
    """Sheet 'Detail PT2' — tab lain, spreadsheet yang sama."""
    global _pt2_worksheet
    if _pt2_worksheet is None:
        client = get_client()
        sh = client.open_by_key(config.SPREADSHEET_ID)
        _pt2_worksheet = sh.worksheet(config.SHEET_NAME_PT2)
    return _pt2_worksheet


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
    """Parse a sheet cell into a number. Handles mixed formatting robustly:
    - '1.234,56' (Indonesian: dot=ribuan, koma=desimal)
    - '1,234.56' (US: koma=ribuan, dot=desimal)
    - '1.234' atau '1,234' (cuma satu jenis separator, tanpa desimal) --
      dideteksi otomatis dari pola pengelompokan 3 digit: kalau cocok pola
      ribuan, semua separator itu dibuang (jadi 1234, bukan 1.234 atau 1.0).
    - '12.5' / '12,5' (desimal biasa, bukan pola ribuan) -- tetap desimal.
    Anything unparseable -> 0. Ini penting karena sel di sheet bisa beda-beda
    format tergantung siapa yang isi manual."""
    raw = (raw or "").strip().replace(" ", "")
    if not raw:
        return 0.0

    def _looks_like_thousands(int_part: str, groups: list) -> bool:
        return (
            bool(groups)
            and all(len(g) == 3 and g.isdigit() for g in groups)
            and int_part.lstrip("-").isdigit()
        )

    has_comma = "," in raw
    has_dot = "." in raw

    if has_comma and has_dot:
        # Dua-duanya ada: yang muncul TERAKHIR adalah pemisah desimal.
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")  # gaya ID
        else:
            raw = raw.replace(",", "")  # gaya US
    elif has_comma:
        parts = raw.split(",")
        if _looks_like_thousands(parts[0], parts[1:]):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(",", ".")
    elif has_dot:
        parts = raw.split(".")
        if _looks_like_thousands(parts[0], parts[1:]):
            raw = raw.replace(".", "")

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
    all_values = _cached_get_all_values(ws)

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
        "target_fi": _col_to_index(config.COL_TARGET_FI) - 1,
        "regional": _col_to_index(getattr(config, "COL_REGIONAL", "T")) - 1,
        "golive_date": _col_to_index(getattr(config, "COL_GOLIVE_DATE", "BD")) - 1,
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

        target_fi_date = _parse_date(cell("target_fi"))
        golive_date_date = _parse_date(cell("golive_date"))

        # Kalender: kalau status fisik (kolom Z) sudah salah satu dari
        # tahap golive ke atas, pakai tanggal Golive (kolom BD); kalau
        # belum, tetap pakai Target FI (kolom AK).
        is_golive_stage = _normalize_status(status_raw) in GOLIVE_STAGE_STATUSES
        cal_date = golive_date_date if is_golive_stage else target_fi_date
        cal_date_source = "golive" if (is_golive_stage and golive_date_date) else ("target_fi" if cal_date else None)

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
            "target_fi_iso": target_fi_date.isoformat() if target_fi_date else None,
            "target_fi_display": target_fi_date.strftime("%d/%b/%y") if target_fi_date else None,
            "golive_date_iso": golive_date_date.isoformat() if golive_date_date else None,
            "golive_date_display": golive_date_date.strftime("%d/%m/%Y") if golive_date_date else None,
            "cal_date_iso": cal_date.isoformat() if cal_date else None,
            "cal_date_display": cal_date.strftime("%d/%m/%Y") if cal_date else None,
            "cal_date_source": cal_date_source,  # "golive" atau "target_fi"
            "regional": cell("regional") or "(TANPA REGIONAL)",
        })

    return {
        "statuses": config.DASHBOARD_STATUSES,
        "pivot_columns": config.PIVOT_COLUMNS,
        "batches": batch_order,
        "branches": sorted(branch_set, key=lambda b: b.lower()),
        "rows": rows,
    }


def _find_progress_stage_index(status_z: str):
    """Index of `status_z` in config.PROGRESS_STAGES (skip the NDE entry,
    which has no fixed Z value), or None if status_z is Drop/kosong/tidak
    dikenal (di luar sequence progress)."""
    for i, stage in enumerate(config.PROGRESS_STAGES):
        if stage["z_values"] and status_z in stage["z_values"]:
            return i
    return None


def compute_progress(status_z: str, has_order: bool = True):
    """
    Return (percent, stage_label, stage_index) for one LOP.
    - percent: 0-100
    - stage_label: label tahap yang sedang berjalan ("NDE", "Survey", ...,
      "Golive"), atau "Drop" kalau status_z ada di PROGRESS_DROP_STATUSES,
      atau None kalau belum ada order sama sekali.
    - stage_index: index di config.PROGRESS_STAGES yang dipakai buat cari
      deadline tahap ini (lihat compute_stage_deadlines), atau None.

    Aturan (lihat komentar PROGRESS_STAGES di config.py): bobot suatu tahap
    dihitung begitu status SUDAH PINDAH ke tahap berikutnya -- kecuali
    Golive (tahap terakhir), yang bobotnya langsung masuk begitu Golive
    dipilih.
    """
    stages = config.PROGRESS_STAGES
    if not has_order:
        return 0, None, None

    if status_z in config.PROGRESS_DROP_STATUSES:
        return None, "Drop", None

    idx = _find_progress_stage_index(status_z)
    nde_weight = stages[0]["weight"]

    if idx is None:
        # Belum ada status Z terisi (baris baru) -> baru NDE yang tercapai.
        return nde_weight, stages[0]["label"], 0

    completed = sum(s["weight"] for s in stages[1:idx])  # tahap 1..idx-1, tuntas
    if idx == len(stages) - 1:  # Golive: bobotnya sendiri ikut dihitung
        completed += stages[idx]["weight"]

    return nde_weight + completed, stages[idx]["label"], idx


def compute_stage_deadlines(wo_terbit_date: datetime.date, target_fi_date: datetime.date = None):
    """Deadline kumulatif tiap tahap dari WO terbit, mengikuti target_days
    di config.PROGRESS_STAGES berurutan.

    Kalau `target_fi_date` diisi (kolom AK -- komit manual tanggal target
    selesai Instalasi):
      - Semua tahap SEBELUM & TERMASUK 'instalasi' memakai target_fi_date
        langsung sebagai deadline-nya (bukan estimasi statis WO_terbit+
        akumulasi lagi) -- begitu ada komit manual, deadline tahap yang
        sedang berjalan mengikuti komit itu supaya tidak overdue palsu
        akibat estimasi lama yang sudah usang.
      - Tahap SESUDAH 'instalasi' (finish_install, golive, dst) tetap
        dihitung kumulatif lanjut dari target_fi_date.
    Kalau belum ada komitmen manual, semua tahap pakai estimasi statis
    WO_terbit+akumulasi seperti biasa. Return list sepanjang
    PROGRESS_STAGES, masing-masing {"key", "label", "deadline": date}."""
    deadlines = []
    cursor = wo_terbit_date
    reached_instalasi = False
    for stage in config.PROGRESS_STAGES:
        if target_fi_date and not reached_instalasi:
            # Sebelum & saat tahap instalasi: ikut komit manual kolom AK.
            deadline = target_fi_date
            if stage["key"] == "instalasi":
                cursor = target_fi_date
                reached_instalasi = True
        else:
            cursor = cursor + datetime.timedelta(days=stage["target_days"])
            deadline = cursor
        deadlines.append({"key": stage["key"], "label": stage["label"], "deadline": deadline})
    return deadlines


def get_target_fi(row_num: int):
    """Nilai kolom AK (Target Finish Instalasi) saat ini. Return
    (parsed_date_or_None, raw_string)."""
    ws = get_worksheet()
    raw = ws.acell(f"{config.COL_TARGET_FI}{row_num}").value or ""
    return _parse_date(raw), raw


def validate_target_fi(row_num: int, z_value: str, raw: str):
    """
    Kolom AK (Target Finish Instalasi):
      - Kalau ada nilai BARU (`raw` terisi): formatnya harus tanggal valid,
        dan TIDAK BOLEH tanggal yang sudah lewat (harus hari ini atau
        setelahnya) -- berlaku kapan pun nilainya diisi/diupdate, apapun
        status Z-nya saat itu.
      - WAJIB ada isinya (baru ATAU sudah ada dari update sebelumnya)
        selama status Z masih di config.PRE_FINISH_INSTALL_STATUSES --
        kalau kolom AK di sheet sudah terisi sebelumnya, boleh dibiarkan
        (tidak perlu diisi ulang tiap update).
    Returns (ok: bool, message: str).
    """
    raw = (raw or "").strip()
    if raw:
        parsed = _parse_date(raw)
        if not parsed:
            return False, "Format tanggal tidak valid."
        if parsed < datetime.date.today():
            return False, "Tanggal Target Finish Instalasi tidak boleh tanggal yang sudah lewat."

    if z_value not in config.PRE_FINISH_INSTALL_STATUSES:
        return True, ""

    if raw:
        return True, ""

    existing_date, _ = get_target_fi(row_num)
    if existing_date:
        return True, ""
    return False, "Tanggal Target Finish Instalasi (kolom AK) wajib diisi untuk status sebelum Finish Instalasi."


def get_document_ui_modes(status_z: str):
    """Mode tampilan tiap dokumen (selain yang di
    config.DOCUMENT_KEYS_HIDDEN_ON_PT3_PAGE) untuk SATU status Z tertentu:
      'hidden' -> status ini masih SEBELUM required_status dokumen itu
      'upload' -> status ini PERSIS required_status-nya (saatnya upload)
      'view'   -> status ini SUDAH LEWAT required_status (cuma lihat file)
    Dipakai update.html (form biasa, server-rendered) -- versi JS
    (index.html) menghitung ini sendiri di client pakai progress_stages
    yang dikirim /api/row, tapi logikanya sama persis (lihat
    _find_progress_stage_index di atas)."""
    current_idx = _find_progress_stage_index(status_z)
    modes = {}
    for key, meta in config.DOCUMENT_TYPES.items():
        if key in config.DOCUMENT_KEYS_HIDDEN_ON_PT3_PAGE:
            continue
        required_idx = _find_progress_stage_index(meta["required_status"])
        if current_idx is None or required_idx is None:
            modes[key] = "hidden"
        elif current_idx < required_idx:
            modes[key] = "hidden"
        elif current_idx == required_idx:
            modes[key] = "upload"
        else:
            modes[key] = "view"
    return modes


def get_document_snapshot(row_num: int):
    """Link + catatan revisi terkini untuk tiap jenis dokumen (BAST, Foto
    Instalasi, Berita Acara Perijinan) satu LOP."""
    ws = get_worksheet()
    result = {}
    for key, meta in config.DOCUMENT_TYPES.items():
        url = (ws.acell(f"{meta['link_col']}{row_num}").value or "").strip()
        log = ws.acell(f"{meta['log_col']}{row_num}").value or ""
        result[key] = {
            "label": meta["label"],
            "required_status": meta["required_status"],
            "url": url or None,
            "log": log,
        }
    return result


def validate_documents_for_status(row_num: int, z_value: str):
    """Cek dokumen wajib untuk status_z yang mau disimpan. Wajib SUDAH ADA
    link-nya di sheet (baru diupload sesi ini, atau sudah pernah ada dari
    update sebelumnya -- tidak perlu upload ulang tiap kali update).
    Returns (ok: bool, message: str)."""
    if not config.DOCUMENT_UPLOAD_REQUIRED:
        return True, ""
    required_keys = config.DOCUMENT_TYPES_BY_STATUS.get(z_value, [])
    if not required_keys:
        return True, ""
    ws = get_worksheet()
    missing_labels = []
    for key in required_keys:
        meta = config.DOCUMENT_TYPES[key]
        url = (ws.acell(f"{meta['link_col']}{row_num}").value or "").strip()
        if not url:
            missing_labels.append(meta["label"])
    if missing_labels:
        return False, f"Dokumen wajib belum diupload untuk status ini: {', '.join(missing_labels)}."
    return True, ""


def upload_row_document(row_num: int, doc_key: str, filename: str, file_stream, mimetype: str,
                         revision_note: str = ""):
    """
    Upload/revisi satu dokumen untuk satu LOP:
      1. Upload file BARU ke Drive dulu (folder khusus LOP ini, dibuat kalau
         belum ada) -- baru setelah itu hapus file LAMA (kalau ada). Urutan
         ini sengaja: kalau upload baru gagal, file lama tetap aman/utuh.
      2. Tulis link baru ke link_col di sheet (menimpa link lama).
      3. Prepend catatan revisi ke log_col ("DD/MM/YYYY : <catatan>",
         terbaru di atas) -- mencatat apa yang diubah/dihapus/ditambah,
         SEBELUM file lama benar-benar dihapus dari Drive.
    Returns URL file yang baru.
    """
    meta = config.DOCUMENT_TYPES.get(doc_key)
    if not meta:
        raise ValueError(f"Jenis dokumen tidak dikenal: {doc_key}")

    ws = get_worksheet()
    old_url = (ws.acell(f"{meta['link_col']}{row_num}").value or "").strip()

    label = get_row_label(row_num)
    lop_label = f"{label.get('ihld') or ''} {label.get('lokasi') or ''}".strip() or f"row{row_num}"

    _new_file_id, new_url = drive_service.upload_document(
        row_num, lop_label, meta["label"], filename, file_stream, mimetype
    )

    ws.update_acell(f"{meta['link_col']}{row_num}", new_url)

    note = (revision_note or "").strip()
    if not note:
        note = "Upload pertama" if not old_url else "Revisi dokumen (tanpa catatan detail)"
    today_str = datetime.date.today().strftime("%d/%m/%Y")
    entry = f"{today_str} : {note}"
    old_log = (ws.acell(f"{meta['log_col']}{row_num}").value or "").strip()
    new_log = entry if not old_log else f"{entry}\n{old_log}"
    ws.update_acell(f"{meta['log_col']}{row_num}", new_log)

    if old_url:
        try:
            drive_service.delete_document(old_url)
        except Exception:
            pass  # jangan gagalkan seluruh request cuma karena hapus file lama gagal

    return new_url


def get_row_snapshot(row_num: int):
    """Return current Z, AA and keterangan info for the update panel.
    `last_note` = topmost entry dari kolom keterangan STATUS SAAT INI (mis. BA
    untuk Instalasi) — konteks untuk "Keterangan sebelumnya".
    `note_preview` ("Riwayat keterangan lengkap") SELALU dari kolom AB
    (KETERANGAN gabungan semua status), bukan cuma status yang sedang aktif.
    Also returns current BL-BQ values (extra_fields) so the update panel can
    show what's already saved without forcing the user to re-enter it."""
    ws = get_worksheet()
    z_val = ws.acell(f"{config.COL_STATUS_Z}{row_num}").value or ""
    aa_val = ws.acell(f"{config.COL_STATUS_AA}{row_num}").value or ""

    last_note = ""
    if z_val in config.STATUS_COLUMN_MAP:
        note_col = config.STATUS_COLUMN_MAP[z_val]["note_col"]
        current_status_note = ws.acell(f"{note_col}{row_num}").value or ""
        last_note = current_status_note.split("\n")[0].strip() if current_status_note.strip() else ""

    note_preview = ws.acell(f"{config.COL_KETERANGAN_AB}{row_num}").value or ""

    extra_fields = {}
    for key, col in config.EXTRA_FIELD_COLUMNS.items():
        extra_fields[key] = ws.acell(f"{col}{row_num}").value or ""

    # ── Aging per-LOP (kolom D = WO terbit -> hari ini) + progress % ────
    wo_terbit_raw = ws.acell(f"{config.COL_WO_TERBIT}{row_num}").value or ""
    wo_terbit_date = _parse_date(wo_terbit_raw)
    today = datetime.date.today()
    aging_days = (today - wo_terbit_date).days if wo_terbit_date else None

    target_fi_raw = ws.acell(f"{config.COL_TARGET_FI}{row_num}").value or ""
    target_fi_date = _parse_date(target_fi_raw)

    progress_percent, progress_stage_label, stage_idx = compute_progress(z_val, has_order=True)

    current_stage_deadline = None
    final_deadline = None
    is_overdue = False
    if wo_terbit_date:
        stage_deadlines = compute_stage_deadlines(wo_terbit_date, target_fi_date)
        final_deadline = stage_deadlines[-1]["deadline"].isoformat()
        target_idx = stage_idx if stage_idx is not None else 0
        if 0 <= target_idx < len(stage_deadlines):
            deadline_date = stage_deadlines[target_idx]["deadline"]
            current_stage_deadline = deadline_date.isoformat()
            is_overdue = today > deadline_date

    return {
        "row": row_num,
        "status_z": z_val,
        "status_aa": aa_val,
        "note_preview": note_preview,
        "last_note": last_note,
        "extra_fields": extra_fields,
        "wo_terbit": wo_terbit_date.strftime("%d/%m/%Y") if wo_terbit_date else (wo_terbit_raw or None),
        "aging_days": aging_days,
        "progress_percent": progress_percent,
        "progress_stage_label": progress_stage_label,
        "current_stage_deadline": current_stage_deadline,
        "final_deadline": final_deadline,
        "is_overdue": is_overdue,
        "target_fi": target_fi_date.strftime("%d/%b/%y") if target_fi_date else None,
        "target_fi_iso": target_fi_date.isoformat() if target_fi_date else None,
        "target_fi_required": z_val in config.PRE_FINISH_INSTALL_STATUSES,
        "documents": get_document_snapshot(row_num),
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
                   extra_fields: dict = None, target_fi: str = None, when: datetime.date = None):
    """
    Apply one update to a row:
      1. Write Z and AA dropdown values.
      2. Resolve the (date_col, note_col) pair from Z.
      3. Prepend "DD/MM/YY : note_text" above whatever is already in note_col.
      4. Overwrite date_col with today's date (or `when` if given).
      5. Kalau `target_fi` diisi, tulis ke kolom AK (Target Finish Instalasi)
         -- kosong/None berarti "jangan diubah", nilai lama tetap dipertahankan
         (validasi wajib-isi untuk status pra-Finish-Instalasi dilakukan
         terpisah lewat validate_target_fi(), BUKAN di sini).
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

    # 5. Target Finish Instalasi (kolom AK) — sama seperti field tambahan:
    #    kosong = tidak diubah (nilai lama, kalau ada, tetap dipakai).
    #    Ditulis dalam format DD/Mon/YY (mis. 05/Aug/26) -- bulan berupa
    #    huruf supaya tidak pernah tertukar dengan DD/MM/YY atau MM/DD/YY,
    #    termasuk kalau sheet dibuka di Excel dengan locale tanggal beda.
    target_fi = (target_fi or "").strip()
    if target_fi:
        parsed_target_fi = _parse_date(target_fi)
        if parsed_target_fi:
            ws.update_acell(f"{config.COL_TARGET_FI}{row_num}", parsed_target_fi.strftime("%d/%b/%y"))

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
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d/%b/%Y", "%d/%b/%y", "%d-%b-%Y", "%d-%b-%y"):
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
    all_values = _cached_get_all_values(ws)

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
    all_values = _cached_get_all_values(ws)
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


def _norm_label(raw: str):
    """Normalisasi Regional/Program: trim + uppercase, supaya 'banten'/'Banten'/
    'BANTEN' dianggap satu grup yang sama, bukan 3 baris terpisah."""
    return (raw or "").strip().upper()


def get_fbb_data():
    """
    Tahap 1 halaman FBB (gabungan PT2+PT3 dari sheet 'Semesta'): baca semua
    baris mentah, tidak ada agregasi berat di sini — supaya filter
    Program/Regional/Branch bisa dihitung ulang bebas di client, sama
    seperti pola dashboard PT3. Target/ACH/GAP/Outlook belum ada di sini,
    menyusul setelah rumusnya dikonfirmasi.
    """
    ws = get_semesta_worksheet()
    all_values = _cached_get_all_values(ws)

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
        "potensi": _col_to_index(config.COL_SEMESTA_POTENSI) - 1,
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

        program_val = _norm_label(cell("program"))
        if not program_val:
            continue  # Program kosong -> tidak bisa diklasifikasi, tetap di-skip
        regional_val = _norm_label(cell("regional")) or "(TANPA REGIONAL)"

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
            "potensi": cell("potensi"),
        })

    return {
        "programs": sorted(programs, key=lambda p: p.lower()),
        "regionals": sorted(regionals, key=lambda r: r.lower()),
        "branches": sorted(branches, key=lambda b: b.lower()),
        "rows": rows,
    }


# ── Sheet TARGET ─────────────────────────────────────────────────────
_target_worksheet = None


def get_target_worksheet():
    global _target_worksheet
    if _target_worksheet is None:
        client = get_client()
        sh = client.open_by_key(config.SPREADSHEET_ID)
        _target_worksheet = sh.worksheet(config.SHEET_NAME_TARGET)
    return _target_worksheet


def _parse_month_to_num(raw):
    """'Agustus' / 'Agu' / '8' / '08' -> 8. None kalau tidak dikenali."""
    raw = (raw or "").strip().lower()
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        return n if 1 <= n <= 12 else None
    for i, name in enumerate(config.MONTH_NAMES_ID, start=1):
        if raw == name or (len(raw) >= 3 and raw[:3] == name[:3]):
            return i
    return None


def get_target_data():
    """List of {regional, program("PT2"/"PT3"), month(1-12), target_port}
    dari sheet TARGET. 1 baris sheet = 1 Regional + 1 Program + 1 Bulan,
    kolomnya REGIONAL, PROGRAM, BULAN, TARGET PORT (lihat config.py)."""
    ws = get_target_worksheet()
    all_values = _cached_get_all_values(ws)
    idx = {
        "regional": _col_to_index(config.COL_TARGET_REGIONAL) - 1,
        "program": _col_to_index(config.COL_TARGET_PROGRAM) - 1,
        "bulan": _col_to_index(config.COL_TARGET_BULAN) - 1,
        "target_port": _col_to_index(config.COL_TARGET_PORT) - 1,
    }
    data_rows = all_values[config.DATA_START_ROW_TARGET - 1:]
    targets = []
    for row in data_rows:
        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""
        regional = cell("regional")
        program = _norm_label(cell("program"))
        month_num = _parse_month_to_num(cell("bulan"))
        if not regional or not month_num:
            continue
        targets.append({
            "regional": regional,
            "program": program,
            "month": month_num,
            "target_port": _to_number(cell("target_port")),
        })
    return targets


# ── FBB summary (Tahap 2): DOD/MTD/Potensi/ACH/YTD/Outlook/GAP ────────
def _normalize_status(raw: str) -> str:
    """Uppercase + buang semua spasi, supaya '5. Golive', '5.Golive', '05. GOLIVE'
    dst dianggap sama -- perbandingan exact-match sebelumnya diam-diam
    menjatuhkan baris yang formatnya sedikit beda (spasi/kapital)."""
    return re.sub(r"\s+", "", (raw or "").strip().upper())


def _is_golive(status_lop: str) -> bool:
    """Golive = status di kolom I persis '5.GOLIVE' (dicocokkan tanpa spasi,
    case-insensitive). Tanggal acuannya kolom L (TGL GOLIVE) -- dicek terpisah
    oleh pemanggil (r['golive_date'])."""
    return _normalize_status(status_lop) == _normalize_status(config.STATUS_LOP_GOLIVE)


def _is_drop_mom(status_lop: str) -> bool:
    return "DROPMOM" in _normalize_status(status_lop)


def _potensi_matches_month(potensi_raw: str, month_num: int) -> bool:
    """'P1 AUG' cocok bulan 8 (Agustus)? (tidak dipakai lagi untuk hitung Potensi
    di ringkasan FBB -- Potensi sekarang dihitung dari status LOP -- tapi
    dibiarkan ada kalau dibutuhkan lagi nanti.)"""
    if not potensi_raw:
        return False
    abbr = config.MONTH_ABBR_EN[month_num - 1]
    return abbr in potensi_raw.strip().upper()


def _is_potensi_status(status_lop: str) -> bool:
    """Potensi = lokasi yang statusnya '3.OGP DEPLOY' (siap/berpotensi golive)."""
    return "OGPDEPLOY" in _normalize_status(status_lop)


def _month_end_date(year: int, month: int) -> datetime.date:
    last_day = calendar.monthrange(year, month)[1]
    return datetime.date(year, month, last_day)


def _load_semesta_rows_for_summary():
    """Baca sheet Semesta dengan tanggal Golive (kolom L) sudah di-parse ke date object."""
    ws = get_semesta_worksheet()
    all_values = _cached_get_all_values(ws)
    idx = {
        "program": _col_to_index(config.COL_SEMESTA_PROGRAM) - 1,
        "ihld": _col_to_index(config.COL_SEMESTA_ID_IHLD) - 1,
        "regional": _col_to_index(config.COL_SEMESTA_REGIONAL) - 1,
        "status_lop": _col_to_index(config.COL_SEMESTA_STATUS_LOP) - 1,
        "final_port": _col_to_index(config.COL_SEMESTA_FINAL_PORT) - 1,
        "tgl_golive": _col_to_index(config.COL_SEMESTA_TGL_GOLIVE) - 1,
        "potensi": _col_to_index(config.COL_SEMESTA_POTENSI) - 1,
    }
    data_rows = all_values[config.DATA_START_ROW_SEMESTA - 1:]
    rows = []
    for row in data_rows:
        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""
        ihld_val = cell("ihld")
        raw_regional = cell("regional")
        program_val = _norm_label(cell("program"))
        if not ihld_val and not raw_regional:
            continue  # baris kosong total, skip
        if not program_val:
            continue  # Program kosong -> tidak bisa diklasifikasi, tetap di-skip
        regional_val = _norm_label(raw_regional) or "(TANPA REGIONAL)"
        rows.append({
            "program": program_val,
            "regional": regional_val,
            "status_lop": cell("status_lop"),
            "port": _to_number(cell("final_port")),
            "golive_date": _parse_date(cell("tgl_golive")),
            "potensi": cell("potensi"),
        })
    return rows


def _compute_actuals(subset_rows, reference_date, current_month, jan1):
    golive_rows = [r for r in subset_rows if _is_golive(r["status_lop"]) and r["golive_date"]]
    month_start = reference_date.replace(day=1)

    dod = sum(r["port"] for r in golive_rows if r["golive_date"] == reference_date)
    mtd = sum(r["port"] for r in golive_rows if month_start <= r["golive_date"] <= reference_date)
    potensi_month = sum(r["port"] for r in subset_rows if _is_potensi_status(r["status_lop"]))
    ytd = sum(r["port"] for r in golive_rows if jan1 <= r["golive_date"] <= reference_date)

    # YTD "-1 bulan berjalan": akumulatif Jan s/d akhir bulan sebelum bulan
    # berjalan. Kalau bulan berjalan Agustus -> jumlahkan s/d akhir Juli.
    prev_month = current_month - 1
    if prev_month >= 1:
        prev_month_end = _month_end_date(reference_date.year, prev_month)
        real_ytd_prev_month = sum(r["port"] for r in golive_rows if jan1 <= r["golive_date"] <= prev_month_end)
    else:
        real_ytd_prev_month = 0

    total_order_port = sum(r["port"] for r in subset_rows)

    return {
        "dod": dod, "mtd": mtd, "potensi": potensi_month, "ytd": ytd,
        "real_ytd_prev_month": real_ytd_prev_month,
        "total_order_port": total_order_port,
    }


def _combine_with_target(actuals, target_month, target_ytd, target_q3):
    ach_mtd = (actuals["mtd"] / target_month * 100) if target_month else 0
    outlook_ytd = actuals["ytd"] + actuals["potensi"]
    ach_ytd = (actuals["ytd"] / target_ytd * 100) if target_ytd else 0
    gap_mtd = target_month - actuals["mtd"]
    gap_q3 = target_q3 - actuals["ytd"]
    ach_total_order = (actuals["ytd"] / target_ytd * 100) if target_ytd else 0

    return {
        **actuals,
        "target_month": target_month,
        "target_ytd": target_ytd,
        "target_q3": target_q3,
        "ach_mtd": ach_mtd,
        "outlook_ytd": outlook_ytd,
        "ach_ytd": ach_ytd,
        "gap_mtd": gap_mtd,
        "gap_q3": gap_q3,
        "ach_total_order": ach_total_order,
    }


def get_fbb_summary(reference_date_str: str = None):
    """
    Tahap 2 halaman FBB: DOD/MTD/Potensi/ACH/YTD/Outlook/GAP per Regional,
    dipecah lagi per Program (PT2/PT3), plus baris total PT2/PT3 gabungan-
    semua-regional dan TOTAL keseluruhan.
    """
    if reference_date_str:
        reference_date = _parse_date(reference_date_str)
        if not reference_date:
            raise ValueError(f"Tanggal tidak valid: {reference_date_str!r}")
    else:
        reference_date = datetime.date.today()

    year = reference_date.year
    current_month = reference_date.month
    jan1 = datetime.date(year, 1, 1)

    rows = _load_semesta_rows_for_summary()
    targets = get_target_data()

    # Normalisasi case-insensitive: kalau "Banten" di satu sheet dan "BANTEN"
    # di sheet lain, tetap harus ketemu -- bukan diam-diam jadi target 0.
    # REGIONAL_UPPER -> PROGRAM_UPPER ("PT2"/"PT3"/"") -> month -> target_port
    target_lookup = {}
    for t in targets:
        reg_key = t["regional"].strip().upper()
        prog_key = (t["program"] or "").strip().upper()
        target_lookup.setdefault(reg_key, {}).setdefault(prog_key, {})[t["month"]] = t["target_port"]

    def target_value(regional, month, program=None):
        prog_map = target_lookup.get((regional or "").strip().upper(), {})
        if program:
            return prog_map.get((program or "").strip().upper(), {}).get(month, 0)
        # Baris gabungan (regional tanpa filter program): jumlahkan semua
        # program yang ditarget untuk regional+bulan ini.
        return sum(months.get(month, 0) for months in prog_map.values())

    def target_value_all_regions(month, program=None):
        return sum(target_value(reg, month, program) for reg in target_lookup.keys())

    def cumulative_target(value_fn, upto_month):
        return sum(value_fn(m) for m in range(1, upto_month + 1))

    regionals = sorted(set(r["regional"] for r in rows), key=lambda s: s.lower())
    programs_present = sorted(set(r["program"] for r in rows), key=lambda s: s.lower())

    # Kolom Target tetap Mei-September (5-9), berapapun bulan berjalannya.
    FIXED_TARGET_MONTHS = [5, 6, 7, 8, 9]
    Q3_END_MONTH = 9

    def build_entry(subset_rows, regional_key, program_key):
        actuals = _compute_actuals(subset_rows, reference_date, current_month, jan1)
        if regional_key is None:
            value_fn = lambda m: target_value_all_regions(m, program_key)
        else:
            value_fn = lambda m: target_value(regional_key, m, program_key)
        t_month = value_fn(current_month)
        t_ytd = cumulative_target(value_fn, current_month)
        t_q3 = cumulative_target(value_fn, Q3_END_MONTH)
        entry = _combine_with_target(actuals, t_month, t_ytd, t_q3)
        entry["target_by_month"] = {m: value_fn(m) for m in FIXED_TARGET_MONTHS}
        return entry

    regional_results = []
    for reg in regionals:
        reg_rows = [r for r in rows if r["regional"] == reg]
        combined = build_entry(reg_rows, reg, None)
        by_program = {}
        for prog in programs_present:
            prog_rows = [r for r in reg_rows if r["program"] == prog]
            by_program[prog] = build_entry(prog_rows, reg, prog)
        regional_results.append({"regional": reg, "combined": combined, "programs": by_program})

    totals_by_program = {}
    for prog in programs_present:
        prog_rows = [r for r in rows if r["program"] == prog]
        totals_by_program[prog] = build_entry(prog_rows, None, prog)

    grand_total = build_entry(rows, None, None)

    prev_month = current_month - 1
    prev_month_label = config.MONTH_LABEL_ID[prev_month - 1] if prev_month >= 1 else None

    return {
        "reference_date": reference_date.isoformat(),
        "current_month": current_month,
        "current_month_label": config.MONTH_LABEL_ID[current_month - 1],
        "prev_month_label": prev_month_label,
        "target_month_labels": {m: config.MONTH_LABEL_ID[m - 1] for m in FIXED_TARGET_MONTHS},
        "programs": programs_present,
        "regionals": regional_results,
        "totals_by_program": totals_by_program,
        "grand_total": grand_total,
        "ach_thresholds": {
            "green": config.ACH_THRESHOLD_GREEN,
            "yellow": config.ACH_THRESHOLD_YELLOW,
            "orange": config.ACH_THRESHOLD_ORANGE,
        },
    }


_PT2_STATUS_LOOKUP = {s.strip().upper(): s for s in config.PT2_STATUSES}


def _match_pt2_status(raw: str):
    return _PT2_STATUS_LOOKUP.get((raw or "").strip().upper())


def get_pt2_dashboard_data():
    """
    Data untuk Dashboard PT2 (read-only — tidak ada update/edit di sini).
    Bentuk return-nya mengikuti persis apa yang dibaca JS di pt2.html:
    statuses, status_colors, golive_status, exclude_from_denom, regionals,
    branches, current_month_label, rows (per-baris, sudah termasuk flag
    is_golive_today / is_golive_month dari kolom TGL CLOSE WO).
    """
    ws = get_pt2_worksheet()
    all_values = _cached_get_all_values(ws)

    idx = {
        "ihld": _col_to_index(config.COL_PT2_ID_IHLD) - 1,
        "lokasi": _col_to_index(config.COL_PT2_LOKASI) - 1,
        "source_order": _col_to_index(config.COL_PT2_SOURCE_ORDER) - 1,
        "batch": _col_to_index(config.COL_PT2_BATCH) - 1,
        "branch": _col_to_index(config.COL_PT2_BRANCH) - 1,
        "regional": _col_to_index(config.COL_PT2_REGIONAL) - 1,
        "status_lop": _col_to_index(config.COL_PT2_STATUS_LOP) - 1,
        "klasifikasi": _col_to_index(config.COL_PT2_KLASIFIKASI_CANCEL) - 1,
        "detail_cancel": _col_to_index(config.COL_PT2_DETAIL_CANCEL) - 1,
        "odp_golive": _col_to_index(config.COL_PT2_ODP_GOLIVE) - 1,
        "final_port": _col_to_index(config.COL_PT2_FINAL_PORT) - 1,
        "tgl_close_wo": _col_to_index(config.COL_PT2_TGL_CLOSE_WO) - 1,
        "progress_h1": _col_to_index(config.COL_PT2_PROGRESS_H1) - 1,
        "progress_h1": _col_to_index(config.COL_PT2_PROGRESS_H1) - 1,
    }

    data_rows = all_values[config.DATA_START_ROW_PT2 - 1:]
    today = datetime.date.today()

    rows = []
    regionals = set()
    branches = set()

    for row in data_rows:
        def cell(key):
            i = idx[key]
            return row[i].strip() if i < len(row) else ""

        ihld_val = cell("ihld")
        lokasi_val = cell("lokasi")
        if not ihld_val and not lokasi_val:
            continue  # baris kosong total, skip

        regional_val = _norm_label(cell("regional")) or "(TANPA REGIONAL)"
        branch_val = _norm_label(cell("branch")) or "(TANPA BRANCH)"
        regionals.add(regional_val)
        branches.add(branch_val)

        status_raw = cell("status_lop")
        status_val = _match_pt2_status(status_raw)

        tgl_close_wo_raw = cell("tgl_close_wo")
        tgl_close_wo_date = _parse_date(tgl_close_wo_raw)
        is_golive_today = bool(tgl_close_wo_date and tgl_close_wo_date == today)
        is_golive_month = bool(
            tgl_close_wo_date
            and tgl_close_wo_date.year == today.year
            and tgl_close_wo_date.month == today.month
        )

        rows.append({
            "ihld": ihld_val,
            "lokasi": lokasi_val,
            "source_order": cell("source_order"),
            "batch": cell("batch") or "(Tanpa Batch)",
            "regional": regional_val,
            "branch": branch_val,
            "status": status_val,        # canonical (salah satu PT2_STATUSES) atau null
            "status_raw": status_raw,
            "port": _to_number(cell("final_port")),
            "klasifikasi": cell("klasifikasi"),
            "detail_cancel": cell("detail_cancel"),
            "odp_golive": cell("odp_golive"),
            "tgl_close_wo_formatted": tgl_close_wo_date.strftime("%d/%m/%Y") if tgl_close_wo_date else tgl_close_wo_raw,
            "status_h1": _match_pt2_status(cell("progress_h1")),
            "tgl_close_wo_iso": tgl_close_wo_date.isoformat() if tgl_close_wo_date else None,
            "is_golive_today": is_golive_today,
            "is_golive_month": is_golive_month,
        })

    return {
        "statuses": config.PT2_STATUSES,
        "status_colors": config.PT2_STATUS_COLORS,
        "golive_status": config.PT2_GOLIVE_STATUS,
        "exclude_from_denom": config.PT2_EXCLUDE_FROM_DENOM,
        "regionals": sorted(regionals, key=lambda r: r.lower()),
        "branches": sorted(branches, key=lambda b: b.lower()),
        "current_month_label": config.MONTH_LABEL_ID[today.month - 1],
        "rows": rows,
    }