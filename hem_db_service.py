"""Koneksi & insert data "HEM / Input Data Semesta" ke Postgres (addon
Railway) -- dipakai oleh endpoint /api/hem/insert di app.py.

Railway OTOMATIS nyuntik env var DATABASE_URL begitu addon Postgres
di-attach ke service ini (lihat config.DATABASE_URL), jadi tidak perlu
diisi manual.

PENTING -- HEM_FIELDS di bawah SENGAJA disalin persis dari COLUMN_GROUPS
di templates/hem.html (key & urutannya harus SAMA PERSIS, itu yang
dipakai frontend buat kirim payload JSON ke endpoint ini). Kalau nanti
nambah/hapus/ubah nama kolom di salah satu sisi, sisi yang satu lagi
WAJIB diupdate juga supaya tetap sinkron -- tidak ada mekanisme
otomatis yang menjaga keduanya tetap sama.
"""
import datetime
import re

import psycopg2
import psycopg2.extras

import config

TABLE_NAME = "data_semesta"

# (key, type) -- type: "number" | "text" | "textarea" | "date" | "datetime"
HEM_FIELDS = [
    ("no", "number"), ("tahun", "number"), ("no_order", "text"),
    ("ihld_lop_id", "text"), ("nama_proyek", "text"), ("status_ihld", "text"),
    ("tipe_desain", "text"), ("prioritas", "text"),

    ("status_wo_tif", "text"), ("new_region_ta", "text"), ("region_tif", "text"),
    ("reg_lama", "text"), ("witel_lama", "text"), ("branch", "text"),
    ("sto", "text"), ("sc", "text"), ("datek", "text"), ("tipe_deploy_actual", "text"),

    ("layanan", "text"), ("nde_wo", "text"), ("tanggal_nde_wo", "date"),
    ("nde_permohonan_ut", "text"),

    ("panjang_kabel_meter", "number"), ("id_pr_material", "text"), ("pid", "text"),
    ("sap", "text"), ("pr", "text"), ("po", "text"),

    ("progress_w01", "text"), ("progress_w02", "text"),
    ("progress_jt_last_update", "text"), ("progress", "text"),
    ("keterangan_detail", "textarea"), ("umur_order", "number"),
    ("grouping_umur_order", "text"), ("issue", "textarea"),

    ("target_fi", "date"), ("tanggal_fi", "date"), ("tgl_go_live", "date"),
    ("bulan_golive", "text"), ("tgl_ut", "date"), ("target_weekly_bast", "text"),

    ("status_drop", "text"), ("status_ut", "text"), ("status_rekon", "text"),
    ("bast", "text"), ("status_ba_drop", "text"), ("tanggal_ba_drop", "date"),
    ("ket_ba_drop", "textarea"), ("ba_redesign", "text"), ("lact", "text"),
    ("baut", "text"),

    ("total_boq_ihld", "number"), ("total_boq_wo", "number"), ("total_drm", "number"),
    ("total_boq_actual_ut_rekon", "number"), ("material_ut", "text"),
    ("jasa_ut", "text"), ("total_ut", "number"), ("nilai_perizinan", "number"),
    ("kenaikan", "number"), ("selisih_nilai_wo_ihld", "number"),
    ("persen_perubahan_nilai", "number"),

    ("nama_mitra", "text"), ("jumlah_manpower", "number"), ("no_wo_smile", "text"),
    ("nama_smile", "text"), ("persen_smile", "number"), ("status_smile", "text"),

    ("sp", "text"), ("nomor_sp", "text"),
]
HEM_FIELD_TYPE = dict(HEM_FIELDS)
HEM_FIELD_KEYS = [k for k, _ in HEM_FIELDS]


def get_connection():
    if not config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL belum ke-set. Attach addon Postgres di Railway ke "
            "service ini dulu (Railway otomatis nyuntik env var DATABASE_URL "
            "begitu sudah di-attach, tidak perlu diisi manual)."
        )
    return psycopg2.connect(config.DATABASE_URL)


