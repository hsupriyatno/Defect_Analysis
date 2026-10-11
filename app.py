import pandas as pd
import streamlit as st
import re
import io
import html
import streamlit as st
import requests
import json

# ... modul import lainnya ...
from datetime import datetime
from google import genai

# Import modul ReportLab untuk PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ---------------------------------------------------------
# KONFIGURASI HALAMAN
# ---------------------------------------------------------
st.set_page_config(
    page_title="AIRFAST Indonesia | Defect & Reliability Analyzer",
    page_icon="✈️",
    layout="wide"
)

# ---------------------------------------------------------
# SIDEBAR - INPUT API KEY GEMINI
# ---------------------------------------------------------
st.sidebar.title("⚙️ Pengaturan API")
api_key_input = st.sidebar.text_input(
    "Masukkan Gemini API Key:",
    type="password",
    help="Dapatkan API Key gratis di https://aistudio.google.com/app/apikey"
)

st.sidebar.markdown("---")
st.sidebar.info(
    "**AIRFAST Indonesia - Reliability Module**\n\n"
    "Aplikasi ini menganalisis defect historis armada pesawat "
    "dan menghasilkan laporan rekayasa keandalan berbasis AI."
)

# ---------------------------------------------------------
# FUNGSI PEMANGGILAN GEMINI API (SDK RESMI)
# ---------------------------------------------------------
def call_gemini_api(prompt_text, user_api_key=""):
    """
    Memanggil Gemini API via REST HTTP Request menggunakan model gemini-3.6-flash.
    """
    # 1. Ambil API Key dari input sidebar atau Streamlit Secrets
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

    # 2. Endpoint REST API Gemini (Menggunakan model gemini-3.6-flash)
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    
    # 3. Header autentikasi menggunakan x-goog-api-key
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

    # 4. Eksekusi Request
    response = requests.post(url, headers=headers, json=payload, timeout=60)
    
    if response.status_code != 200:
        raise ValueError(f"Error {response.status_code}: {response.text}")
        
    res_json = response.json()
    
    try:
        return res_json['candidates'][0]['content']['parts'][0]['text']
    except (KeyError, IndexError):
        raise ValueError(f"Respon tidak sesuai format: {res_json}")
# ---------------------------------------------------------
# MEMBACA DAN MEMFILTER DATABASE EXCEL (HANYA UNSCHEDULED)
# ---------------------------------------------------------
@st.cache_data
def load_defect_db_by_sheet(sheet_name):
    try:
        df = pd.read_excel("defect_database.xlsx", sheet_name=sheet_name)
        
        if not df.empty:
            # Mengubah setiap sel menjadi string dengan aman untuk menghindari error tipe data float/NaN
            combined_text = df.astype(object).fillna('').astype(str).agg(' '.join, axis=1).str.upper()
            
            # Pola kata kunci / format Scheduled Maintenance yang ingin DIKELUARKAN (EXCLUDE)
            scheduled_pattern = r'(WO|OCK-WO|OCL-WO|DUE\s*LIST|ROUTINE|INSPECTION|COMPANY\s*CONVENIENCE)'
            
            mask_scheduled = combined_text.str.contains(scheduled_pattern, regex=True, na=False)
            df_unscheduled = df[~mask_scheduled].copy()
            
            return df_unscheduled
            
        return df
    except Exception as e:
        st.error(f"Gagal membaca sheet '{sheet_name}'. Detail error: {str(e)}")
        return pd.DataFrame()

