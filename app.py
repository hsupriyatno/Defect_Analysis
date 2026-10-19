import pandas as pd
import streamlit as st
import re
import io
import html
import requests
import json
import os
from datetime import datetime

# Import modul ReportLab untuk PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ---------------------------------------------------------
# KONFIGURASI HALAMAN & CUSTOM CSS UNTUK MENAIKKAN LOGO
# ---------------------------------------------------------
st.set_page_config(
    page_title="AIRFAST Indonesia | Defect & Reliability Analyzer",
    page_icon="✈️",
    layout="wide"
)

# Custom CSS untuk menghilangkan padding atas pada sidebar agar logo naik ke paling atas
st.markdown(
    """
    <style>
        /* Mengurangi padding atas area sidebar secara keseluruhan */
        [data-testid="stSidebarContent"] {
            padding-top: 1rem !important;
        }
        /* Menghilangkan margin atas pada blok pertama di sidebar */
        [data-testid="stSidebarUserContent"] {
            padding-top: 0rem !important;
        }
    </style>
    """,
    unsafe_allow_html=True
)

# ---------------------------------------------------------
# SISTEM PENYIMPANAN DAN BACA LAPORAN LOKAL (DAN KEY)
# ---------------------------------------------------------
REPORT_STORAGE_FILE = "saved_reports.json"