def _cast_value(field_type, raw, key=None):
    """String dari JS -> tipe Python yang cocok buat psycopg2 ("" / None
    jadi NULL). number di-parse lewat _parse_number() (kecuali kolom
    "tahun", lihat _extract_year_number()); date/datetime di-parse &
    dinormalisasi ke ISO lewat _parse_date()/_parse_datetime(); text/
    textarea dikirim apa adanya sebagai string."""
    if raw is None:
        return None
    val = str(raw).strip()
    if val == "":
        return None
    if field_type == "number":
        if key == "tahun":
            return _extract_year_number(val)
        return _parse_number(val)
    if field_type == "date":
        return _parse_date(val)
    if field_type == "datetime":
        return _parse_datetime(val)
    return val


# Kolom "tahun" di file sumber ternyata bercampur: angka murni ("2026")
# ATAU label + tahun ("CO 2025", kemungkinan singkatan "Carry Over 2025").
# Atas keputusan pengguna, label seperti "CO" dibuang -- hanya 4 digit
# tahunnya yang disimpan (mis. "CO 2025" -> 2025). Info "CO" itu sendiri
# TIDAK disimpan di kolom manapun setelah ini.
_YEAR_RE = re.compile(r"(\d{4})")


def _extract_year_number(val):
    m = _YEAR_RE.search(val)
    if not m:
        raise ValueError(f"'{val}' tidak mengandung tahun (4 digit angka) yang valid")
    return int(m.group(1))


# Token error formula Excel & placeholder "kosong" -- atas keputusan
# pengguna, ini dianggap NULL (baris tetap masuk, kolom itu kosong)
# alih-alih menggagalkan baris.
_NUMBER_NULL_TOKENS = {"#n/a", "#ref!", "#value!", "#div/0!", "#null!", "#num!", "#name?", "-"}

# Format Indonesia: titik = pemisah ribuan, koma = desimal, mis.
# "7.111.783,00" atau "-4.609.953". Regex mengharuskan grup 3 digit
# persis setelah tiap titik supaya tidak salah anggap titik desimal
# biasa (mis. "12.5") sebagai format Indonesia.
_ID_THOUSANDS_RE = re.compile(r'^-?\d{1,3}(\.\d{3})+(,\d+)?$')
_PLAIN_COMMA_DECIMAL_RE = re.compile(r'^-?\d+,\d+$')


def _parse_number(val):
    """Normalisasi angka dari file Excel/CSV: buang tanda '%' (nilai
    persen disimpan sebagai angka utuh, bukan pecahan -- keputusan
    pengguna), pahami format ribuan/desimal ala Indonesia, dan anggap
    token error Excel / '-' sebagai NULL. Sisanya di-parse lewat
    float() seperti biasa."""
    v = val.strip()
    if v.lower() in _NUMBER_NULL_TOKENS:
        return None
    if v.endswith("%"):
        v = v[:-1].strip()
    if _ID_THOUSANDS_RE.match(v):
        v = v.replace(".", "").replace(",", ".")
    elif _PLAIN_COMMA_DECIMAL_RE.match(v):
        v = v.replace(",", ".")
    try:
        f = float(v)
    except ValueError:
        raise ValueError(f"'{val}' bukan angka yang valid")
    return int(f) if f.is_integer() else f


# Format tanggal yang diterima, dicoba berurutan sampai salah satu cocok.
# "%Y-%m-%d" -> ISO, dikirim oleh <input type="date"> di form manual.
# "%d/%m/%Y" dan "%d-%m-%Y" -> format Indonesia umum dari file Excel/CSV
# (mis. "21/07/2026"). Pola "DD-Mon-YY" (mis. "05-Agu-26") ditangani
# terpisah lewat _MONTH_ABBR karena singkatan bulannya campur
# Indonesia/Inggris (Agu/Aug, Mei/May, Okt/Oct, Des/Dec, dst).
_DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"]

_DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",   # dari <input type="datetime-local">
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
]

_MONTH_ABBR = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "mei": 5, "may": 5,
    "jun": 6, "jul": 7,
    "agu": 8, "aug": 8,
    "sep": 9,
    "okt": 10, "oct": 10,
    "nov": 11,
    "des": 12, "dec": 12,
}
_MONTH_ABBR_DATE_RE = re.compile(r'^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})$')

# Serial date Excel: hari sejak 1899-12-30 (termasuk bug tahun kabisat
# 1900 bawaan Excel/Lotus). Dipakai kalau sel Excel bertipe tanggal asli
# tapi kebetulan terbaca sebagai angka mentah, bukan teks terformat.
_EXCEL_EPOCH = datetime.date(1899, 12, 30)

