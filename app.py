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
    Sanitizes AI-generated text for FPDF's Latin-1 limitations.
    Prevents the 'Character outside range' error.
    """
    if not text: return ""
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201a': "'",
        '\u201c': '"', '\u201d': '"', '\u201e': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '*',
        '\u2112': 'L', '\u2115': 'N', '\u211d': 'R',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    
    # Remove characters that cannot be represented in Latin-1
    return unicodedata.normalize('NFKD', text).encode('latin-1', 'replace').decode('latin-1')

# ==========================================
# LATEX RENDERING ENGINE
# ==========================================
def render_latex_to_image(latex_str, font_size=12, color='#D2D7E6'):
    """Renders LaTeX into high-res transparent PNGs via Matplotlib."""
    if not latex_str.startswith('$'):
        latex_str = f"${latex_str}$"
    
    buf = io.BytesIO()
    plt.rc('text', usetex=False) # Use Matplotlib's internal parser
    fig = plt.figure(figsize=(0.01, 0.01)) 
    
    fig.text(0, 0, latex_str, fontsize=font_size, color=color)
    plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0.03, dpi=300)
    plt.close(fig)
    buf.seek(0)
    return buf

# ==========================================
# CONTENT GENERATION (EXHAUSTIVE MODE)
# ==========================================
def fetch_content(class_num, subject, chapter):
    """Generates detailed educational content with model failover."""
    prompt = f"""
    You are an expert {subject} educator. Generate an EXHAUSTIVE, high-level study guide for "{chapter}" for Class {class_num}.
    
    CONTENT STRUCTURE REQUIRED:
    1. Comprehensive Chapter Overview (Minimum 300 words).
    2. Detailed Concepts: Break down every sub-topic with deep technical explanations.
    3. Mathematical/Scientific Foundations: Explicitly state all laws, derivations, and formulas.
    4. Comparative Analysis: Tables or lists comparing related concepts (e.g., A vs B).
    5. Solved Numerical Examples: Provide 5 step-by-step solved problems.
    6. Question Bank:
       - 20 MCQs with reasoning for answers.
       - 15 Short Answer questions (3 marks each).
       - 10 Long Answer/Essay questions (5 marks each).
    
    TECHNICAL RULES:
    - ALWAYS wrap math symbols, variables, equations, and chemical formulas in single dollar signs ($).
    - Use LaTeX strictly (e.g., $\\Delta H$, $C_6H_{{12}}O_6$, $\\int_0^\\infty$).
    - Use Markdown headers (##, ###).
    """

    for model_name in GEMINI_MODELS:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            if response.text: return response.text
        except Exception: continue
            
    raise RuntimeError("Critical Error: All AI nodes failed to respond.")

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
    """Handles hybrid text-LaTeX line rendering."""
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
                h_mm = 4.8  # Slightly larger for better readability
                w_mm = (w_px / h_px) * h_mm
                
                if pdf.get_x() + w_mm > (pdf.w - 15):
                    pdf.ln(7)
                
                x, y = pdf.get_x(), pdf.get_y()
                pdf.image(img_buf, x=x, y=y, h=h_mm)
                pdf.set_x(x + w_mm + 1)
            except:
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(8)

def generate_educational_notes(class_num, subject, chapter, status):
    status.write("🧠 Accessing Deep Knowledge Base...")
    raw_markdown = fetch_content(class_num, subject, chapter)
    
    status.write("📐 Configuring Page Layouts...")
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
    status.write("🖋️ Typesetting Complex Formulas...")
    for line in raw_markdown.split('\n'):
        line = line.strip()
        if not line:
            pdf.ln(4); continue
            
        if line.startswith('## '):
            pdf.ln(6)
            pdf.set_font('Helvetica', 'B', 17)
            pdf.set_text_color(*GOLD)
            pdf.cell(0, 10, normalize_pdf_text(line[3:]), ln=True)
            pdf.set_draw_color(*GOLD)
            pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + 50, pdf.get_y())
            pdf.ln(4)
        elif line.startswith('### '):
            pdf.ln(2)
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
st.set_page_config(page_title="EduNotes Microservice", page_icon="🧬", layout="wide")

st.title("🧬 EduNotes: Exhaustive Knowledge Engine")
st.markdown("Generates detailed, LaTeX-ready notes with textbook-quality formatting.")

with st.sidebar:
    st.header("Parameters")
    cls = st.selectbox("Select Class", [str(i) for i in range(1, 13)], index=11)
    sub = st.text_input("Subject", "Physics")
    chp = st.text_input("Chapter", "Quantum Mechanics")
    st.info("Uses sequential model failover for 100% uptime.")

if st.button("🚀 Start Deep Generation", use_container_width=True):
    if not API_KEY:
        st.error("Missing Environment Variable: GEMINI_API_KEY")
    else:
        with st.status("Initializing High-Fidelity Pipeline...", expanded=True) as status:
            try:
                pdf_data = generate_educational_notes(cls, sub, chp, status)
                status.update(label="✨ Exhaustive Notes Compiled!", state="complete")
                st.download_button(
                    label="📥 Download Detailed Study Guide (PDF)",
                    data=pdf_data,
                    file_name=f"{chp}_Complete_Notes.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                status.update(label="❌ Generation Failed", state="error")
                st.error(f"Traceback: {e}")