# ---------------------------------------------------------
# FUNGSI PENGOLAH TEKS & FISHBONE DIAGRAM UNTUK PDF
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
# FUNGSI GENERATE LAPORAN PDF (4 BAGIAN UTAMA + TABEL HISTORI)
# ---------------------------------------------------------
def generate_pdf_report(ac_type, kasus_baru, selected_ata, ai_sys_text, ai_rca_text, ai_rec_text, df_history, lang="id"):
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
            "title": "LAPORAN EVALUASI KEANDALAN TEKNIS",
            "sec1": "1. INFORMASI DEFECT YANG DILAPORKAN",
            "sec2": "2. SYSTEM DESCRIPTION & OPERATIONAL PRINCIPLES",
            "sec3": "3. REPETITIVE DEFECT ANALYSIS & ROOT CAUSE ANALYSIS (RCA)",
            "sec4": "4. CORRECTIVE & PREVENTIVE RECOMMENDATIONS",
            "sec5": f"5. HISTORICAL RECORD (DATABASE UNSCHEDULED: {len(df_history)} Record Ditemukan)",
            "ac_label": "Aircraft Type:",
            "defect_label": "Defect Dilaporkan:",
            "ata_label": "ATA Chapter:",
            "no_match": "Tidak ada riwayat defect yang cocok."
        },
        "en": {
            "title": "TECHNICAL RELIABILITY EVALUATION REPORT",
            "sec1": "1. REPORTED DEFECT INFORMATION",
            "sec2": "2. SYSTEM DESCRIPTION & OPERATIONAL PRINCIPLES",
            "sec3": "3. REPETITIVE DEFECT ANALYSIS & ROOT CAUSE ANALYSIS (RCA)",
            "sec4": "4. CORRECTIVE & PREVENTIVE RECOMMENDATIONS",
            "sec5": f"5. HISTORICAL RECORD (UNSCHEDULED DATABASE: {len(df_history)} Records Found)",
            "ac_label": "Aircraft Type:",
            "defect_label": "Reported Defect:",
            "ata_label": "ATA Chapter:",
            "no_match": "No historical records matched."
        }
    }[lang]

    title_style = ParagraphStyle(
        'DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=15, leading=18,
        textColor=colors.HexColor('#1E3A8A'), spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11,
        textColor=colors.HexColor('#4B5563'), spaceAfter=10
    )
    h2_style = ParagraphStyle(
        'SectionHeader', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=11, leading=14,
        textColor=colors.HexColor('#1E3A8A'), spaceBefore=12, spaceAfter=6
    )
    body_style = ParagraphStyle(
        'BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=14,
        textColor=colors.HexColor('#1F2937')
    )
    table_cell_style = ParagraphStyle(
        'TableCell', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12,
        textColor=colors.HexColor('#1F2937')
    )
    table_header_style = ParagraphStyle(
        'TableHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=9, leading=12,
        textColor=colors.white
    )

    # Header Dokumen
    story.append(Paragraph(labels["title"], title_style))
    story.append(Paragraph(f"Generated on: {datetime.now().strftime('%d %B %Y, %H:%M WIB')} | System: AIRFAST Indonesia - Defect Analyzer", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1E3A8A'), spaceAfter=10))

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
        ('PADDING', (0,0), (-1,-1), 5),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E5E7EB'))
    ]))
    story.append(t_info)
    story.append(Spacer(1, 8))

    # 2. System Description
    story.append(Paragraph(labels["sec2"], h2_style))
    story.extend(process_ai_text_for_pdf(ai_sys_text, styles))
    story.append(Spacer(1, 8))

    # 3. Repetitive Defect & RCA (Bold Biru)
    story.append(Paragraph(labels["sec3"], h2_style))
    story.extend(process_ai_text_for_pdf(ai_rca_text, styles))
    story.append(Spacer(1, 8))

    # 4. Corrective & Preventive Recommendations (Bold Biru)
    story.append(Paragraph(labels["sec4"], h2_style))
    story.extend(process_ai_text_for_pdf(ai_rec_text, styles))
    story.append(Spacer(1, 10))

    # 5. Historical Record (Tabel Database Unscheduled)
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
        
        for _, row in df_pdf.head(10).iterrows():
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

        t_hist = Table(table_data, colWidths=[75, 80, 200, 201])
        t_hist.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
            ('PADDING', (0,0), (-1,-1), 5),
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
        placeholder="Contoh: 33"
    )

kasus_baru = st.text_area(
    "Masukkan Deskripsi Defect / Discrepancy Baru:",
    placeholder="Contoh: HYDRAULIC PUMP CYCLING FREQUENTLY IN FLIGHT"
)

