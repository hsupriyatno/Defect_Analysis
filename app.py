import pandas as pd
import streamlit as st
import re
import io
import html
import requests
import json
import base64
import os
from datetime import datetime
import pdfplumber
import tempfile
import re
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
import google.generativeai as genai
from fpdf import FPDF
from fpdf.errors import FPDFException
import re

def clean_text_for_pdf(text):
    if not isinstance(text, str):
        return str(text)
    # Hapus karakter unicode/box-drawing yang tidak didukung fpdf core font
    cleaned = re.sub(r'[│┌┐└┘├┤┬┴┼─═║╔╗╚╝╠╣╦╩╬•]', ' ', text)
    return cleaned

# ==========================================
# 1. SET TOKEN OAUTH / BEARER & GEMINI CONFIG
# ==========================================
api_key = os.environ.get("GEMINI_API_KEY", "GANTI_DENGAN_API_KEY_ANDA")
genai.configure(api_key=api_key)

# ---------------------------------------------------------
# INISIALISASI SESSION STATE (WAJIB ADA DI AWAL SCRIPT)
# ---------------------------------------------------------
if "iterations" not in st.session_state:
    st.session_state["iterations"] = []

if "ac_type" not in st.session_state:
    st.session_state["ac_type"] = "DHC6-300"

if "ac_reg" not in st.session_state:
    st.session_state["ac_reg"] = ""

if "selected_ata" not in st.session_state:
    st.session_state["selected_ata"] = ""

if "kasus_baru" not in st.session_state:
    st.session_state["kasus_baru"] = ""

# ---------------------------------------------------------
# KONFIGURASI HALAMAN & CUSTOM CSS
# ---------------------------------------------------------
st.set_page_config(
    page_title="AIRFAST Indonesia | Defect & Reliability Analyzer",
    page_icon="✈️",
    layout="wide"
)

st.markdown(
    """
    <style>
        [data-testid="stSidebarContent"] {
            padding-top: 1rem !important;
        }
        [data-testid="stSidebarUserContent"] {
            padding-top: 0rem !important;
        }
    </style>
    """,
    unsafe_allow_html=True
)

# ---------------------------------------------------------
# SISTEM PENYIMPANAN DAN BACA LAPORAN LOKAL
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
# MODUL RAG: PENCARIAN MANUAL PDF LOKAL
# ---------------------------------------------------------
@st.cache_data(show_spinner=False)
def search_reference_library(fleet_type, query_text, max_pages_per_pdf=5):
    folder_mapping = {
        "DHC6-300": "DHC6-300",
        "DHC6-400": "DHC6-400",
        "B737-8": "B737-8",
        "BELL-412": "Bell412",
        "AS350B3": "AS350B3",
        "MIL-171": "MIL171"
    }
    
    folder_name = folder_mapping.get(fleet_type, fleet_type)
    target_dir = os.path.join("reference_library", folder_name)
    
    retrieved_contexts = []
    detected_ata = "N/A"
    
    if not os.path.exists(target_dir):
        return f"Direktori referensi untuk armada {fleet_type} tidak ditemukan di {target_dir}.", detected_ata
    
    text_upper = query_text.upper()
    raw_words = re.findall(r'\b[A-Za-z0-9]+\b', text_upper)
    stop_words = {"THE", "TO", "OF", "AND", "A", "IN", "IS", "IT", "FOR", "ON", "WITH", "NOT", "FAIL", "VALVE"}
    keywords = [w for w in raw_words if len(w) > 2 and w not in stop_words]
    
    ata_match = re.search(r'(?:ATA)?\s*(\d{2})[-]?(\d{2})?', text_upper)
    if ata_match:
        detected_ata = f"{ata_match.group(1)}-{ata_match.group(2) if ata_match.group(2) else '10'}"

    try:
        pdf_files = [f for f in os.listdir(target_dir) if f.lower().endswith('.pdf')]
        
        for file_name in pdf_files[:5]:
            file_path = os.path.join(target_dir, file_name)
            with pdfplumber.open(file_path) as pdf:
                for page_idx, page in enumerate(pdf.pages[:max_pages_per_pdf]):
                    page_text = page.extract_text()
                    if not page_text:
                        continue
                        
                    page_text_upper = page_text.upper()
                    score = sum(1 for kw in keywords if kw in page_text_upper)
                    if detected_ata != "N/A" and detected_ata[:2] in page_text_upper:
                        score += 5
                        
                    if score > 0:
                        snippet = page_text[:800].replace('\n', ' ')
                        retrieved_contexts.append(f"[Sumber: {file_name} - Hal {page_idx+1}]\n{snippet}")
                        
    except Exception as e:
        return f"Gagal membaca file referensi PDF: {str(e)}", detected_ata

    if retrieved_contexts:
        combined_context = "\n\n".join(retrieved_contexts[:3])
        return combined_context, detected_ata
    else:
        return "Tidak ditemukan referensi spesifik di manual PDF lokal. Menggunakan standar analisis umum.", detected_ata

# ---------------------------------------------------------
# SIDEBAR - LOGO LOKAL & PENGATURAN API KEY
# ---------------------------------------------------------
if os.path.exists("logo.png"):
    st.sidebar.image("logo.png", use_container_width=True)

st.sidebar.title("⚙️ Pengaturan API")
api_key_input = st.sidebar.text_input(
    "Masukkan Gemini API Key:",
    type="password",
    key="user_api_key",
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
def call_gemini_api(prompt_text, user_api_key="", uploaded_file=None):
    api_key = user_api_key.strip() if user_api_key else st.session_state.get('user_api_key', '').strip()
    if not api_key:
        api_key = os.environ.get("GEMINI_API_KEY", "")

    if not api_key:
        raise ValueError("API Key tidak ditemukan. Silakan masukkan API Key di sidebar.")

    clean_api_key = str(api_key).strip().strip('"').strip("'")
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": clean_api_key
    }
    
    parts = [{"text": prompt_text}]
    
    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        base64_data = base64.b64encode(file_bytes).decode('utf-8')
        mime_type = uploaded_file.type
        
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": base64_data
            }
        })
    
    payload = {
        "contents": [{"parts": parts}]
    }

    response = requests.post(url, headers=headers, json=payload, timeout=90)
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
# FUNGSI PEMBANTU: PARSER TABEL MARKDOWN KE REPORTLAB
# ---------------------------------------------------------
def parse_markdown_table_to_reportlab(text_block, styles):
    lines = text_block.strip().split('\n')
    table_data = []
    
    th_style = ParagraphStyle('THead', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=8, textColor=colors.whitesmoke, alignment=1)
    td_style = ParagraphStyle('TData', parent=styles['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.darkslategray)
    td_center = ParagraphStyle('TDataCenter', parent=styles['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.darkslategray, alignment=1)

    is_table_active = False
    
    for line in lines:
        line = line.strip()
        if line.startswith('|') and line.endswith('|'):
            if '---' in line or '::' in line:
                continue
            
            cells = [c.strip() for c in line.split('|')[1:-1]]
            if not cells or cells[0] == '':
                continue
                
            if not is_table_active:
                formatted_cells = [Paragraph(f"<b>{c}</b>", th_style) for c in cells]
                is_table_active = True
            else:
                formatted_cells = []
                for i, c in enumerate(cells):
                    if i < 3:
                        formatted_cells.append(Paragraph(c, td_center))
                    else:
                        formatted_cells.append(Paragraph(c, td_style))
            
            table_data.append(formatted_cells)
            
    if len(table_data) > 1:
        col_widths = [50, 80, 70, 315]
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d3d3d3')),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F3F4F6')]),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('TOPPADDING', (0,0), (-1,-1), 4),
        ]))
        return t
    return None

