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
# CONFIGURATION
# ==========================================
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

GEMINI_MODELS = [
    "gemini-1.5-pro", # Pro models are better for long-form, 30+ page content
    "gemini-flash-latest", 
    "gemini-2.0-flash"
]

# Design Palette
NAVY, GOLD, CYAN = (3, 0, 46), (255, 196, 61), (0, 188, 212)
DARK_CARD, WHITE_TEXT, BODY_TEXT = (20, 24, 45), (240, 240, 250), (210, 215, 230)

# ==========================================
# TEXT & LATEX HELPERS
# ==========================================
def clean_and_normalize(text):
    """Strips markdown artifacts and sanitizes for PDF."""
    text = re.sub(r'#+\s*', '', text) # Remove #
    text = text.replace('**', '').replace('__', '').replace('*', '') # Remove bold/italic
    
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '•',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    
    text = unicodedata.normalize('NFKD', text)
    return text.encode('latin-1', 'ignore').decode('latin-1')

def render_latex(latex_str, font_size=12, color='#D2D7E6'):
    """High-fidelity LaTeX rendering via Matplotlib."""
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
class DeepNotesPDF(FPDF):
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

def write_line(pdf, line):
    """Splits text and LaTeX for inline rendering."""
    line = clean_and_normalize(line)
    parts = re.split(r'(\$.*?\$)', line)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*BODY_TEXT)
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            try:
                img_buf = render_latex(part)
                img = Image.open(img_buf)
                w_px, h_px = img.size
                h_mm = 5.0
                w_mm = (w_px / h_px) * h_mm
                if pdf.get_x() + w_mm > (pdf.w - 15): pdf.ln(7)
                pdf.image(img_buf, x=pdf.get_x(), y=pdf.get_y() - 1, h=h_mm)
                pdf.set_x(pdf.get_x() + w_mm + 1)
            except:
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(8)

# ==========================================
# MAIN LOGIC
# ==========================================
def generate_long_form_notes(cls, sub, chp, status):
    # This prompt is engineered for extreme length
    prompt = f"""
    Generate an EXHAUSTIVE 10,000-word academic study guide for {chp} (Class {cls} {sub}).
    
    REQUIRED SECTIONS FOR 30+ PAGE OUTPUT:
    1. Historical Background: The scientists and experiments that led to these theories.
    2. Fundamental Principles: Deep, multi-paragraph explanations of every core concept.
    3. Mathematical Derivations: Step-by-step mathematical proof for every formula. 
       - USE LaTeX for every single variable/formula (e.g., $h\\nu = \\phi_0 + K_{{max}}$).
    4. Real-World Applications: At least 10 detailed case studies.
    5. Solved Problems: 15 complex, multi-part numerical problems with full solutions.
    6. Exhaustive Question Bank: 
       - 50 MCQs with reasoning.
       - 30 Short Answer Questions with full answers.
       - 20 Long Answer Questions with bulleted model answers.
    
    FORMATTING:
    - Use ## for Main Sections and ### for Sub-sections.
    - NEVER use ** or #### symbols.
    """
    
    content = ""
    for model in GEMINI_MODELS:
        try:
            status.write(f"Querying {model} for deep content...")
            res = client.models.generate_content(model=model, contents=prompt)
            if res.text:
                content = res.text
                break
        except: continue

    pdf = DeepNotesPDF(f"Class {cls} | {sub} | {chp}")
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # Cover Page
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')
    pdf.set_y(110)
    pdf.set_font('Helvetica', 'B', 35)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 15, clean_and_normalize(chp.upper()), align='C')
    pdf.set_font('Helvetica', '', 20)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.cell(0, 20, clean_and_normalize(sub), ln=True, align='C')
    
    # Content
    pdf.add_page()
    for line in content.split('\n'):
        l = line.strip()
        if not l or '|-' in l: continue
        
        if l.startswith('#'):
            pdf.ln(5)
            is_sub = l.startswith('###')
            pdf.set_font('Helvetica', 'B', 14 if is_sub else 18)
            pdf.set_text_color(*(CYAN if is_sub else GOLD))
            pdf.cell(0, 10, clean_and_normalize(l), ln=True)
            pdf.ln(3)
        else:
            write_line(pdf, l)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Deep EduGen", page_icon="📚")
st.title("📚 Deep Education Notes Service")
st.markdown("Generates exhaustive, textbook-length PDFs with perfect LaTeX rendering.")

col1, col2, col3 = st.columns(3)
with col1: class_in = st.text_input("Class", "12")
with col2: subj_in = st.text_input("Subject", "Physics")
with col3: chap_in = st.text_input("Chapter", "Dual Nature of Matter")

if st.button("Generate 30+ Page Study Guide", use_container_width=True):
    if not API_KEY:
        st.error("Set GEMINI_API_KEY environment variable.")
    else:
        with st.status("Generating Deep Content...") as status:
            try:
                pdf_file = generate_long_form_notes(class_in, subj_in, chap_in, status)
                status.update(label="✅ Compilation Complete!", state="complete")
                st.download_button(
                    label="📥 Download Exhaustive PDF",
                    data=pdf_file,
                    file_name=f"{chap_in}_Deep_Notes.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"Error: {e}")
