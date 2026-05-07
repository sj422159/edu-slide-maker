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

# Priority list: Pro models handle long-form 30+ page content best
GEMINI_MODELS = [
    "gemini-1.5-pro", 
    "gemini-1.5-flash",
    "gemini-2.0-flash",
    "gemini-flash-latest"
]

# Color Palette
NAVY, GOLD, CYAN = (3, 0, 46), (255, 196, 61), (0, 188, 212)
DARK_CARD, WHITE_TEXT, BODY_TEXT = (20, 24, 45), (240, 240, 250), (210, 215, 230)

# ==========================================
# TEXT & LATEX UTILITIES
# ==========================================
def clean_and_normalize_text(text):
    """Removes markdown symbols and sanitizes for PDF Latin-1 support."""
    # Remove #### and **
    text = re.sub(r'#+\s*', '', text)
    text = text.replace('**', '').replace('__', '').replace('*', '')
    
    # Manual map for common typography errors
    replacements = {
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-', '\u2022': '•',
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    
    # Final Unicode cleanup
    text = unicodedata.normalize('NFKD', text)
    return text.encode('latin-1', 'ignore').decode('latin-1')

def render_math_formula(latex_str, font_size=12, color='#D2D7E6'):
    """Renders LaTeX into a high-res transparent PNG via Matplotlib."""
    if not latex_str.startswith('$'): 
        latex_str = f"${latex_str}$"
        
    buf = io.BytesIO()
    plt.rc('text', usetex=False) # Use Matplotlib's internal renderer
    fig = plt.figure(figsize=(0.01, 0.01)) 
    
    # Text color matches BODY_TEXT for seamless reading
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
            # Background Fill
            self.set_fill_color(*NAVY)
            self.rect(0, 0, self.w, self.h, 'F')
            # Top Bar
            self.set_fill_color(*DARK_CARD)
            self.rect(0, 0, self.w, 14, 'F')
            self.set_font('Helvetica', 'B', 8)
            self.set_text_color(*CYAN)
            self.set_xy(10, 4)
            self.cell(0, 6, clean_and_normalize_text(self.title_info), align='R')
            self.set_y(22)

def process_and_write_line(pdf, line):
    """Splits text and LaTeX for inline image placement."""
    line = clean_and_normalize_text(line)
    parts = re.split(r'(\$.*?\$)', line)
    
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*BODY_TEXT)
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            try:
                img_buf = render_math_formula(part)
                img = Image.open(img_buf)
                w_px, h_px = img.size
                h_mm = 5.0 # Height in mm to match text
                w_mm = (w_px / h_px) * h_mm
                
                # Check for line wrap
                if pdf.get_x() + w_mm > (pdf.w - 15):
                    pdf.ln(7)
                
                x_pos, y_pos = pdf.get_x(), pdf.get_y()
                # Align slightly below baseline for math subscripts
                pdf.image(img_buf, x=x_pos, y=y_pos - 0.5, h=h_mm)
                pdf.set_x(x_pos + w_mm + 1)
            except:
                # Fallback to text if LaTeX rendering fails
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(8)

# ==========================================
# CORE GENERATION LOGIC
# ==========================================
def generate_exhaustive_pdf(cls, sub, chp, status):
    # Prompt is tuned for 30+ page depth
    prompt = f"""
    Act as a Distinguished Professor. Create a textbook-length (minimum 8,000 words) study guide for {chp} (Class {cls} {sub}).
    
    STRUCTURE:
    1. Introduction & Historical Significance (Deep overview).
    2. Fundamental Concepts: Exhaustive explanation of every concept.
    3. Mathematical Proofs & Derivations: Use LaTeX strictly for every variable/formula wrapped in $ (e.g. $E=mc^2$).
    4. Practical Applications & Industry Usage.
    5. Detailed Numerical Problems: 10 step-by-step solved examples.
    6. Exhaustive Question Bank: 40 MCQs, 20 Short Answers, 15 Long Answers (all with full model answers).

    FORMATTING RULES:
    - Use ## for Section Titles.
    - Use ### for Subsection Titles.
    - DO NOT use ** or #### symbols.
    """
    
    markdown_data = ""
    for model in GEMINI_MODELS:
        try:
            status.write(f"Initiating sequence with {model}...")
            res = client.models.generate_content(model=model, contents=prompt)
            if res.text:
                markdown_data = res.text
                break
        except:
            continue

    if not markdown_data:
        raise Exception("All Gemini models failed. Check your API quota/key.")

    pdf = DeepNotesPDF(f"Class {cls} | {sub} | {chp}")
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # --- Cover Page ---
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')
    pdf.set_y(100)
    pdf.set_font('Helvetica', 'B', 32)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 15, clean_and_normalize_text(chp.upper()), align='C')
    pdf.set_font('Helvetica', '', 18)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.cell(0, 20, clean_and_normalize_text(sub), ln=True, align='C')
    
    # --- Content ---
    pdf.add_page()
    status.write("Compiling pages and typesetting math...")
    for line in markdown_data.split('\n'):
        l = line.strip()
        if not l or '---' in l or '|-' in l:
            continue
        
        if l.startswith('#'):
            pdf.ln(5)
            is_sub = l.startswith('###')
            pdf.set_font('Helvetica', 'B', 14 if is_sub else 18)
            pdf.set_text_color(*(CYAN if is_sub else GOLD))
            pdf.cell(0, 10, clean_and_normalize_text(l), ln=True)
            pdf.ln(3)
        else:
            process_and_write_line(pdf, l)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# INTERFACE
# ==========================================
st.set_page_config(page_title="DeepNotes Service", page_icon="📓")

st.title("📓 DeepNotes: Exhaustive PDF Engine")
st.markdown("Generates textbook-style study guides with textbook-quality LaTeX equations.")

col1, col2, col3 = st.columns(3)
with col1: cls_input = st.text_input("Class", "12")
with col2: sub_input = st.text_input("Subject", "Physics")
with col3: chp_input = st.text_input("Chapter", "Photoelectric Effect")

if st.button("🚀 Generate Exhaustive Study Guide", use_container_width=True):
    if not API_KEY:
        st.error("API Key not found in environment variables.")
    else:
        with st.status("Building Master Document...", expanded=True) as status:
            try:
                final_pdf = generate_exhaustive_pdf(cls_input, sub_input, chp_input, status)
                status.update(label="✨ Study Guide Ready!", state="complete")
                st.download_button(
                    "📥 Download Professional PDF",
                    data=final_pdf,
                    file_name=f"{chp_input}_Exhaustive_Notes.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                st.error(f"Generation Failed: {e}")
