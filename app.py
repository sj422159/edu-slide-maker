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
# Make sure GEMINI_API_KEY is set in your environment variables
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

GEMINI_MODELS = [
    "gemini-flash-latest", 
    "gemini-2.0-flash", 
    "gemini-1.5-flash"
]

# Professional Dark Palette
NAVY, GOLD, CYAN = (3, 0, 46), (255, 196, 61), (0, 188, 212)
DARK_CARD, WHITE_TEXT, BODY_TEXT = (20, 24, 45), (240, 240, 250), (210, 215, 230)

# ==========================================
# UTILITY: TEXT CLEANING & NORMALIZATION
# ==========================================
def clean_markdown_formatting(text):
    """
    Removes Markdown syntax symbols like ####, **, and __ 
    so the final PDF text looks clean and professional.
    """
    # Remove header hashes (e.g., #### Section -> Section)
    text = re.sub(r'#+\s*', '', text)
    # Remove bold and italic markers (e.g., **Text** -> Text)
    text = text.replace('**', '').replace('__', '').replace('*', '')
    return text

def normalize_pdf_text(text):
    """
    Sanitizes Unicode characters for FPDF's Latin-1 font support.
    """
    if not text: return ""
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '*',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    
    # Normalize and strip remaining unsupported characters
    text = unicodedata.normalize('NFKD', text)
    return text.encode('latin-1', 'ignore').decode('latin-1')

# ==========================================
# LATEX RENDERING
# ==========================================
def render_latex_to_image(latex_str, font_size=12, color='#D2D7E6'):
    """Renders LaTeX into a high-res transparent PNG for the PDF."""
    if not latex_str.startswith('$'): 
        latex_str = f"${latex_str}$"
        
    buf = io.BytesIO()
    plt.rc('text', usetex=False)
    fig = plt.figure(figsize=(0.01, 0.01)) 
    
    # Matplotlib handles the math typesetting
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
        # Header only on content pages
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
    """Parses text and LaTeX, rendering symbols as images."""
    # Clean table pipes and Markdown artifacts
    if '|' in line:
        line = line.replace('|', '  ')
    
    line = clean_markdown_formatting(line)
    line = normalize_pdf_text(line)
    
    # Split line into text and LaTeX chunks
    parts = re.split(r'(\$.*?\$)', line)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*BODY_TEXT)
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            try:
                img_buf = render_latex_to_image(part)
                img = Image.open(img_buf)
                w_px, h_px = img.size
                h_mm = 4.8  # Target height to match text
                w_mm = (w_px / h_px) * h_mm
                
                # Check for line wrap
                if pdf.get_x() + w_mm > (pdf.w - 15):
                    pdf.ln(7)
                
                pdf.image(img_buf, x=pdf.get_x(), y=pdf.get_y(), h=h_mm)
                pdf.set_x(pdf.get_x() + w_mm + 1)
            except:
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(8)

# ==========================================
# CORE SERVICE LOGIC
# ==========================================
def generate_educational_notes(class_num, subject, chapter, status):
    prompt = f"""
    Act as an expert {subject} teacher. Provide a COMPLETE, exhaustive guide for {chapter} (Class {class_num}).
    
    RULES:
    - Use LaTeX strictly for ALL formulas/symbols wrapped in $...$.
    - Use Markdown headers (##, ###).
    - Include Definitions, Formulas, Solved Examples, and a Question Bank (MCQs & Long Answer).
    """
    
    raw_markdown = ""
    for model in GEMINI_MODELS:
        try:
            status.write(f"Trying model: {model}...")
            res = client.models.generate_content(model=model, contents=prompt)
            if res.text: 
                raw_markdown = res.text
                break
        except: 
            continue

    if not raw_markdown:
        raise Exception("All models failed to generate content.")

    pdf = NotesPDF(f"Class {class_num} | {subject} | {chapter}")
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # -- Cover Page (Simple & Clean) --
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
    
    # -- Content Pages --
    pdf.add_page()
    for line in raw_markdown.split('\n'):
        line_clean = line.strip()
        if not line_clean or '---' in line_clean or '|-' in line_clean:
            continue
        
        if line_clean.startswith('#'):
            pdf.ln(4)
            is_sub = line_clean.startswith('###')
            header_text = clean_markdown_formatting(line_clean)
            pdf.set_font('Helvetica', 'B', 14 if is_sub else 17)
            pdf.set_text_color(*(CYAN if is_sub else GOLD))
            pdf.cell(0, 10, normalize_pdf_text(header_text), ln=True)
            pdf.ln(2)
        else:
            process_text_line(pdf, line_clean)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# STREAMLIT UI
# ==========================================
st.set_page_config(page_title="Notes Microservice", page_icon="📝")

st.title("📝 Perfect Study Guide Gen")
st.markdown("Automated failover AI with high-fidelity LaTeX rendering.")

col1, col2, col3 = st.columns(3)
with col1: cls_input = st.text_input("Class", "12")
with col2: sub_input = st.text_input("Subject", "Physics")
with col3: chp_input = st.text_input("Chapter", "Photoelectric Effect")

if st.button("Generate Final PDF", use_container_width=True):
    if not API_KEY:
        st.error("Missing API Key (GEMINI_API_KEY).")
    else:
        with st.status("Building Study Guide...") as status:
            try:
                pdf_output = generate_educational_notes(cls_input, sub_input, chp_input, status)
                status.update(label="✅ Generation Successful!", state="complete")
                st.download_button(
                    "📥 Download Clean PDF", 
                    data=pdf_output, 
                    file_name=f"{chp_input}_Notes.pdf", 
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                status.update(label="❌ Failed", state="error")
                st.error(str(e))
