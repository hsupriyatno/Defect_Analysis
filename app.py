import pandas as pd
import streamlit as st
import requests
import json
import re
import io
import html
from datetime import datetime

# Import modul ReportLab untuk PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

st.set_page_config(page_title="AI Reliability Assistant", page_icon="✈️", layout="wide")

# ---------------------------------------------------------
# 1. API KEY & FUNGSI PEMANGGILAN GEMINI REST API
# ---------------------------------------------------------
API_KEY = "AQ.Ab8RN6IqgCP6DRtBGsftwpbfah1B22ZkJCC3olYQhM8ptHMBXA"

def call_gemini_api(prompt_text, api_key):
    """
    Memanggil Gemini REST API langsung menggunakan header x-goog-api-key 
    untuk menghindari error 401 ACCESS_TOKEN_TYPE_UNSUPPORTED pada key AQ.
    """
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key.strip()
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
    
    if response.status_code == 200:
        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise Exception(f"Struktur respon API tidak sesuai: {data}")
    else:
        raise Exception(f"HTTP {response.status_code}: {response.text}")

# ---------------------------------------------------------
# 2. MEMBACA DATABASE EXCEL BERDASARKAN SHEET
# ---------------------------------------------------------
@st.cache_data
def load_defect_db_by_sheet(sheet_name):
    try:
        df = pd.read_excel("defect_database.xlsx", sheet_name=sheet_name)
        return df
    except Exception as e:
        st.error(f"Gagal membaca sheet '{sheet_name}' pada file Excel. Pastikan nama sheet sudah sesuai.")
        return pd.DataFrame()

# ---------------------------------------------------------
# 3. FUNGSI PENGOLAH TEKS & FISHBONE DIAGRAM KHUSUS PDF
# ---------------------------------------------------------
def process_ai_text_for_pdf(text, styles):
    story_elements = []
    
    body_style = ParagraphStyle(
        'BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=11, leading=15,
        textColor=colors.HexColor('#1F2937')
    )
    
    code_style = ParagraphStyle(
        'FishboneCode', parent=styles['Normal'], fontName='Courier', fontSize=8.5, leading=11,
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
# 4. FUNGSI GENERATE PDF REPORT
# ---------------------------------------------------------
def generate_pdf_report(ac_type, kasus_baru, selected_ata, ai_response_text, df_history, lang="id"):
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
            "sec2": "2. ANALISIS REKAYASA & REKOMENDASI (AI)",
            "sec3": f"3. RIWAYAT DEFECT TERAKHIR DALAM DATABASE ({len(df_history)} Record Ditemukan)",
            "ac_label": "Aircraft Type:",
            "defect_label": "Defect Dilaporkan:",
            "ata_label": "ATA Chapter:",
            "no_match": "Tidak ada riwayat defect yang cocok."
        },
        "en": {
            "title": "TECHNICAL RELIABILITY EVALUATION REPORT",
            "sec1": "1. REPORTED DEFECT INFORMATION",
            "sec2": "2. ENGINEERING ANALYSIS & RECOMMENDATIONS",
            "sec3": f"3. LATEST HISTORICAL MATCHES IN DATABASE ({len(df_history)} Records Found)",
            "ac_label": "Aircraft Type:",
            "defect_label": "Reported Defect:",
            "ata_label": "ATA Chapter:",
            "no_match": "No historical records matched."
        }
    }[lang]

    title_style = ParagraphStyle(
        'DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=16, leading=20,
        textColor=colors.HexColor('#1E3A8A'), spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        'DocSubTitle', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12,
        textColor=colors.HexColor('#4B5563'), spaceAfter=10
    )
    h2_style = ParagraphStyle(
        'SectionHeader', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=12, leading=16,
        textColor=colors.HexColor('#1E3A8A'), spaceBefore=10, spaceAfter=6
    )
    body_style = ParagraphStyle(
        'BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=11, leading=15,
        textColor=colors.HexColor('#1F2937')
    )
    table_cell_style = ParagraphStyle(
        'TableCell', parent=styles['Normal'], fontName='Helvetica', fontSize=10, leading=13,
        textColor=colors.HexColor('#1F2937')
    )
    table_header_style = ParagraphStyle(
        'TableHeader', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, leading=13,
        textColor=colors.white
    )

    story.append(Paragraph(labels["title"], title_style))
    story.append(Paragraph(f"Generated on: {datetime.now().strftime('%d %B %Y, %H:%M WIB')} | System: Airfast Indonesia - Defect Analyzer", subtitle_style))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1E3A8A'), spaceAfter=10))

    story.append(Paragraph(labels["sec1"], h2_style))
    info_data = [
        [Paragraph(f"<b>{labels['ac_label']}</b>", body_style), Paragraph(html.escape(ac_type), body_style)],
        [Paragraph(f"<b>{labels['defect_label']}</b>", body_style), Paragraph(html.escape(kasus_baru), body_style)],
        [Paragraph(f"<b>{labels['ata_label']}</b>", body_style), Paragraph(html.escape(selected_ata) if selected_ata else "N/A", body_style)]
    ]
    t_info = Table(info_data, colWidths=[140, 416])
    t_info.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#F3F4F6')),
        ('PADDING', (0,0), (-1,-1), 6),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#E5E7EB'))
    ]))
    story.append(t_info)
    story.append(Spacer(1, 10))

    story.append(Paragraph(labels["sec2"], h2_style))
    ai_elements = process_ai_text_for_pdf(ai_response_text, styles)
    story.extend(ai_elements)
    story.append(Spacer(1, 12))

    story.append(Paragraph(labels["sec3"], h2_style))
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

        t_hist = Table(table_data, colWidths=[80, 85, 195, 196])
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
# 5. INTERFACE STREAMLIT
# ---------------------------------------------------------
st.title("🛠️ Reliability & Defect Analyzer")
st.write("Sistem analisis teknis berbasis histori perbaikan armada dan Machine Learning Gemini AI.")

