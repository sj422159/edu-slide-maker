import os
import re
import io
import unicodedata
import streamlit as st
import matplotlib.pyplot as plt
from PIL import Image
from fpdf import FPDF
from google import genai
from google.genai import types

# ==========================================
# CONFIGURATION & MODEL LIST
# ==========================================
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

GEMINI_MODELS = [
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite-preview",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

# Professional Dark Palette
NAVY = (3, 0, 46)
GOLD = (255, 196, 61)
CYAN = (0, 188, 212)
DARK_CARD = (20, 24, 45)
WHITE_TEXT = (240, 240, 250)
BODY_TEXT = (210, 215, 230)

# ==========================================
# UTILITY: TEXT NORMALIZATION
# ==========================================
def normalize_pdf_text(text):
    """
    Aggressively sanitizes text for FPDF's Latin-1 limitations.
    Converts smart quotes and strips unsupported characters.
    """
    if not text: return ""
    
    # 1. Manual map for common LLM typography
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201a': "'",
        '\u201c': '"', '\u201d': '"', '\u201e': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '*',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    
    # 2. Normalize Unicode to decompose characters (e.g., accents)
    text = unicodedata.normalize('NFKD', text)
    
    # 3. Encode to latin-1, ignoring errors, then decode back
    return text.encode('latin-1', 'ignore').decode('latin-1')

# ==========================================
# LATEX RENDERING ENGINE
# ==========================================
def render_latex_to_image(latex_str, font_size=12, color='#D2D7E6'):
    """Renders LaTeX into high-res transparent PNGs."""
    if not latex_str.startswith('$'):
        latex_str = f"${latex_str}$"
    
    buf = io.BytesIO()
    plt.rc('text', usetex=False)
    fig = plt.figure(figsize=(0.01, 0.01)) 
    
    # Note: Matplotlib handles the full Unicode range for math
    fig.text(0, 0, latex_str, fontsize=font_size, color=color)
    plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0.04, dpi=300)
    plt.close(fig)
    buf.seek(0)
    return buf

# ==========================================
# CONTENT GENERATION (EXHAUSTIVE MODE)
# ==========================================
def fetch_content(class_num, subject, chapter):
    prompt = f"""
    You are an expert {subject} educator. Generate an EXHAUSTIVE, high-level study guide for "{chapter}" for Class {class_num}.
    
    STRUCTURE:
    1. Comprehensive Overview (300+ words).
    2. Deep Concept Breakdowns.
    3. Mathematical Foundations with all laws and derivations.
    4. Comparative Tables.
    5. 5 Solved Step-by-Step Problems.
    6. Question Bank: 20 MCQs, 15 Short, 10 Long questions with answers.
    
    TECHNICAL RULES:
    - ALWAYS wrap ALL math/symbols/equations in single dollar signs ($).
    - Example: Use $V_0 = \\left( \\frac{{h}}{{e}} \\right) f - \\frac{{\\Phi}}{{e}}$.
    - Use Markdown headers (##, ###).
    """

    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            if response.text: return response.text
        except Exception: continue
            
    raise RuntimeError("Critical Error: All Gemini models failed.")

# ==========================================
# PDF COMPILER
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
    """Processes lines, keeping LaTeX as images and text as normalized strings."""
    # Split by LaTeX blocks first
    parts = re.split(r'(\$.*?\$)', line)
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            try:
                img_buf = render_latex_to_image(part)
                img = Image.open(img_buf)
                w_px, h_px = img.size
                h_mm = 5.0 # Match text height
                w_mm = (w_px / h_px) * h_mm
                
                if pdf.get_x() + w_mm > (pdf.w - 15):
                    pdf.ln(7)
                
                x, y = pdf.get_x(), pdf.get_y()
                pdf.image(img_buf, x=x, y=y, h=h_mm)
                pdf.set_x(x + w_mm + 1)
            except:
                # If LaTeX render fails, strip $ and normalize
                clean_part = normalize_pdf_text(part.replace('$', ''))
                pdf.set_font('Helvetica', '', 11)
                pdf.set_text_color(*BODY_TEXT)
                pdf.write(5, clean_part)
        else:
            # Normalize plain text parts
            clean_text = normalize_pdf_text(part)
            pdf.set_font('Helvetica', '', 11)
            pdf.set_text_color(*BODY_TEXT)
            pdf.write(5, clean_text)
    pdf.ln(8)

def generate_educational_notes(class_num, subject, chapter, status):
    status.write("🧠 Gathering exhaustive data...")
    raw_markdown = fetch_content(class_num, subject, chapter)
    
    status.write("📐 Generating high-res formulas...")
    title_info = f"Class {class_num} | {subject} | {chapter}"
    pdf = NotesPDF(title_info)
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # -- Cover Page --
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')
    pdf.set_y(90)
    pdf.set_font('Helvetica', 'B', 32)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 15, normalize_pdf_text(chapter.upper()), align='C')
    pdf.set_font('Helvetica', '', 18)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.cell(0, 20, normalize_pdf_text(f"Exhaustive Study Guide | {subject}"), ln=True, align='C')
    
    # -- Body Pages --
    pdf.add_page()
    for line in raw_markdown.split('\n'):
        line = line.strip()
        if not line:
            pdf.ln(4); continue
            
        if line.startswith('## '):
            pdf.ln(6)
            pdf.set_font('Helvetica', 'B', 17)
            pdf.set_text_color(*GOLD)
            pdf.cell(0, 10, normalize_pdf_text(line[3:]), ln=True)
            pdf.ln(4)
        elif line.startswith('### '):
            pdf.set_font('Helvetica', 'B', 14)
            pdf.set_text_color(*CYAN)
            pdf.cell(0, 10, normalize_pdf_text(line[4:]), ln=True)
        else:
            process_text_line(pdf, line)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# INTERFACE
# ==========================================
st.set_page_config(page_title="Notes Microservice", page_icon="🧬")

st.title("🧬 Exhaustive Education Engine")
st.markdown("Generates perfect LaTeX notes with failover and character safety.")

cls = st.sidebar.selectbox("Class", [str(i) for i in range(1, 13)], index=11)
sub = st.sidebar.text_input("Subject", "Physics")
chp = st.sidebar.text_input("Chapter", "Dual Nature of Radiation")

if st.button("🚀 Generate Full Notes", use_container_width=True):
    if not API_KEY:
        st.error("Missing GEMINI_API_KEY")
    else:
        with st.status("Initializing...", expanded=True) as status:
            try:
                pdf_data = generate_educational_notes(cls, sub, chp, status)
                status.update(label="✅ Compilation Complete!", state="complete")
                st.download_button(
                    label="📥 Download Detailed PDF",
                    data=pdf_data,
                    file_name=f"{chp}_Complete_Notes.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                status.update(label="❌ Failed", state="error")
                st.error(f"Error detail: {str(e)}")
