import os
import re
import io
import unicodedata
import streamlit as st
import matplotlib.pyplot as plt
from PIL import Image
from fpdf import FPDF
from google import genai

# ==========================================
# CONFIGURATION & COLORS
# ==========================================
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

GEMINI_MODELS = [
    "gemini-flash-latest", "gemini-2.0-flash", "gemini-1.5-flash"
]

NAVY, GOLD, CYAN = (3, 0, 46), (255, 196, 61), (0, 188, 212)
DARK_CARD, WHITE_TEXT, BODY_TEXT = (20, 24, 45), (240, 240, 250), (210, 215, 230)

# ==========================================
# UTILITY: TEXT CLEANING
# ==========================================
def clean_markdown_symbols(text):
    """Removes Markdown syntax like ####, ***, and __ for clean PDF rendering."""
    # Remove header hashes
    text = re.sub(r'#+\s*', '', text)
    # Remove bold/italic markers
    text = text.replace('**', '').replace('__', '').replace('*', '')
    return text

def normalize_pdf_text(text):
    if not text: return ""
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '*',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = unicodedata.normalize('NFKD', text)
    return text.encode('latin-1', 'ignore').decode('latin-1')

# ==========================================
# LATEX RENDERING
# ==========================================
def render_latex_to_image(latex_str, font_size=12, color='#D2D7E6'):
    if not latex_str.startswith('$'): latex_str = f"${latex_str}$"
    buf = io.BytesIO()
    plt.rc('text', usetex=False)
    fig = plt.figure(figsize=(0.01, 0.01)) 
    fig.text(0, 0, latex_str, fontsize=font_size, color=color)
    plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0.04, dpi=300)
    plt.close(fig)
    buf.seek(0)
    return buf

# ==========================================
# PDF ENGINE
# ==========================================
class NotesPDF(FPDF):
    def __init__(self, title_info):
        super().__init__()
        self.title_info = title_info

    def header(self):
        if self.page_no() > 1:
            self.set_fill_color(*NAVY)
            self.rect(0, 0, self.w, self.h, 'F')
            self.set_fill_color(*DARK_CARD)
            self.rect(0, 0, self.w, 14, 'F')
            self.set_font('Helvetica', 'B', 8)
            self.set_text_color(*CYAN)
            self.set_xy(10, 4)
            self.cell(0, 6, normalize_pdf_text(self.title_info), align='R')
            self.set_y(20)

def process_text_line(pdf, line):
    # Detect and clean table rows
    if '|' in line:
        line = line.replace('|', '  ') # Convert separators to spacing
        
    line = clean_markdown_symbols(line)
    line = normalize_pdf_text(line)
    
    parts = re.split(r'(\$.*?\$)', line)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*BODY_TEXT)
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            try:
                img_buf = render_latex_to_image(part)
                img = Image.open(img_buf)
                w_px, h_px = img.size
                h_mm = 4.8
                w_mm = (w_px / h_px) * h_mm
                if pdf.get_x() + w_mm > (pdf.w - 15): pdf.ln(7)
                pdf.image(img_buf, x=pdf.get_x(), y=pdf.get_y(), h=h_mm)
                pdf.set_x(pdf.get_x() + w_mm + 1)
            except:
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(8)

def generate_educational_notes(class_num, subject, chapter, status):
    # Prompt logic
    prompt = f"Expert {subject} teacher. Exhaustive guide for {chapter} Class {class_num}. Use LaTeX $...$. Use markdown headers."
    
    raw_markdown = ""
    for model in GEMINI_MODELS:
        try:
            res = client.models.generate_content(model=model, contents=prompt)
            if res.text: raw_markdown = res.text; break
        except: continue

    pdf = NotesPDF(f"Class {class_num} | {subject} | {chapter}")
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # -- Simplified Cover Page --
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')
    pdf.set_y(100)
    pdf.set_font('Helvetica', 'B', 32)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 15, normalize_pdf_text(chapter.upper()), align='C')
    pdf.set_font('Helvetica', '', 18)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.cell(0, 20, normalize_pdf_text(subject), ln=True, align='C')
    
    # -- Content --
    pdf.add_page()
    for line in raw_markdown.split('\n'):
        if not line.strip() or '---' in line or '|-' in line: continue
        
        if line.startswith('#'):
            pdf.ln(4)
            cleaned_header = clean_markdown_symbols(line)
            pdf.set_font('Helvetica', 'B', 15 if '###' in line else 18)
            pdf.set_text_color(*(CYAN if '###' in line else GOLD))
            pdf.cell(0, 10, normalize_pdf_text(cleaned_header), ln=True)
            pdf.ln(2)
        else:
            process_text_line(pdf, line)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# STREAMLIT INTERFACE
# ==========================================
st.set_page_config(page_title="EduNotes Gen", page_icon="🧪")
st.title("🧪 Clean Study Guide Generator")

c1, c2, c3 = st.columns(3)
with c1: cls = st.text_input("Class", "12")
with c2: sub = st.text_input("Subject", "Physics")
with c3: chp = st.text_input("Chapter", "Dual Nature of Matter")

if st.button("Generate Clean PDF", use_container_width=True):
    with st.status("Processing...") as status:
        pdf_data = generate_educational_notes(cls, sub, chp, status)
        st.download_button("📥 Download PDF", data=pdf_data, file_name=f"{chp}.pdf", mime="application/pdf")
