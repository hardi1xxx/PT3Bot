"""
JALANKAN SEKALI SAJA, DI KOMPUTER LOKAL ANDA (bukan di server/Railway).
Tujuannya: login pakai akun Google pribadi Anda lewat browser, lalu
menghasilkan refresh_token yang nanti dipakai drive_service.py di server
supaya bisa akses Drive pribadi Anda tanpa perlu login ulang tiap saat.

Persiapan:
  1. pip install google-auth-oauthlib
  2. Di Google Cloud Console (project yang sama dengan yang dipakai Sheets):
       APIs & Services -> Credentials -> Create Credentials
       -> OAuth client ID -> Application type: "Desktop app"
     Download file JSON-nya, ganti namanya jadi client_secret.json,
     taruh di folder yang sama dengan script ini.
  3. Kalau OAuth consent screen masih "Testing", tambahkan email Anda
     sendiri di daftar "Test users" (APIs & Services -> OAuth consent screen).

Cara pakai:
  python get_drive_refresh_token.py

Browser akan kebuka, minta Anda login & klik "Allow". Setelah itu,
3 nilai di bawah akan tercetak -- itu yang perlu Anda copy ke
Environment Variables di Railway.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

# Scope "drive" (bukan "drive.file") supaya bisa akses folder yang sudah
# ada sebelumnya juga, bukan cuma file yang dibuat lewat app ini.
SCOPES = ["https://www.googleapis.com/auth/drive"]

if __name__ == "__main__":
    flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
    # access_type=offline & prompt=consent WAJIB supaya Google benar-benar
    # mengeluarkan refresh_token (kalau tidak, kadang cuma access_token
    # yang berumur pendek dan refresh_token-nya kosong).
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    print("\n================ SIMPAN 3 BARIS INI DI RAILWAY (Environment Variables) ================")
    print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print("=========================================================================================")
    print("\nCatatan: refresh_token ini berlaku terus sampai Anda cabut aksesnya manual")
    print("lewat https://myaccount.google.com/permissions, jadi cukup dijalankan SEKALI.")