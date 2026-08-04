# PT3 Status Bot

Web dashboard + bot Telegram untuk update status/keterangan "Detail PT3"
tanpa buka Google Sheet secara manual.

## Struktur

- `config.py` — mapping kolom (Z → tanggal/keterangan), daftar dropdown Z & AA,
  plus kolom dashboard (Total Order, Total Port, Batch, BH, daftar status aktif)
- `sheets_service.py` — semua baca/tulis ke Google Sheets (dipakai bersama oleh web & bot),
  termasuk `get_dashboard_data()` yang menghitung semua angka/tabel dashboard
- `app.py` — web dashboard (Flask): halaman utama + API JSON (`/api/dashboard`,
  `/api/row/<id>`, `/api/row/<id>/update`) yang dipakai halaman dashboard
- `bot.py` — bot Telegram: alur `/update` → cari → pilih status → isi keterangan → konfirmasi
- `Procfile` — dua process: `web` (dashboard) dan `worker` (bot polling)
- `templates/` — `base.html` (shell: sidebar + header + logo), `index.html` (dashboard),
  `update.html` (halaman update mandiri, fallback dari link langsung)
- `static/style.css` — semua styling dashboard
- `static/chart.umd.min.js` — Chart.js versi lokal (tidak pakai CDN luar, supaya tetap
  jalan di jaringan kantor yang dibatasi)
- `static/logo-telkomakses.png`, `static/logo-infranexia.png` — **belum disertakan**,
  lihat `static/LOGO_README.txt`

## Dashboard (baru)

Halaman utama (`/`) sekarang berupa dashboard, bukan cuma kotak pencarian:

- 4 kartu ringkasan: Total Order (hitung kolom A), Total Port (jumlah kolom AG),
  Lokasi Sedang Berjalan, dan % Progress Finish Instalasi
- Tabel **Data / Port**: baris = Batch (kolom C), kolom = status fisik (kolom Z),
  nilai = jumlah port (AG) — untuk 5 status: Perijinan, Persiapan, Matdev, Instalasi,
  Finish Instalasi
- Tabel **Data / LOP**: sama seperti di atas tapi nilainya jumlah baris (LOP), bukan port
- Donut chart distribusi total order per status
- Rekap kolom BH (hitung otomatis semua nilai unik + jumlahnya)
- Panel pencarian + daftar lokasi sedang berjalan, dengan panel update di sampingnya
  (klik lokasi → form update langsung muncul, simpan tanpa reload halaman)

Semua angka dashboard dihitung ulang dari sheet setiap kali halaman dibuka /
tombol "Refresh" ditekan (tidak di-cache di server).

## Deploy ke Railway (project baru)

1. Push folder ini ke repo GitHub baru.
2. Di Railway: **New Project → Deploy from GitHub repo**, pilih repo ini.
3. Railway akan otomatis mendeteksi `Procfile` dan membuat 2 service: `web` dan `worker`.
   Kalau tidak otomatis, tambahkan manual: **New Service → dari repo yang sama**, lalu di
   Settings service kedua set **Start Command** ke `python bot.py`.
4. Di **kedua** service (web & worker), buka tab **Variables** dan isi:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — paste seluruh isi file JSON service account sebagai satu baris
   - `TELEGRAM_BOT_TOKEN` — token dari BotFather (hanya perlu di service `worker`, tapi aman juga diisi di keduanya)
   - `SPREADSHEET_ID` — `100Sx_DgMZjobEM_Q9xoHXU4O26iHdy_hANfbM7NxWNQ`
   - `SHEET_NAME` — `Detail PT3`
   - `FLASK_SECRET_KEY` — string acak bebas (hanya perlu di service `web`)
5. Service `web` otomatis dapat env var `PORT` dari Railway — tidak perlu diisi manual.
6. Deploy. Cek log masing-masing service:
   - `web` → harus muncul gunicorn listening di `$PORT`
   - `worker` → harus muncul log `Bot starting (polling)...`
7. Buka domain publik dari service `web` (Railway kasih otomatis, atau generate lewat Settings → Networking).
8. Di Telegram, chat bot kamu → `/start` lalu `/update`.

## Penting — soal keamanan key

File JSON service account yang dipakai untuk setup ini sempat terlihat di chat.
Setelah semua variable di atas sudah dimasukkan ke Railway dan berjalan normal:

1. Buka Google Cloud Console → IAM & Admin → Service Accounts → `pt3-bot-writer@jpppt23...`
2. Tab **Keys** → hapus (delete) key lama yang sempat terlihat itu
3. (Opsional, kalau merasa perlu) buat key baru, lalu update ulang `GOOGLE_SERVICE_ACCOUNT_JSON` di Railway

## Catatan logika update

- Baris dicari lewat kombinasi kolom I (IHLD) + J (LOKASI IHLD)
- Kolom Z menentukan satu-satunya pasangan kolom tanggal/keterangan yang dipakai —
  kolom AA (sub status) dicatat terpisah dan tidak mengubah routing
- Setiap update baru ditulis dengan format `DD/MM/YY : keterangan`, ditaruh **di atas**
  entri lama (bukan di bawah)
- Kolom tanggal pasangannya selalu di-overwrite dengan tanggal terbaru saja (bukan riwayat)
- Kolom AB tidak pernah ditulis oleh bot — itu tetap formula gabungan otomatis di sheet