if st.button("Analisis Kasus & History", type="primary"):
    if not api_key_input.strip():
        st.error("⚠️ Silakan masukkan Gemini API Key Anda pada menu Sidebar di sebelah kiri terlebih dahulu.")
    elif kasus_baru.strip():
        df_defect = load_defect_db_by_sheet(ac_type)
        
        if not df_defect.empty:
            stop_words = {
                "FOUND", "DURING", "PRE", "FLIGHT", "AFTER", "IN", "AT", "ON", "AND", 
                "THE", "TO", "OF", "WITH", "IS", "WAS", "FOR", "CHECK", "INSPECTED"
            }
            
            raw_words = re.findall(r'\b[A-Za-z0-9]+\b', kasus_baru.upper())
            keywords = [w for w in raw_words if len(w) > 2 and w not in stop_words]
            
            mask = pd.Series(True, index=df_defect.index)
            
            if keywords:
                pattern = "|".join(keywords)
                mask = mask & df_defect['Note / Report'].astype(str).str.contains(pattern, case=False, na=False)
                
            if selected_ata.strip():
                ata_clean = re.sub(r'\D', '', selected_ata)
                if 'ATA' in df_defect.columns and ata_clean:
                    mask = mask & df_defect['ATA'].astype(str).str.contains(ata_clean, na=False)

            history_match = df_defect[mask]

            if not history_match.empty:
                df_history_sorted = history_match.copy()
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
                
                context_history = df_history_sorted[kolom_tersedia].head(15).to_string(index=False)
                jumlah_match = len(df_history_sorted)
            else:
                context_history = "Tidak ditemukan riwayat defect serupa di database armada ini."
                jumlah_match = 0
                date_range_info = "N/A"
                df_history_sorted = pd.DataFrame()

            st.session_state['ac_type'] = ac_type
            st.session_state['kasus_baru'] = kasus_baru
            st.session_state['selected_ata'] = selected_ata
            st.session_state['history_match'] = df_history_sorted

            prompt = f"""
Anda adalah seorang pakar Reliability Engineering penerbangan spesialis armada {ac_type}.

TIPE PESAWAT: {ac_type}
KASUS BARU YANG DILAPORKAN:
{kasus_baru} (ATA Chapter: {selected_ata if selected_ata else 'N/A'})

DATA HISTORI DARI DATABASE UNSCHEDULED ARMADA {ac_type}:
- Total Ditemukan: {jumlah_match} record/kejadian defect serupa.
- Periode Waktu Kejadian: {date_range_info}

SAMPEL 15 RECORD TERAKHIR DARI DATABASE UNSCHEDULED ARMADA {ac_type}:
{context_history}

TUGAS ANDA:
Berikan analisis teknis lengkap yang terfokus pada sistem/komponen tipe pesawat {ac_type} dalam format berikut (dalam Bahasa Indonesia):

[BAGIAN SYSTEM]
1. SYSTEM DESCRIPTION: Jelaskan secara ringkas prinsip kerja, fungsi utama, dan arsitektur sistem/komponen terkait pada armada {ac_type} agar pembaca memiliki pemahaman awal yang baik.

[BAGIAN RCA]
2. REPETITIVE DEFECT & RCA: Analisis indikasi repetitive defect (berdasarkan {jumlah_match} kejadian) dan Root Cause Analysis (RCA) spesifik untuk {ac_type}:
   - Sertakan Diagram Fishbone / Ishikawa sederhana di dalam block code (menggunakan tanda triple backtick ``` ).
   - Gunakan karakter ASCII standar (huruf, spasi, hyphens, plus, pipa).

[BAGIAN REKOMENDASI]
3. CORRECTIVE & PREVENTIVE RECOMMENDATIONS: Berikan langkah troubleshooting praktis, Corrective Action (IAW AMM/WDM {ac_type}), serta **Operational Special Test / Test Flight Monitoring** yang ketat sebelum status observasi maintenance dilepas.

[BAGIAN ENGLISH]
Provide the exact same technical analysis translated into professional aviation engineering English specifically for {ac_type}, maintaining the exact same structured sections ([BAGIAN SYSTEM], [BAGIAN RCA], [BAGIAN REKOMENDASI]).
"""

            with st.spinner(f"Menganalisis histori armada {ac_type} dan menyusun rekomendasi via Gemini..."):
                try:
                    full_text = call_gemini_api(prompt, api_key_input)
                except Exception as e:
                    st.error(f"⚠️ **Gagal terhubung ke Gemini API:** {e}")
                    full_text = ""