# ---------------------------------------------------------
# FUNGSI UTAMA: GENERATE LAPORAN PDF
# ---------------------------------------------------------
def generate_pdf_report(ac_type, kasus_baru, selected_ata, iterations_list, df_history, lang="id"):
    buffer = io.BytesIO()
    try:
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
                "title": "TECHNICAL RELIABILITY EVALUATION & INVESTIGATION AUDIT TRAIL",
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

        title_style = ParagraphStyle('DocTitle', parent=styles['Heading1'], fontName='Helvetica-Bold', fontSize=14, leading=17, textColor=colors.HexColor('#1E3A8A'), spaceAfter=4)
        subtitle_style = ParagraphStyle('DocSubTitle', parent=styles['Normal'], fontName='Helvetica', fontSize=8.5, leading=11, textColor=colors.HexColor('#4B5563'), spaceAfter=10)
        h2_style = ParagraphStyle('SectionHeader', parent=styles['Heading2'], fontName='Helvetica-Bold', fontSize=10.5, leading=13, textColor=colors.HexColor('#1E3A8A'), spaceBefore=10, spaceAfter=5)
        body_style = ParagraphStyle('BodyDark', parent=styles['Normal'], fontName='Helvetica', fontSize=9.5, leading=13, textColor=colors.HexColor('#1F2937'))
        code_style = ParagraphStyle('CodeFishbone', parent=styles['Normal'], fontName='Courier', fontSize=7.5, leading=9.5, textColor=colors.HexColor('#111827'), backColor=colors.HexColor('#F3F4F6'), borderPadding=6, spaceBefore=4, spaceAfter=6)

        story.append(Paragraph(labels["title"], title_style))
        story.append(Paragraph(f"Generated on: {datetime.now().strftime('%d %B %Y, %H:%M WIB')} | System: AIRFAST Indonesia - Defect Analyzer", subtitle_style))
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#1E3A8A'), spaceAfter=8))

        story.append(Paragraph(labels["sec1"], h2_style))
        info_data = [
            [Paragraph(f"<b>{labels['ac_label']}</b>", body_style), Paragraph(html.escape(f"{ac_type}"), body_style)],
            [Paragraph(f"<b>{labels['ata_label']}</b>", body_style), Paragraph(html.escape(selected_ata) if selected_ata else "N/A", body_style)],
            [Paragraph(f"<b>{labels['defect_label']}</b>", body_style), Paragraph(html.escape(kasus_baru), body_style)]
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

        for idx, iter_data in enumerate(iterations_list):
            if idx > 0:
                story.append(Paragraph(f"<b>--- ITERATION / FEEDBACK STEP {idx} ---</b>", h2_style))
                feedback_note = iter_data.get('feedback_text', '')
                if feedback_note:
                    story.append(Paragraph(f"<b>Engineer Feedback:</b> {html.escape(feedback_note)}", body_style))
                    story.append(Spacer(1, 4))

            full_result = iter_data.get('analysis_result', '')
            if not full_result:
                sys_i = iter_data.get('ai_sys_id', '') if lang == 'id' else iter_data.get('ai_sys_en', '')
                rca_i = iter_data.get('ai_rca_id', '') if lang == 'id' else iter_data.get('ai_rca_en', '')
                rec_i = iter_data.get('ai_rec_id', '') if lang == 'id' else iter_data.get('ai_rec_en', '')
                full_result = f"[SYSTEM]\n{sys_i}\n\n[RCA]\n{rca_i}\n\n[RECOMMENDATIONS]\n{rec_i}"

            if "[BAHASA_INGGRIS]" in full_result:
                parts_lang = full_result.split("[BAHASA_INGGRIS]")
                target_block = parts_lang[1] if lang == "en" else parts_lang[0]
            else:
                target_block = full_result

            target_block = target_block.replace("[BAHASA_INDONESIA]", "").replace("[BAHASA_INGGRIS]", "")

            sys_content, rca_content, rec_content = "", "", ""
            if "[SYSTEM]" in target_block:
                s_split = target_block.split("[SYSTEM]")[1].split("[RCA]")
                sys_content = s_split[0].strip()
                if len(s_split) > 1:
                    r_split = s_split[1].split("[RECOMMENDATIONS]")
                    rca_content = r_split[0].strip()
                    if len(r_split) > 1:
                        rec_content = r_split[1].strip()
            else:
                rca_content = target_block

            if sys_content:
                story.append(Paragraph(labels["sec2"], h2_style))
                story.append(Paragraph(html.escape(sys_content).replace('\n', '<br/>'), body_style))
                story.append(Spacer(1, 4))

            if rca_content:
                story.append(Paragraph(labels["sec3"], h2_style))
                chunks = rca_content.split('\n\n')
                for chunk in chunks:
                    is_ascii_diagram = ("+--" in chunk) or ("\\-->" in chunk) or ("===" in chunk) or ("---+" in chunk)
                    
                    if "```" in chunk or is_ascii_diagram:
                        clean_diagram = chunk.replace("```", "").strip()
                        story.append(Paragraph(html.escape(clean_diagram).replace('\n', '<br/>').replace(' ', '&nbsp;'), code_style))
                        story.append(Spacer(1, 4))
                    elif '|' in chunk and ('---' in chunk or '::' in chunk) and not is_ascii_diagram:
                        pdf_table = parse_markdown_table_to_reportlab(chunk, styles)
                        if pdf_table:
                            story.append(pdf_table)
                            story.append(Spacer(1, 4))
                        else:
                            story.append(Paragraph(html.escape(chunk).replace('\n', '<br/>'), body_style))
                    else:
                        story.append(Paragraph(html.escape(chunk).replace('\n', '<br/>'), body_style))
                        story.append(Spacer(1, 4))

            if rec_content:
                story.append(Paragraph(labels["sec4"], h2_style))
                story.append(Paragraph(html.escape(rec_content).replace('\n', '<br/>'), body_style))
                story.append(Spacer(1, 6))

        story.append(Spacer(1, 8))
        story.append(Paragraph(labels["sec5"], h2_style))
        
        if df_history is not None and not df_history.empty:
            hist_data = []
            th_style_h = ParagraphStyle('THist', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=7.5, textColor=colors.whitesmoke, alignment=1)
            td_style_h = ParagraphStyle('TDataH', parent=styles['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.darkslategray)
            td_center_h = ParagraphStyle('TDataCenterH', parent=styles['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.darkslategray, alignment=1)

            hist_data.append([
                Paragraph("<b>Date</b>", th_style_h),
                Paragraph("<b>AML No</b>", th_style_h),
                Paragraph("<b>Note / Report</b>", th_style_h),
                Paragraph("<b>Corrective Action</b>", th_style_h)
            ])

            for _, row in df_history.iterrows():
                d_str = str(row.get('Date', ''))[:10]
                aml_str = str(row.get('AML No', ''))
                note_str = str(row.get('Note / Report', ''))
                act_str = str(row.get('Corrective Action', ''))
                
                hist_data.append([
                    Paragraph(d_str, td_center_h),
                    Paragraph(aml_str, td_center_h),
                    Paragraph(note_str, td_style_h),
                    Paragraph(act_str, td_style_h)
                ])

            col_widths_h = [60, 75, 190, 190]
            t_hist = Table(hist_data, colWidths=col_widths_h, repeatRows=1)
            t_hist.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1E3A8A')),
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#d3d3d3')),
                ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor('#F3F4F6')]),
                ('BOTTOMPADDING', (0,0), (-1,-1), 4),
                ('TOPPADDING', (0,0), (-1,-1), 4),
            ]))
            story.append(t_hist)
        else:
            story.append(Paragraph(labels["no_match"], body_style))

        doc.build(story)
        buffer.seek(0)
        return buffer.getvalue()
        
    except Exception as e:
        print(f"PDF Generation Error: {e}")
        fallback_buffer = io.BytesIO()
        fallback_doc = SimpleDocTemplate(fallback_buffer, pagesize=letter)
        fallback_story = [Paragraph(f"Error generating full PDF: {str(e)}", getSampleStyleSheet()['Normal'])]
        fallback_doc.build(fallback_story)
        fallback_buffer.seek(0)
        return fallback_buffer.getvalue()

