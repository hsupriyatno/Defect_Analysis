import pandas as pd
import streamlit as st
import re
import io
import html
import requests
import json
import base64
import os
import google.generativeai as genai
from datetime import datetime
import pdfplumber  # Pastikan pdfplumber sudah terinstall (pip install pdfplumber)
import tempfile
# Import modul ReportLab untuk PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

# ---------------------------------------------------------
# INISIALISASI SESSION STATE (WAJIB ADA DI AWAL SCRIPT)
# ---------------------------------------------------------
if "iterations" not in st.session_state:
    st.session_state["iterations"] = []

if "ac_type" not in st.session_state:
    st.session_state["ac_type"] = "B737-8"

if "ac_reg" not in st.session_state:
    st.session_state["ac_reg"] = ""

if "selected_ata" not in st.session_state:
    st.session_state["selected_ata"] = ""

if "kasus_baru" not in st.session_state:
    st.session_state["kasus_baru"] = ""

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
def call_gemini_api(prompt_text, user_api_key="", uploaded_file=None):
    # 1. Prioritas utama: Ambil dari parameter yang dikirim ATAU dari session_state sidebar
    api_key = user_api_key.strip() if user_api_key else st.session_state.get('user_api_key', '').strip()
    
    # 2. Fallback cadangan: Jika masih kosong, coba ambil dari st.secrets
    if not api_key:
        try:
            if "GEMINI_API_KEY" in st.secrets:
                api_key = st.secrets["GEMINI_API_KEY"]
        except Exception:
            pass

    # 3. Jika benar-benar tidak ditemukan sama sekali
    if not api_key:
        raise ValueError("API Key tidak ditemukan. Silakan masukkan API Key di sidebar.")

    clean_api_key = str(api_key).strip().strip('"').strip("'")
    
    # Gunakan model aktif gemini-3.8-flash (atau gemini-2.0-flash)
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": clean_api_key
    }
    
    # Susun payload teks
    parts = [{"text": prompt_text}]
    
    # Tambahkan file jika ada lampiran (Gambar / PDF)
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
        "contents": [
            {
                "parts": parts
            }
        ]
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
# 1. FUNGSI PEMBANTU: PARSER TABEL MARKDOWN KE REPORTLAB
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
# 2. FUNGSI UTAMA: GENERATE LAPORAN PDF (DENGAN BAGIAN 5)
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

        # 1. Informasi Defect
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

        # Loop iterasi analisis
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
                    # Cek apakah ini diagram ASCII (mengandung pola struktur seperti +-- atau \--> atau ===)
                    is_ascii_diagram = ("+--" in chunk) or ("\\-->" in chunk) or ("===" in chunk) or ("---+" in chunk)
                    
                    if "```" in chunk or is_ascii_diagram:
                        clean_diagram = chunk.replace("```", "").strip()
                        story.append(Paragraph(html.escape(clean_diagram).replace('\n', '<br/>').replace(' ', '&nbsp;'), code_style))
                        story.append(Spacer(1, 4))
                    # Pastikan benar-benar tabel Markdown (memiliki baris pemisah |---|) DAN bukan diagram ASCII
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

        # 5. HISTORICAL RECORD SECTION (DITAMBAHKAN KEMBALI DI SINI)
        story.append(Spacer(1, 8))
        story.append(Paragraph(labels["sec5"], h2_style))
        
        if df_history is not None and not df_history.empty:
            hist_data = []
            th_style_h = ParagraphStyle('THist', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=7.5, textColor=colors.whitesmoke, alignment=1)
            td_style_h = ParagraphStyle('TDataH', parent=styles['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.darkslategray)
            td_center_h = ParagraphStyle('TDataCenterH', parent=styles['Normal'], fontName='Helvetica', fontSize=7, textColor=colors.darkslategray, alignment=1)

            # Header Tabel Histori
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

            # Lebar kolom proporsional total 515 pt
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
# MAPPING REGISTRASI FLEET & ATA CHAPTER 4-DIGIT
# ---------------------------------------------------------
AIRCRAFT_REG_MAP = {
    "B737-8": ["PK-OFI", "PK-OFM"],
    "DHC6-300": ["PK-OCJ", "PK-OCK", "PK-OCL"],
    "DHC6-400": ["PK-OAM", "PK-OCF", "PK-OCG", "PK-OCH", "PK-OCI"],
    "BELL-412": ["PK-OCA", "PK-OCD"],
    "AS350B3": ["PK-ODC"],
    "MIL-171": ["PK-OMI", "PK-OMS"]
}

# Daftar ATA Chapter 4-Digit Terstandar (Bisa ditambahkan/disesuaikan list-nya)
ATA_4DIGIT_OPTIONS = [
    "21-00 - AIR CONDITIONING",
    "21-20 - DISTRIBUTION",
    "21-21 - AIR DISTRIBUTION AND RECIRCULATION",
    "21-23 - LAVATORY/GALLEY VENTILATION",
    "21-24 - INDIVIDUAL AIR DISTRIBUTION",
    "21-26 - AVIONICS EQUIPMENT VENTILATION",
    "21-27 - AVIONICS EQUIPMENT GROUND COOLING",
    "21-28 - CARGO COMPARTMENT VENTILATION",
    "21-30 - PRESSURIZATION CONTROL",
    "21-31 - PRESSURE CONTROL AND MONITORING",
    "21-40 - HEATING",
    "21-41 - COCKPIT AIR HEATING",
    "21-43 - CARGO COMPARTMENT HEATING",
    "21-50 - AIR COOLING",
    "21-51 - FLOW CONTROL AND INDICATING",
    "21-52 - AIR COOLING SYSTEM",
    "21-53 - PACK COOLING AIR CONTROL",
    "21-55 - EMERGENCY RAM AIR INLET",
    "21-58 - CONDITIONED SERVICE AIR SYSTEM (CSAS)",
    "21-60 - TEMPERATURE CONTROL",
    "21-61 - PACK TEMPERATURE CONTROL",
    "21-63 - COCKPIT AND CABIN TEMPERATURE CONTROL",
    "22-00 - AUTO FLIGHT",
    "22-10 - AUTOPILOT/FLIGHT DIRECTOR (AP/FD)",
    "22-11 - AP/FD ENGAGEMENT",
    "22-12 - CRUISE MODES",
    "22-13 - COMMON MODES",
    "22-30 - AUTOTHRUST",
    "22-31 - AUTOTHRUST ENGAGEMENT",
    "22-32 - AUTOTHRUST MODES",
    "22-60 - FLIGHT AUGMENTATION COMPUTER (FAC)",
    "22-61 - ARTIFICIAL FEEL AND RUDDER TRAVEL LIMITING",
    "22-62 - RUDDER TRIM COMPUTATION",
    "22-63 - YAW DAMPER COMPUTATION",
    "22-64 - CONFIGURATION AND OPERATIONAL SPEED COMPUTATION",
    "22-65 - ENGAGEMENT AND INTERNAL MONITORING",
    "22-66 - FLIGHT AUGMENTATION COMPUTER (FAC)",
    "22-67 - FAC: ELECTRICAL AND HYDRAULIC POWER SUPPLY",
    "22-68 - FAC: DISCRETE/ANALOG INTERFACES",
    "22-69 - FAC: ARINC BUS INTERFACES",
    "22-70 - FLIGHT MANAGEMENT SYSTEM (FMS)",
    "22-71 - FLIGHT PLAN/DATA BASE",
    "22-72 - NAVIGATION/LATERAL FUNCTIONS",
    "22-73 - PERFORMANCE/VERTICAL FUNCTIONS",
    "22-74 - OPERATIONAL USE",
    "22-75 - EIS DISPLAYS",
    "22-76 - DATA LINK AND PRINTER FUNCTIONS",
    "22-81 - FLIGHT CONTROL UNIT (FCU)",
    "22-82 - MULTIPURPOSE CONTROL AND DISPLAY UNIT (MCDU)",
    "22-83 - FLIGHT MANAGEMENT AND GUIDANCE COMPUTER (FMGC)",
    "22-84 - FMGS: ELECTRICAL POWER SUPPLY",
    "22-85 - FMGS: DISCRETE/ANALOG INTERFACES",
    "22-86 - FMGS: BUS INTERFACES",
    "22-91 - FAULT ISOLATION FUNCTION - PRINCIPLE",
    "22-92 - AFS FAULT ISOLATION",
    "22-96 - AFS TEST",
    "22-97 - LAND CAT III CAPABILITY TEST",
    "23-00 - COMMUNICATIONS",
    "23-11 - HF SYSTEM",
    "23-12 - VHF SYSTEM",
    "23-13 - RADIO MANAGEMENT",
    "23-28 - SATELLITE COMMUNICATION",
    "23-30 - PASSENGER ADDRESS AND ENTERTAINMENT",
    "23-31 - PASSENGER ADDRESS SYSTEM",
    "23-32 - ANNOUNCEMENT - MUSIC TAPE REPRODUCER SYSTEM",
    "23-33 - PASSENGER ENTERTAINMENT",
    "23-36 - PASSENGER ENTERTAINMENT",
    "23-42 - COCKPIT TO GROUND CREW CALL SYSTEM",
    "23-43 - FLIGHT CREW INTERPHONE",
    "23-44 - CABIN AND SERVICE INTERPHONE",
    "23-51 - AUDIO MANAGEMENT SYSTEM (AMS) ",
    "23-52 - RADIO AND AUDIO MANAGEMENT PANELS (RMP) ",
    "23-60 - STATIC DISCHARGING",
    "23-61 - STATIC DISCHARGER",
    "23-71 - COCKPIT VOICE RECORDER",
    "23-72 - ANTI HIJACK CAMERA MONITORING",
    "23-73 - CABIN INTERCOMMUNICATION DATA SYSTEM (CIDS) ",
    "24-00 - ELECTRICAL POWER",
    "24-20 - AC GENERATION",
    "24-21 - INTEGRATED DRIVE GENERATOR SYSTEM (IDG, GCU) ",
    "24-22 - AC MAIN GENERATION",
    "24-23 - AC AUXILIARY GENERATION",
    "24-24 - AC EMERGENCY GENERATION",
    "24-25 - AC ESSENTIAL GENERATION SWITCHING",
    "24-26 - GALLEY SUPPLY CONTROL",
    "24-27 - AC GENERATION MONITORING AND INDICATING",
    "24-28 - STATIC INVERTER",
    "24-30 - DC GENERATION",
    "24-32 - DC MAIN GENERATION",
    "24-34 - DC EMERGENCY GENERATION",
    "24-35 - DC ESSENTIAL & NORMAL GENERATION SWITCHING",
    "24-37 - DC GENERATION MONITORING AND INDICATING",
    "24-38 - DC GENERATION - BATTERIES",
    "24-40 - EXTERNAL POWER",
    "24-41 - AC EXTERNAL POWER CONTROL",
    "24-42 - AC GROUND SERVICE BUS CONTROL",
    "24-43 - DC GROUND SERVICE BUS CONTROL",
    "24-50 - AC ELECTRICAL LOAD DISTRIBUTION",
    "24-51 - AC MAIN DISTRIBUTION",
    "24-52 - AC ESSENTIAL DISTRIBUTION",
    "24-56 - GALLEY SUPPLY",
    "24-60 - DC ELECTRICAL LOAD DISTRIBUTION",
    "24-61 - DC MAIN DISTRIBUTION",
    "24-62 - DC ESSENTIAL DISTRIBUTION",
    "24-67 - REFUELING ON BATTERY",
    "24-92 - ELECTRICAL ROUTING",
    "25-00 - EQUIPMENT/FURNISHINGS",
    "25-10 - COCKPIT",
    "25-11 - COCKPIT SEATS",
    "25-13 - LINING AND FURNISHINGS",
    "25-14 - COCKPIT EQUIPMENT RACKS",
    "25-15 - FLIGHT CREW FOOT WARMERS",
    "25-18 - COCKPIT ADDITIONAL ELECTRICAL SUPPLY",
    "25-20 - PASSENGER COMPARTMENT",
    "25-21 - PASSENGER COMPARTMENT SEATS",
    "25-22 - CABIN ATTENDANT SEATS",
    "25-23 - LININGS AND FURNISHINGS",
    "25-24 - OVERHEAD STOWAGE COMPARTMENTS",
    "25-25 - PASSENGER SERVICE INFORMATION UNITS (PSIU) ",
    "25-26 - CURTAINS AND PARTITIONS",
    "25-27 - ANCILLARY EQUIPMENT",
    "25-28 - FLOOR COVERING",
    "25-29 - ELECTRICAL SERVICE SUPPLY",
    "25-30 - BUFFET AND GALLEY",
    "25-31 - FORWARD GALLEY",
    "25-33 - AFT GALLEY",
    "25-34 - GALLEY COOLING",
    "25-35 - GALLEY EQUIPMENT",
    "25-40 - LAVATORIES",
    "25-41 - FORWARD LAVATORIES",
    "25-43 - AFT LAVATORIES",
    "25-44 - RAZOR SUPPLY",
    "25-45 - LAVATORY EQUIPMENT",
    "25-50 - CARGO COMPARTMENTS",
    "25-52 - SEMI AUTOMATIC CARGO LOADING",
    "25-54 - LININGS AND FURNISHINGS/FORWARD CARGO COMPARTMENT",
    "25-55 - LININGS AND FURNISHINGS/AFT CARGO COMPARTMENT",
    "25-60 - EMERGENCY",
    "25-61 - ESCAPE FACILITIES - COCKPIT",
    "25-62 - ESCAPE FACILITIES - CABIN",
    "25-63 - EVACUATION SIGNALING EQUIPMENT",
    "25-64 - FIRST AID EQUIPMENT",
    "25-65 - MISCELLANEOUS EMERGENCY EQUIPMENT",
    "25-66 - FLOATATION AND SURVIVAL EQUIPMENT",
    "25-68 - RAPID DECOMPRESSION SAFETY SYSTEM",
    "25-70 - ACCESSORY COMPARTMENTS",
    "25-71 - AVIONICS COMPARTMENT",
    "25-80 - INSULATION (THERMAL AND ACOUSTICAL) ",
    "26-00 - FIRE PROTECTION",
    "26-10 - DETECTION",
    "26-12 - ENGINE FIRE AND OVERHEAT DETECTION",
    "26-13 - APU FIRE AND OVERHEAT DETECTION",
    "26-15 - AVIONICS COMPARTMENT SMOKE DETECTION",
    "26-16 - CARGO COMPARTMENT SMOKE DETECTION",
    "26-17 - LAVATORY SMOKE DETECTION",
    "26-20 - EXTINGUISHING",
    "26-21 - ENGINE FIRE EXTINGUISHING",
    "26-22 - APU FIRE EXTINGUISHING",
    "26-23 - CARGO COMPARTMENT FIRE EXTINGUISHING",
    "26-24 - PORTABLE FIRE EXTINGUISHER",
    "26-25 - LAVATORY FIRE EXTINGUISHING",
    "27-00 - FLIGHT CONTROLS",
    "27-10 - AILERON",
    "27-14 - AILERON AND HYDRAULIC ACTUATION",
    "27-20 - RUDDER",
    "27-21 - RUDDER CONTROL",
    "27-22 - RUDDER TRIM ACTUATION",
    "27-23 - ARTIFICIAL FEEL AND RUDDER TRAVEL LIMITING - ACTUATION",
    "27-24 - RUDDER HYDRAULIC ACTUATION",
    "27-25 - RUDDER POSITION INDICATING",
    "27-26 - YAW DAMPER ACTUATION",
    "27-30 - ELEVATOR",
    "27-34 - ELEVATOR AND HYDRAULIC ACTUATION",
    "27-40 - TRIMMABLE HORIZONTAL STABILIZER (THS) ",
    "27-41 - THS MECHANICAL CONTROL",
    "27-44 - THS HYDRAULIC ACTUATION",
    "27-45 - THS POSITION INDICATING",
    "27-50 - FLAPS",
    "27-51 - FLAPS ELECTRICAL CONTROL AND MONITORING",
    "27-54 - FLAPS HYDRAULIC ACTUATION AND POWER TRANSMISSION",
    "27-55 - FLAPS POSITION INDICATING",
    "27-60 - SPOILER",
    "27-64 - SPOILER HYDRAULIC ACTUATION",
    "27-80 - LIFT AUGMENTING",
    "27-81 - SLATS ELECTRICAL CONTROL AND MONITORING",
    "27-84 - SLATS HYDRAULIC ACTUATION AND POWER TRANSMISSION",
    "27-85 - SLATS POSITION INDICATING",
    "27-90 - ELECTRICAL FLIGHT CONTROL SYSTEM (EFCS) ",
    "27-92 - CONTROL INPUTS INTERFACE AND POWER SUPPLY",
    "27-93 - ELEVATOR AILERON CONTROL SYSTEM",
    "27-94 - SPOILER ELEVATOR CONTROL SYSTEM",
    "27-95 - FLIGHT CONTROL DATA CONCENTRATOR (FCDC) ",
    "27-96 - MAINTENANCE AND SAFETY TESTS/BITE",
    "28-00 - FUEL",
    "28-10 - STORAGE",
    "28-11 - TANKS",
    "28-12 - TANK VENTING SYSTEM",
    "28-13 - WING CENTER TANK EXTERNAL VENTILATING SYSTEM",
    "28-15 - INTERCELL TRANSFER",
    "28-16 - FUEL RECIRCULATION - COOLING",
    "28-18 - TANK IGNITION PREVENTION SYSTEM",
    "28-20 - DISTRIBUTION",
    "28-21 - MAIN FUEL PUMP SYSTEM",
    "28-22 - APU FUEL PUMP SYSTEM",
    "28-23 - CROSSFEED SYSTEM",
    "28-24 - ENGINE LP FUEL SHUT OFF",
    "28-25 - REFUEL/DEFUEL SYSTEM",
    "28-26 - MAIN TRANSFER SYSTEM",
    "28-28 - ADDITIONAL CENTER TANK TRANSFER SYSTEM",
    "28-29 - APU LP FUEL SHUT OFF",
    "28-40 - INDICATING",
    "28-42 - QUANTITY INDICATING",
    "28-43 - MANUAL (MAGNETIC) INDICATORS",
    "28-46 - TANK LEVEL SENSING",
    "28-51 - AUXILIARY FUEL MANAGEMENT COMPUTER (AFMC) - OPERATIONAL USE",
    "29-00 - HYDRAULIC POWER",
    "29-10 - MAIN HYDRAULIC POWER",
    "29-11 - GREEN MAIN HYDRAULIC POWER",
    "29-12 - BLUE MAIN HYDRAULIC POWER",
    "29-13 - YELLOW MAIN HYDRAULIC POWER",
    "29-14 - HYDRAULIC RESERVOIR PRESSURIZING SYSTEM",
    "29-16 - HYDRAULIC RESERVOIR FILLING",
    "29-17 - SEAL DRAIN SYSTEM",
    "29-19 - GROUND INTERNAL LEAK TEST SYSTEM",
    "29-20 - AUXILIARY HYDRAULIC POWER",
    "29-22 - BLUE AUXILIARY HYDRAULIC POWER",
    "29-23 - POWER TRANSFER",
    "29-24 - YELLOW AUXILIARY HYDRAULIC POWER",
    "29-30 - INDICATING",
    "29-31 - HYDRAULIC FLUID QUANTITY INDICATING",
    "29-32 - HYDRAULIC FLUID PRESSURE INDICATING",
    "29-33 - HYDRAULIC FLUID TEMPERATURE INDICATING",
    "29-34 - RESERVOIR PRESSURIZATION INDICATING",
    "30-00 - ICE AND RAIN PROTECTION",
    "30-10 - AIRFOIL",
    "30-11 - WING ICE PROTECTION",
    "30-21 - ENGINE AIR INTAKE ICE PROTECTION",
    "30-31 - PROBE ICE PROTECTION",
    "30-42 - WINDSHIELD ANTI-ICING AND DEFOGGING",
    "30-45 - WINDSHIELD RAIN PROTECTION",
    "30-71 - DRAIN MAST ICE PROTECTION",
    "30-72 - WATER SERVICING PANELS HEATING",
    "30-73 - POTABLE WATER ICE PROTECTION",
    "30-80 - DETECTION",
    "30-81 - ICE DETECTION",
    "31-00 - INDICATING/RECORDING SYSTEMS",
    "31-10 - INSTRUMENTS AND CONTROL PANELS",
    "31-14 - OVERHEAD PANEL",
    "31-17 - MISCELLANEOUS CABIN/CARGO PANELS",
    "31-21 - ELECTRICAL CLOCK",
    "31-30 - CENTRALIZED FAULT DISPLAY SYSTEM (CFDS) AND DATA RECORDING SYSTEM",
    "31-32 - CENTRALIZED FAULT DISPLAY INTERFACE UNIT (CFDIU)",
    "31-33 - DIGITAL FLIGHT DATA RECORDING SYSTEM (DFDRS) - INTERCONNECTION",
    "31-35 - MULTIFUNCTION PRINTING",
    "31-36 - AIRCRAFT CONDITION MONITORING INTERCONNECTION",
    "31-37 - AIRCRAFT CONDITION MONITORING INPUT INTERFACE",
    "31-38 - UP AND DOWN DATA LOADING SYSTEM ACQUISITION/INTERFACE AND EQUIPMENT",
    "31-39 - COMBINED VOICE AND DATA RECORDING / RECORDING AND AIRCRAFT LOCATING SYSTEM",
    "31-50 - CENTRAL WARNING SYSTEMS",
    "31-51 - OPERATIONAL PRESENTATION",
    "31-52 - FWC - ACQUISITION/INTERFACE",
    "31-53 - FLIGHT WARNING COMPUTER (FWC)",
    "31-54 - SDAC - ACQUISITION/INTERFACE",
    "31-55 - SYSTEM DATA ACQUISITION CONCENTRATOR (SDAC)",
    "31-56 - ECAM CONTROL PANEL",
    "31-60 - CENTRAL WARNING SYSTEMS",
    "31-61 - EIS - CONTROL",
    "31-62 - DMC - ACQUISITION/INTERFACE",
    "31-63 - DISPLAY MANAGEMENT COMPUTER (DMC)",
    "31-64 - PRIMARY FLIGHT DISPLAY (PFD)",
    "31-65 - NAVIGATION DISPLAY (ND)",
    "31-66 - ECAM ENGINE/WARNING DISPLAY",
    "31-67 - ECAM SYSTEM/STATUS DISPLAY",
    "31-68 - EIS - SWITCHING",
    "31-69 - EIS - TEST/BITE",
    "32-00 - LANDING GEAR",
    "32-10 - MAIN GEAR AND DOORS",
    "32-11 - MAIN GEAR",
    "32-12 - MAIN GEAR DOORS",
    "32-20 - NOSE GEAR AND DOORS",
    "32-21 - NOSE GEAR",
    "32-22 - NOSE GEAR DOORS",
    "32-30 - EXTENSION AND RETRACTION",
    "32-31 - NORMAL EXTENSION AND RETRACTION",
    "32-33 - FREE FALL EXTENSION",
    "32-40 - WHEELS AND BRAKES",
    "32-41 - WHEELS",
    "32-42 - NORMAL BRAKING",
    "32-43 - ALTERNATE BRAKING WITH ANTI SKID",
    "32-44 - ALTERNATE BRAKING WITHOUT ANTI SKID",
    "32-45 - PARKING/ULTIMATE EMERGENCY BRAKING",
    "32-46 - BRAKES AND STEERING - BITE",
    "32-47 - BRAKE SYSTEM TEMPERATURE",
    "32-48 - BRAKE COOLING",
    "32-49 - TIRE PRESSURE INDICATING SYSTEM (TPIS)",
    "32-51 - STEERING",
    "32-60 - POSITION AND WARNING",
    "32-61 - INDICATING AND WARNING",
    "32-62 - LANDING GEAR - POSITION INFORMATION",
    "32-69 - LANDING GEAR - BITE",
    "33-00 - LIGHTS",
    "33-10 - COCKPIT",
    "33-12 - GENERAL ILLUMINATION",
    "33-13 - INSTRUMENT AND PANEL INTEGRAL LIGHTING",
    "33-14 - ANNUNCIATOR LIGHT TEST AND DIMMING",
    "33-20 - CABIN",
    "33-21 - GENERAL ILLUMINATION",
    "33-23 - CALL SYSTEM",
    "33-24 - LAVATORY LIGHTING",
    "33-25 - PASSENGER READING LIGHTS",
    "33-26 - LAVATORY LIGHTED SIGNS",
    "33-27 - LIGHT - WORK, CABIN ATTENDANT",
    "33-30 - CARGO AND SERVICE COMPARTMENTS",
    "33-31 - SERVICE AREA LIGHTING",
    "33-32 - AIR CONDITIONING DUCT AND ACCESSORY COMPARTMENT LIGHTING",
    "33-34 - FWD - AFT CARGO COMPARTMENT LIGHTING",
    "33-35 - AVIONICS COMPARTMENT LIGHTING",
    "33-37 - WHEEL WELL LIGHTING",
    "33-40 - EXTERIOR",
    "33-41 - NAVIGATION LIGHTS",
    "33-42 - LANDING LIGHTS",
    "33-43 - RUNWAY TURNOFF LIGHTS",
    "33-46 - TAXI AND TAKE OFF LIGHTS",
    "33-47 - LOGO LIGHTS",
    "33-48 - ANTI-COLLISION/STROBE LIGHTING",
    "33-49 - WING AND ENGINE SCAN LIGHTING",
    "33-50 - EMERGENCY LIGHTING",
    "33-51 - CABIN EMERGENCY LIGHTING",
    "34-00 - NAVIGATION",
    "34-10 - AIR DATA/INERTIAL REFERENCE (ADIRS)",
    "34-11 - SENSORS, POWER SUPPLY AND SWITCHING",
    "34-12 - AIR DATA/INERTIAL REFERENCE SYSTEM",
    "34-13 - AIR DATA",
    "34-14 - ATTITUDE, HEADING AND POSITION",
    "34-18 - ADIRS - BITE",
    "34-20 - STANDBY NAVIGATION SYSTEMS",
    "34-21 - STANDBY DATA : ALTITUDE AND AIRSPEED",
    "34-22 - STANDBY DATA : ATTITUDE AND HEADING",
    "34-36 - ILS",
    "34-41 - WEATHER RADAR SYSTEM",
    "34-42 - RADIO ALTIMETER",
    "34-43 - TRAFFIC AND TERRAIN COLLISION AVOIDANCE SYSTEM",
    "34-48 - GROUND PROXIMITY WARNING SYSTEM (GPWS)",
    "34-51 - DME",
    "34-52 - ATC/MODE S (SELECT) ",
    "34-53 - ADF",
    "34-55 - VOR/MARKER",
    "34-57 - RADIO MAGNETIC INFORMATION - SWITCHING AND INDICATING",
    "34-58 - SATELLITE NAVIGATION",
    "34-72 - TRAFFIC AND TERRAIN INTEGRATED SURVEILLANCE SYSTEM (T/TISS) ",
    "35-00 - OXYGEN",
    "35-10 - CREW OXYGEN",
    "35-11 - OXYGEN STORAGE",
    "35-12 - OXYGEN DISTRIBUTION",
    "35-13 - CONTROL AND INDICATING",
    "35-20 - PASSENGER OXYGEN",
    "35-21 - OXYGEN STORAGE",
    "35-22 - OXYGEN DISTRIBUTION",
    "35-23 - CONTROL AND INDICATING",
    "35-30 - PORTABLE OXYGEN",
    "35-31 - FLIGHT CREW PORTABLE DEVICES",
    "35-32 - CABIN ATTENDANTS PORTABLE DEVICES",
    "36-00 - PNEUMATIC",
    "36-10 - DISTRIBUTION",
    "36-11 - ENGINE BLEED AIR SUPPLY SYSTEM",
    "36-12 - APU BLEED AIR SUPPLY AND CROSSBLEED SYSTEMS",
    "36-13 - GROUND COMPRESSED AIR SUPPLY SYSTEM",
    "36-14 - ENVIRONMENT PROTECTION",
    "36-20 - INDICATING",
    "36-21 - PRESSURE AND TEMPERATURE MONITORING",
    "36-22 - LEAK DETECTION",
    "38-00 - WATER/WASTE",
    "38-10 - POTABLE",
    "38-11 - WATER STORAGE",
    "38-12 - DISTRIBUTION",
    "38-13 - QUANTITY INDICATING",
    "38-14 - WATER DRAINING (DUMP) ",
    "38-30 - WASTE DISPOSAL",
    "38-31 - TOILET SYSTEM",
    "38-32 - WASTE WATER DRAIN",
    "38-40 - AIR SUPPLY",
    "38-41 - AIR SUPPLY SYSTEM",
    "38-42 - AIR SUPPLY BOOST SYSTEM",
    "44-33 - BROADBAND COMMUNICATION SYSTEM",
    "44-34 - ON-BOARD MOBILE TELEPHONY SYSTEM",
    "46-00 - INFORMATION SYSTEMS",
    "46-20 - FLIGHT DECK INFORMATION SYSTEMS",
    "46-21 - AIR TRAFFIC AND INFORMATION MANAGEMENT SYSTEM (ATIMS) - COMPONENTS AND INTERFACES",
    "46-22 - ATIMS - AIR TRAFFIC SERVICES (ATS) ",
    "46-23 - ATIMS - AIRLINE INFORMATION SERVICES",
    "46-24 - ATIMS - ROUTER",
    "46-26 - FLIGHT OPERATION PERIPHERALS",
    "47-00 - INERT GAS SYSTEM",
    "47-10 - GENERATION/STORAGE",
    "47-11 - INERT GAS GENERATION SYSTEM (IGGS) ",
    "47-20 - DISTRIBUTION",
    "47-21 - DISTRIBUTION CONTROL",
    "47-31 - SUPPLY CONTROL",
    "47-41 - SUPPLY INDICATING",
    "49-00 - AIRBORNE AUXILIARY POWER",
    "49-10 - POWER PLANT",
    "49-11 - POWER PLANT",
    "49-12 - APU MOUNTS",
    "49-13 - FIRE SEALS",
    "49-15 - ELECTRICAL HARNESS",
    "49-16 - AIR INTAKE SYSTEM",
    "49-17 - POWER PLANT DRAIN SYSTEM",
    "49-20 - ENGINE",
    "49-21 - POWER SECTION",
    "49-23 - LOAD COMPRESSOR",
    "49-26 - ACCESSORY DRIVE GEARBOX",
    "49-30 - ENGINE FUEL AND CONTROL",
    "49-31 - FUEL DISTRIBUTION",
    "49-32 - APU FUEL CONTROL",
    "49-40 - IGNITION AND STARTING",
    "49-41 - IGNITION",
    "49-42 - STARTING",
    "49-50 - AIR",
    "49-51 - BLEED AND SURGE AIR",
    "49-52 - ACCESSORY COOLING",
    "49-60 - ENGINE CONTROLS",
    "49-61 - CONTROL AND MONITORING",
    "49-62 - EMERGENCY SHUTDOWN",
    "49-70 - INDICATING",
    "49-71 - POWER INDICATING",
    "49-72 - TEMPERATURE INDICATING",
    "49-73 - ANALYZERS",
    "49-80 - EXHAUST",
    "49-81 - EXHAUST SYSTEM",
    "49-90 - OIL",
    "49-91 - STORAGE AND DISTRIBUTION",
    "49-93 - OIL INDICATING",
    "49-94 - LOW OIL PRESSURE AND HIGH OIL TEMPERATURE WARNING",
    "49-96 - APU - OIL HEATING",
    "51-00 - STANDARD PRACTICES AND STRUCTURES",
    "51-21 - PROTECTIVE TREATMENT",
    "51-22 - CORROSION PREVENTION",
    "51-23 - COATINGS APPLICATION - GENERAL",
    "51-24 - SEALING - GENERAL",
    "51-70 - REPAIRS",
    "51-73 - REPAIRS OF MINOR DAMAGE",
    "51-74 - REPAIRS OF CORRODED AREAS",
    "51-75 - PAINT REPAIRS",
    "51-76 - SEALING REPAIRS",
    "51-77 - STANDARD COMPOSITE REPAIRS",
    "51-78 - CLEANING PROCESSES",
    "52-00 - DOORS",
    "52-10 - PASSENGER/CREW",
    "52-11 - FWD PASSENGER/CREW DOOR",
    "52-13 - AFT PASSENGER/CREW DOOR",
    "52-20 - EMERGENCY EXIT",
    "52-21 - PASSENGER COMPARTMENT EMERGENCY EXIT",
    "52-22 - PASSENGER COMPARTMENT EMERGENCY EXIT",
    "52-30 - CARGO",
    "52-31 - FORWARD CARGO COMPARTMENT DOOR",
    "52-32 - AFT CARGO COMPARTMENT DOOR",
    "52-33 - BULK CARGO COMPARTMENT DOOR",
    "52-35 - CARGO COMPARTMENT DOOR ELECTRICAL CONTROL SYSTEM",
    "52-36 - CARGO COMPARTMENT DOOR HYDRAULIC SYSTEM",
    "52-40 - SERVICE",
    "52-41 - ACCESS DOORS",
    "52-42 - SERVICE DOORS",
    "52-50 - FIXED INTERIOR",
    "52-51 - PASSENGER COMPARTMENT FIXED INTERIOR DOORS",
    "52-70 - DOOR WARNING",
    "52-71 - DOOR MONITORING AND FLIGHT LOCK FUNCTION",
    "52-73 - EMERGENCY ESCAPE SLIDE RELEASE AND OVERPRESSURE WARNING SYSTEMS",
    "52-80 - LANDING GEAR",
    "52-81 - MAIN GEAR DOORS",
    "52-82 - NOSE GEAR DOORS",
    "52-90 - MISCELLANEOUS DOORS",
    "52-91 - DOORS - RAM AIR TURBINE",
    "52-92 - DOOR - OVERPRESSURE RELEASE, APU COMPARTMENT",
    "53-00 - FUSELAGE",
    "53-10 - NOSE FORWARD FUSELAGE",
    "53-11 - MAIN STRUCTURE",
    "53-12 - FLOOR PANELS",
    "53-15 - FAIRINGS",
    "53-16 - FITTINGS",
    "53-19 - MISCELLANEOUS",
    "53-20 - FORWARD FUSELAGE",
    "53-21 - MAIN STRUCTURE",
    "53-22 - FLOOR PANELS",
    "53-23 - SUPPORT STRUTS",
    "53-25 - FAIRINGS",
    "53-29 - MISCELLANEOUS",
    "53-30 - CENTER FUSELAGE",
    "53-31 - MAIN STRUCTURE",
    "53-32 - FLOOR PANELS",
    "53-33 - SUPPORT STRUTS",
    "53-35 - FAIRINGS",
    "53-36 - FITTINGS",
    "53-39 - MISCELLANEOUS",
    "53-40 - REAR FUSELAGE",
    "53-41 - MAIN STRUCTURE",
    "53-42 - FLOOR PANELS",
    "53-43 - SUPPORT STRUTS",
    "53-45 - FAIRINGS",
    "53-46 - FITTINGS",
    "53-49 - MISCELLANEOUS",
    "53-50 - CONE/REAR FUSELAGE",
    "53-51 - MAIN STRUCTURE",
    "53-53 - SUPPORT STRUTS",
    "53-55 - FAIRINGS",
    "53-56 - FITTINGS",
    "53-58 - TAIL CONE",
    "53-59 - MISCELLANEOUS",
    "54-00 - NACELLES/PYLONS",
    "54-10 - NACELLE SECTION",
    "54-50 - PYLONS",
    "54-51 - PYLON BOX",
    "54-52 - FORWARD FAIRING",
    "54-53 - PYLON-TO-WING CENTER FILLETS",
    "54-54 - AFT FIXED FAIRING",
    "54-55 - AFT MOVABLE FAIRING",
    "54-56 - LOWER FAIRING",
    "54-57 - PYLON-TO-NACELLE JUNCTION",
    "55-00 - STABILIZERS",
    "55-10 - HORIZONTAL STABILIZER",
    "55-11 - TRIMMABLE HORIZONTAL STABILIZER SPAR BOX",
    "55-12 - TRIMMABLE HORIZONTAL STABILIZER LEADING EDGE",
    "55-13 - TRIMMABLE HORIZONTAL STABILIZER TRAILING EDGE",
    "55-14 - TRIMMABLE HORIZONTAL STABILIZER TIPS",
    "55-15 - TRIMMABLE HORIZONTAL STABILIZER APRON",
    "55-16 - TRIMMABLE HORIZONTAL STABILIZER ATTACH FITTINGS",
    "55-20 - ELEVATORS",
    "55-21 - ELEVATOR SPAR BOX",
    "55-22 - ELEVATOR LEADING EDGE",
    "55-24 - ELEVATOR TIPS AND INBOARD END CAPS",
    "55-26 - ELEVATOR ATTACH FITTINGS",
    "55-30 - VERTICAL STABILIZER",
    "55-31 - VERTICAL STABILIZER SPAR BOX",
    "55-32 - VERTICAL STABILIZER LEADING EDGE",
    "55-33 - VERTICAL STABILIZER TRAILING EDGE",
    "55-34 - VERTICAL STABILIZER TIP",
    "55-36 - VERTICAL STABILIZER ATTACH FITTINGS",
    "55-40 - RUDDER",
    "55-41 - RUDDER MAIN STRUCTURE",
    "55-42 - RUDDER LEADING EDGE",
    "55-43 - RUDDER FILLET FAIRING",
    "55-44 - RUDDER TIP",
    "55-46 - RUDDER ATTACH FITTINGS",
    "56-00 - WINDOWS",
    "56-10 - COCKPIT",
    "56-11 - FIXED WINDOWS",
    "56-12 - SLIDING WINDOWS",
    "56-21 - CABIN WINDOWS",
    "56-22 - CABIN DUMMY WINDOW",
    "56-31 - PASSENGER/CREW DOOR WINDOWS",
    "57-00 - WINGS",
    "57-10 - CENTER WING",
    "57-17 - ACCESS COVERS",
    "57-20 - OUTER WING",
    "57-21 - MAIN STRUCTURE",
    "57-24 - FIXED PARTITION",
    "57-26 - FITTINGS",
    "57-27 - ACCESS COVERS",
    "57-29 - MISCELLANEOUS",
    "57-30 - WING TIP",
    "57-31 - MAIN STRUCTURE",
    "57-37 - ACCESS DOORS",
    "57-39 - MISCELLANEOUS",
    "57-40 - LEADING EDGE AND LEADING EDGE DEVICES",
    "57-41 - LEADING EDGE",
    "57-42 - SLAT 1",
    "57-43 - SLAT 2",
    "57-44 - SLAT 3",
    "57-45 - SLAT 4",
    "57-46 - SLAT 5",
    "57-50 - TRAILING EDGE AND TRAILING EDGE DEVICES",
    "57-51 - TRAILING EDGE",
    "57-52 - INBOARD FLAP",
    "57-53 - OUTBOARD FLAP",
    "57-55 - FAIRINGS",
    "57-59 - MISCELLANEOUS",
    "57-60 - AILERONS",
    "57-61 - AILERONS - MAIN STRUCTURE",
    "57-70 - SPOILERS",
    "57-71 - SPOILER 1",
    "57-72 - SPOILER 2",
    "57-73 - SPOILER 3",
    "57-74 - SPOILER 4",
    "57-75 - SPOILER 5",
    "70-00 -  STANDARD PRACTICES - ENGINE",
    "70-10 - SAFETY PRACTICES",
    "70-24 - PRECAUTIONARY MEASURES",
    "70-25 - NON-DESTRUCTIVE TESTING",
    "70-26 - INSPECTION/CHECK",
    "70-27 - CLEANING",
    "70-28 - REPAIR",
    "70-30 - LIST OF MATERIALS",
    "70-40 - IDENTIFICATION",
    "70-50 - MISCELLANEOUS",
    "70-60 - STANDARD PRACTICES - NACELLE/ENGINE BUILD-UP UNIT (EBU)",
    "70-70 - WIRING",
    "70-71 - POWER PLANT WIRING STANDARD PRACTICES - ATA INDEX",
    "70-80 - PRESERVATION",
    "70-00 - STANDARD PRACTICES - ENGINE - GENERAL",
    "70-11 - PRECAUTIONARY MEASURES TO BE TAKEN DURING REMOVAL/INSTALLATION",
    "70-21 - SEALS",
    "70-22 - LOCKING",
    "70-23 - TIGHTENING TORQUES",
    "70-25 - NON-DESTRUCTIVE TESTING",
    "70-27 - CLEANING",
    "70-28 - REPAIR",
    "70-30 - LIST OF MATERIALS",
    "70-51 - FLUORESCENT PENETRANT INSPECTION - PORTABLE - WATER WASHABLE",
    "70-52 - EDDY CURRENT INSPECTION OF FAN BLADE LEADING EDGE",
    "70-60 - STANDARD PRACTICES - NACELLE/ENGINE BUILD-UP UNIT (EBU)",
    "70-71 - POWER PLANT WIRING STANDARD PRACTICES - ATA INDEX",
    "71-00 - POWER PLANT - GENERAL",
    "71-13 - FAN COWL",
    "71-21 - FORWARD MOUNT",
    "71-22 - AFT MOUNT",
    "71-40 - ATTACH FITTINGS",
    "71-51 - ELECTRICAL HARNESS - GENERAL WIRING - FAN ZONE AND EQUIPMENT LIST",
    "71-52 - ELECTRICAL HARNESS - GENERAL WIRING - CORE ZONE AND EQUIPMENT LIST",
    "71-60 - AIR INTAKE",
    "71-61 - AIR INTAKE COWL",
    "71-70 - ENGINE DRAINS",
    "71-71 - DRAIN SYSTEM",
    "71-00 - POWER PLANT - GENERAL",
    "71-10 - COWLING",
    "71-11 - AIR INTAKE COWL",
    "71-13 - FAN COWL",
    "71-20 - MOUNTS",
    "71-21 - FORWARD MOUNT",
    "71-22 - AFT MOUNT",
    "71-40 - ATTACH FITTINGS",
    "71-50 - ELECTRICAL HARNESS",
    "71-51 - ELECTRICAL HARNESS - GENERAL WIRING - FAN ZONE AND EQUIPMENT LIST",
    "71-52 - ELECTRICAL HARNESS - GENERAL WIRING - CORE ZONE AND EQUIPMENT LIST",
    "71-70 - POWER PLANT DRAINS",
    "71-71 - DRAIN SYSTEM",
    "71-73 - OIL/HYD/WATER DRAINS",
    "72-62 - (CFMI) - TRANSFER GEARBOX ASSEMBLY",
    "72-00 - ENGINE - GENERAL",
    "72-20 - AIR INLET SECTION",
    "72-21 - FAN AND BOOSTER MODULE",
    "72-22 - NO.1 AND NO.2 BEARING SUPPORT MODULE",
    "72-23 - FAN CASE MODULE",
    "72-24 - FAN FRAME MODULE",
    "72-30 - HIGH PRESSURE COMPRESSOR (HPC) MODULE ASSEMBLY",
    "72-31 - HIGH PRESSURE COMPRESSOR (HPC) ROTOR ASSEMBLY",
    "72-32 - FORWARD COMPRESSOR STATOR ASSEMBLY",
    "72-40 - COMBUSTION SECTION",
    "72-41 - COMBUSTOR DIFFUSER NOZZLE ASSEMBLY",
    "72-42 - COMBUSTION CHAMBER ASSEMBLY",
    "72-50 - TURBINE SECTION",
    "72-51 - STAGE 1 HIGH PRESSURE TURBINE (HPT) NOZZLE ASSEMBLY",
    "72-52 - HPT ROTOR ASSEMBLY",
    "72-53 - STAGE 2 HPT NOZZLE ASSEMBLY",
    "72-54 - TURBINE CENTER FRAME ASSEMBLY",
    "72-56 - LPT STAGE 1 NOZZLE ASSEMBLY",
    "72-57 - LPT SHAFT MODULE",
    "72-58 - LPT ROTOR AND STATOR MODULE",
    "72-59 - TURBINE REAR FRAME MODULE",
    "72-60 - ACCESSORY DRIVES",
    "72-61 - INLET GEARBOX (IGB) AND NO.3 BEARING ASSEMBLY",
    "72-62 - TRANSFER GEARBOX (TGB) ASSEMBLY",
    "72-63 - ACCESSORY GEARBOX (AGB) ASSEMBLY",
    "72-00 - ENGINE - GENERAL",
    "72-21 - FAN AND BOOSTER ASSEMBLY",
    "72-22 - NO.1 AND NO.2 BEARING SUPPORT ASSEMBLY",
    "72-23 - FAN FRAME ASSEMBLY",
    "72-30 - HP COMPRESSOR SECTION",
    "72-31 - HPC ROTOR ASSEMBLY",
    "72-32 - HPC FRONT STATOR ASSEMBLY",
    "72-33 - HPC REAR STATOR ASSEMBLY",
    "72-40 - COMBUSTION SECTION",
    "72-41 - COMBUSTION CASE ASSEMBLY",
    "72-42 - COMBUSTION CHAMBER ASSEMBLY",
    "72-50 - TURBINE SECTION",
    "72-51 - HPT NOZZLE ASSEMBLY",
    "72-52 - HPT ROTOR ASSEMBLY",
    "72-53 - HPT SHROUD AND STAGE 1 LPT NOZZLE ASSEMBLY",
    "72-54 - LPT ROTOR/STATOR ASSEMBLY",
    "72-55 - LPT SHAFT",
    "72-56 - TURBINE FRAME ASSEMBLY",
    "72-60 - ACCESSORY DRIVE SECTION",
    "72-61 - INLET GEARBOX ASSEMBLY",
    "72-62 - TRANSFER GEARBOX ASSEMBLY",
    "72-63 - ACCESSORY GEARBOX ASSEMBLY",
    "73-00 - ENGINE FUEL AND CONTROL - GENERAL",
    "73-10 - DISTRIBUTION",
    "73-11 - ENGINE FUEL DISTRIBUTION COMPONENTS",
    "73-20 - CONTROLLING",
    "73-21 - ENGINE CONTROLLING COMPONENTS",
    "73-25 - FUNCTIONAL INTERFACES",
    "73-29 - FULL AUTHORITY DIGITAL ENGINE CONTROL (FADEC) SYSTEM",
    "73-30 - INDICATING",
    "73-31 - FUEL FLOW INDICATING",
    "73-34 - FUEL FILTER CLOGGING WARNING SYSTEM",
    "73-00 - ENGINE FUEL AND CONTROL - GENERAL",
    "73-10 - DISTRIBUTION",
    "73-11 - DISTRIBUTION",
    "73-20 - CONTROLLING",
    "73-21 - CONTROLLING",
    "73-25 - FUNCTIONAL INTERFACES",
    "73-29 - FADEC SYSTEM",
    "73-30 - INDICATING",
    "73-31 - FUEL FLOW INDICATING",
    "73-34 - FUEL FILTER CLOGGING WARNING SYSTEM",
    "74-00 - IGNITION - GENERAL",
    "74-10 - ELECTRICAL POWER SUPPLY",
    "74-11 - IGNITION POWER SUPPLY",
    "74-20 - DISTRIBUTION",
    "74-21 - IGNITION DISTRIBUTION COMPONENTS",
    "74-30 - SWITCHING",
    "74-31 - IGNITION STARTING AND CONTINUOUS RELIGHT",
    "74-00 - IGNITION - GENERAL",
    "74-10 - ELECTRICAL POWER SUPPLY",
    "74-11 - IGNITION POWER SUPPLY",
    "74-20 - DISTRIBUTION",
    "74-21 - HIGH VOLTAGE DISTRIBUTION SYSTEM",
    "74-30 - SWITCHING",
    "74-31 - IGNITION STARTING AND CONTINUOUS RELIGHT",
    "75-00 - AIR - GENERAL",
    "75-10 - ENGINE ANTI-ICING",
    "75-11 - ENGINE ANTI-ICING COMPONENTS",
    "75-20 - COOLING",
    "75-21 - HPT ACTIVE CLEARANCE CONTROL (ACC) SYSTEM",
    "75-22 - LPT ACTIVE CLEARANCE CONTROL (ACC) SYSTEM",
    "75-23 - MODULATED TURBINE CONTROL AND CORE", "COMPARTMENT COOLING SYSTEM",
    "75-24 - ELECTRONIC ENGINE CONTROL (EEC) COOLING SYSTEM",
    "75-26 - REVERSE BLEED SYSTEM COOLING",
    "75-30 - COMPRESSOR CONTROL",
    "75-31 - VARIABLE BLEED VALVE (VBV) SYSTEM",
    "75-32 - VARIABLE STATOR VANE (VSV) SYSTEM",
    "75-33 - TRANSIENT BLEED VALVE (TBV) SYSTEM",
    "75-00 - AIR - GENERAL",
    "75-20 - COOLING",
    "75-21 - HPT ACTIVE CLEARANCE CONTROL SYSTEM",
    "75-22 - LPT ACTIVE CLEARANCE CONTROL SYSTEM",
    "75-23 - ROTOR ACTIVE CLEARANCE CONTROL SYSTEM (RACC) ",
    "75-24 - ECU COOLING SYSTEM",
    "75-25 - NACELLE COMPARTMENT AND ACCESSORY COOLING",
    "75-26 - TRANSIENT BLEED VALVE SYSTEM",
    "75-27 - IGNITION SYSTEM COOLING",
    "75-30 - COMPRESSOR CONTROL",
    "75-31 - VARIABLE BLEED VALVE SYSTEM (VBV) ",
    "75-32 - VARIABLE STATOR VANE SYSTEM (VSV) ",
    "75-41 - NACELLE TEMPERATURE INDICATING",
    "76-00 - ENGINE CONTROLS - GENERAL",
    "76-10 - POWER CONTROLS",
    "76-11 - THROTTLE CONTROL",
    "76-12 - ENGINE MASTER CONTROL",
    "77-00 - ENGINE INDICATING - GENERAL",
    "77-10 - POWER",
    "77-11 - POWER INDICATING SYSTEM",
    "77-20 - TEMPERATURE",
    "77-21 - EXHAUST GAS TEMPERATURE SYSTEM",
    "77-22 - CORE COMPARTMENT TEMPERATURE SYSTEM",
    "77-30 - ANALYZERS",
    "77-31 - VIBRATION SENSORS",
    "77-00 - ENGINE INDICATING - GENERAL",
    "77-10 - POWER",
    "77-11 - N1 ROTATIONAL SPEED INDICATING SYSTEM",
    "77-20 - TEMPERATURE",
    "77-21 - EXHAUST GAS TEMPERATURE INDICATING SYSTEM",
    "77-22 - T5 THERMOCOUPLE",
    "77-23 - T3 THERMOCOUPLE",
    "77-30 - ANALYZERS",
    "77-31 - VIBRATION SENSORS",
    "77-32 - ENGINE VIBRATION INDICATING SYSTEM",
    "78-00 - EXHAUST - GENERAL",
    "78-10 - COLLECTOR/NOZZLE",
    "78-11 - HOT EXHAUST",
    "78-12 - EXHAUST NOZZLE",
    "78-30 - THRUST REVERSER",
    "78-31 - THRUST REVERSER (T/R) CONTROL AND INDICATING",
    "78-32 - THRUST REVERSER STRUCTURE",
    "78-36 - COWL - THRUST REVERSER OPENING MECHANISM",
    "78-37 - THRUST REVERSER INDEPENDENT LOCKING SYSTEM",
    "78-00 - EXHAUST - GENERAL",
    "78-11 - HOT EXHAUST",
    "78-30 - THRUST REVERSER",
    "78-31 - THRUST REVERSER CONTROL AND INDICATING",
    "78-32 - THRUST REVERSER STRUCTURE",
    "78-33 - THRUST REVERSER SYSTEM COMPONENTS",
    "78-36 - THRUST REVERSER OPENING MECHANISM",
    "78-37 - THRUST REVERSER INDEPENDENT LOCKING SYSTEM",
    "79-00 - OIL - GENERAL",
    "79-10 - STORAGE",
    "79-11 - OIL TANK",
    "79-20 - DISTRIBUTION",
    "79-21 - OIL DISTRIBUTION COMPONENTS",
    "79-22 - DUCTING",
    "79-30 - INDICATING",
    "79-31 - OIL QUANTITY INDICATING SYSTEM",
    "79-32 - OIL PRESSURE/TEMPERATURE INDICATING SYSTEM",
    "79-34 - LOW OIL PRESSURE WARNING SYSTEM",
    "79-35 - OIL FILTER CLOGGING WARNING SYSTEM",
    "79-36 - DEBRIS MONITORING SYSTEM",
    "79-00 - OIL - GENERAL",
    "79-11 - OIL TANK",
    "79-20 - DISTRIBUTION",
    "79-21 - DISTRIBUTION",
    "79-30 - INDICATING",
    "79-31 - OIL QUANTITY INDICATING SYSTEM",
    "79-32 - OIL TEMPERATURE INDICATING SYSTEM",
    "79-33 - OIL PRESSURE INDICATING SYSTEM",
    "79-34 - LOW OIL PRESSURE WARNING SYSTEM",
    "79-35 - OIL FILTER CLOGGING WARNING SYSTEM",
    "79-36 - DEBRIS MONITORING SYSTEM",
    "80-00 - STARTING - GENERAL",
    "80-10 - CRANKING",
    "80-11 - PNEUMATIC STARTER AND VALVE SYSTEM",
    "80-12 - DUCTING",
    "80-00 - STARTING - GENERAL",
    "80-10 - CRANKING",
    "80-11 - PNEUMATIC STARTER AND VALVE SYSTEM",
    "80-13 - DUCTING",

]

# ---------------------------------------------------------
# ANTARMUKA UTAMA
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
    selected_ata_raw = st.selectbox(
        "🏷️ ATA Chapter (4-Digit):",
        options=ATA_4DIGIT_OPTIONS,
        help="Pilih sub-sistem ATA Chapter 4-digit yang sesuai."
    )
    
    if selected_ata_raw != "-- Pilih ATA Chapter --":
        selected_ata = selected_ata_raw.split(" | ")[0].strip()
    else:
        selected_ata = ""

kasus_baru = st.text_area(
    "Masukkan Deskripsi Defect / Discrepancy Baru:",
    placeholder="Contoh: Landing Light not illuminate"
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
if st.button("Analisis Kasus & History", type="primary"):
    if not api_key_input.strip():
        st.error("⚠️ Silakan masukkan Gemini API Key Anda pada menu Sidebar di sebelah kiri terlebih dahulu.")
    elif not kasus_baru.strip():
        st.warning("⚠️ Silakan masukkan deskripsi defect atau discrepancy terlebih dahulu.")
    else:
        df_defect = load_defect_db_by_sheet(ac_type)
        
        if df_defect.empty:
            st.error(f"⚠️ Database maintenance untuk armada {ac_type} kosong atau tidak ditemukan.")
        else:
            text_upper = kasus_baru.upper()
            
            stop_words = {
                "FOUND", "DURING", "PRE", "FLIGHT", "AFTER", "BEFORE", "IN", "AT", "ON", "AND", 
                "THE", "TO", "OF", "WITH", "IS", "WAS", "FOR", "CHECK", "INSPECTED", "OUR", "AIRCRAFT",
                "PLEASE", "KINDLY", "REPORTED", "BROKEN", "DAMAGED", "FAIL", "FAILED", "FAULT"
            }
            
            raw_words = re.findall(r'\b[A-Za-z0-9]+\b', text_upper)
            core_keywords = [w for w in raw_words if len(w) > 2 and w not in stop_words]
            
            mask_ata = pd.Series(True, index=df_defect.index)
            if selected_ata.strip():
                ata_digits = re.sub(r'\D', '', selected_ata)
                main_ata_2digit = ata_digits[:2] if len(ata_digits) >= 2 else ata_digits
                
                if 'ATA' in df_defect.columns and main_ata_2digit:
                    excel_ata_clean = df_defect['ATA'].astype(str).str.replace(r'\D', '', regex=True)
                    mask_ata = excel_ata_clean.str.startswith(main_ata_2digit) | excel_ata_clean.str.contains(main_ata_2digit, na=False)

            df_ata_subset = df_defect[mask_ata] if selected_ata.strip() else df_defect
            history_match = pd.DataFrame()
            
            if not df_ata_subset.empty:
                row_text = df_ata_subset.astype(object).fillna('').astype(str).agg(' '.join, axis=1).str.upper()
                
                if core_keywords:
                    primary_components = [w for w in core_keywords if w in ["SPRING", "CARTRIDGE", "VALVE", "PUMP", "ACTUATOR", "SENSOR", "SWITCH", "BATTERY", "LIGHT", "MIRROR", "SEAT"]]
                    
                    if primary_components:
                        primary_pattern = "|".join(primary_components)
                        mask_strict = row_text.str.contains(primary_pattern, case=False, na=False)
                        
                        if "LAVATORY" in text_upper or "LAV" in text_upper:
                            mask_lav = row_text.str.contains(r'LAVATORY|\bLAV\b', case=False, regex=True, na=False)
                            history_match = df_ata_subset[mask_strict & mask_lav].copy()
                        else:
                            history_match = df_ata_subset[mask_strict].copy()
                    else:
                        pattern_all = ".*".join(core_keywords)
                        mask_fallback = row_text.str.contains(pattern_all, case=False, regex=True, na=False)
                        history_match = df_ata_subset[mask_fallback].copy()
                        
                        if history_match.empty:
                            mask_any = row_text.str.contains("|".join(core_keywords), case=False, na=False)
                            history_match = df_ata_subset[mask_any].copy()

                    if not history_match.empty:
                        history_match['Relevance_Score'] = history_match.astype(object).fillna('').astype(str).agg(' '.join, axis=1).apply(
                            lambda x: sum(10 for kw in core_keywords if kw in x.upper())
                        )
                        history_match = history_match.sort_values(by=['Relevance_Score', 'Date'], ascending=[False, False])
                else:
                    history_match = df_ata_subset.copy()

            # --- SISTEM FALLBACK & FILTER 4-DIGIT PRESISI ---
            df_gemini_trend = history_match.copy() if not history_match.empty else pd.DataFrame()
            
            search_pattern = ""
            if selected_ata and len(selected_ata) >= 4:
                main_ch = selected_ata.split('-')[0]
                sub_digit = selected_ata.split('-')[1][0] if len(selected_ata.split('-')[1]) > 0 else "4"
                search_pattern = f"{main_ch}-{sub_digit}"
            
            if not df_gemini_trend.empty and 'Corrective Action' in df_gemini_trend.columns and search_pattern:
                df_strict = df_gemini_trend[df_gemini_trend['Corrective Action'].astype(str).str.contains(search_pattern, case=False, na=False)]
                if not df_strict.empty:
                    df_gemini_trend = df_strict

            jumlah_filtered_match = len(df_gemini_trend)

            if not df_gemini_trend.empty:
                df_history_sorted = df_gemini_trend.head(15).copy()
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
            else:
                context_history = "Tidak ditemukan riwayat defect serupa di sub-sistem ini."
                date_range_info = "N/A"
                df_history_sorted = pd.DataFrame()

            st.session_state['ac_type'] = ac_type
            st.session_state['ac_reg'] = ac_reg
            st.session_state['kasus_baru'] = kasus_baru
            st.session_state['selected_ata'] = selected_ata
            st.session_state['history_match'] = df_history_sorted

            if jumlah_filtered_match >= 5:
                selected_method_instruction = """
            [METODE ANALISIS YANG WAJIB DIGUNAKAN: FISHBONE / ISHIKAWA DIAGRAM]
            1. Tuliskan terlebih dahulu paragraf tinjauan tren & korelasi historis di awal.
            2. Buatlah diagram Fishbone / Ishikawa menggunakan teks ASCII di dalam blok kode (``` ... ```).
            3. SETELAH diagram Fishbone, wajib tuliskan paragraf analisis dan evaluasi mendalam yang mengulas poin-poin penyebab utama dari diagram tersebut (misalnya analisis faktor Machine, Material, Method, dan Environment).
            """
            else:
                selected_method_instruction = """
            [METODE ANALISIS YANG WAJIB DIGUNAKAN: 5 WHYS INVESTIGATION]
            1. Tuliskan paragraf tinjauan tren di awal.
            2. Buat penelusuran 5 Whys di dalam blok kode (``` ... ```).
            3. SETELAH penelusuran 5 Whys, wajib tuliskan paragraf evaluasi kesimpulan akar masalah.
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

DATA HISTORI DARI DATABASE UNSCHEDULED ARMADA {ac_type} (Spesifik Sub-sistem ATA {selected_ata}):
- Total Ditemukan: {jumlah_filtered_match} record/kejadian defect serupa.
- Periode Waktu Kejadian: {date_range_info}
- Buatkan tabel Jumlah Defect Dalam Tiap bulan vs Bulan berdasarkan data spesifik sub-sistem ini.

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
(Provide exact System Description translated into professional aviation engineering English)

[RCA]
(Provide exact RCA translated into professional aviation engineering English)

[RECOMMENDATIONS]
(Provide exact Recommendations translated into professional aviation engineering English)
"""

            with st.spinner(f"Menganalisis histori & dokumen lampiran armada {ac_type} via Gemini..."):
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
                    
                    df_history_to_save = df_history_sorted.copy()
                    for col in df_history_to_save.columns:
                        if pd.api.types.is_datetime64_any_dtype(df_history_to_save[col]) or 'date' in col.lower():
                            df_history_to_save[col] = df_history_to_save[col].astype(str).replace('NaT', '')

                    report_payload = {
                        "dan_number": dan_code,
                        "ac_type": ac_type,
                        "ac_reg": ac_reg,
                        "kasus_baru": kasus_baru,
                        "selected_ata": selected_ata,
                        "iterations": [initial_iteration],
                        "history_match": df_history_to_save.to_dict(orient="records") if not df_history_sorted.empty else []
                    }
                    save_report_to_disk(report_key_label, report_payload)
                    
                    # Refresh halaman agar bagian 1 sampai 5 langsung muncul
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

        # 1. TAMPILKAN SYSTEM DESCRIPTION
        if sys_text:
            st.markdown("### 2. SYSTEM DESCRIPTION & OPERATIONAL PRINCIPLES")
            st.markdown(sys_text)

        # 2. TAMPILKAN RCA (URUTAN: REVIEW -> DIAGRAM -> ANALISIS PENUTUP DIAGRAM)
        if rca_text:
            st.markdown("### 3. REPETITIVE DEFECT ANALYSIS & ROOT CAUSE ANALYSIS (RCA)")
            
            # Bersihkan tag penanda dari Gemini
            clean_rca = rca_text.replace("[RCA_REVIEW]", "").replace("[RCA]", "").strip()
            
            # Pisahkan menjadi 3 bagian jika ada blok kode diagram (```)
            if "```" in clean_rca:
                parts_code = clean_rca.split("```")
                rca_before = parts_code[0].strip()                    # 1. Analisis/Tinjauan awal di atas diagram
                diagram_content = "```" + parts_code[1] + "```"       # 2. Diagram Fishbone
                rca_after = parts_code[2].strip() if len(parts_code) > 2 else "" # 3. Analisis/Review penutup di bawah diagram
            else:
                rca_before = clean_rca
                diagram_content = ""
                rca_after = ""
            
            # TAHAP 1: Tampilkan Tinjauan / Analisis Awal di atas diagram
            if rca_before:
                st.markdown(rca_before)
            
            # TAHAP 2: Tampilkan Diagram Fishbone di tengah
            if diagram_content:
                st.markdown(diagram_content)
                
            # TAHAP 3: Tampilkan Analisis / Review terhadap Diagram Fishbone di bawahnya
            if rca_after:
                st.markdown("#### Analisis & Evaluasi Root Cause:")
                st.markdown(rca_after)
            
            st.markdown("---")
            
        # 4. TAMPILKAN RECOMMENDATIONS
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
                active_api_key = st.session_state.get('user_api_key', '').strip()
                if not active_api_key:
                    st.error("⚠️ API Key tidak ditemukan di sidebar. Silakan masukkan API Key di panel sebelah kiri.")
                else:
                    if 'all_reports' not in locals() and 'all_reports' not in globals():
                        all_reports = load_reports_from_json() if 'load_reports_from_json' in globals() else []

                    active_dan = st.session_state.get('dan_code', None)
                    current_report = None
                    
                    if 'all_reports' in locals() and all_reports:
                        for rep in all_reports:
                            if rep.get("dan_number") == active_dan:
                                current_report = rep
                                break
                        if not current_report:
                            current_report = all_reports[-1]
                    
                    if not current_report:
                        current_report = {
                            "dan_number": active_dan or "DAN-TEMP",
                            "ac_type": st.session_state.get('ac_type', ''),
                            "ac_reg": st.session_state.get('ac_reg', ''),
                            "kasus_baru": st.session_state.get('kasus_baru', ''),
                            "selected_ata": st.session_state.get('selected_ata', ''),
                            "iterations": []
                        }
                        if 'all_reports' in locals():
                            all_reports.append(current_report)

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

INFORMASI AWAL DEFECT (DAN: {current_report.get('dan_number')}):
- Aircraft Type: {current_report.get('ac_type')} ({current_report.get('ac_reg', 'N/A')})
- ATA Chapter: {current_report.get('selected_ata', 'N/A')}
- Initial Defect: {current_report.get('kasus_baru')}

CATATAN EKSEKUSI & TEMUAN ENGINEER DI LAPANGAN (ITERASI {iteration_number}):
{feedback_text if feedback_text.strip() else '(Tidak ada catatan teks, mengandalkan analisis lampiran gambar/PDF)'}
{feedback_file_instruction}

TUGAS ANDA:
Lakukan evaluasi ulang, pertajam Root Cause Analysis (RCA), dan berikan rekomendasi tindakan tingkat lanjut (Next Action Step/Secondary Troubleshooting) dalam Bahasa Indonesia dan Bahasa Inggris dengan format persis:

[BAHASA_INDONESIA]
[SYSTEM]
Penjelasan singkat relevansi temuan/rektifikasi baru terhadap sistem terkait.

[RCA]
Analisis penyebab mendalam (Deep-Dive RCA) berdasarkan hasil eksekusi dan lampiran visual/dokumen terbaru.

[RECOMMENDATIONS]
Rekomendasi tindakan teknis lanjutan, pemeriksaan komponen sekunder, atau penyesuaian interval inspeksi.

[BAHASA_INGGRIS]
[SYSTEM]
(System update in English)

[RCA]
(Deep-Dive RCA update in English)

[RECOMMENDATIONS]
(Next action recommendations in English)
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
                            
                            if 'save_reports_to_json' in globals() and 'all_reports' in locals():
                                save_reports_to_json(all_reports)

                            st.success(f"✅ Analisis Lanjutan Iterasi ke-{iteration_number} berhasil ditambahkan dan disimpan!")
                            st.rerun()
                            
                        except Exception as e:
                            st.error(f"⚠️ Gagal melakukan analisis lanjutan: {e}")

st.markdown("---")

# --- SECTION 1: DOWNLOAD LAPORAN PDF ---
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

# --- SECTION 2: HISTORICAL RECORD ---
st.markdown("---")
st.subheader(f"5. HISTORICAL RECORD (Database Unscheduled {st.session_state.get('ac_type', '')} - {len(df_hist_pdf)} Record Ditemukan):")

if not df_hist_pdf.empty:
    st.dataframe(df_hist_pdf, use_container_width=True)
else:
    st.info("Tidak ada catatan riwayat histori yang ditemukan.")

import streamlit as st
import io

# =========================================================
# MODUL ENGINEERING ORDER (EO) GENERATOR (Airfast Indonesia Standard)
# =========================================================
st.markdown("---")
st.subheader("🛠️ Engineering Order (EO) Generator (Form ATD-F-308)")
st.markdown("Gunakan modul ini untuk menghasilkan draf Engineering Order formal berstandar Airfast Indonesia berdasarkan hasil analisis dan dokumen referensi manual (AMM/WDM/FTD).")

with st.form("eo_form"):
    col1, col2 = st.columns(2)
    with col1:
        eo_number = st.text_input("Nomor EO (Engineering Order)", value="EO-B38M/M33-00-1167 IR")
        eo_title = st.text_input("Judul Subject EO", value="LIGHTS - GENERAL - FLIGHT COMPARTMENT DOOR HEADER WIRE BUNDLE INSPECTION AND PROTECTION")
        compliance_category = st.selectbox("Klasifikasi Compliance", ["Mandatory / Urgent", "Recommended / Fleet Improvement", "Routine Inspection"])
    with col2:
        compliance_time = st.text_input("Batas Waktu Pelaksanaan (Compliance)", value="To be performed at next scheduled maintenance opportunity")
        man_hours = st.text_input("Estimasi Man-Hours", value="2.30 Man-Hours / A/P Engineer")
        part_number = st.text_input("Part Number / KIT Required", value="TOP KIT - RETROFIT KIT (P/N 002A0147-1)")

    uploaded_refs = st.file_uploader(
        "Unggah Dokumen Referensi Pendukung (AMM / WDM / FTD / SB)", 
        type=["pdf", "txt", "docx"], 
        accept_multiple_files=True
    )

    generate_eo_btn = st.form_submit_button("✨ Generate Draf Engineering Order (Form ATD-F-308)")

# Pastikan di sini tidak ada indentasi (rata kiri) atau sejajar dengan with st.form jika di luar form:
if generate_eo_btn:
    with st.spinner("Membaca dokumen referensi PDF & menyusun draf EO Form ATD-F-308..."):
        try:
            # 1. Gunakan SDK baru
            from google import genai
            from fpdf import FPDF

            API_KEY = "AQ.Ab8RN6LEuJfRe4nvItnmg-6HgIzzn6f52KUe98HWVy-PG3wCow"
            
            # Inisialisasi client baru sesuai potongan kode di gambar
            client = genai.Client(api_key=API_KEY)

            # (... proses pembacaan PDF referensi & penyusunan prompt_eo tetap di sini ...)

            # 2. Panggil generate content menggunakan SDK baru
            response = client.models.generate_content(
                model='gemini-3.8-flash',
                contents=prompt_eo,
            )
            generated_eo_text = response.text

            st.success("Draf Engineering Order (Form ATD-F-308) berhasil disusun!")
            st.code(generated_eo_text, language="markdown")

            # 3. Ekstraksi teks dari file pendukung (PDF/TXT)
            ref_text_content = ""
            if uploaded_refs:
                for file in uploaded_refs:
                    ref_text_content += f"\n\n--- DOKUMEN REFERENSI: {file.name} ---\n"
                    if file.name.lower().endswith('.pdf'):
                        try:
                            with pdfplumber.open(file) as pdf:
                                for page_idx, page in enumerate(pdf.pages):
                                    text = page.extract_text()
                                    if text:
                                        ref_text_content += f"[Halaman {page_idx+1}]\n{text}\n"
                        except Exception as e:
                            ref_text_content += f"(Gagal membaca PDF: {str(e)})\n"
                    elif file.name.lower().endswith('.txt'):
                        ref_text_content += file.read().decode('utf-8', errors='ignore')

            # 4. Susun prompt profesional yang dinamis dan menyesuaikan input form
            prompt_eo = f"""
            Bertindaklah sebagai Senior Technical Services & Reliability Engineer di Airfast Indonesia.
            Tugas Anda adalah menyusun draf Engineering Order (EO) formal berstandar tinggi mengikuti struktur Form ATD-F-308 secara presisi berdasarkan data input dan dokumen referensi pesawat yang dilampirkan.

            DATA INPUT UTAMA:
            - Nomor EO: {eo_number}
            - Subject / Judul: {eo_title}
            - ATA Chapter: Sesuaikan secara akurat dengan Subject/Defect (misal: ATA 34 untuk Altimeter/Navigasi, dll.)
            - Aircraft Applicability: Deteksi dan sesuaikan jenis pesawat (misalnya De Havilland DHC-6 Twin Otter atau armada terkait) berdasarkan nomor registrasi atau dokumen referensi yang diunggah. Jangan gunakan armada Boeing jika pesawatnya DHC-6.
            - Classification: {compliance_category} ({compliance_time})
            - Estimasi Man Power: {man_hours}
            - Material / Parts Required: {part_number}
            
            TEKS DOKUMEN REFERENSI / DEFECT YANG DIUNGGAH:
            {ref_text_content if ref_text_content else "Gunakan referensi manual AMM/WDM/CMM yang relevan dengan tipe pesawat tersebut."}

            INSTRUKSI FORMAT OUTPUT:
            Susun dengan layout rapi menggunakan format Markdown persis seperti berikut:
            # ENGINEERING ORDER (EO)
            ==================================================
            **EO NUMBER:** {eo_number}
            **TITLE:** {eo_title}
            **ATA CHAPTER:** (Isi sesuai ATA Chapter yang benar untuk kasus ini)
            **AIRCRAFT APPLICABILITY:** (Isi sesuai tipe pesawat aktual, misal: DHC-6 Twin Otter)
            **CLASSIFICATION:** {compliance_category} | **COMPLIANCE:** {compliance_time}
            
            ## 1. REASON & BACKGROUND
            (Uraikan latar belakang investigasi defect, temuan operasional, dan referensi manual terkait secara teknis mendalam)
            
            ## 2. MATERIAL / PARTS & TOOLING REQUIRED
            (Cantumkan part number, consumables, dan special tools yang dibutuhkan sesuai tipe pesawat)
            
            ## 3. ACCOMPLISHMENT INSTRUCTIONS (STEP-BY-STEP)
            **Phase A: Safety & Preparation**
            **Phase B: Inspection & Testing**
            **Phase C: Component Removal & Installation / Rectification**
            **Phase D: Operational Check & Return to Service**
            ==================================================
            """

            # 5. Generate konten draf EO dari AI
            response = active_model.generate_content(prompt_eo)
            generated_eo_text = response.text

            st.success("Draf Engineering Order (Form ATD-F-308) berhasil disusun dengan sukses!")
            st.code(generated_eo_text, language="markdown")

            # --- MODUL UNDUH PDF FORM ATD-F-308 BERBASIS FPDF2 ---
            from fpdf import FPDF

            class AirfastEOPDF(FPDF):
                def header(self):
                    # Header Kop Surat Standar Perusahaan
                    self.set_font('Helvetica', 'B', 11)
                    self.cell(0, 6, 'AIRFAST INDONESIA - ENGINEERING ORDER (ATD-F-308)', 0, 1, 'C')
                    self.set_font('Helvetica', 'I', 8)
                    self.cell(0, 4, 'Technical Services & Reliability Engineering Department', 0, 1, 'C')
                    self.line(10, 18, 200, 18)
                    self.ln(4)

                def footer(self):
                    # Footer Halaman
                    self.set_y(-15)
                    self.set_font('Helvetica', 'I', 8)
                    self.cell(0, 10, f'Page {self.page_no()}/{{nb}} - Airfast Indonesia Confidential', 0, 0, 'C')

            # Inisialisasi obyek PDF ukuran A4
            pdf = AirfastEOPDF(orientation='P', unit='mm', format='A4')
            pdf.alias_nb_pages()
            pdf.add_page()
            pdf.set_font("Helvetica", size=9)

            # Proses perataan teks draf EO dari hasil generate AI ke dalam PDF
            for line in generated_eo_text.split('\n'):
                # Membersihkan format markdown tebal/miring agar bersih tercetak di PDF
                clean_line = line.replace('**', '').replace('##', '').replace('#', '').strip()
                
                if line.startswith('#'):
                    pdf.ln(2)
                    pdf.set_font("Helvetica", 'B', 10)
                    pdf.set_text_color(0, 43, 73) # Warna biru gelap ala Airfast
                    pdf.cell(0, 6, clean_line, ln=True)
                    pdf.set_font("Helvetica", size=9)
                    pdf.set_text_color(34, 34, 34)
                elif line.startswith('-') or line.startswith('*'):
                    pdf.multi_cell(0, 5, f"   {clean_line}")
                else:
                    pdf.multi_cell(0, 5, clean_line if clean_line else " ")

            # Render output biner PDF
            pdf_bytes = pdf.output()

            # Tombol Unduh Streamlit
            st.download_button(
                label="📄 Unduh Dokumen Form ATD-F-308 Resmi (.PDF)",
                data=bytes(pdf_bytes),
                file_name=f"{eo_number.replace('/', '_')}_Form_ATD_F_308.pdf",
                mime="application/pdf"
            )

        except Exception as e:
            st.error(f"Gagal menghasilkan draf EO dari AI: {e}")