def load_saved_reports():
    if os.path.exists(REPORT_STORAGE_FILE):
        try:
            with open(REPORT_STORAGE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_report_to_disk(report_key, report_data):
    reports = load_saved_reports()
    reports[report_key] = report_data
    with open(REPORT_STORAGE_FILE, "w") as f:
        json.dump(reports, f, indent=4)

def get_next_dan_key(ac_type, defect_text):
    reports = load_saved_reports()
    next_num = len(reports) + 1
    dan_id = f"DAN{next_num:04d}"
    
    short_desc = defect_text[:25] + "..." if len(defect_text) > 25 else defect_text
    display_name = f"{dan_id} | {ac_type} | {short_desc}"
    
    while display_name in reports:
        next_num += 1
        dan_id = f"DAN{next_num:04d}"
        display_name = f"{dan_id} | {ac_type} | {short_desc}"
        
    return display_name, dan_id

# ---------------------------------------------------------
# SIDEBAR - LOGO LOKAL & PENGATURAN API KEY
# ---------------------------------------------------------
# 1. TAMPILKAN LOGO AIRFAST DI PALING ATAS SIDEBAR
if os.path.exists("logo.png"):
    st.sidebar.image("logo.png", use_container_width=True)

# 2. PENGATURAN API KEY (DI BAWAH LOGO)
st.sidebar.title("⚙️ Pengaturan API")
api_key_input = st.sidebar.text_input(
    "Masukkan Gemini API Key:",
    type="password",
    help="Dapatkan API Key gratis di https://aistudio.google.com/app/apikey"
)

st.sidebar.markdown("---")
st.sidebar.subheader("📂 Riwayat Analisis Tersimpan")
saved_reports = load_saved_reports()

if saved_reports:
    selected_saved_key = st.sidebar.selectbox(
        "Pilih Laporan Sebelumnya:",
        options=["-- Buat Analisis Baru --"] + list(saved_reports.keys())
    )
    
    if selected_saved_key != "-- Buat Analisis Baru --":
        if st.sidebar.button("Muat Laporan Ini"):
            data = saved_reports[selected_saved_key]
            st.session_state['ac_type'] = data['ac_type']
            st.session_state['kasus_baru'] = data['kasus_baru']
            st.session_state['selected_ata'] = data['selected_ata']
            st.session_state['current_dan_key'] = selected_saved_key
            st.session_state['history_match'] = pd.DataFrame(data['history_match'])
            
            # Memuat array iterations secara utuh (dengan fallback jika data lama)
            if 'iterations' in data:
                st.session_state['iterations'] = data['iterations']
            else:
                st.session_state['iterations'] = [{
                    "iteration_index": 0,
                    "title": "Analisis Awal",
                    "timestamp": "N/A",
                    "feedback": "",
                    "ai_sys_id": data.get('ai_sys_id', ''),
                    "ai_rca_id": data.get('ai_rca_id', ''),
                    "ai_rec_id": data.get('ai_rec_id', ''),
                    "ai_sys_en": data.get('ai_sys_en', ''),
                    "ai_rca_en": data.get('ai_rca_en', ''),
                    "ai_rec_en": data.get('ai_rec_en', '')
                }]
            st.rerun()
else:
    st.sidebar.info("Belum ada laporan tersimpan.")

st.sidebar.markdown("---")
st.sidebar.info(
    "**AIRFAST Indonesia - Reliability Module**\n\n"
    "Aplikasi ini menganalisis defect historis armada pesawat "
    "dan menghasilkan laporan rekayasa keandalan berbasis AI."
)

# ---------------------------------------------------------
# FUNGSI PEMANGGILAN GEMINI API
# ---------------------------------------------------------
def call_gemini_api(prompt_text, user_api_key=""):
    api_key = user_api_key.strip() if user_api_key else ""
    
    if not api_key:
        try:
            if "GEMINI_API_KEY" in st.secrets:
                api_key = st.secrets["GEMINI_API_KEY"]
        except Exception:
            pass

    if not api_key:
        raise ValueError("API Key tidak ditemukan. Silakan masukkan API Key di sidebar atau konfigurasi Streamlit Secrets.")

    clean_api_key = str(api_key).strip().strip('"').strip("'")
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": clean_api_key
    }
    
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt_text}
                ]
            }
        ]
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    if response.status_code != 200:
        raise ValueError(f"Error {response.status_code}: {response.text}")
        
    res_json = response.json()
    try:
        return res_json['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError):
        raise ValueError(f"Respon tidak sesuai format: {res_json}")

# ---------------------------------------------------------
# MEMBACA DAN MEMFILTER DATABASE EXCEL
# ---------------------------------------------------------
@st.cache_data
def load_defect_db_by_sheet(sheet_name):
    try:
        df = pd.read_excel("defect_database.xlsx", sheet_name=sheet_name)
        if not df.empty:
            combined_text = df.astype(object).fillna('').astype(str).agg(' '.join, axis=1).str.upper()
            scheduled_pattern = r'(WO|OCK-WO|OCL-WO|DUE\s*LIST|ROUTINE|INSPECTION|COMPANY\s*CONVENIENCE)'
            
            mask_scheduled = combined_text.str.contains(scheduled_pattern, regex=True, na=False)
            df_unscheduled = df[~mask_scheduled].copy()
            return df_unscheduled
        return df
    except Exception as e:
        st.error(f"Gagal membaca sheet '{sheet_name}'. Detail error: {str(e)}")
        return pd.DataFrame()

# ---------------------------------------------------------
# FUNGSI PENGOLAH TEKS UNTUK PDF
# ---------------------------------------------------------
def process_ai_text_for_pdf(text, styles):
    story_elements = []
    
    body_style = ParagraphStyle(
        'BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14,
        textColor=colors.HexColor('#1F2937')
    )
    
    code_style = ParagraphStyle(
        'FishboneCode', parent=styles['Normal'], fontName='Courier', fontSize=8, leading=10,
        textColor=colors.HexColor('#1E3A8A'), backColor=colors.HexColor('#F3F4F6'),
        borderPadding=6, spaceBefore=6, spaceAfter=8
    )

    parts = text.split("```")
    
    for i, part in enumerate(parts):
        if not part.strip():
            continue
            
        if i % 2 == 1:
            clean_code = re.sub(r'^(text|ascii|markdown)\n', '', part.strip(), flags=re.IGNORECASE)
            clean_code = html.escape(clean_code).replace('\n', '<br/>').replace(' ', '&nbsp;')
            story_elements.append(Paragraph(clean_code, code_style))
        else:
            clean_text = html.escape(part)
            clean_text = clean_text.replace('\n', '<br/>')
            clean_text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', clean_text)
            clean_text = re.sub(r'#{1,6}\s*', '', clean_text)
            clean_text = clean_text.replace('---', '')
            story_elements.append(Paragraph(clean_text, body_style))
            
    return story_elements

# ---------------------------------------------------------
# FUNGSI GENERATE LAPORAN PDF (MENAMPUNG SEMUA ITERASI)
# ---------------------------------------------------------
def generate_pdf_report(ac_type, kasus_baru, selected_ata, iterations_list, df_history, lang="id"):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=28, leftMargin=28, topMargin=28, bottomMargin=28
    )
    story = []
    styles = getSampleStyleSheet()

    labels = {
        "id": {
            "title": "LAPORAN EVALUASI KEANDALAN TEKNIS & REKAM JEJAK INVESTIGASI",
            "sec1": "1. INFORMASI DEFECT YANG DILAPORKAN",
            "sec5": f"5. HISTORICAL RECORD (DATABASE UNSCHEDULED: {len(df_history)} Record Ditemukan)",
            "ac_label": "Aircraft Type:",
            "defect_label": "Defect Dilaporkan:",
            "ata_label": "ATA Chapter:",
            "no_match": "Tidak ada riwayat defect yang cocok."
        },
        "en": {
            "title": "TECHNICAL RELIABILITY EVALUATION & INVESTIGATION AUDIT TRAIL",
            "sec1": "1. REPORTED DEFECT INFORMATION",
            "sec5": f"5. HISTORICAL RECORD (UNSCHEDULED DATABASE: {len(df_history)} Records Found)",
            "ac_label": "Aircraft Type:",
            "defect_label": "Reported Defect:",
            "ata_label": "ATA Chapter:",
            "no_match": "No historical records matched."
        }
    }[lang]

    title_style = ParagraphStyle(
        'DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=14, leading=17,
        textColor=colors.HexColor('#1E3A8A'), spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11,
        textColor=colors.HexColor('#4B5563'), spaceAfter=10
    )
    h2_style = ParagraphStyle(
        'SectionHeader', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10.5, leading=13,
        textColor=colors.HexColor('#1E3A8A'), spaceBefore=10, spaceAfter=5
    )
    body_style = ParagraphStyle(
        'BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=9.5, leading=13,
        textColor=colors.HexColor('#1F2937')
    )
    table_cell_style = ParagraphStyle(
        'TableCell', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11,
        textColor=colors.HexColor('#1F2937')
    )
    table_header_style = ParagraphStyle(
        'TableHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8.5, leading=11,
        textColor=colors.white
    )

    story.append(Paragraph(labels["title"], title_style))
    story.append(Paragraph(f"Generated on: {datetime.now().strftime('%d %B %Y, %H:%M WIB')} | System: AIRFAST Indonesia - Defect Analyzer", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1E3A8A'), spaceAfter=8))

    # 1. Informasi Defect
    story.append(Paragraph(labels["sec1"], h2_style))
    info_data = [
        [Paragraph(f"<b>{labels['ac_label']}</b>", body_style), Paragraph(html.escape(ac_type), body_style)],
        [Paragraph(f"<b>{labels['defect_label']}</b>", body_style), Paragraph(html.escape(kasus_baru), body_style)],
        [Paragraph(f"<b>{labels['ata_label']}</b>", body_style), Paragraph(html.escape(selected_ata) if selected_ata else "N/A", body_style)]
    ]
    t_info = Table(info_data, colWidths=[130, 426])
    t_info.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F3F4F6')),
        ('PADDING', (0,0), (-1,-1), 4),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E5E7EB'))
    ]))
    story.append(t_info)
    story.append(Spacer(1, 6))

    # Perulangan untuk mencetak semua iterasi ke dalam PDF
    for iter_data in iterations_list:
        idx = iter_data['iteration_index']
        
        if idx == 0:
            story.append(Paragraph("<b>TAHAP 1: ANALISIS KELAYAKAN & RCA AWAL</b>", h2_style))
        else:
            story.append(Spacer(1, 4))
            story.append(Paragraph(f"<b>TAHAP {idx + 1}: {html.escape(iter_data.get('title', ''))}</b>", h2_style))
            if iter_data.get('feedback'):
                story.append(Paragraph(f"<i>Feedback Lapangan: {html.escape(iter_data.get('feedback'))}</i>", body_style))

        # System Description
        story.append(Paragraph("<b>System Description & Operational Principles:</b>", ParagraphStyle('SubSec', parent=body_style, fontName='Helvetica-Bold', textColor=colors.HexColor('#374151'))))
        sys_text = iter_data.get('ai_sys_id', '') if lang == 'id' else iter_data.get('ai_sys_en', '')
        story.extend(process_ai_text_for_pdf(sys_text, styles))
        
        # RCA
        story.append(Paragraph("<b>Repetitive Defect Analysis & Root Cause Analysis (RCA):</b>", ParagraphStyle('SubSec2', parent=body_style, fontName='Helvetica-Bold', textColor=colors.HexColor('#374151'))))
        rca_text = iter_data.get('ai_rca_id', '') if lang == 'id' else iter_data.get('ai_rca_en', '')
        story.extend(process_ai_text_for_pdf(rca_text, styles))
        
        # Recommendations
        story.append(Paragraph("<b>Corrective & Preventive Recommendations:</b>", ParagraphStyle('SubSec3', parent=body_style, fontName='Helvetica-Bold', textColor=colors.HexColor('#374151'))))
        rec_text = iter_data.get('ai_rec_id', '') if lang == 'id' else iter_data.get('ai_rec_en', '')
        story.extend(process_ai_text_for_pdf(rec_text, styles))
        story.append(Spacer(1, 6))

    # 5. Historical Record Tabel
    story.append(Paragraph(labels["sec5"], h2_style))
    if not df_history.empty:
        df_pdf = df_history.copy()
        if 'Date_Parsed' in df_pdf.columns:
            df_pdf = df_pdf.sort_values(by='Date_Parsed', ascending=False)
        elif 'Date' in df_pdf.columns:
            df_pdf['Date_Temp'] = pd.to_datetime(df_pdf['Date'], errors='coerce')
            df_pdf = df_pdf.sort_values(by='Date_Temp', ascending=False)

        kolom_pdf = ['Date', 'AML No', 'Note / Report', 'Corrective Action']
        kolom_tersedia = [c for c in kolom_pdf if c in df_pdf.columns]
        
        table_data = [[Paragraph(f"<b>{html.escape(col)}</b>", table_header_style) for col in kolom_tersedia]]
        
        for _, row in df_pdf.head(15).iterrows():
            row_cells = []
            for col in kolom_tersedia:
                val = row[col]
                if col == 'Date' and pd.notna(val):
                    try:
                        val = pd.to_datetime(val).strftime('%Y-%m-%d')
                    except Exception:
                        val = str(val).split()[0]
                else:
                    val = str(val) if pd.notna(val) else "-"
                row_cells.append(Paragraph(html.escape(val), table_cell_style))
            table_data.append(row_cells)

        t_hist = Table(table_data, colWidths=[70, 80, 203, 203])
        t_hist.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
            ('PADDING', (0,0), (-1,-1), 4),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#D1D5DB')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F9FAFB')])
        ]))
        story.append(t_hist)
    else:
        story.append(Paragraph(labels["no_match"], body_style))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ---------------------------------------------------------
