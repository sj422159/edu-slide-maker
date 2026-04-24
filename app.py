import streamlit as st
import os, json, re, time, zipfile
from io import BytesIO
from google import genai
from google.genai import types
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

# ================= CONFIG =================
API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=API_KEY)

SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)

# Colors
BG_BLUE = RGBColor(10, 25, 80)
GOLD = RGBColor(255, 196, 61)
WHITE = RGBColor(255, 255, 255)
BLACK = RGBColor(30, 30, 30)

# ================= GEMINI =================
def generate_text(prompt):
    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return resp.text

def generate_slides(class_num, subject, chapter):
    prompt = f"""
Create a JSON array of 20 slides for Class {class_num} {subject} chapter "{chapter}".

Format:
[
{{"type":"title","title":"...","body":["..."]}},
{{"type":"content","title":"...","body":["point1","point2"]}}
]

Rules:
- First slide = title
- Last slide = summary
- Each slide 5 bullet points
Return JSON only.
"""
    text = generate_text(prompt)
    try:
        return json.loads(text)
    except:
        return [
            {"type":"title","title":chapter,"body":[f"Class {class_num} {subject}"]},
            {"type":"content","title":"Error","body":["AI response parsing failed"]}
        ]

# ================= PPT =================
def add_slide(prs, title, bullets, is_title=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    # background
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = BG_BLUE if is_title else WHITE

    # title
    tx = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(11), Inches(1))
    tf = tx.text_frame
    p = tf.paragraphs[0]
    p.text = title
    p.font.size = Pt(36)
    p.font.bold = True
    p.font.color.rgb = GOLD if is_title else BG_BLUE

    # bullets
    for i, b in enumerate(bullets):
        box = slide.shapes.add_textbox(Inches(1), Inches(2+i*0.8), Inches(11), Inches(0.7))
        tf = box.text_frame
        p = tf.paragraphs[0]
        p.text = f"• {b}"
        p.font.size = Pt(20)
        p.font.color.rgb = WHITE if is_title else BLACK

def create_ppt(class_num, subject, chapter):
    slides = generate_slides(class_num, subject, chapter)

    prs = Presentation()
    prs.slide_width = SLIDE_W
    prs.slide_height = SLIDE_H

    for i, s in enumerate(slides):
        add_slide(
            prs,
            s.get("title",""),
            s.get("body",[]),
            is_title=(i==0)
        )

    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)

    filename = f"{chapter.replace(' ','_')}.pptx"
    return buf, filename

# ================= PDF NOTES =================
from fpdf import FPDF

def create_notes(class_num, subject, chapter):
    text = generate_text(f"""
Write detailed study notes for Class {class_num} {subject} chapter "{chapter}".
Include:
- Definitions
- Examples
- Summary
""")

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    for line in text.split("\n"):
        pdf.multi_cell(0, 8, line)

    buf = BytesIO(pdf.output())
    buf.seek(0)

    filename = f"{chapter.replace(' ','_')}_notes.pdf"
    return buf, filename

# ================= UI =================
st.set_page_config(page_title="AI Edu Generator", page_icon="📚")

st.title("📚 AI Education Generator")

col1, col2 = st.columns(2)

with col1:
    class_num = st.text_input("Class")
    subject = st.text_input("Subject")

with col2:
    chapter = st.text_input("Chapter")
    mode = st.multiselect(
        "Generate",
        ["PPT", "Notes"],
        default=["PPT","Notes"]
    )

if st.button("🚀 Generate"):

    if not class_num or not subject or not chapter:
        st.error("Fill all fields")
        st.stop()

    ppt_buf = None
    notes_buf = None

    with st.spinner("Generating..."):

        if "PPT" in mode:
            ppt_buf, ppt_name = create_ppt(class_num, subject, chapter)

        if "Notes" in mode:
            notes_buf, notes_name = create_notes(class_num, subject, chapter)

    st.success("Done!")

    # ZIP
    if ppt_buf and notes_buf:
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as z:
            z.writestr(ppt_name, ppt_buf.getvalue())
            z.writestr(notes_name, notes_buf.getvalue())
        zip_buf.seek(0)

        st.download_button("Download ZIP", zip_buf, "content.zip")

    elif ppt_buf:
        st.download_button("Download PPT", ppt_buf, ppt_name)

    elif notes_buf:
        st.download_button("Download Notes", notes_buf, notes_name)
