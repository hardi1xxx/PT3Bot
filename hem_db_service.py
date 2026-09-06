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
    ("progress_jt_last_update", "datetime"), ("progress", "text"),
    ("keterangan_detail", "textarea"), ("umur_order", "number"),
    ("grouping_umur_order", "text"), ("issue", "textarea"),

    ("target_fi", "date"), ("tanggal_fi", "date"), ("tgl_go_live", "date"),
    ("bulan_golive", "text"), ("tgl_ut", "date"), ("target_weekly_bast", "date"),

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


def _cast_value(field_type, raw):
    """String dari JS -> tipe Python yang cocok buat psycopg2 ("" / None
    jadi NULL). text/textarea/date/datetime dikirim apa adanya sebagai
    string -- Postgres cast otomatis 'YYYY-MM-DD' / 'YYYY-MM-DDTHH:MM'
    ke kolom DATE/TIMESTAMP kalau tabelnya memang bertipe begitu."""
    if raw is None:
        return None
    val = str(raw).strip()
    if val == "":
        return None
    if field_type == "number":
        try:
            f = float(val)
        except ValueError:
            raise ValueError(f"'{raw}' bukan angka yang valid")
        return int(f) if f.is_integer() else f
    return val


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
    errors = []
    for i, r in enumerate(rows):
        try:
            values.append([_cast_value(HEM_FIELD_TYPE[k], r.get(k)) for k in used_keys])
        except ValueError as e:
            errors.append(f"Baris {i + 1}: {e}")

    if not values:
        return 0, errors

    cols_sql = ", ".join(f'"{k}"' for k in used_keys)
    query = f'INSERT INTO {TABLE_NAME} ({cols_sql}) VALUES %s'

    conn = get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(cur, query, values)
    finally:
        conn.close()

    return len(values), errors


def build_create_table_sql():
    """DDL bantuan kalau tabel data_semesta BELUM ada di Postgres-nya --
    ditampilkan lewat /debug/hem-schema buat di-copy-paste manual ke tab
    Query Railway. Sengaja TIDAK dieksekusi otomatis oleh aplikasi (biar
    perubahan schema tetap sepenuhnya keputusan/kontrol Anda)."""
    type_sql = {
        "number": "NUMERIC", "text": "TEXT", "textarea": "TEXT",
        "date": "DATE", "datetime": "TIMESTAMP",
    }
    cols = ",\n  ".join(f'"{k}" {type_sql[t]}' for k, t in HEM_FIELDS)
    return f'CREATE TABLE IF NOT EXISTS {TABLE_NAME} (\n  id SERIAL PRIMARY KEY,\n  {cols}\n);'
