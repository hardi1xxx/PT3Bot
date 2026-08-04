Taruh file logo di folder ini dengan nama persis seperti berikut, dashboard
akan otomatis menampilkannya di header (kanan atas):

  static/logo-telkomakses.png
  static/logo-infranexia.png

Format PNG/SVG dengan latar transparan, tinggi ideal sekitar 40-60px.
Kalau file belum ada / nama tidak cocok, dashboard tetap jalan normal dan
otomatis menampilkan teks "TELKOM AKSES" / "INFRANEXIA" sebagai pengganti
sementara (lihat base.html, ada fallback onerror di tag <img>).

File ini (LOGO_README.txt) boleh dihapus, tidak dipakai oleh aplikasi.
