from curl_cffi import requests
import sqlite3
import os
import time
from datetime import datetime, timedelta
import logging

# Menentukan path absolute agar aman jika dijalankan via cron
script_dir = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.path.join(script_dir, 'idx_scraper.db')
DOWNLOAD_DIR = os.path.expanduser('~/anything/paperless/consume')

PROXY_URL = "http://55fc1fa1b0a2c5ead766__cr.id,jp,my,ph,sg,gb,us:1442dd9ccddcf177@gw.dataimpulse.com:823"
PROXIES = {
    "http": PROXY_URL,
    "https": PROXY_URL,
}

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(script_dir, 'scraper.log')),
        logging.StreamHandler()
    ]
)

def init_db():
    """Inisialisasi database SQLite untuk melacak file yang sudah didownload."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS downloaded_files (
            id TEXT PRIMARY KEY,
            pengumuman_id TEXT,
            filename TEXT,
            download_date TEXT
        )
    ''')
    conn.commit()
    conn.close()

def is_downloaded(file_url):
    """Cek apakah file URL sudah pernah didownload sebelumnya."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT id FROM downloaded_files WHERE id = ?', (file_url,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_downloaded(file_url, pengumuman_id, filename):
    """Tandai file URL sebagai sudah didownload di database."""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''
        INSERT INTO downloaded_files (id, pengumuman_id, filename, download_date)
        VALUES (?, ?, ?, ?)
    ''', (file_url, pengumuman_id, filename, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def download_file(url, filename, kode_emiten):
    """Fungsi untuk mendownload file dari URL dan menyimpannya ke folder emiten."""
    target_dir = os.path.join(DOWNLOAD_DIR, kode_emiten)
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    
    filepath = os.path.join(target_dir, filename)
    logging.info(f"Mendownload: {filename}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64 AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36)'
    }
    
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, headers=headers, proxies=PROXIES, impersonate="chrome", stream=True, timeout=30)
            if response.status_code == 403 and attempt < max_retries:
                logging.warning(f"Kena 403 Forbidden saat mendownload {filename}. Retry {attempt}/{max_retries} dalam 3 detik...")
                time.sleep(3)
                continue
                
            response.raise_for_status()
            with open(filepath, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            logging.info(f"Berhasil disimpan: {filepath}")
            return True
        except Exception as e:
            logging.error(f"Gagal mendownload {url}: {e}")
            return False
            
    return False

def scrape_idx():
    logging.info("Memulai proses scraping IDX...")
    init_db()
    
    today_str = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
    
    page_size = 50
    index_from = 0
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64 AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36)',
        'Accept': 'application/json, text/plain, */*',
        'Referer': 'https://www.idx.co.id/id/perusahaan-tercatat/keterbukaan-informasi'
    }
    
    consecutive_existing = 0
    # Jika menemukan file yang sudah didownload sebanyak 20 kali berturut-turut,
    # kita asumsikan sudah mencapai data lama dan bisa berhenti, 
    # agar tidak melooping seluruh data history sejak tahun 1901 setiap dijalankan.
    max_consecutive_existing = 20 
    
    while True:
        url = f"https://www.idx.co.id/primary/ListedCompany/GetAnnouncement?kodeEmiten=&emitenType=*&indexFrom={index_from}&pageSize={page_size}&dateFrom=19010101&dateTo={today_str}&lang=id&keyword="
        logging.info(f"Mengambil data API (indexFrom={index_from})...")
        
        max_retries = 3
        data = None
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.get(url, headers=headers, proxies=PROXIES, impersonate="chrome", timeout=30)
                if response.status_code == 403 and attempt < max_retries:
                    logging.warning(f"Kena 403 Forbidden saat memanggil API. Retry {attempt}/{max_retries} dalam 3 detik...")
                    time.sleep(3)
                    continue
                    
                response.raise_for_status()
                data = response.json()
                break # Sukses, keluar dari loop retry
            except Exception as e:
                logging.error(f"Gagal memanggil API: {e}")
                break
                
        if data is None:
            # Gagal mendapatkan data setelah retry (atau gagal karena error lain)
            break
            
        replies = data.get('Replies', [])
        if not replies:
            logging.info("Tidak ada data lagi dari API.")
            break
            
        for reply in replies:
            pengumuman = reply.get('pengumuman', {})
            pengumuman_id = pengumuman.get('Id2')
            kode_emiten = pengumuman.get('Kode_Emiten', 'UNKNOWN').strip()
            attachments = reply.get('attachments', [])
            
            for att in attachments:
                file_url = att.get('FullSavePath')
                original_filename = att.get('OriginalFilename', '')
                
                if not file_url:
                    continue
                    
                lower_name = original_filename.lower()
                # Filter hanya file PDF dan XLSX
                if not (lower_name.endswith('.pdf') or lower_name.endswith('.xlsx')):
                    continue
                    
                if is_downloaded(file_url):
                    # File sudah pernah didownload
                    consecutive_existing += 1
                    continue
                else:
                    consecutive_existing = 0
                    
                    # Bersihkan karakter aneh pada nama file jika ada
                    safe_filename = "".join([c for c in original_filename if c.isalpha() or c.isdigit() or c in ' ._-()[]']).rstrip()
                    
                    if download_file(file_url, safe_filename, kode_emiten):
                        mark_downloaded(file_url, pengumuman_id, safe_filename)
                        time.sleep(1) # Delay sopan supaya tidak diblokir server
        
        if consecutive_existing >= max_consecutive_existing:
            logging.info(f"Ditemukan {consecutive_existing} file yang sudah pernah didownload berturut-turut.")
            logging.info("Diasumsikan sudah mengejar seluruh file terbaru. Berhenti untuk siklus ini.")
            break
            
        index_from += page_size
        time.sleep(2) # Delay sopan antar halaman API
        
    logging.info("Proses scraping selesai.")

if __name__ == "__main__":
    scrape_idx()
