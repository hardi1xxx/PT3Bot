# PT3 Status Bot

Web dashboard + bot Telegram untuk update status/keterangan "Detail PT3"
tanpa buka Google Sheet secara manual.

## Struktur

- `config.py` — mapping kolom (Z → tanggal/keterangan), daftar dropdown Z & AA
- `sheets_service.py` — semua baca/tulis ke Google Sheets (dipakai bersama oleh web & bot)
- `app.py` — web dashboard (Flask): cari site, lihat riwayat, isi update
- `bot.py` — bot Telegram: alur `/update` → cari → pilih status → isi keterangan → konfirmasi
- `Procfile` — dua process: `web` (dashboard) dan `worker` (bot polling)

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
# PT3Bot