# ANTARMUKA UTAMA
# ---------------------------------------------------------
st.title("🛠️ Defect & Reliability Analyzer")
st.write("Sistem Analisis Rekayasa Keandalan Penerbangan Berbasis Histori Maintenance & Gemini AI.")

col_ac, col_ata = st.columns([2, 1])

with col_ac:
    ac_type = st.selectbox(
        "✈️ Pilih Armada / Tipe Pesawat:",
        options=["B737-8", "DHC6-300", "DHC6-400", "BELL-412", "AS350B3", "MIL-171"],
        help="Sistem akan otomatis mencocokkan data dari sheet Excel sesuai tipe ini."
    )

with col_ata:
    selected_ata = st.text_input(
        "ATA Chapter (Opsional):",
        placeholder="Contoh: 25"
    )

kasus_baru = st.text_area(
    "Masukkan Deskripsi Defect / Discrepancy Baru:",
    placeholder="Contoh: LAVATORY DOOR SPRING BROKEN"
)

if st.button("Analisis Kasus & History", type="primary"):
    if not api_key_input.strip():
        st.error("⚠️ Silakan masukkan Gemini API Key Anda pada menu Sidebar di sebelah kiri terlebih dahulu.")
    elif kasus_baru.strip():
        df_defect = load_defect_db_by_sheet(ac_type)
        
        if not df_defect.empty:
            text_upper = kasus_baru.upper()
            
            # Daftar kata umum / stop words yang diabaikan agar fokus pada inti kerusakan
            stop_words = {
                "FOUND", "DURING", "PRE", "FLIGHT", "AFTER", "BEFORE", "IN", "AT", "ON", "AND", 
                "THE", "TO", "OF", "WITH", "IS", "WAS", "FOR", "CHECK", "INSPECTED", "OUR", "AIRCRAFT",
                "PLEASE", "KINDLY", "REPORTED", "BROKEN", "DAMAGED", "FAIL", "FAILED", "FAULT"
            }
            
            raw_words = re.findall(r'\b[A-Za-z0-9]+\b', text_upper)
            # Ambil semua kata kunci penting yang panjangnya > 2 huruf
            core_keywords = [w for w in raw_words if len(w) > 2 and w not in stop_words]
            
            # 1. Filter berdasarkan ATA Chapter terlebih dahulu (jika diisi)
            mask_ata = pd.Series(True, index=df_defect.index)
            if selected_ata.strip():
                ata_clean = re.sub(r'\D', '', selected_ata)
                if 'ATA' in df_defect.columns and ata_clean:
                    mask_ata = df_defect['ATA'].astype(str).str.contains(ata_clean, na=False)

            df_ata_subset = df_defect[mask_ata] if selected_ata.strip() else df_defect
            history_match = pd.DataFrame()
            
            if not df_ata_subset.empty:
                row_text = df_ata_subset.astype(object).fillna('').astype(str).agg(' '.join, axis=1).str.upper()
                
                if core_keywords:
                    # PENYARINGAN KETAT (STRICT FILTER): 
                    # Baris HARUS mengandung kata kunci utama yang spesifik (misalnya wajib ada kata 'SPRING' dan 'DOOR' jika diinput bersamaan)
                    # Kita buat mask di mana setiap baris harus memenuhi minimal kata kunci komponen inti (misal: 'SPRING')
                    
                    primary_components = [w for w in core_keywords if w in ["SPRING", "CARTRIDGE", "VALVE", "PUMP", "ACTUATOR", "SENSOR", "SWITCH", "BATTERY", "LIGHT", "MIRROR", "SEAT"]]
                    
                    if primary_components:
                        # Jika ada komponen utama yang sangat spesifik, filter wajib mengandung komponen tersebut
                        primary_pattern = "|".join(primary_components)
                        mask_strict = row_text.str.contains(primary_pattern, case=False, na=False)
                        
                        # Jika user juga mengetik lokasi spesifik (misal: LAVATORY), pastikan lokasi tersebut juga ada untuk mempersempit
                        if "LAVATORY" in text_upper or "LAV" in text_upper:
                            mask_lav = row_text.str.contains(r'LAVATORY|\bLAV\b', case=False, regex=True, na=False)
                            history_match = df_ata_subset[mask_strict & mask_lav].copy()
                        else:
                            history_match = df_ata_subset[mask_strict].copy()
                    else:
                        # Fallback jika tidak ada komponen primer, gunakan seluruh core keywords dengan syarat ketat
                        pattern_all = ".*".join(core_keywords) # Urutan atau kombinasi kata
                        mask_fallback = row_text.str.contains(pattern_all, case=False, regex=True, na=False)
                        history_match = df_ata_subset[mask_fallback].copy()
                        
                        # Jika masih terlalu banyak/kosong, gunakan metode pencocokan kata minimum
                        if history_match.empty:
                            mask_any = row_text.str.contains("|".join(core_keywords), case=False, na=False)
                            history_match = df_ata_subset[mask_any].copy()

                    if not history_match.empty:
                        # Berikan skoring relevansi berdasarkan jumlah kecocokan kata kunci
                        history_match['Relevance_Score'] = history_match.astype(object).fillna('').astype(str).agg(' '.join, axis=1).apply(
                            lambda x: sum(10 for kw in core_keywords if kw in x.upper())
                        )
                        history_match = history_match.sort_values(by=['Relevance_Score', 'Date'], ascending=[False, False])
                else:
                    history_match = df_ata_subset.copy()

            if not history_match.empty:
                df_history_sorted = history_match.head(15).copy()
                df_history_sorted['Date_Parsed'] = pd.to_datetime(df_history_sorted['Date'], errors='coerce')
                df_history_sorted = df_history_sorted.sort_values(by='Date_Parsed', ascending=False)
                
                min_date = df_history_sorted['Date_Parsed'].min()
                max_date = df_history_sorted['Date_Parsed'].max()
                
                if pd.notnull(min_date) and pd.notnull(max_date):
                    start_str = min_date.strftime('%d %B %Y')
                    end_str = max_date.strftime('%d %B %Y')
                    months_diff = round((max_date - min_date).days / 30.44)
                    date_range_info = f"Rentang waktu dari {start_str} sampai {end_str} (sekitar {months_diff} bulan)."
                else:
                    date_range_info = "Rentang waktu data tidak dapat diidentifikasi."

                kolom_ringkas = ['Date', 'AML No', 'Note / Report', 'Corrective Action', 'P/N Off', 'P/N On', 'ATA']
                kolom_tersedia = [c for c in kolom_ringkas if c in df_history_sorted.columns]
                context_history = df_history_sorted[kolom_tersedia].to_string(index=False)
                jumlah_match = len(history_match)
            else:
                context_history = "Tidak ditemukan riwayat defect serupa di database armada ini."
                jumlah_match = 0
                date_range_info = "N/A"
                df_history_sorted = pd.DataFrame()

            st.session_state['ac_type'] = ac_type
            st.session_state['kasus_baru'] = kasus_baru
            st.session_state['selected_ata'] = selected_ata
            st.session_state['history_match'] = df_history_sorted

            if jumlah_match >= 5:
                selected_method_instruction = """
[METODE ANALISIS YANG WAJIB DIGUNAKAN: FISHBONE / ISHIKAWA DIAGRAM]
1. Tuliskan terlebih dahulu paragraf ringkas "REPETITIVE DEFECT ANALYSIS" yang mengevaluasi frekuensi kejadian, kecenderungan berulang, dan tren histori kerusakan komponen ini pada armada.
2. Selanjutnya, buatlah analisis Root Cause Analysis (RCA) menggunakan Diagram Fishbone / Ishikawa sederhana di dalam block code (menggunakan tanda triple backtick ``` ) dengan mengkategorikan penyebab ke dalam: Manpower, Machine/LRU, Material/Hardware, Method, dan Environment.
"""
            else:
                selected_method_instruction = """
[METODE ANALISIS YANG WAJIB DIGUNAKAN: 5 WHYS INVESTIGATION]
1. Tuliskan terlebih dahulu paragraf ringkas "REPETITIVE DEFECT ANALYSIS" yang mengevaluasi frekuensi kejadian dan status kecenderungan insiden terisolasi pada armada.
2. Selanjutnya, lakukan investigasi Root Cause Analysis (RCA) menggunakan metode penelusuran "5 Whys" (bertanya "Mengapa" sebanyak 5 tahapan secara berurutan mulai dari gejala permukaan hingga menemukan akar penyebab tunggal yang paling mendalam).
"""

            prompt = f"""
Anda adalah seorang pakar Reliability Engineering penerbangan spesialis armada {ac_type}.

TIPE PESAWAT: {ac_type}
KASUS BARU YANG DILAPORKAN:
{kasus_baru} (ATA Chapter: {selected_ata if selected_ata else 'N/A'})

DATA HISTORI DARI DATABASE UNSCHEDULED ARMADA {ac_type}:
- Total Ditemukan: {jumlah_match} record/kejadian defect serupa.
- Periode Waktu Kejadian: {date_range_info}

SAMPEL RECORD TERAKHIR DARI DATABASE UNSCHEDULED ARMADA {ac_type}:
{context_history}

TUGAS ANDA:
Berikan analisis teknis lengkap dalam Bahasa Indonesia dengan format persis berikut:

[BAHASA_INDONESIA]
[SYSTEM]
Jelaskan secara ringkas prinsip kerja, fungsi utama, dan arsitektur sistem/komponen terkait pada armada {ac_type}.

[RCA]
{selected_method_instruction}

[RECOMMENDATIONS]
Berikan langkah troubleshooting praktis, Corrective Action (IAW AMM/WDM {ac_type}), serta Operational Special Test / Test Flight Monitoring.

[BAHASA_INGGRIS]
[SYSTEM]
(Provide the exact same System Description translated into professional aviation engineering English for {ac_type})

[RCA]
(Provide the exact same Repetitive Defect Analysis and RCA translated into professional aviation engineering English for {ac_type})

[RECOMMENDATIONS]
(Provide the exact same Corrective & Preventive Recommendations translated into professional aviation engineering English for {ac_type})
"""

            with st.spinner(f"Menganalisis histori armada {ac_type} dan menyusun rekomendasi via Gemini..."):
                try:
                    full_text = call_gemini_api(prompt, api_key_input)
                except Exception as e:
                    st.error(f"⚠️ **Gagal terhubung ke Gemini API:** {e}")
                    full_text = ""

                if full_text:
                    text_id_sys, text_id_rca, text_id_rec = "", "", ""
                    text_en_sys, text_en_rca, text_en_rec = "", "", ""

                    try:
                        if "[BAHASA_INGGRIS]" in full_text:
                            parts_lang = full_text.split("[BAHASA_INGGRIS]")
                            id_block = parts_lang[0]
                            en_block = parts_lang[1]
                        else:
                            id_block = full_text
                            en_block = full_text

                        if "[SYSTEM]" in id_block and "[RCA]" in id_block and "[RECOMMENDATIONS]" in id_block:
                            sys_split = id_block.split("[SYSTEM]")[1].split("[RCA]")
                            text_id_sys = sys_split[0].strip()
                            rca_split = sys_split[1].split("[RECOMMENDATIONS]")
                            text_id_rca = rca_split[0].strip()
                            text_id_rec = rca_split[1].strip()

                        if "[SYSTEM]" in en_block and "[RCA]" in en_block and "[RECOMMENDATIONS]" in en_block:
                            sys_split_en = en_block.split("[SYSTEM]")[1].split("[RCA]")
                            text_en_sys = sys_split_en[0].strip()
                            rca_split_en = sys_split_en[1].split("[RECOMMENDATIONS]")
                            text_en_rca = rca_split_en[0].strip()
                            text_en_rec = rca_split_en[1].strip()

                    except Exception as parse_err:
                        text_id_sys = full_text
                        text_en_sys = full_text

                    # Simpan Analisis Awal sebagai Iterasi Ke-0
                    initial_iteration = {
                        "iteration_index": 0,
                        "title": "Analisis Awal (Initial Reliability Evaluation)",
                        "timestamp": datetime.now().strftime("%d %B %Y, %H:%M WIB"),
                        "feedback": "",
                        "ai_sys_id": text_id_sys,
                        "ai_rca_id": text_id_rca,
                        "ai_rec_id": text_id_rec,
                        "ai_sys_en": text_en_sys,
                        "ai_rca_en": text_en_rca,
                        "ai_rec_en": text_en_rec
                    }

                    st.session_state['iterations'] = [initial_iteration]

                    # Simpan Laporan dengan Kunci DAN Otomatis
                    report_key_label, dan_code = get_next_dan_key(ac_type, kasus_baru)
                    st.session_state['current_dan_key'] = report_key_label
                    
                    df_history_to_save = df_history_sorted.copy()
                    for col in df_history_to_save.columns:
                        if pd.api.types.is_datetime64_any_dtype(df_history_to_save[col]) or 'date' in col.lower():
                            df_history_to_save[col] = df_history_to_save[col].astype(str).replace('NaT', '')

                    report_payload = {
                        "dan_number": dan_code,
                        "ac_type": ac_type,
                        "kasus_baru": kasus_baru,
                        "selected_ata": selected_ata,
                        "iterations": [initial_iteration],
                        "history_match": df_history_to_save.to_dict(orient="records") if not df_history_sorted.empty else []
                    }
                    save_report_to_disk(report_key_label, report_payload)