col_ac, col_ata = st.columns([2, 1])

with col_ac:
    ac_type = st.selectbox(
        "✈️ Pilih Aircraft Type / Fleet:",
        options=["B737-8", "DHC6-300", "DHC6-400", "BELL-412", "AS350B3", "MIL-171"],
        help="Sistem akan otomatis memuat database histori sesuai sheet tipe pesawat ini."
    )

with col_ata:
    selected_ata = st.text_input(
        "ATA Chapter (Opsional):",
        placeholder="Contoh: 33"
    )

kasus_baru = st.text_area(
    "Masukkan Deskripsi Defect / Pilot Report Baru:",
    placeholder="Contoh: RH LANDING LIGHT OFF DURING FLIGHT"
)

if st.button("Analisis Kasus & History", type="primary"):
    if kasus_baru.strip():
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

DATA HISTORI DARI DATABASE ARMADA {ac_type}:
- Total Ditemukan: {jumlah_match} record/kejadian defect serupa.
- Periode Waktu Kejadian: {date_range_info}

SAMPEL 15 RECORD TERAKHIR DARI DATABASE ARMADA {ac_type}:
{context_history}

TUGAS ANDA:
Berikan analisis teknis lengkap yang terfokus pada sistem/komponen tipe pesawat {ac_type} dalam 2 BAGIAN EKSPLISIT:

[BAGIAN INDONESIA]
1. Analisis Indikasi Repetitive Defect (gunakan data total {jumlah_match} kejadian dalam periode {date_range_info} untuk tipe {ac_type}).
2. Root Cause Analysis (RCA) spesifik untuk {ac_type}:
   - Sertakan Diagram Fishbone / Ishikawa sederhana di dalam block code (menggunakan tanda triple backtick ``` ).
   - PENTING: Gunakan karakter ASCII standar seperti huruf, spasi, hyphens (-), plus (+), dan pipa (|). JANGAN gunakan karakter unicode khusus atau balok tebal.
3. Rekomendasi Langkah Troubleshooting / Corrective Action (IAW AMM/WDM {ac_type}).

[BAGIAN ENGLISH]
Provide the exact same technical analysis translated into professional aviation engineering English specifically for {ac_type}.
"""

            with st.spinner(f"Menganalisis histori armada {ac_type} dan menyusun rekomendasi via Gemini..."):
                try:
                    # Memanggil REST API langsung dengan header x-goog-api-key
                    full_text = call_gemini_api(prompt, API_KEY)

                except Exception as e:
                    st.error(f"⚠️ **Gagal terhubung ke Gemini API:** {e}")
                    
                    fallback_id = f"1. ANALISIS REPETITIVE DEFECT: Terdeteksi {jumlah_match} kejadian serupa pada armada {ac_type} ATA {selected_ata if selected_ata else 'N/A'} ({date_range_info}).\n\n2. ROOT CAUSE ANALYSIS (RCA):\n```\n[ENVIRONMENT]           [MECHANICAL]\n      |                       |\n      +-- Moisture Ingress    +-- Vibration\n      |                       |\n-------------------------------------------> DEFECT: {kasus_baru}\n      |                       |\n      +-- Voltage Fluctuation +-- Component Wear\n      |                       |\n[ELECTRICAL]            [MAINTENANCE]\n```\n\n3. REKOMENDASI TROUBLESHOOTING: Visual inspection, wiring insulation check, ground stud bonding test IAW AMM {ac_type}."
                    
                    fallback_en = f"1. REPETITIVE DEFECT ANALYSIS: Recorded {jumlah_match} similar occurrences under {ac_type} ATA {selected_ata if selected_ata else 'N/A'} ({date_range_info}).\n\n2. ROOT CAUSE ANALYSIS (RCA):\n```\n[ENVIRONMENT]           [MECHANICAL]\n      |                       |\n      +-- Moisture Ingress    +-- Vibration\n      |                       |\n-------------------------------------------> DEFECT: {kasus_baru}\n      |                       |\n      +-- Voltage Fluctuation +-- Component Wear\n      |                       |\n[ELECTRICAL]            [MAINTENANCE]\n```\n\n3. TROUBLESHOOTING RECOMMENDATION: Visual inspection, wiring insulation test, ground stud bonding integrity IAW {ac_type} AMM."
                    
                    full_text = f"[BAGIAN INDONESIA]\n{fallback_id}\n\n[BAGIAN ENGLISH]\n{fallback_en}"

                if "[BAGIAN ENGLISH]" in full_text:
                    parts = full_text.split("[BAGIAN ENGLISH]")
                    text_id = parts[0].replace("[BAGIAN INDONESIA]", "").strip()
                    text_en = parts[1].strip()
                else:
                    text_id = full_text
                    text_en = full_text

                st.session_state['ai_result_id'] = text_id
                st.session_state['ai_result_en'] = text_en

    else:
        st.warning("Mohon masukkan deskripsi defect terlebih dahulu.")

# ---------------------------------------------------------
# 6. TAMPILAN HASIL & DOKUMEN PDF
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