# "DROP" di kolom target_fi/tanggal_fi/tgl_go_live artinya target itu
# sendiri dibatalkan (bukan data rusak) -- atas keputusan pengguna,
# dianggap NULL, sisanya (mayoritas tanggal asli di kolom2 itu) tetap
# tersimpan sebagai tanggal. Data tanggal yang benar-benar rusak/typo
# (mis. "170Feb026") SENGAJA tidak ditangkap di sini -- tetap gagal
# per baris supaya dikoreksi manual di file sumbernya.
_DATE_NULL_TOKENS = {"drop"}


def _parse_date(val):
    """Normalisasi string tanggal ke ISO 'YYYY-MM-DD'.

    PENTING: kalau string mentah (mis. '21/07/2026') dikirim apa adanya
    ke Postgres tanpa dinormalisasi, Postgres membacanya pakai DateStyle
    default (MDY -- bulan/hari/tahun), jadi '21' dibaca sebagai bulan
    dan meledak dengan error "date/time field value out of range" untuk
    tanggal yang harinya > 12. Di sinilah tempatnya divalidasi & diubah
    ke ISO SEBELUM sampai ke Postgres -- dan karena ini dipanggil per
    baris (lewat _cast_value di dalam loop insert_rows), satu tanggal
    salah format hanya menggagalkan baris itu, bukan seluruh batch."""
    if val.lower() in _DATE_NULL_TOKENS:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.datetime.strptime(val, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = _MONTH_ABBR_DATE_RE.match(val)
    if m:
        day_s, mon_s, year_s = m.groups()
        mon = _MONTH_ABBR.get(mon_s.lower())
        if mon:
            year = int(year_s)
            if year < 100:
                year += 2000
            try:
                return datetime.date(year, mon, int(day_s)).isoformat()
            except ValueError:
                pass  # mis. tanggal 31 di bulan yg cuma 30 hari -- lanjut ke error di bawah
    if val.isdigit() and 1 <= int(val) <= 100000:
        return (_EXCEL_EPOCH + datetime.timedelta(days=int(val))).isoformat()
    raise ValueError(
        f"tanggal '{val}' tidak dikenali formatnya "
        f"(harus YYYY-MM-DD, DD/MM/YYYY, atau DD-Mon-YY)"
    )



def _parse_datetime(val):
    """Sama seperti _parse_date() tapi untuk kolom timestamp (ikut jam)."""
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.datetime.strptime(val, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    try:
        return _parse_date(val) + " 00:00:00"
    except ValueError:
        pass
    raise ValueError(
        f"tanggal/waktu '{val}' tidak dikenali formatnya "
        f"(harus YYYY-MM-DD HH:MM atau DD/MM/YYYY HH:MM)"
    )



def insert_rows(rows):
    """rows: list of dict {field_key: value} (persis format `r` yang
    dipakai renderSqlOutput() di hem.html). Key di luar HEM_FIELD_KEYS
    diabaikan diam-diam (bukan error), supaya aman kalau frontend nanti
    kirim field ekstra.

    Return: (jumlah_baris_berhasil_masuk, list_pesan_error_per_baris).
    Baris yang gagal di-cast (mis. kolom angka diisi teks) DILEWATI
    (tidak membatalkan baris lain), errornya dilaporkan per baris."""
    if not rows:
        return 0, ["Tidak ada baris data yang dikirim."]

    used_keys = [
        k for k in HEM_FIELD_KEYS
        if any(str(r.get(k, "") or "").strip() != "" for r in rows)
    ]
    if not used_keys:
        return 0, ["Tidak ada kolom terisi di baris manapun."]

    values = []
    row_dicts = []
    errors = []
    for i, r in enumerate(rows):
        try:
            row_values = []
            row_dict = {}
            for k in used_keys:
                try:
                    v = _cast_value(HEM_FIELD_TYPE[k], r.get(k), key=k)
                except ValueError as e:
                    raise ValueError(f"kolom '{k}': {e}") from None
                row_values.append(v)
                row_dict[k] = v
            values.append(row_values)
            row_dicts.append(row_dict)
        except ValueError as e:
            errors.append(f"Baris {i + 1}: {e}")

    if not values:
        return 0, errors

    cols_sql = ", ".join(f'"{k}"' for k in used_keys)
    query = f'INSERT INTO {TABLE_NAME} ({cols_sql}) VALUES %s RETURNING id_semesta, ihld_lop_id'

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                returned = psycopg2.extras.execute_values(cur, query, values, fetch=True)
                # returned dijamin urutannya SAMA PERSIS dengan `values`/`row_dicts`
                # (RETURNING pada satu statement INSERT...VALUES mengikuti urutan
                # input; execute_values(fetch=True) menyambung hasil tiap halaman
                # sesuai urutan juga).
                for idx, ((id_semesta, ihld_lop_id), row_dict) in enumerate(zip(returned, row_dicts)):
                    if not ihld_lop_id:
                        continue  # baris ini tidak ada ihld_lop_id -- tidak bisa ditautkan
                    savepoint = f"sp_fanout_{idx}"
                    cur.execute(f"SAVEPOINT {savepoint}")
                    try:
                        cur.execute("SELECT 1 FROM ihld WHERE id_ihld = %s", (ihld_lop_id,))
                        if cur.fetchone() is None:
                            # File IHLD/LOP utk project ini belum pernah diimpor --
                            # biarkan baris data_semesta ini tidak tertaut dulu
                            # (keputusan pengguna), jangan bikin baris ihld/anak.
                            cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                            continue
                        cur.execute(
                            "UPDATE ihld SET id_semesta = %s WHERE id_ihld = %s",
                            (id_semesta, ihld_lop_id),
                        )
                        _fanout_children(cur, ihld_lop_id, row_dict)
                        cur.execute(f"RELEASE SAVEPOINT {savepoint}")
                    except Exception as e:
                        # Gagal menaut/menyebar utk SATU baris ini saja tidak boleh
                        # membatalkan seluruh batch insert data_semesta yang sudah
                        # berhasil -- rollback cuma sampai savepoint ini, lanjut ke
                        # baris berikutnya.
                        cur.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        errors.append(
                            f"ihld_lop_id={ihld_lop_id!r}: baris data_semesta berhasil "
                            f"disimpan, TAPI gagal menautkan/menyebar ke ihld -- "
                            f"{type(e).__name__}: {e}"
                        )
    finally:
        conn.close()

    return len(values), errors


# Pemetaan kolom data_semesta -> kolom di tiap tabel anak "ihld". Dipakai
# oleh _fanout_children() setelah baris data_semesta berhasil diinsert
# DAN ihld_lop_id-nya sudah cocok dengan baris ihld yang ada (lihat
# insert_rows() di atas). Format: {kolom_tabel_anak: kolom_data_semesta}.
# Kolom tabel anak yang TIDAK ada di sini (mis. data_area.tanggal_create_ihld,
# data_progress.aging_wo_to_gl) memang tidak tersedia dari file Data
# Semesta -- tetap NULL sampai ada sumber data lain utk itu.
_DATA_AREA_MAP = {
    "nomor": "no", "nama_proyek": "nama_proyek", "new_region_ta": "new_region_ta",
    "status_wo_tif": "status_wo_tif", "region_tif": "region_tif", "reg_lama": "reg_lama",
    "layanan": "layanan", "nde_wo": "nde_wo", "tanggal_nde_wo": "tanggal_nde_wo",
    "no_order": "no_order", "tipe_deploy_actual": "tipe_deploy_actual", "sc": "sc",
    "datek": "datek", "total_boq_ihld": "total_boq_ihld", "prioritas": "prioritas",
    "umur_order": "umur_order", "grouping_umur_order": "grouping_umur_order",
    "nde_permohonan_ut": "nde_permohonan_ut",
}
_DATA_MATERIAL_MAP = {
    "panjang_kabel_meter": "panjang_kabel_meter", "id_pr_material": "id_pr_material",
    "id_pid": "pid", "id_sap": "sap", "id_pr": "pr", "id_po": "po",
}
_DATA_PROGRESS_MAP = {
    "nama_mitra": "nama_mitra", "jumlah_manpower": "jumlah_manpower",
    "total_drm": "total_drm", "progress": "progress",
    "keterangan_detail": "keterangan_detail", "target_fi": "target_fi",
    "status_drop": "status_drop", "tanggal_fi": "tanggal_fi",
    "tanggal_go_live": "tgl_go_live", "tanggal_ut": "tgl_ut", "issue": "issue",
}
_PROSES_DOKUMEN_MAP = {
    "total_boq_wo": "total_boq_wo", "total_boq_actual_ut": "total_boq_actual_ut_rekon",
    "status_ut": "status_ut", "material_ut": "material_ut", "jasa_ut": "jasa_ut",
    "total_ut": "total_ut", "nilai_perizinan": "nilai_perizinan", "kenaikan": "kenaikan",
    "status_lact": "lact", "ba_redesign": "ba_redesign", "status_baut": "baut",
    "sp": "sp", "nomor_sp": "nomor_sp", "no_wo_smile": "no_wo_smile",
    "nama_smile": "nama_smile", "persen_smile": "persen_smile", "status_smile": "status_smile",
    "status_rekon": "status_rekon", "status_bast": "bast",
    "target_weekly_bast": "target_weekly_bast",
}
_DOKUMEN_FILE_MAP = {
    "status_ba_drop": "status_ba_drop", "tanggal_ba_drop": "tanggal_ba_drop",
    "lact": "lact", "baut": "baut", "ba_redesign": "ba_redesign",
}


def _upsert_child(cur, table, pk_col, pk_val, fk_col, fk_val, col_map, source_row):
    """INSERT ... ON CONFLICT DO UPDATE satu baris ke tabel anak `table`.
    pk_val dibuat deterministik dari id_ihld (lihat _fanout_children) --
    supaya import ulang project yang sama meng-UPDATE baris anak yang
    sama, bukan menumpuk baris baru (konsisten dgn kebijakan upsert
    ihld itu sendiri)."""
    cols = [pk_col, fk_col] + list(col_map.keys())
    vals = [pk_val, fk_val] + [source_row.get(src_key) for src_key in col_map.values()]
    cols_sql = ", ".join(f'"{c}"' for c in cols)
    placeholders = ", ".join(["%s"] * len(vals))
    update_sql = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in col_map.keys())
    query = (
        f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders}) '
        f'ON CONFLICT ("{pk_col}") DO UPDATE SET {update_sql}'
    )
    cur.execute(query, vals)