# ---------------------------------------------------------
# MAPPING REGISTRASI FLEET & ATA CHAPTER
# ---------------------------------------------------------
AIRCRAFT_REG_MAP = {
    "B737-8": ["PK-OFI", "PK-OFM"],
    "DHC6-300": ["PK-OCJ", "PK-OCK", "PK-OCL"],
    "DHC6-400": ["PK-OAM", "PK-OCF", "PK-OCG", "PK-OCH", "PK-OCI"],
    "BELL-412": ["PK-OCA", "PK-OCD"],
    "AS350B3": ["PK-ODC"],
    "MIL-171": ["PK-OMI", "PK-OMS"]
}

# ---------------------------------------------------------
# ANTARMUKA UTAMA: DEFECT & RELIABILITY ANALYZER
# ---------------------------------------------------------
st.title("🛠️ Defect & Reliability Analyzer")
st.write("Sistem Analisis Rekayasa Keandalan Penerbangan Berbasis Histori Maintenance & Gemini AI.")

col_ac, col_reg, col_ata = st.columns([1.5, 1.5, 3])

with col_ac:
    ac_type = st.selectbox(
        "✈️ Pilih Armada / Tipe Pesawat:",
        options=list(AIRCRAFT_REG_MAP.keys()),
        help="Sistem akan otomatis menyesuaikan opsi registrasi pesawat yang tersedia."
    )

with col_reg:
    available_regs = AIRCRAFT_REG_MAP.get(ac_type, ["N/A"])
    ac_reg = st.selectbox(
        "🛩️ Aircraft Registration:",
        options=available_regs,
        help=f"Registrasi aktif yang terdaftar untuk armada {ac_type}."
    )

with col_ata:
    selected_ata = st.text_input(
        "🏷️ ATA Chapter (Opsional - Ketik Bebas):",
        placeholder="Contoh: 27-21 atau 34-11",
        help="Ketik nomor ATA chapter manual sesuai kebutuhan, atau kosongkan agar RAG mendeteksi otomatis."
    )

kasus_baru = st.text_area(
    "Masukkan Deskripsi Defect / Discrepancy Baru:",
    placeholder="Contoh: Nose Landing Gear vibration during taxi"
)

uploaded_file = st.file_uploader(
    "📎 Lampirkan Dokumen PDF / Foto Kerusakan (Opsional):",
    type=["png", "jpg", "jpeg", "pdf"],
    help="Anda dapat melampirkan foto komponen rusak, skema WDM/AMM, atau file PDF AML/laporan teknis untuk dibaca oleh AI."
)

if uploaded_file is not None and uploaded_file.type.startswith("image/"):
    st.image(uploaded_file, caption="Preview Foto Kerusakan / Dokumen Lampiran", width=300)