if full_text:
                    # Pisahkan Bahasa Indonesia dan Bahasa Inggris terlebih dahulu
                    if "[BAGIAN ENGLISH]" in full_text:
                        id_part = full_text.split("[BAGIAN ENGLISH]")[0]
                        en_part = full_text.split("[BAGIAN ENGLISH]")[1]
                    else:
                        id_part = full_text
                        en_part = full_text

                    # Parsing Sub-bagian Bahasa Indonesia
                    text_id_sys = ""
                    text_id_rca = ""
                    text_id_rec = ""
                    
                    # Parsing Sub-bagian Bahasa Inggris
                    text_en_sys = ""
                    text_en_rca = ""
                    text_en_rec = ""

                    try:
                        # Split ID parts
                        parts_id = id_part.split("[BAGIAN RCA]")
                        text_id_sys = parts_id[0].replace("[BAGIAN SYSTEM]", "").strip()
                        sub_id = parts_id[1].split("[BAGIAN REKOMENDASI]")
                        text_id_rca = sub_id[0].strip()
                        text_id_rec = sub_id[1].strip()

                        # Split EN parts
                        parts_en = en_part.split("[BAGIAN RCA]")
                        text_en_sys = parts_en[0].replace("[BAGIAN SYSTEM]", "").strip()
                        sub_en = parts_en[1].split("[BAGIAN REKOMENDASI]")
                        text_en_rca = sub_en[0].strip()
                        text_en_rec = sub_en[1].strip()
                    except Exception:
                        # Fallback jika format meleset
                        text_id_sys = id_part
                        text_en_sys = en_part

                    # Simpan ke session state
                    st.session_state['ai_sys_id'] = text_id_sys
                    st.session_state['ai_rca_id'] = text_id_rca
                    st.session_state['ai_rec_id'] = text_id_rec
                    
                    st.session_state['ai_sys_en'] = text_en_sys
                    st.session_state['ai_rca_en'] = text_en_rca
                    st.session_state['ai_rec_en'] = text_en_rec
    else:
        st.warning("Mohon masukkan deskripsi defect terlebih dahulu.")

# ---------------------------------------------------------
# HASIL DAN DOWNLOAD REPORT
# ---------------------------------------------------------
if 'ai_result_id' in st.session_state:
    st.subheader(f"📋 Hasil Analisis AI ({st.session_state['ac_type']})")
    st.write(st.session_state['ai_result_id'])
    
    st.divider()
    
    col_pdf1, col_pdf2 = st.columns([2, 2])
    with col_pdf1:
        pdf_lang = st.radio(
            "🌐 Pilih Bahasa Laporan PDF:",
            options=["Bahasa Indonesia", "English"],
            horizontal=True
        )
    
    lang_code = "id" if pdf_lang == "Bahasa Indonesia" else "en"
    selected_ai_text = st.session_state['ai_result_id'] if lang_code == "id" else st.session_state['ai_result_en']
    
    pdf_data = generate_pdf_report(
        st.session_state['ac_type'],
        st.session_state['kasus_baru'],
        st.session_state['selected_ata'],
        selected_ai_text,
        st.session_state['history_match'],
        lang=lang_code
    )
    
    with col_pdf2:
        st.write("")
        st.download_button(
            label=f"📥 Download Laporan ({pdf_lang})",
            data=pdf_data,
            file_name=f"Reliability_Report_{st.session_state['ac_type']}_{lang_code.upper()}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            type="primary"
        )
    
    st.subheader(f"🔍 Riwayat Defect Terkait - Database {st.session_state['ac_type']} ({len(st.session_state['history_match'])} Record Ditemukan):")
    st.dataframe(st.session_state['history_match'], use_container_width=True)