def _fanout_children(cur, id_ihld, source_row):
    """Sebar `source_row` (dict kolom data_semesta -> nilai, dari SATU
    baris yang baru diinsert) ke 5 tabel anak ihld. ID tiap anak dibuat
    deterministik ("<id_ihld>-AREA" dst) supaya 1 project = 1 baris per
    tabel anak yang selalu ter-update saat diimpor ulang."""
    _upsert_child(cur, "data_area", "id_data_area", f"{id_ihld}-AREA", "id_ihld", id_ihld, _DATA_AREA_MAP, source_row)
    _upsert_child(cur, "data_material", "id_material", f"{id_ihld}-MTRL", "id_ihld", id_ihld, _DATA_MATERIAL_MAP, source_row)
    _upsert_child(cur, "data_progress", "id_progress", f"{id_ihld}-PRG", "id_ihld", id_ihld, _DATA_PROGRESS_MAP, source_row)
    _upsert_child(cur, "proses_dokumen", "id_proses_dokumen", f"{id_ihld}-DOK", "id_ihld", id_ihld, _PROSES_DOKUMEN_MAP, source_row)
    _upsert_child(cur, "dokumen_file", "id_file", f"{id_ihld}-FILE", "id_ihld", id_ihld, _DOKUMEN_FILE_MAP, source_row)


