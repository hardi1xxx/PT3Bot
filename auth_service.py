"""Login server-side untuk INTRA.

PENTING soal keamanan (supaya jelas kenapa desainnya begini): data user
dibaca dari Postgres (service "DB MASTER") di SINI, di server -- TIDAK
PERNAH dikirim ke browser dalam bentuk apapun. Jadi user (via Inspect
Element / tab Network) tidak akan pernah bisa melihat data user sama
sekali.

Password tetap di-hash (werkzeug generate_password_hash/check_password_hash
-- pakai scrypt, sudah termasuk salt otomatis) sebagai lapisan tambahan,
supaya kalau database ini somehow bocor, password asli user tetap tidak
langsung kebaca.

SEBELUMNYA modul ini baca dari users.xlsx (openpyxl) -- sekarang sumber
datanya dipindah ke tabel `users` di Postgres (lihat master_db_service.py),
dikelola lewat halaman /master-data > tab Users (khusus role developer).
Fungsi verify_login/current_user/can_access_menu/login_required TIDAK
berubah sama sekali, cuma load_users() yang gantinya sumber data.
"""
import functools

from flask import session, redirect, url_for, request
from werkzeug.security import check_password_hash

import master_db_service

ROLE_LABELS = {
    "developer": "Developer",
    "admin": "Admin",
    "manager": "Manager",
    "waspang": "Waspang",
    "TIF": "TIF",
    "Telkomsel": "Telkomsel",
}


def load_users():
    """Ambil semua user dari tabel Postgres `users` (service DB MASTER).
    SENGAJA tidak di-cache (sama seperti versi Excel sebelumnya) supaya
    perubahan (tambah/hapus/reset password user lewat /master-data)
    langsung kepakai tanpa perlu restart server."""
    return master_db_service.list_users_with_hash()


def verify_login(password):
    """Return dict user kalau password cocok dengan salah satu user di
    Postgres, None kalau tidak ada yang cocok.

    NIK tidak diminta di form login -- password sendiri yang jadi
    kredensial buat kenalin user-nya (makanya tiap user WAJIB punya
    password unik masing-masing, jangan sampai 2 user pakai password
    yang sama, nanti yang kepilih cuma yang baris pertama ketemu). Role &
    akses per-project (kolom role/project) tetap jalan seperti biasa
    karena hasilnya tetap dict user yang lengkap.

    Tiap baris di-bungkus try/except: kalau ADA SATU user yang
    password_hash-nya rusak/format-nya salah, baris itu di-skip aja --
    supaya tidak bikin SEMUA orang gagal login gara-gara satu baris yang
    rusak.

    Catatan performa: check_password_hash (scrypt) sengaja lambat demi
    keamanan, dan di sini di-loop ke semua user tiap kali login. Untuk
    jumlah user yang kecil (internal tool) ini masih aman."""
    if not password:
        return None
    for u in load_users():
        stored_hash = u["password_hash"]
        if not stored_hash:
            continue
        try:
            if check_password_hash(stored_hash, password):
                return u
        except ValueError:
            # Format hash user ini rusak. Skip baris ini saja.
            continue
    return None


def current_user():
    return session.get("user")


def can_access_menu(user, key):
    """developer/admin/manager bebas akses semua menu, role 'user' cuma
    menu sesuai kolom project-nya ('ALL' juga bebas akses semua)."""
    if not user:
        return False
    if user["role"] in ("developer", "admin", "manager"):
        return True
    return user["project"] == "ALL" or user["project"] == key


def login_required(view_func):
    """Decorator: redirect ke /login kalau belum login. Simpan halaman yang
    dituju di ?next= supaya begitu login sukses, langsung diarahkan balik
    ke situ (bukan selalu ke halaman pilihan project)."""
    @functools.wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login", next=request.path))
        return view_func(*args, **kwargs)
    return wrapped