# =========================================================================
# PROSES KETIKA TOMBOL "Analisis Kasus & History" DIKLIK
# =========================================================================
if st.button("Analisis Kasus & History", key="btn_analisis_kasus", type="primary"):
    if not api_key_input.strip() and not os.environ.get("GEMINI_API_KEY"):
        st.error("⚠️ Silakan masukkan Gemini API Key Anda pada menu Sidebar di sebelah kiri terlebih dahulu.")
    elif not kasus_baru.strip():
        st.warning("⚠️ Silakan masukkan deskripsi defect atau discrepancy terlebih dahulu.")
    else:
        # --- PANGGIL SISTEM RAG LOKAL ---
        with st.spinner("Memindai manual referensi PDF lokal (RAG)..."):
            rag_context, detected_ata = search_reference_library(ac_type, kasus_baru)
            if not selected_ata and detected_ata != "N/A":
                selected_ata = detected_ata

        df_defect = load_defect_db_by_sheet(ac_type)
        
        if df_defect.empty:
            st.error(f"⚠️ Database maintenance untuk armada {ac_type} kosong atau tidak ditemukan.")
        else:
            # --- 1. TENTUKAN TARGET ATA (Prioritas: Input User -> Deteksi RAG PDF -> Deteksi Teks Kasus) ---
            target_ata_final = selected_ata.strip()
            if not target_ata_final and 'detected_ata' in locals() and detected_ata != "N/A":
                target_ata_final = detected_ata
            if not target_ata_final:
                match_ata_text = re.search(r'\b(2[0-9]|3[0-8]|5[0-7])\b', kasus_baru)
                if match_ata_text:
                    target_ata_final = match_ata_text.group(1)

            # --- 2. FILTER MUTLAK BERDASARKAN KOLOM 'ATA' DI EXCEL ---
            ata_col_name = None
            for col in df_defect.columns:
                if col.strip().upper() == 'ATA' or 'ATA' in col.upper():
                    ata_col_name = col
                    break

            df_ata_subset = pd.DataFrame()
            if ata_col_name and target_ata_final:
                # Ambil 2 digit utama angka ATA (misal "27-21" atau "27" menjadi "27")
                clean_target_digits = re.sub(r'\D', '', target_ata_final)[:2]
                if clean_target_digits:
                    excel_ata_series = df_defect[ata_col_name].astype(str).str.replace(r'\D', '', regex=True)
                    # WAJIB KETAT: Hanya ambil baris yang kolom ATA-nya diawali dengan 2 digit target
                    mask_strict_ata = excel_ata_series.str.startswith(clean_target_digits, na=False)
                    df_ata_subset = df_defect[mask_strict_ata].copy()
            else:
                df_ata_subset = df_defect.copy()

            # --- 3. FILTER DUA DIMENSI: EKSTRAKSI KATA KUNCI DINAMIS & OTOMATIS ---
            history_match = pd.DataFrame()
            
            if not df_ata_subset.empty:
                row_text = df_ata_subset.astype(object).fillna('').astype(str).agg(' '.join, axis=1).str.upper()
                text_upper = kasus_baru.upper()
                
                # A. Ekstraksi Frasa & Kata Kunci Bermakna (Hindari Kata Sambung Umum)
                raw_words = re.findall(r'\b[A-Za-z0-9]+\b', text_upper)
                stop_words = {
                    "THE", "TO", "OF", "AND", "A", "IN", "IS", "IT", "FOR", "ON", "WITH", "NOT", 
                    "FAIL", "FAILED", "FAULT", "FOUND", "DURING", "PRE", "FLIGHT", "AFTER", "BEFORE",
                    "AT", "THIS", "AN", "BY", "FROM", "AS", "THAT", "OR", "BE", "WAS", "FIRST", "ATTEMPT"
                }
                # Hanya ambil kata kunci teknis yang panjangnya > 2 dan bukan stop words
                significant_keywords = [w for w in raw_words if len(w) > 2 and w not in stop_words]

                # Deteksi Kasus Khusus
                is_lavatory_case = any(k in text_upper for k in ["LAVATORY", "LAV", "TOILET", "CABIN"])
                is_entry_door_case = any(k in text_upper for k in ["FORWARD ENTRY", "FWD ENTRY", "PASSENGER DOOR", "ENTRY DOOR"])
                is_engine_start_case = any(k in text_upper for k in ["START", "STARTING", "IGNITION", "CRANK"]) and any(k in text_upper for k in ["ENG", "ENGINE"])

                exclude_pattern = r'PAINT|PEEL|OFF|DETERIORATED|SCRATCH|DENT|CORROSION|POLISH|CLEAN|FIRST\s*AID|MEDICAL\s*KIT|POB'
                mask_exclude = row_text.str.contains(exclude_pattern, regex=True, na=False)

                # B. MASKING BERLAPIS KETAT (Mencegah First Aid Kit atau kata parsial lolos)
                mask_system = pd.Series(False, index=df_ata_subset.index)
                
                if is_engine_start_case:
                    # Jika kasus Start Engine, baris WAJIB mengandung elemen Engine DAN Start/Ignition secara bersamaan
                    has_engine = row_text.str.contains(r'\bENG\b|\bENGINE\b|\bENG1\b|\bENG2\b', regex=True, na=False)
                    has_start = row_text.str.contains(r'START|STARTING|CRANK|IGNITION', regex=True, na=False)
                    mask_system = has_engine & has_start
                elif significant_keywords:
                    # Untuk kasus lain, minimal harus mencocokkan sebagian besar kata kunci inti secara simultan
                    # (Tidak boleh hanya mengandalkan 1 kata umum seperti 'FIRST')
                    match_counts = sum(row_text.str.contains(kw, case=False, regex=False, na=False) for kw in significant_keywords)
                    # Syarat: minimal ada 2 kata kunci yang cocok, atau 1 kata jika total keyword input hanya 1
                    threshold = min(2, len(significant_keywords))
                    mask_system = match_counts >= threshold

                # Mask Lokasi Khusus
                mask_location = pd.Series(True, index=df_ata_subset.index)
                if is_lavatory_case:
                    mask_location = row_text.str.contains(r'LAVATORY|\bLAV\b|CABIN', case=False, regex=True, na=False) & \
                                    ~row_text.str.contains(r'FORWARD ENTRY|FWD ENTRY|CARGO', case=False, regex=True, na=False)
                elif is_entry_door_case:
                    mask_location = row_text.str.contains(r'ENTRY|PASSENGER DOOR', case=False, regex=True, na=False)

                # C. Eksekusi Hasil Match yang Bersih dari False Positive
                if not target_ata_final and not significant_keywords:
                    history_match = pd.DataFrame()
                else:
                    history_match = df_ata_subset[mask_system & mask_location & ~mask_exclude].copy()

                # PENGAMAN TAMBAHAN: Singkirkan baris yang merujuk pada E/O / Engineering Order
                if not history_match.empty:
                    row_text_all = history_match.astype(object).fillna('').astype(str).agg(' '.join, axis=1).str.upper()
                    mask_eo_ref = row_text_all.str.contains(r'E/O|\bEO\b|ENGINEERING\s*ORDER', regex=True, na=False)
                    history_match = history_match[~mask_eo_ref].copy()

                # D. HITUNG SKOR RELEVANSI BERBASIS KECOCOKAN FRASA
                if not history_match.empty:
                    def calc_relevance(r_str):
                        score = 0
                        for kw in significant_keywords:
                            if kw in r_str: score += 20
                        # Bonus tinggi jika frasa spesifik "START" dan "ENG" berdampingan
                        if "START" in r_str and ("ENG" in r_str or "ENGINE" in r_str):
                            score += 50
                        return score

                    row_text_subset = history_match.astype(object).fillna('').astype(str).agg(' '.join, axis=1).str.upper()
                    history_match['Relevance_Score'] = row_text_subset.apply(calc_relevance)
                    
                    if 'Date' in history_match.columns:
                        history_match['Date_Parsed'] = pd.to_datetime(history_match['Date'], errors='coerce')
                        history_match = history_match.sort_values(by=['Relevance_Score', 'Date_Parsed'], ascending=[False, False])
            else:
                history_match = pd.DataFrame()

            df_gemini_trend = history_match.copy() if not history_match.empty else pd.DataFrame()
            jumlah_filtered_match = len(df_gemini_trend)

            # --- 4. SIAPKAN VARIABEL KONTEN TABEL & RENTANG WAKTU UNTUK GEMINI ---
            if not df_gemini_trend.empty:
                df_history_sorted = df_gemini_trend.copy()
                min_date = df_history_sorted['Date_Parsed'].min() if 'Date_Parsed' in df_history_sorted.columns else None
                max_date = df_history_sorted['Date_Parsed'].max() if 'Date_Parsed' in df_history_sorted.columns else None
                
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
            else:
                context_history = "Tidak ditemukan riwayat defect serupa di sub-sistem ini."
                date_range_info = "N/A"
                df_history_sorted = pd.DataFrame()

            st.session_state['ac_type'] = ac_type
            st.session_state['ac_reg'] = ac_reg
            st.session_state['kasus_baru'] = kasus_baru
            st.session_state['selected_ata'] = target_ata_final if target_ata_final else selected_ata
            st.session_state['history_match'] = df_history_sorted

            # --- 5. MENYUSUN PROMPT GEMINI ---
            if jumlah_filtered_match >= 5:
                selected_method_instruction = """
                [METODE ANALISIS & REKAPITULASI YANG WAJIB DIGUNAKAN: FISHBONE / ISHIKAWA DIAGRAM]
                1. Tuliskan terlebih dahulu paragraf tinjauan tren & korelasi historis di awal.
                2. **WAJIB BUAT TABEL REKAPITULASI BULANAN** berdasarkan data historis di atas dalam format Markdown persis seperti ini:
                   | Bulan | Jumlah Defect | Keterangan / Tren |
                   | :--- | :---: | :--- |
                   | (Contoh: Bulan-1) | (Jumlah) | (Keterangan singkat) |
                3. Buatlah diagram Fishbone / Ishikawa menggunakan teks ASCII di dalam blok kode (``` ... ```).
                4. SETELAH diagram Fishbone, wajib tuliskan paragraf analisis dan evaluasi mendalam yang mengulas poin-poin penyebab utama dari diagram tersebut (Machine, Material, Method, Environment).
                """
            else:
                selected_method_instruction = """
                [METODE ANALISIS & REKAPITULASI YANG WAJIB DIGUNAKAN: 5 WHYS INVESTIGATION]
                1. Tuliskan paragraf tinjauan tren di awal.
                2. **WAJIB BUAT TABEL REKAPITULASI BULANAN** berdasarkan data historis di atas dalam format Markdown persis seperti ini:
                   | Bulan | Jumlah Defect | Keterangan / Tren |
                   | :--- | :---: | :--- |
                   | (Contoh: Bulan-1) | (Jumlah) | (Keterangan singkat) |
                3. Buat penelusuran 5 Whys di dalam blok kode (``` ... ```).
                4. SETELAH penelusuran 5 Whys, wajib tuliskan paragraf evaluasi kesimpulan akar masalah.
                """

            additional_file_instruction = ""
            if uploaded_file is not None:
                additional_file_instruction = (
                    "\n\nCATATAN MULTIMODAL:\n"
                    "User melampirkan file gambar/PDF tambahan terkait defect ini. "
                    "Harap analisis visual/dokumen dari lampiran tersebut dan gabungkan temuan visualnya "
                    "ke dalam System Description, RCA, serta Rekomendasi."
                )

            prompt = f"""
Anda adalah seorang pakar Reliability Engineering penerbangan spesialis armada {ac_type}.

TIPE PESAWAT: {ac_type}
REGISTRASI PESAWAT: {ac_reg if ac_reg else 'N/A'}
KASUS BARU YANG DILAPORKAN:
{kasus_baru} (ATA Chapter Target: {selected_ata if selected_ata else 'N/A'})
{additional_file_instruction}

REFERENSI MANUAL PDF RESMI (RAG):
{rag_context}

DATA HISTORI DARI DATABASE UNSCHEDULED ARMADA {ac_type} (Spesifik Sub-sistem ATA {selected_ata}):
- Total Ditemukan: {jumlah_filtered_match} record/kejadian defect serupa.
- Periode Waktu Kejadian: {date_range_info}

SAMPEL RECORD TERAKHIR DARI DATABASE UNSCHEDULED ARMADA {ac_type}:
{context_history}

MANDATORY COMPLIANCE INSTRUCTIONS:
1. **MANUAL REFERENCES BY AIRCRAFT TYPE**: 
   - If fleet is B737-8: You MUST explicitly cite exact AMM tasks, WDM diagrams, and Boeing FTD bulletins.
   - If fleet is DHC6-300 / DHC6-400 / Bell412 / AS350B3 / MIL171: Cite exact AMM tasks, WDM, or manufacturer maintenance manuals (strictly exclude Boeing-specific FTD).
2. **IFIM INTEGRATION**: Always check whether an applicable **IFIM (Fault Isolation Manual)** task exists for this defect symptom and incorporate the fault isolation procedure.

TUGAS ANDA:
Berikan analisis teknis lengkap dalam Bahasa Indonesia dengan format persis berikut:

[BAHASA_INDONESIA]
[SYSTEM]
Jelaskan secara ringkas prinsip kerja, fungsi utama, dan arsitektur sistem/komponen terkait pada armada {ac_type}. Wajib cantumkan nomor rujukan AMM/WDM yang relevan.

[RCA]
{selected_method_instruction}
*Catatan Investigasi*: Lakukan pengecekan referensi apakah terdapat panduan **IFIM (Fault Isolation Manual)** untuk fault code/gejala ini. Jika ada, integrasikan logika pohon kesalahan (fault isolation tree) dari IFIM tersebut.

[RECOMMENDATIONS]
1. Berikan langkah troubleshooting praktis IAW AMM/WDM {ac_type}.
2. **WAJIB CEK & IMPLEMENTASIKAN IFIM**: Jika dokumen referensi/RAG atau standar pabrikan menyediakan task IFIM, tuliskan nomor task IFIM tersebut beserta langkah isolasi kerusakannya.
3. Cantumkan prosedur Corrective Action, Test Flight Monitoring, atau Operational Special Test.

[BAHASA_INGGRIS]
[SYSTEM]
(Provide exact System Description translated into professional aviation engineering English, including AMM/WDM references)

[RCA]
(Provide exact RCA translated into professional aviation engineering English, incorporating IFIM logic if available)

[RECOMMENDATIONS]
(Provide exact Recommendations translated into professional aviation engineering English, explicitly stating AMM tasks and IFIM fault isolation procedures)
"""

            with st.spinner(f"Menganalisis histori, dokumen RAG, & lampiran armada {ac_type} via Gemini..."):
                try:
                    full_text = call_gemini_api(prompt, api_key_input, uploaded_file=uploaded_file)
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

                    report_key_label, dan_code = get_next_dan_key(ac_type, kasus_baru)
                    st.session_state['current_dan_key'] = report_key_label
                    st.session_state['dan_code'] = dan_code
                    
                    df_history_to_save = df_history_sorted.copy()
                    for col in df_history_to_save.columns:
                        if pd.api.types.is_datetime64_any_dtype(df_history_to_save[col]) or 'date' in col.lower():
                            df_history_to_save[col] = df_history_to_save[col].astype(str).replace('NaT', '')

                    report_payload = {
                        "dan_number": dan_code,
                        "ac_type": ac_type,
                        "ac_reg": ac_reg,
                        "kasus_baru": kasus_baru,
                        "selected_ata": target_ata_final if target_ata_final else selected_ata,
                        "iterations": [initial_iteration],
                        "history_match": df_history_to_save.to_dict(orient="records") if not df_history_sorted.empty else []
                    }
                    save_report_to_disk(report_key_label, report_payload)
                    st.rerun()