def build_create_table_sql():
    """DDL bantuan kalau tabel data_semesta BELUM ada di Postgres-nya --
    ditampilkan lewat /debug/hem-schema buat di-copy-paste manual ke tab
    Query Railway. Sengaja TIDAK dieksekusi otomatis oleh aplikasi (biar
    perubahan schema tetap sepenuhnya keputusan/kontrol Anda).

    CATATAN: skema produksi sebenarnya (tabel data_semesta + ihld + anak2-
    nya) didefinisikan lengkap di file terpisah schema_ihld.sql, TERMASUK
    kolom id_semesta sbg PK, relasi FK ke ihld, trigger updated_at, dst --
    yang tidak semuanya direplikasi di fungsi generik ini. DDL di bawah
    HANYA fallback minimal kalau tabelnya belum ada sama sekali; kalau
    schema_ihld.sql sudah pernah dijalankan, JANGAN pakai DDL di bawah --
    pakai schema_ihld.sql sebagai sumber kebenaran skemanya."""
    type_sql = {
        "number": "NUMERIC", "text": "TEXT", "textarea": "TEXT",
        "date": "DATE", "datetime": "TIMESTAMP",
    }
    cols = ",\n  ".join(f'"{k}" {type_sql[t]}' for k, t in HEM_FIELDS)
    return f'CREATE TABLE IF NOT EXISTS {TABLE_NAME} (\n  id_semesta BIGSERIAL PRIMARY KEY,\n  {cols}\n);'


