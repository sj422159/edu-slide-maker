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
# Ensure GEMINI_API_KEY is set in your environment
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

# Priority list for Failover logic
GEMINI_MODELS = [
    "gemini-1.5-pro",    # Best for reaching 30+ pages
    "gemini-1.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest"
]

# Color Palette (Dark Academic)
NAVY, GOLD, CYAN = (3, 0, 46), (255, 196, 61), (0, 188, 212)
DARK_CARD, WHITE_TEXT, BODY_TEXT = (20, 24, 45), (240, 240, 250), (210, 215, 230)

# ==========================================
# UTILITIES: TEXT & MATH
# ==========================================
def clean_and_normalize(text):
    """Removes markdown noise and sanitizes for PDF Latin-1 support."""
    # Strip # and * markers
    text = re.sub(r'#+\s*', '', text)
    text = text.replace('**', '').replace('__', '').replace('*', '')
    
    # Manual map for typography
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '•',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    
    # Normalize and strip remaining non-latin1
    text = unicodedata.normalize('NFKD', text)
    return text.encode('latin-1', 'ignore').decode('latin-1')

def render_math(latex_str, font_size=12, color='#D2D7E6'):
    """Renders LaTeX into a high-res transparent PNG via Matplotlib."""
    if not latex_str.startswith('$'): 
        latex_str = f"${latex_str}$"
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
            # Dark Background
            self.set_fill_color(*NAVY)
            self.rect(0, 0, self.w, self.h, 'F')
            # Navigation Header
            self.set_fill_color(*DARK_CARD)
            self.rect(0, 0, self.w, 14, 'F')
            self.set_font('Helvetica', 'B', 8)
            self.set_text_color(*CYAN)
            self.set_xy(10, 4)
            self.cell(0, 6, clean_and_normalize(self.title_info), align='R')
            self.set_y(22)

def write_academic_line(pdf, line):
    """Parses text and LaTeX, rendering symbols as textbook-quality images."""
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
                h_mm = 4.8 # Height to match standard text
                w_mm = (w_px / h_px) * h_mm
                
                if pdf.get_x() + w_mm > (pdf.w - 15):
                    pdf.ln(7)
                
                pdf.image(img_buf, x=pdf.get_x(), y=pdf.get_y() - 0.5, h=h_mm)
                pdf.set_x(pdf.get_x() + w_mm + 1)
            except:
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(8)

# ==========================================
# GENERATION SERVICE
# ==========================================
def generate_master_notes(cls, sub, chp, status):
    # Prompt explicitly requests massive depth and answer line counts
    prompt = f"""
    Generate an EXHAUSTIVE, academic study guide for {chp} (Class {cls} {sub}).
    The final output must be extremely detailed to fill a 30-page document.
    
    CONTENT REQUIREMENTS:
    1. Introduction & Historical Context: Detailed background.
    2. Deep Theory: Break every sub-topic into multiple detailed paragraphs.
    3. Mathematical Foundations: Use LaTeX ($...$) for every formula and proof.
    4. QUESTION BANK DEPTH:
       - 50 MCQs with reasoning.
       - 25 Short Answer Questions: Every answer MUST be 4-5 lines of detailed explanation.
       - 20 Long Answer Questions: Every answer MUST be 10+ lines with bullet points and theory.
    
    STRICT FORMATTING:
    - Use ## for Main Sections, ### for Sub-sections.
    - NO ** or #### symbols.
    """
    
    content_raw = ""
    for model in GEMINI_MODELS:
        try:
            status.write(f"Connecting to AI Node: {model}...")
            res = client.models.generate_content(model=model, contents=prompt)
            if res.text:
                content_raw = res.text
                break
        except: continue

    if not content_raw:
        raise Exception("All models failed. Check network or API Key.")

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
    pdf.set_font('Helvetica', '', 20)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.cell(0, 20, clean_and_normalize(sub), ln=True, align='C')
    
    # -- Content Generation --
    pdf.add_page()
    status.write("Processing complex LaTeX and typesetting pages...")
    for line in content_raw.split('\n'):
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
            write_academic_line(pdf, l)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# INTERFACE
# ==========================================
st.set_page_config(page_title="Deep EduGen", page_icon="📓")
st.title("📓 MasterStudy: High-Volume Edu-Service")
st.markdown("Generates exhaustive, textbook-grade study guides with perfect mathematical rendering.")

col1, col2, col3 = st.columns(3)
with col1: c_val = st.text_input("Class", "12")
with col2: s_val = st.text_input("Subject", "Physics")
with col3: h_val = st.text_input("Chapter", "Photoelectric Effect")

if st.button("Generate Master Study Guide", use_container_width=True):
    if not API_KEY:
        st.error("API Key not found.")
    else:
        with st.status("Orchestrating Deep-Content Pipeline...") as status:
            try:
                pdf_file = generate_master_notes(c_val, s_val, h_val, status)
                status.update(label="✅ Deep Generation Successful!", state="complete")
                st.download_button(
                    "📥 Download Exhaustive PDF",
                    data=pdf_file,
                    file_name=f"{h_val}_MasterNotes.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"Pipeline Error: {e}")
