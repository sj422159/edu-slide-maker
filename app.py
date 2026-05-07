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
# CONFIGURATION & API
# ==========================================
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

GEMINI_MODELS = [
    "gemini-1.5-pro", 
    "gemini-1.5-flash",
    "gemini-2.0-flash"
]

# Color Palette
NAVY, GOLD, CYAN = (3, 0, 46), (255, 196, 61), (0, 188, 212)
DARK_CARD, WHITE_TEXT, BODY_TEXT = (20, 24, 45), (240, 240, 250), (210, 215, 230)

# ==========================================
# UTILITIES
# ==========================================
def clean_and_normalize(text):
    text = re.sub(r'#+\s*', '', text)
    text = text.replace('**', '').replace('__', '').replace('*', '')
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '•',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return unicodedata.normalize('NFKD', text).encode('latin-1', 'ignore').decode('latin-1')

def render_math(latex_str, font_size=12, color='#D2D7E6'):
    if not latex_str.startswith('$'): latex_str = f"${latex_str}$"
    buf = io.BytesIO()
    plt.rc('text', usetex=False)
    fig = plt.figure(figsize=(0.01, 0.01)) 
    fig.text(0, 0, latex_str, fontsize=font_size, color=color)
    plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0.05, dpi=300)
    plt.close(fig)
    buf.seek(0)
    return buf

# ==========================================
# PDF ENGINE
# ==========================================
class MasterNotesPDF(FPDF):
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
            self.cell(0, 6, clean_and_normalize(self.title_info), align='R')
            self.set_y(22)

def write_complex_line(pdf, line):
    line = clean_and_normalize(line)
    parts = re.split(r'(\$.*?\$)', line)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*BODY_TEXT)
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            try:
                img_buf = render_math(part)
                img = Image.open(img_buf)
                w_px, h_px = img.size
                h_mm = 4.8
                w_mm = (w_px / h_px) * h_mm
                if pdf.get_x() + w_mm > (pdf.w - 15): pdf.ln(7)
                pdf.image(img_buf, x=pdf.get_x(), y=pdf.get_y() - 0.5, h=h_mm)
                pdf.set_x(pdf.get_x() + w_mm + 1)
            except:
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(8)

# ==========================================
# GENERATION LOGIC
# ==========================================
def generate_master_guide(cls, sub, chp, status):
    # Prompt explicitly requests answer lengths
    prompt = f"""
    Generate an EXHAUSTIVE, textbook-quality study guide for {chp} (Class {cls} {sub}).
    The goal is a 30-page comprehensive document.
    
    REQUIRED CONTENT:
    1. Exhaustive Theory: Break every concept into deep, multi-paragraph sub-sections.
    2. Mathematical Proofs: Use LaTeX strictly ($...$) for every variable and equation.
    3. Solved Examples: 15 multi-step problems with full working.
    4. QUESTION BANK RULES:
       - 40 MCQs with reasoning.
       - 20 Short Answer Questions: Each answer MUST be 4 to 5 lines of detailed explanation.
       - 15 Long Answer Questions: Each answer MUST be more than 8 lines, including sub-points and detailed theory.
    
    STRICT FORMATTING:
    - Use ## for Section Headers and ### for Sub-headers.
    - NO ** symbols or #### symbols.
    """
    
    markdown_data = ""
    for model in GEMINI_MODELS:
        try:
            status.write(f"Connecting to {model} for deep processing...")
            res = client.models.generate_content(model=model, contents=prompt)
            if res.text:
                markdown_data = res.text
                break
        except: continue

    pdf = MasterNotesPDF(f"Class {cls} | {sub} | {chp}")
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # -- Cover Page --
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')
    pdf.set_y(100)
    pdf.set_font('Helvetica', 'B', 32)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 15, clean_and_normalize(chp.upper()), align='C')
    pdf.set_font('Helvetica', '', 18)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.cell(0, 20, clean_and_normalize(sub), ln=True, align='C')
    
    # -- Content --
    pdf.add_page()
    status.write("Generating high-fidelity PDF and LaTeX images...")
    for line in markdown_data.split('\n'):
        l = line.strip()
        if not l or '---' in l or '|-' in l: continue
        
        if l.startswith('#'):
            pdf.ln(5)
            is_sub = l.startswith('###')
            pdf.set_font('Helvetica', 'B', 14 if is_sub else 18)
            pdf.set_text_color(*(CYAN if is_sub else GOLD))
            pdf.cell(0, 10, clean_and_normalize(l), ln=True)
            pdf.ln(3)
        else:
            write_complex_line(pdf, l)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# INTERFACE
# ==========================================
st.set_page_config(page_title="EduMaster", page_icon="📖")
st.title("📖 EduMaster: Deep Content Microservice")

col1, col2, col3 = st.columns(3)
with col1: c_in = st.text_input("Class", "12")
with col2: s_in = st.text_input("Subject", "Physics")
with col3: h_in = st.text_input("Chapter", "Dual Nature of Radiation")

if st.button("Generate Exhaustive 30+ Page Guide", use_container_width=True):
    with st.status("Initializing High-Token Pipeline...") as status:
        try:
            pdf_out = generate_master_guide(c_in, s_in, h_in, status)
            status.update(label="✅ Compilation Successful!", state="complete")
            st.download_button("📥 Download Master PDF", data=pdf_out, file_name=f"{h_in}_Detailed.pdf", mime="application/pdf")
        except Exception as e:
            st.error(f"Error: {e}")