# =============================================================================
# Import file "IHLD/LOP" (sumber terpisah dari "Data Semesta" di atas) --
# ngisi tabel `ihld`, dipakai oleh endpoint /api/hem/insert-ihld di app.py.
#
# BEDA dengan HEM_FIELDS: key pertama di tiap tuple adalah HEADER ASLI di
# file export LOP (mis. "iHLD LoP ID"), BUKAN nama kolom Postgres -- ini
# dicocokkan langsung dengan header yang dikirim frontend (hem.html kirim
# ihldRows apa adanya, key-nya = header asli file, SAMA seperti dipakai
# IHLD_FIELD_MAP di sana buat auto-isi form manual). Kalau file sumbernya
# ganti nama kolom, update di sini DAN di IHLD_FIELD_MAP/hem.html.
IHLD_TABLE_NAME = "ihld"

IHLD_FIELDS = [
    ("Status Order", "status_order", "text"),
    ("Tipe Desain", "tipe_desain", "text"),
    ("Nama Proyek", "nama_proyek", "text"),
    ("iHLD LoP ID", "ihld_lop_id", "text"),  # juga dipakai sbg id_ihld (PK)
    ("Smart Planning Polygon ID", "smart_planning_polygon", "text"),
    ("eProposal LoP ID", "eproposal_lop_id", "text"),
    ("eProposal LoP Parent ID", "eproposal_lop_parent_id", "text"),
    ("Kode Program", "kode_program", "text"),
    ("Revenue Plan", "revenue_plan", "text"),
    ("Nama CFU", "nama_cfu", "text"),
    ("Kategori", "kategori", "text"),
    ("Jenis Program", "jenis_program", "text"),
    ("Batch Program", "batch_program", "text"),
    ("Regional", "regional", "text"),
    ("Witel", "witel", "text"),
    ("Witel Lama", "witel_lama", "text"),
    ("Datel", "datel", "text"),
    ("STO", "sto", "text"),
    ("WOK", "wok", "text"),
    ("Telkomsel Area", "telkomsel_area", "text"),
    ("Telkomsel Regional", "telkomsel_regional", "text"),
    ("Telkomsel Branch", "telkomsel_branch", "text"),
    ("Telkomsel Cluster", "telkomsel_cluster", "text"),
    ("Durasi Desain", "durasi_desain", "duration"),
    ("Total BOQ", "total_boq", "number"),
    ("Capex per Port", "capex_per_port", "number"),
    ("Tahun Program", "tahun_program", "number"),
    ("ODP Plan", "odp_plan", "number"),
    ("Odp Real", "odp_real", "number"),
    ("Alpro MTEL", "alpro_mtel", "text"),
    ("Jenis Kebutuhan OLT", "jenis_kebutuhan_olt", "text"),
    ("Jenis Kebutuhan OTN", "jenis_kebutuhan_otn", "text"),
    ("Site BTS CSF", "site_bts_csf", "text"),
    ("Total Port", "total_port", "number"),
    ("No PR", "no_pr", "text"),
    ("No PO", "no_po", "text"),
    ("Nilai PO", "nilai_po", "number"),
    ("No GR", "no_gr", "text"),
    ("Nilai GR", "nilai_gr", "number"),
    ("No IR", "no_ir", "text"),
    ("Nilai IR", "nilai_ir", "number"),
    ("Status eProposal", "status_eproposal", "text"),
    ("Status Tomps", "status_tomps", "text"),
    ("Status Tomps - Last Activity", "status_tomps_last_activity", "datetime"),
    ("Status SAP", "status_sap", "text"),
    ("Status Proyek", "status_proyek", "text"),
    ("Estimasi Go Live", "estimasi_go_live", "date"),
    ("Kategori Mitra", "kategori_mitra", "text"),
    ("Nama Mitra", "nama_mitra", "text"),
    ("ODP Go Live", "odp_go_live", "date"),
    ("Star Click ID", "star_click_id", "text"),
    ("Dibuat Oleh", "dibuat_oleh", "text"),
    ("Username / NIK Pembuat", "username_nik_pembuat", "text"),
    ("Disubmit Pada", "disubmit_pada", "datetime"),
    ("Dibuat", "dibuat", "datetime"),
    ("Diperbarui Pada", "diperbarui_pada", "datetime"),
]