# ---------------------------------------------------------
# HASIL TAMPILAN, DOWNLOAD PDF, & HISTORICAL RECORD
# ---------------------------------------------------------
if 'iterations' in st.session_state and st.session_state['iterations']:
    st.subheader(f"📋 Rekam Jejak Analisis & Evaluasi Keandalan ({st.session_state.get('ac_type')})")
    
    st.markdown("### 1. INFORMASI DEFECT YANG DILAPORKAN")
    st.info(f"**Aircraft Type:** {st.session_state.get('ac_type')}\n\n**ATA Chapter:** {st.session_state.get('selected_ata', 'N/A')}\n\n**Defect / Kasus Dilaporkan:**\n{st.session_state.get('kasus_baru')}")
    
    # PERLOOPINGAN SELURUH ITERASI (AWAL HINGGA TERAKHIR) SECARA KRONOLOGIS
    for iter_data in st.session_state['iterations']:
        idx = iter_data['iteration_index']
        
        if idx == 0:
            st.markdown("---")
            st.markdown("## 🔹 TAHAP 1: ANALISIS KELAYAKAN & RCA AWAL")
            st.caption(f"Waktu Analisis: {iter_data.get('timestamp', 'N/A')}")
        else:
            st.markdown("---")
            st.markdown(f"## 🔄 TAHAP {idx + 1}: {iter_data.get('title', f'Iterasi Ke-{idx}')}")
            st.caption(f"Waktu Update: {iter_data.get('timestamp', 'N/A')}")
            st.warning(f"**Feedback / Temuan Lapangan Engineer:**\n{iter_data.get('feedback')}")

        st.markdown("### 2. SYSTEM DESCRIPTION & OPERATIONAL PRINCIPLES")
        st.write(iter_data.get('ai_sys_id', ''))
        
        st.markdown("### 3. REPETITIVE DEFECT ANALYSIS & ROOT CAUSE ANALYSIS (RCA)")
        st.write(iter_data.get('ai_rca_id', ''))
        
        st.markdown("### 4. CORRECTIVE & PREVENTIVE RECOMMENDATIONS")
        st.write(iter_data.get('ai_rec_id', ''))
    
    st.divider()

    # --- FITUR FEEDBACK LAPANGAN & DEEP-DIVE RCA ---
    with st.expander("🛠️ Tambahkan Hasil Eksekusi & Feedback Engineer (Analisis Lanjutan / Iterasi Baru)"):
        feedback_baru = st.text_area(
            "Masukkan hasil tindakan / temuan baru setelah rekomendasi dijalankan:",
            placeholder="Contoh: Pegas sudah diganti IAW AMM, namun setelah 3 hari posisi door kembali loose."
        )
        
        if st.button("Jalankan Analisis Lanjutan (Deep-Dive RCA)", type="secondary"):
            if feedback_baru.strip():
                current_iterations = st.session_state.get('iterations', [])
                iter_num = len(current_iterations)

                combined_kasus_terkini = f"Defect Awal: {st.session_state.get('kasus_baru')} \n\nFeedback Lapangan Iterasi Ke-{iter_num}: {feedback_baru}"
                
                with st.spinner("Gemini sedang memproses analisis lanjutan berdasarkan feedback lapangan..."):
                    try:
                        iterative_prompt = f"""
                        Anda adalah pakar Senior Reliability Engineering penerbangan untuk armada {st.session_state.get('ac_type')}.
                        
                        KASUS & FEEDBACK INVESTIGASI LANJUTAN:
                        {combined_kasus_terkini}
                        
                        TUGAS ANDA:
                        Berikan analisis lanjutan yang lebih mendalam (Deep-Dive RCA) untuk Iterasi ke-{iter_num}. Sajikan dalam Bahasa Indonesia dengan format persis berikut:

                        [BAHASA_INDONESIA]
                        [SYSTEM]
                        Evaluasi ulang performa atau titik kritis pada sistem terkait berdasarkan feedback lapangan terkini.

                        [RCA]
                        1. Berikan paragraf evaluasi REPETITIVE DEFECT ANALYSIS terkait kendala yang berulang ini.
                        2. Lakukan penelusuran RCA 5 Whys / Fishbone mendalam untuk menemukan akar penyebab tersembunyi (hidden root cause).

                        [RECOMMENDATIONS]
                        Berikan instruksi tindakan korektif lanjutan yang lebih ketat (misalnya modifikasi teknik, inspeksi material, atau engineering evaluation).

                        [BAHASA_INGGRIS]
                        [SYSTEM]
                        (Translate system re-evaluation into professional aviation English)

                        [RCA]
                        (Translate deep-dive RCA analysis into professional aviation English)

                        [RECOMMENDATIONS]
                        (Translate advanced recommendations into professional aviation English)
                        """
                        
                        full_text_iter = call_gemini_api(iterative_prompt, api_key_input)
                        
                        if "[BAHASA_INGGRIS]" in full_text_iter:
                            parts_lang = full_text_iter.split("[BAHASA_INGGRIS]")
                            id_block = parts_lang[0]
                            en_block = parts_lang[1]
                        else:
                            id_block = full_text_iter
                            en_block = full_text_iter

                        new_sys_id, new_rca_id, new_rec_id = "", "", ""
                        new_sys_en, new_rca_en, new_rec_en = "", "", ""

                        if "[SYSTEM]" in id_block and "[RCA]" in id_block and "[RECOMMENDATIONS]" in id_block:
                            sys_split = id_block.split("[SYSTEM]")[1].split("[RCA]")
                            new_sys_id = sys_split[0].strip()
                            rca_split = sys_split[1].split("[RECOMMENDATIONS]")
                            new_rca_id = rca_split[0].strip()
                            new_rec_id = rca_split[1].strip()

                        if "[SYSTEM]" in en_block and "[RCA]" in en_block and "[RECOMMENDATIONS]" in en_block:
                            sys_split_en = en_block.split("[SYSTEM]")[1].split("[RCA]")
                            new_sys_en = sys_split_en[0].strip()
                            rca_split_en = sys_split_en[1].split("[RECOMMENDATIONS]")
                            new_rca_en = rca_split_en[0].strip()
                            new_rec_en = rca_split_en[1].strip()

                        new_iteration_obj = {
                            "iteration_index": iter_num,
                            "title": f"Deep-Dive RCA & Feedback Lapangan",
                            "timestamp": datetime.now().strftime("%d %B %Y, %H:%M WIB"),
                            "feedback": feedback_baru,
                            "ai_sys_id": new_sys_id,
                            "ai_rca_id": new_rca_id,
                            "ai_rec_id": new_rec_id,
                            "ai_sys_en": new_sys_en,
                            "ai_rca_en": new_rca_en,
                            "ai_rec_en": new_rec_en
                        }

                        current_iterations.append(new_iteration_obj)
                        st.session_state['iterations'] = current_iterations

                        # Simpan pembaruan array iterations ke file JSON
                        if 'current_dan_key' in st.session_state:
                            reports = load_saved_reports()
                            if st.session_state['current_dan_key'] in reports:
                                reports[st.session_state['current_dan_key']]['iterations'] = current_iterations
                                with open(REPORT_STORAGE_FILE, "w") as f:
                                    json.dump(reports, f, indent=4)

                        st.success(f"✅ Iterasi Ke-{iter_num} berhasil ditambahkan ke riwayat DAN ini!")
                        st.rerun()

                    except Exception as err:
                        st.error(f"Gagal memproses analisis lanjutan: {err}")

    st.divider()

    # Tombol Download PDF Report
    col_pdf1, col_pdf2 = st.columns([2, 2])
    with col_pdf1:
        pdf_lang = st.radio(
            "🌐 Pilih Bahasa Laporan PDF:",
            options=["Bahasa Indonesia", "English"],
            horizontal=True,
            key="pdf_lang_radio"
        )
    
    lang_code = "id" if pdf_lang == "Bahasa Indonesia" else "en"
    df_hist_pdf = st.session_state.get('history_match', pd.DataFrame())
    
    pdf_data = generate_pdf_report(
        st.session_state.get('ac_type', 'AIRCRAFT'),
        st.session_state.get('kasus_baru', ''),
        st.session_state.get('selected_ata', ''),
        st.session_state['iterations'],
        df_hist_pdf,
        lang=lang_code
    )
    
    with col_pdf2:
        st.write("")
        st.download_button(
            label=f"📥 Download Laporan ({pdf_lang})",
            data=pdf_data,
            file_name=f"Reliability_Report_{st.session_state.get('ac_type')}_{lang_code.upper()}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            type="primary"
        )

    # Tabel Historical Record di UI bawah
    st.markdown("---")
    st.subheader(f"5. HISTORICAL RECORD (Database Unscheduled {st.session_state.get('ac_type')} - {len(df_hist_pdf)} Record Ditemukan):")
    st.dataframe(df_hist_pdf, use_container_width=True)