# ---------------------------------------------------------
# HASIL TAMPILAN, DOWNLOAD PDF, & HISTORICAL RECORD
# ---------------------------------------------------------
if 'iterations' in st.session_state and st.session_state['iterations']:
    st.subheader(f"📋 Rekam Jejak Analisis & Evaluasi Keandalan ({st.session_state.get('ac_type')})")
    
    st.markdown("### 1. INFORMASI DEFECT YANG DILAPORKAN")
    st.info(
        f"**Aircraft Type:** {st.session_state.get('ac_type')} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"**Aircraft Registration:** {st.session_state.get('ac_reg', 'N/A')}\n\n"
        f"**ATA Chapter (4-Digit):** {st.session_state.get('selected_ata', 'N/A')}\n\n"
        f"**Defect / Kasus Dilaporkan:**\n{st.session_state.get('kasus_baru')}"
    )
    
    for iter_data in st.session_state['iterations']:
        idx = iter_data.get('iteration_index', 0)
        
        if idx == 0:
            st.markdown("---")
            st.markdown("## 🔹 TAHAP 1: ANALISIS KELAYAKAN & RCA AWAL")
            st.caption(f"Waktu Analisis: {iter_data.get('timestamp', 'N/A')}")
        else:
            st.markdown("---")
            st.markdown(f"## 🔄 TAHAP {idx + 1}: {iter_data.get('title', f'Iterasi Ke-{idx}')}")
            st.caption(f"Waktu Update: {iter_data.get('timestamp', 'N/A')}")
            st.warning(f"**Feedback / Temuan Lapangan Engineer:**\n{iter_data.get('feedback', iter_data.get('feedback_text', 'N/A'))}")

        sys_text, rca_text, rec_text = "", "", ""
        
        full_result = iter_data.get('analysis_result', '')
        if full_result:
            try:
                if "[SYSTEM]" in full_result or "[RCA]" in full_result:
                    parts_sys = full_result.split("[RCA]")
                    sys_text = parts_sys[0].replace("[SYSTEM]", "").replace("[BAHASA_INDONESIA]", "").strip()
                    if len(parts_sys) > 1:
                        parts_rec = parts_sys[1].split("[RECOMMENDATIONS]")
                        rca_text = parts_rec[0].strip()
                        if len(parts_rec) > 1:
                            rec_text = parts_rec[1].split("[BAHASA_INGGRIS]")[0].strip()
                else:
                    rca_text = full_result
            except:
                rca_text = full_result
        else:
            sys_text = iter_data.get('ai_sys_id', '')
            rca_text = iter_data.get('ai_rca_id', '')
            rec_text = iter_data.get('ai_rec_id', '')

        if sys_text:
            st.markdown("### 2. SYSTEM DESCRIPTION & OPERATIONAL PRINCIPLES")
            st.markdown(sys_text)

        if rca_text:
            st.markdown("### 3. REPETITIVE DEFECT ANALYSIS & ROOT CAUSE ANALYSIS (RCA)")
            clean_rca = rca_text.replace("[RCA_REVIEW]", "").replace("[RCA]", "").strip()
            
            if "```" in clean_rca:
                parts_code = clean_rca.split("```")
                rca_before = parts_code[0].strip()
                diagram_content = "```" + parts_code[1] + "```"
                rca_after = parts_code[2].strip() if len(parts_code) > 2 else ""
            else:
                rca_before = clean_rca
                diagram_content = ""
                rca_after = ""
            
            if rca_before:
                st.markdown(rca_before)
            if diagram_content:
                st.markdown(diagram_content)
            if rca_after:
                st.markdown("#### Analisis & Evaluasi Root Cause:")
                st.markdown(rca_after)
            
            st.markdown("---")
            
        if rec_text:
            st.markdown("### 4. CORRECTIVE & PREVENTIVE RECOMMENDATIONS")
            st.markdown(rec_text)
    
    st.divider()

    with st.expander("🛠️ Tambahkan Hasil Eksekusi & Feedback Engineer (Analisis Lanjutan / Iterasi Baru)", expanded=False):
        feedback_text = st.text_area(
            "Masukkan hasil tindakan / temuan baru setelah rekomendasi dijalankan:",
            placeholder="Contoh: Pegas sudah diganti IAW AMM, namun setelah 3 hari posisi door kembali loose.",
            key="feedback_input_text"
        )
        
        uploaded_feedback_file = st.file_uploader(
            "📎 Lampirkan Bukti Rektifikasi / Foto Temuan / PDF AML (Opsional):",
            type=["png", "jpg", "jpeg", "pdf"],
            help="Lampirkan foto hasil penggantian komponen, foto keausan/kerusakan lanjutan, atau dokumen perbaikan.",
            key="feedback_file_uploader"
        )
        
        if uploaded_feedback_file is not None and uploaded_feedback_file.type.startswith("image/"):
            st.image(uploaded_feedback_file, caption="Preview Bukti Rektifikasi / Temuan Baru", width=300)

        if st.button("Jalankan Analisis Lanjutan (Deep-Dive RCA)", type="primary"):
            if not feedback_text.strip() and uploaded_feedback_file is None:
                st.warning("⚠️ Masukkan catatan temuan perbaikan atau lampirkan file sebelum menjalankan analisis lanjutan.")
            else:
                active_api_key = st.session_state.get('user_api_key', '').strip() or os.environ.get("GEMINI_API_KEY", "")
                if not active_api_key:
                    st.error("⚠️ API Key tidak ditemukan di sidebar. Silakan masukkan API Key di panel sebelah kiri.")
                else:
                    active_dan = st.session_state.get('dan_code', None)
                    current_report = None
                    
                    saved_reps = load_saved_reports()
                    current_report_key = st.session_state.get('current_dan_key', None)
                    if current_report_key and current_report_key in saved_reps:
                        current_report = saved_reps[current_report_key]
                    
                    if not current_report:
                        current_report = {
                            "dan_number": active_dan or "DAN-TEMP",
                            "ac_type": st.session_state.get('ac_type', ''),
                            "ac_reg": st.session_state.get('ac_reg', ''),
                            "kasus_baru": st.session_state.get('kasus_baru', ''),
                            "selected_ata": st.session_state.get('selected_ata', ''),
                            "iterations": st.session_state.get('iterations', [])
                        }

                    previous_iterations = current_report.get("iterations", [])
                    iteration_number = len(previous_iterations) + 1
                    
                    feedback_file_instruction = ""
                    if uploaded_feedback_file is not None:
                        feedback_file_instruction = (
                            "\n\nCATATAN MULTIMODAL REKTIFIKASI:\n"
                            "Engineer melampirkan foto/dokumen bukti rektifikasi atau temuan fisik terbaru. "
                            "Harap periksa dan evaluasi bukti visual/dokumen ini untuk mempertajam analisis penyebab berulang (Deep-Dive RCA) "
                            "serta berikan tindakan perbaikan tingkat lanjut."
                        )

                    prompt_feedback = f"""
Anda adalah pakar Reliability Engineering penerbangan yang sedang melakukan evaluasi berulang (iterasi ke-{iteration_number}) untuk armada {current_report.get('ac_type')}.

# Di dalam prompt Gemini (baik Analisis Utama, Feedback, maupun EO), bagian rujukan dokumen dapat disesuaikan secara dinamis:

INFORMASI AWAL DEFECT (DAN: {current_report.get('dan_number')}):
- Aircraft Type: {current_report.get('ac_type')} ({current_report.get('ac_reg', 'N/A')})
- ATA Chapter: {current_report.get('selected_ata', 'N/A')}
- Initial Defect: {current_report.get('kasus_baru')}

CATATAN EKSEKUSI & TEMUAN ENGINEER DI LAPANGAN (ITERASI {iteration_number}):
{feedback_text if feedback_text.strip() else '(Tidak ada catatan teks, mengandalkan analisis lampiran gambar/PDF)'}
{feedback_file_instruction}

MANDATORY COMPLIANCE INSTRUCTIONS FOR THIS ITERATION:
1. **AMM / WDM / FTD REFERENCES**: Always cite exact AMM tasks, WDM diagrams, or FTD bulletins relevant to this feedback and troubleshooting step.
2. **IFIM INTEGRATION**: Check and incorporate specific **IFIM (Fault Isolation Manual)** troubleshooting steps or test procedures if applicable to the updated findings.

TUGAS ANDA:
Lakukan evaluasi ulang, pertajam Root Cause Analysis (RCA), dan berikan rekomendasi tindakan tingkat lanjut (Next Action Step/Secondary Troubleshooting) dalam Bahasa Indonesia dan Bahasa Inggris dengan format persis:

[BAHASA_INDONESIA]
[SYSTEM]
Penjelasan singkat relevansi temuan/rektifikasi baru terhadap sistem terkait, termasuk rujukan AMM/WDM.

[RCA]
Analisis penyebab mendalam (Deep-Dive RCA) berdasarkan hasil eksekusi dan lampiran visual/dokumen terbaru, dengan mengintegrasikan logika panduan IFIM jika ada.

[RECOMMENDATIONS]
Rekomendasi tindakan teknis lanjutan, langkah pemeriksaan AMM/IFIM task spesifik, atau penyesuaian interval inspeksi.

[BAHASA_INGGRIS]
[SYSTEM]
(System update in English with AMM/WDM references)

[RCA]
(Deep-Dive RCA update in English, incorporating IFIM logic)

[RECOMMENDATIONS]
(Next action recommendations in English with explicit AMM/IFIM tasks)
"""

                    with st.spinner(f"Menganalisis feedback & lampiran rektifikasi iterasi ke-{iteration_number}..."):
                        try:
                            new_analysis_text = call_gemini_api(
                                prompt_feedback, 
                                active_api_key, 
                                uploaded_file=uploaded_feedback_file
                            )
                            
                            new_iteration = {
                                "iteration_index": iteration_number,
                                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                "feedback_text": feedback_text,
                                "has_attachment": uploaded_feedback_file is not None,
                                "attachment_name": uploaded_feedback_file.name if uploaded_feedback_file else None,
                                "analysis_result": new_analysis_text
                            }
                            
                            current_report.setdefault("iterations", []).append(new_iteration)
                            st.session_state['iterations'].append(new_iteration)
                            
                            if current_report_key:
                                save_report_to_disk(current_report_key, current_report)

                            st.success(f"✅ Analisis Lanjutan Iterasi ke-{iteration_number} berhasil ditambahkan dan disimpan!")
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"⚠️ Gagal melakukan analisis lanjutan: {e}")

