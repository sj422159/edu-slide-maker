import os
import re
import io
import time
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

# Design Palette
NAVY = (3, 0, 46)
GOLD = (255, 196, 61)
CYAN = (0, 188, 212)
DARK_CARD = (20, 24, 45)
WHITE_TEXT = (240, 240, 250)
BODY_TEXT = (210, 215, 230)

# ==========================================
# LATEX RENDERING ENGINE
# ==========================================
def render_latex_to_image(latex_str, font_size=12, color='#D2D7E6'):
    if not latex_str.startswith('$'):
        latex_str = f"${latex_str}$"
    
    buf = io.BytesIO()
    plt.rc('text', usetex=False)
    fig = plt.figure(figsize=(0.01, 0.01)) 
    
    fig.text(0, 0, latex_str, fontsize=font_size, color=color)
    plt.savefig(buf, format='png', transparent=True, bbox_inches='tight', pad_inches=0.02, dpi=300)
    plt.close(fig)
    buf.seek(0)
    return buf

# ==========================================
# CONTENT GENERATION WITH FAILOVER
# ==========================================
def fetch_content(class_num, subject, chapter):
    prompt = f"""
    You are an expert {subject} teacher. Create a COMPLETE, highly detailed study guide for "{chapter}" for Class {class_num}.
    
    STRICT RULES:
    1. Use LaTeX for ALL math/science symbols, equations, and chemical formulas. Wrap them in single dollar signs.
       Example: Use $\\rightarrow$ for arrows, $H_2O$ for water, and $x^2$ for squares.
    2. Format using Markdown headers (## for sections, ### for sub-sections).
    3. Include 15 MCQs, 10 Short Answer, and 5 Long Answer questions at the end with detailed model answers.
    4. Provide exhaustive theory notes.
    """

    for model_name in GEMINI_MODELS:
        try:
            st.write(f"📡 Attempting with {model_name}...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            if response.text:
                return response.text
        except Exception as e:
            st.warning(f"⚠️ {model_name} failed: {str(e)[:100]}... Trying next model.")
            continue
            
    raise RuntimeError("All Gemini models failed. Please check your API key or connection.")

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
            self.cell(0, 6, self.title_info, align='R')
            self.set_y(20)

def process_text_line(pdf, line):
    parts = re.split(r'(\$.*?\$)', line)
    pdf.set_font('Helvetica', '', 11)
    pdf.set_text_color(*BODY_TEXT)
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            try:
                img_buf = render_latex_to_image(part)
                img = Image.open(img_buf)
                w_px, h_px = img.size
                h_mm = 4.5
                w_mm = (w_px / h_px) * h_mm
                
                if pdf.get_x() + w_mm > (pdf.w - 15):
                    pdf.ln(6)
                
                x, y = pdf.get_x(), pdf.get_y()
                pdf.image(img_buf, x=x, y=y, h=h_mm)
                pdf.set_x(x + w_mm + 1)
            except:
                pdf.write(5, part.replace('$', ''))
        else:
            pdf.write(5, part)
    pdf.ln(7)

def generate_educational_notes(class_num, subject, chapter):
    raw_markdown = fetch_content(class_num, subject, chapter)
    
    title_info = f"Class {class_num} | {subject} | {chapter}"
    pdf = NotesPDF(title_info)
    pdf.set_auto_page_break(auto=True, margin=20)
    
    # Cover
    pdf.add_page()
    pdf.set_fill_color(*NAVY)
    pdf.rect(0, 0, pdf.w, pdf.h, 'F')
    pdf.set_y(100)
    pdf.set_font('Helvetica', 'B', 28)
    pdf.set_text_color(*GOLD)
    pdf.multi_cell(0, 15, chapter.upper(), align='C')
    pdf.set_font('Helvetica', '', 16)
    pdf.set_text_color(*WHITE_TEXT)
    pdf.cell(0, 20, f"{subject} - Study Guide", ln=True, align='C')
    
    # Body
    pdf.add_page()
    for line in raw_markdown.split('\n'):
        line = line.strip()
        if not line:
            pdf.ln(3)
            continue
            
        if line.startswith('## '):
            pdf.ln(5)
            pdf.set_font('Helvetica', 'B', 16)
            pdf.set_text_color(*GOLD)
            pdf.cell(0, 10, line[3:], ln=True)
            pdf.ln(2)
        elif line.startswith('### '):
            pdf.set_font('Helvetica', 'B', 13)
            pdf.set_text_color(*CYAN)
            pdf.cell(0, 10, line[4:], ln=True)
        else:
            process_text_line(pdf, line)

    buf = io.BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf

# ==========================================
# MICRO-INTERFACE
# ==========================================
st.set_page_config(page_title="Notes Microservice", page_icon="🧪")

st.title("🧪 Smart Education Engine")
st.markdown("Generates perfect LaTeX notes with automated model failover.")

col1, col2, col3 = st.columns(3)
with col1: cls = st.text_input("Class", "12")
with col2: sub = st.text_input("Subject", "Chemistry")
with col3: chp = st.text_input("Chapter", "Chemical Kinetics")

if st.button("🚀 Generate PDF Notes", use_container_width=True):
    if not API_KEY:
        st.error("API Key missing.")
    else:
        with st.status("Initializing AI Pipeline...", expanded=True) as status:
            try:
                pdf_data = generate_educational_notes(cls, sub, chp)
                status.update(label="✅ PDF Generated Successfully!", state="complete")
                st.download_button(
                    label="📥 Download Study Guide",
                    data=pdf_data,
                    file_name=f"{chp}_Notes.pdf",
                    mime="application/pdf",
                    use_container_width=True
                )
            except Exception as e:
                status.update(label="❌ Pipeline Failed", state="error")
                st.error(f"Error: {e}")