# File export LOP pakai "-" sebagai penanda "kosong" di SEMUA tipe kolom
# (bukan cuma angka seperti di file Data Semesta) -- lihat contoh file
# "LOP--REGIONAL-2-...csv" yang diperiksa manual.
_IHLD_NULL_TOKEN = "-"

_DURATION_RE = re.compile(r'(-?\d+(?:[.,]\d+)?)')


def _parse_duration_number(val):
    """Kolom 'Durasi Desain' formatnya '<angka> <satuan>' (mis. '0
    Detik'). SENGAJA cuma angkanya yang diambil -- satuannya (Detik/
    Jam/Hari, dll.) tidak dikonversi/diseragamkan karena belum jelas
    satuan apa saja yang muncul di seluruh data, jadi disimpan apa
    adanya sebagai angka mentah sesuai satuan aslinya di baris itu."""
    m = _DURATION_RE.search(val)
    if not m:
        raise ValueError(f"'{val}' tidak mengandung angka durasi yang valid")
    f = float(m.group(1).replace(",", "."))
    return int(f) if f.is_integer() else f


def _cast_ihld_value(field_type, raw):
    if raw is None:
        return None
    val = str(raw).strip()
    if val == "" or val == _IHLD_NULL_TOKEN:
        return None
    if field_type == "number":
        return _parse_number(val)
    if field_type == "duration":
        return _parse_duration_number(val)
    if field_type == "date":
        return _parse_date(val)
    if field_type == "datetime":
        return _parse_datetime(val)
    return val


def insert_ihld_rows(rows):
    """rows: list of dict {header_asli_file: value} (persis `ihldRows` di
    hem.html, sebelum dipetakan IHLD_FIELD_MAP). Upsert berdasarkan
    id_ihld = nilai kolom "iHLD LoP ID" -- import ulang project yang sama
    akan MENIMPA (UPDATE) baris ihld yang sudah ada, bukan menumpuk baris
    baru (keputusan pengguna).

    Baris tanpa "iHLD LoP ID" terisi dilewati sebagai error (tidak bisa
    upsert tanpa kunci utama). Baris lain yang gagal di-cast dilewati per
    baris, tidak membatalkan baris lainnya."""
    if not rows:
        return 0, ["Tidak ada baris data yang dikirim."]

    used = [
        (header, col, typ) for header, col, typ in IHLD_FIELDS
        if any(str(r.get(header, "") or "").strip() not in ("", _IHLD_NULL_TOKEN) for r in rows)
    ]
    if not used:
        return 0, ["Tidak ada kolom terisi di baris manapun."]

    values = []
    errors = []
    for i, r in enumerate(rows):
        try:
            row_values = {}
            for header, col, typ in used:
                try:
                    row_values[col] = _cast_ihld_value(typ, r.get(header))
                except ValueError as e:
                    raise ValueError(f"kolom '{header}': {e}") from None
            id_ihld = row_values.get("ihld_lop_id")
            if not id_ihld:
                raise ValueError("'iHLD LoP ID' kosong -- wajib diisi (dipakai sebagai kunci utama)")
            values.append((str(id_ihld), row_values))
        except ValueError as e:
            errors.append(f"Baris {i + 1}: {e}")

    if not values:
        return 0, errors

    db_cols = [col for _, col, _ in used]
    all_cols = ["id_ihld"] + db_cols
    rows_for_insert = [
        [id_ihld] + [row_values.get(c) for c in db_cols]
        for id_ihld, row_values in values
    ]

    cols_sql = ", ".join(f'"{c}"' for c in all_cols)
    update_sql = ", ".join(f'"{c}" = EXCLUDED."{c}"' for c in db_cols)
    query = (
        f'INSERT INTO {IHLD_TABLE_NAME} ({cols_sql}) VALUES %s '
        f'ON CONFLICT (id_ihld) DO UPDATE SET {update_sql}'
    )

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, query, rows_for_insert)
    finally:
        conn.close()

    return len(values), errors