st.markdown("---")

# --- SECTION: DOWNLOAD LAPORAN PDF ---
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

try:
    pdf_data = generate_pdf_report(
        st.session_state.get('ac_type', 'AIRCRAFT'),
        st.session_state.get('kasus_baru', ''),
        st.session_state.get('selected_ata', ''),
        st.session_state.get('iterations', []),
        df_hist_pdf,
        lang=lang_code
    )
except Exception as e:
    pdf_data = b""

with col_pdf2:
    st.write("")
    if pdf_data:
        st.download_button(
            label=f"📥 Download Laporan ({pdf_lang})",
            data=pdf_data,
            file_name=f"Reliability_Report_{st.session_state.get('ac_type')}_{lang_code.upper()}_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            type="primary"
        )

# --- SECTION 2: HISTORICAL RECORD (MENGGUNAKAN LEBAR FULL-WIDTH) ---
st.markdown("---")
ac_name_display = st.session_state.get('ac_type', '')
record_count = len(df_hist_pdf)
st.subheader(f"5. HISTORICAL RECORD (Database Unscheduled {ac_name_display}: {record_count} Record Ditemukan):")

if not df_hist_pdf.empty:
    st.dataframe(df_hist_pdf, use_container_width=True)
else:
    st.info("Tidak ada catatan riwayat histori yang ditemukan.")


# =========================================================================
# ENGINEERING ORDER GENERATOR (FORM ATD-F-308) - DI BAWAH BAGIAN 5
# =========================================================================
st.markdown("---")
st.title("Airfast Indonesia - Engineering Order Generator")
st.subheader("Formulir Input Data Engineering Order")

eo_number = st.text_input("Nomor EO", "EO/2026/001")
eo_title = st.text_input("Subject / Judul EO", "Inspection and Rectification of Aircraft System")
compliance_category = st.selectbox("Classification", ["Mandatory", "Recommended", "Optional"], key="eo_class")

eo_aircraft_type = st.selectbox(
    "Pilih Tipe Armada Pesawat (EO)",
    ["DHC6-300", "DHC6-400", "B737-8", "Bell412", "AS350B3", "MIL171"],
    key="eo_ac_type"
)

uploaded_custom_files = st.file_uploader(
    "Unggah Dokumen Pendukung / Foto Defect Tambahan (Opsional)",
    type=["pdf", "txt", "png", "jpg", "jpeg"],
    accept_multiple_files=True,
    key="eo_custom_files"
)

st.markdown("---")

if st.button("✨ Generate Draf Engineering Order (Form ATD-F-308)", key="btn_gen_eo"):
    with st.spinner("Membaca manual referensi spesifik & menyusun draf EO otomatis via AI..."):
        try:
            api_key = os.environ.get("GEMINI_API_KEY", st.session_state.get('user_api_key', ''))
            genai.configure(api_key=api_key)

            current_defect_topic = st.session_state.get('kasus_baru', eo_title)
            current_ata_topic = st.session_state.get('selected_ata', '')

            ref_text_content = ""
            folder_path = os.path.join("reference_library", eo_aircraft_type)
            if os.path.exists(folder_path):
                ref_files = [f for f in os.listdir(folder_path) if f.lower().endswith('.pdf')]
                
                for file_name in ref_files:
                    file_path = os.path.join(folder_path, file_name)
                    try:
                        with pdfplumber.open(file_path) as pdf:
                            for page_idx, page in enumerate(pdf.pages[:15]):
                                text = page.extract_text()
                                if text:
                                    text_upper = text.upper()
                                    if (current_ata_topic and current_ata_topic[:2] in text_upper) or \
                                       any(kw in text_upper for kw in current_defect_topic.upper().split() if len(kw) > 3):
                                        ref_text_content += f"\n\n--- SERVER MANUAL REF ({file_name} - Page {page_idx+1}) ---\n{text[:1000]}\n"
                                        break
                    except Exception:
                        continue

            if not ref_text_content and ref_files:
                file_path = os.path.join(folder_path, ref_files[0])
                with pdfplumber.open(file_path) as pdf:
                    for page_idx, page in enumerate(pdf.pages[:3]):
                        text = page.extract_text()
                        if text:
                            ref_text_content += f"\n\n--- SERVER MANUAL REF ({ref_files[0]}) ---\n{text}\n"

            if uploaded_custom_files:
                for file in uploaded_custom_files:
                    ref_text_content += f"\n\n--- USER UPLOAD ({file.name}) ---\n"
                    if file.name.lower().endswith('.pdf'):
                        with pdfplumber.open(file) as pdf:
                            for page_idx, page in enumerate(pdf.pages):
                                text = page.extract_text()
                                if text:
                                    ref_text_content += f"[Page {page_idx+1}]\n{text}\n"
                    elif file.name.lower().endswith('.txt'):
                        ref_text_content += file.read().decode('utf-8', errors='ignore')

            prompt_eo = f"""
Act as a Senior Technical Services & Reliability Engineer at PT Airfast Indonesia.
Your task is to draft a formal, high-standard Engineering Order (EO) following Form ATD-F-308 strictly based on the specific defect case and reference manuals below.

# Di dalam prompt Gemini (baik Analisis Utama, Feedback, maupun EO), bagian rujukan dokumen dapat disesuaikan secara dinamis:


SELECTED AIRCRAFT FLEET: {eo_aircraft_type}
- EO Number: {eo_number}
- Subject / Defect Focus: {eo_title} (Reported Defect Context: {current_defect_topic}, ATA Chapter: {current_ata_topic if current_ata_topic else 'Auto-detect'})
- Classification: {compliance_category}

RELEVANT REFERENCE TEXT EXTRACTED (SERVER & USER):
{ref_text_content if ref_text_content else "Use standard fleet references for this specific system."}

MANDATORY COMPLIANCE INSTRUCTIONS FOR THIS EO:
1. **AMM / WDM / FTD REFERENCES**: You MUST explicitly cite exact AMM tasks, WDM diagrams, or Boeing FTD bulletins in the reference and accomplishment sections. Never omit manual numbers.
2. **IFIM INTEGRATION**: Always check whether an applicable **IFIM (Fault Isolation Manual)** task exists for this defect symptom. If available, incorporate the step-by-step fault isolation procedure (troubleshooting logic tree) into the Accomplishment Instructions.

OUTPUT FORMAT INSTRUCTIONS:
- Write the entire draft in professional English.
- Follow the formal Airfast Indonesia Form ATD-F-308 structure precisely:
  1. Reason of Issuance
  2. Description & Background (Must detail the technical anomaly and background)
  3. Reference Documents (Explicitly list AMM, WDM, FTD, and IFIM tasks)
  4. Compliance & Applicability
  5. Tools & Parts Required
  6. Man Power Estimation
  7. Accomplishment Instructions (Must include step-by-step AMM/IFIM procedures)
  8. Return to Service (RTS)
  9. Authorization & Sign-Off
"""
            active_model = genai.GenerativeModel('gemini-3.8-flash')
            response = active_model.generate_content(prompt_eo)
            st.session_state['generated_eo_text'] = response.text
        except Exception as e:
            st.error(f"Gagal memproses draf EO: {e}")

def create_eo_pdf_bytes(eo_text):
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_margins(left=8, top=10, right=4)
    pdf.add_page()
    
    font_loaded = False
    if os.path.exists("fonts/calibri.ttf"):
        try:
            pdf.add_font("Calibri", "", "fonts/calibri.ttf", uni=True)
            pdf.add_font("Calibri", "B", "fonts/calibrib.ttf", uni=True)
            font_name = "Calibri"
            font_loaded = True
        except Exception:
            pass
            
    if not font_loaded and os.path.exists("C:/Windows/Fonts/calibri.ttf"):
        try:
            pdf.add_font("Calibri", "", "C:/Windows/Fonts/calibri.ttf", uni=True)
            pdf.add_font("Calibri", "B", "C:/Windows/Fonts/calibrib.ttf", uni=True)
            font_name = "Calibri"
            font_loaded = True
        except Exception:
            pass
            
    if not font_loaded:
        font_name = "Arial"

    pdf.set_font(font_name, "B", 14)
    pdf.cell(0, 8, "PT AIRFAST INDONESIA", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font(font_name, "B", 11)
    pdf.cell(0, 6, "ENGINEERING ORDER - FORM ATD-F-308", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)
    
    pdf.set_font(font_name, size=11)
    for line in eo_text.split('\n'):
        sanitized_line = (line.replace("–", "-")
                            .replace("—", "-")
                            .replace("“", '"')
                            .replace("”", '"')
                            .replace("‘", "'")
                            .replace("’", "'")
                            .replace("Ω", "Ohm")
                            .replace("±", "+/-")
                            .replace("°", " deg ")
                            .replace("|", " ")
                            .replace("---", "")
                            .replace("___", "")
                            .replace("**", "")
                            .replace("###", "")
                            .strip())
        if not sanitized_line:
            pdf.ln(3)
            continue
        pdf.multi_cell(0, 5.5, sanitized_line, align='L', new_x="LMARGIN", new_y="NEXT")
        
    return bytes(pdf.output())

if 'generated_eo_text' in st.session_state and st.session_state['generated_eo_text']:
    generated_eo_text = st.session_state['generated_eo_text']
    st.success("Engineering Order (Form ATD-F-308) English draft successfully generated!")
    st.markdown("### 📋 Official Engineering Order Draft (ATD-F-308 - English)")
    st.code(generated_eo_text, language="markdown")

    st.markdown("---")
    pdf_bytes = create_eo_pdf_bytes(generated_eo_text)
    st.download_button(
        label="📥 Download Official Engineering Order (PDF)",
        data=pdf_bytes,
        file_name=f"EO_{eo_number}.pdf",
        mime="application/pdf",
        key="download_eo_pdf"
    )
