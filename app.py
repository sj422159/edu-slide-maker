import streamlit as st
import os, json, requests, re, time, zipfile
from urllib.parse import quote_plus
from google import genai
from google.genai import types
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.oxml.ns import qn
from io import BytesIO
from PIL import Image
from fpdf import FPDF

# ==========================================
# CONFIGURATION
# ==========================================
API_KEY = os.getenv('GEMINI_API_KEY')
client = genai.Client(api_key=API_KEY)

# Professional Color Palette
DARK_BG       = RGBColor(3, 0, 46)
ACCENT_BG     = RGBColor(30, 60, 114)
CARD_BG       = RGBColor(34, 40, 60)
WHITE         = RGBColor(255, 255, 255)
LIGHT_GRAY    = RGBColor(200, 210, 225)
ACCENT_GOLD   = RGBColor(255, 196, 61)
ACCENT_CYAN   = RGBColor(0, 188, 212)
SUBTLE_GRAY   = RGBColor(120, 130, 150)

SLIDE_W = Inches(13.33)
SLIDE_H = Inches(7.5)
FONT_TITLE = "Segoe UI"
FONT_BODY  = "Segoe UI"

LOGO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")

# ==========================================
# GEMINI HELPERS
# ==========================================
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
]

def parse_json_response(text):
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', text.strip(), flags=re.MULTILINE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    match = re.search(r'\[.*\]', cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Could not parse JSON from response:\n{text[:500]}")

def gemini_generate(prompt, response_mime='application/json'):
    """Try each model in GEMINI_MODELS; on rate limit move to the next."""
    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                return client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type=response_mime)
                )
            except Exception as e:
                err = str(e)
                if '429' in err or 'RESOURCE_EXHAUSTED' in err:
                    if attempt < 2:
                        time.sleep(15 * (attempt + 1))
                        continue
                    errors.append(f"{model}: rate limited")
                    break  # move to next model
                else:
                    raise
    raise RuntimeError(f"All Gemini models exhausted. Tried: {', '.join(errors)}")

def gemini_generate_text(prompt):
    """Generate plain text (for notes) with model fallback."""
    errors = []
    for model in GEMINI_MODELS:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                return resp.text
            except Exception as e:
                err = str(e)
                if '429' in err or 'RESOURCE_EXHAUSTED' in err:
                    if attempt < 2:
                        time.sleep(15 * (attempt + 1))
                        continue
                    errors.append(f"{model}: rate limited")
                    break
                else:
                    raise
    raise RuntimeError(f"All Gemini models exhausted. Tried: {', '.join(errors)}")

# ==========================================
# IMAGE SCRAPER
# ==========================================
def scrape_image(query):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        url = f"https://www.bing.com/images/search?q={quote_plus(query)}&first=1"
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        img_urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+?)&quot;', resp.text)
        if not img_urls:
            img_urls = re.findall(r'src2?="(https?://[^"]+\.(?:jpg|jpeg|png|webp))', resp.text)
        for img_url in img_urls[:5]:
            try:
                r = requests.get(img_url, headers=headers, timeout=10)
                if r.status_code == 200 and len(r.content) > 2000:
                    stream = BytesIO(r.content)
                    img = Image.open(stream)
                    if img.format not in ('BMP', 'GIF', 'JPEG', 'PNG', 'TIFF', 'WMF'):
                        buf = BytesIO()
                        img.convert('RGBA').save(buf, format='PNG')
                        buf.seek(0)
                        return buf
                    stream.seek(0)
                    return stream
            except Exception:
                continue
        return None
    except Exception:
        return None

# ==========================================
# SHAPE HELPERS
# ==========================================
def set_slide_bg(slide, color):
    bg = slide.background
    bg.fill.solid()
    bg.fill.fore_color.rgb = color

def add_textbox(slide, left, top, width, height, text, font_size=18,
                color=WHITE, bold=False, alignment=PP_ALIGN.LEFT,
                font_name=FONT_BODY, anchor=MSO_ANCHOR.TOP):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    tf.auto_size = None
    try:
        tf.paragraphs[0].alignment = alignment
    except Exception:
        pass
    txBox.text_frame._txBody.bodyPr.set('anchor', {
        MSO_ANCHOR.TOP: 't', MSO_ANCHOR.MIDDLE: 'ctr', MSO_ANCHOR.BOTTOM: 'b'
    }.get(anchor, 't'))
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.color.rgb = color
    p.font.bold = bold
    p.font.name = font_name
    p.alignment = alignment
    return txBox

def add_bullet_list(slide, left, top, width, height, items, font_size=16,
                    color=LIGHT_GRAY, bullet_char="▸"):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for idx, item in enumerate(items):
        if idx == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = f"{bullet_char}  {item}"
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        p.font.name = FONT_BODY
        p.space_after = Pt(6)
    return txBox

def add_accent_line(slide, left, top, width, color=ACCENT_CYAN):
    shape = slide.shapes.add_shape(1, left, top, width, Pt(3))
    shape.fill.solid()
    shape.fill.fore_color.rgb = color
    shape.line.fill.background()
    return shape

def add_rounded_card(slide, left, top, width, height, fill_color=CARD_BG):
    shape = slide.shapes.add_shape(5, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    shape.line.fill.background()
    shape.shadow.inherit = False
    return shape

# ==========================================
# LOGO HELPER
# ==========================================
def _lock_shape(shape):
    cNvPicPr = shape._element.find('.//' + qn('p:cNvPicPr'))
    if cNvPicPr is not None:
        locks = cNvPicPr.find(qn('a:picLocks'))
        if locks is None:
            from lxml import etree
            locks = etree.SubElement(cNvPicPr, qn('a:picLocks'))
        locks.set('noSelect', '1')
        locks.set('noMove', '1')
        locks.set('noResize', '1')
        locks.set('noRot', '1')
        locks.set('noChangeAspect', '1')

def add_logo(slide):
    if not os.path.exists(LOGO_PATH):
        return
    pic = slide.shapes.add_picture(LOGO_PATH, Inches(0.3), Inches(0.2), height=Inches(0.5))
    _lock_shape(pic)

# ==========================================
# SLIDE BUILDERS
# ==========================================
def build_title_slide(prs, title, subtitle):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_accent_line(slide, Inches(1.5), Inches(2.5), Inches(10.33))
    add_textbox(slide, Inches(1.5), Inches(2.7), Inches(10.33), Inches(1.5),
                title, font_size=42, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_textbox(slide, Inches(1.5), Inches(4.3), Inches(10.33), Inches(1),
                subtitle, font_size=20, color=WHITE, alignment=PP_ALIGN.CENTER)
    add_accent_line(slide, Inches(1.5), Inches(5.2), Inches(10.33))
    add_logo(slide)

def build_section_slide(prs, section_number, section_title):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(1), Inches(2.3), Inches(11.33), Inches(1),
                f"SECTION {section_number}", font_size=18, color=ACCENT_CYAN,
                bold=True, alignment=PP_ALIGN.CENTER)
    add_textbox(slide, Inches(1), Inches(3.1), Inches(11.33), Inches(1.5),
                section_title, font_size=38, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(5), Inches(4.7), Inches(3.33), ACCENT_GOLD)
    add_logo(slide)

def build_content_slide(prs, title, bullets):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(0.5), Inches(0.3), Inches(12.33), Inches(0.8),
                title, font_size=30, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(0.8), Inches(1.2), Inches(11.73))
    add_rounded_card(slide, Inches(0.8), Inches(1.5), Inches(11.73), Inches(5.5))
    if isinstance(bullets, list):
        add_bullet_list(slide, Inches(1.3), Inches(1.8), Inches(10.73), Inches(5),
                        bullets, font_size=18, color=WHITE)
    else:
        lines = [l.strip('- •▸').strip() for l in str(bullets).split('\n') if l.strip()]
        if len(lines) <= 1:
            lines = [s.strip() for s in str(bullets).split('.') if s.strip()]
        add_bullet_list(slide, Inches(1.3), Inches(1.8), Inches(10.73), Inches(5),
                        lines[:10], font_size=18, color=WHITE)
    add_logo(slide)

def build_diagram_slide(prs, title, bullets, search_query):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(0.5), Inches(0.3), Inches(12.33), Inches(0.8),
                title, font_size=28, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(0.8), Inches(1.2), Inches(11.73))
    add_rounded_card(slide, Inches(0.8), Inches(1.5), Inches(5.5), Inches(5.5))
    if isinstance(bullets, list):
        items = bullets
    else:
        items = [l.strip('- •▸').strip() for l in str(bullets).split('\n') if l.strip()]
        if len(items) <= 1:
            items = [s.strip() for s in str(bullets).split('.') if s.strip()]
    add_bullet_list(slide, Inches(1.2), Inches(1.8), Inches(4.8), Inches(5),
                    items[:8], font_size=18, color=WHITE)
    img_stream = scrape_image(search_query)
    if img_stream:
        add_rounded_card(slide, Inches(6.8), Inches(1.5), Inches(6.03), Inches(5.5),
                         fill_color=RGBColor(25, 30, 48))
        slide.shapes.add_picture(img_stream, Inches(7.0), Inches(1.7),
                                 width=Inches(5.6), height=Inches(5.0))
    else:
        add_rounded_card(slide, Inches(6.8), Inches(1.5), Inches(6.03), Inches(5.5),
                         fill_color=RGBColor(25, 30, 48))
        add_textbox(slide, Inches(7.0), Inches(3.5), Inches(5.5), Inches(1),
                    "[Diagram: " + search_query + "]",
                    font_size=16, color=SUBTLE_GRAY, alignment=PP_ALIGN.CENTER)
    add_logo(slide)

def build_summary_slide(prs, title, points):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(0.5), Inches(0.4), Inches(12.33), Inches(0.8),
                title, font_size=32, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(0.8), Inches(1.3), Inches(11.73), ACCENT_GOLD)
    mid = (len(points) + 1) // 2
    left_items = points[:mid]
    right_items = points[mid:]
    add_rounded_card(slide, Inches(0.8), Inches(1.6), Inches(5.6), Inches(5.3))
    add_bullet_list(slide, Inches(1.2), Inches(1.9), Inches(4.9), Inches(4.8),
                    left_items, font_size=18, color=WHITE, bullet_char="✦")
    if right_items:
        add_rounded_card(slide, Inches(6.9), Inches(1.6), Inches(5.6), Inches(5.3))
        add_bullet_list(slide, Inches(7.3), Inches(1.9), Inches(4.9), Inches(4.8),
                        right_items, font_size=18, color=WHITE, bullet_char="✦")
    add_logo(slide)

def build_thank_you_slide(prs, class_num, subject, chapter):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    set_slide_bg(slide, DARK_BG)
    add_textbox(slide, Inches(1), Inches(2.5), Inches(11.33), Inches(1.5),
                "Thank You", font_size=48, color=ACCENT_GOLD, bold=True,
                alignment=PP_ALIGN.CENTER, font_name=FONT_TITLE)
    add_accent_line(slide, Inches(5), Inches(4.2), Inches(3.33), ACCENT_GOLD)
    add_textbox(slide, Inches(1), Inches(4.5), Inches(11.33), Inches(1),
                f"Class {class_num} {subject} — {chapter}",
                font_size=18, color=WHITE, alignment=PP_ALIGN.CENTER)
    add_logo(slide)

# ==========================================
# PROMPTS
# ==========================================
GEMINI_PROMPT_TEMPLATE = """You are an expert {subject} teacher. Create a structured presentation on "{chapter}" for Class {class_num} students.

Return a JSON array of exactly 30 slide objects. Each slide must have these keys:
- "type": one of "title", "section", "content", "diagram", "summary"
- "title": slide heading
- "body": array of 4-6 concise bullet point strings (not needed for title/section)
- "search_query": a short image search query ONLY for "diagram" type slides (e.g. "labeled diagram of ..."). Leave empty string for other types.
- "section_number": integer, only for "section" type slides

STRUCTURE RULES:
- Slide 1: type "title" (main title slide)
- Then organize into 4-6 logical sections based on the chapter content
- Each section starts with a "section" slide
- Within each section: mix of "content" (text-only explanation) and "diagram" slides (where a labeled diagram is essential)
- Use "diagram" type ONLY for slides that genuinely need a visual. About 8-10 diagram slides total.
- Last slide: type "summary"
- Keep bullet points concise (max 15 words each)

Return ONLY the JSON array, no other text."""

GEMINI_PROMPT_TEXT_ONLY = """You are an expert {subject} teacher. Create a detailed, text-heavy presentation on "{chapter}" for Class {class_num} students.

Return a JSON array of exactly 30 slide objects. Each slide must have these keys:
- "type": one of "title", "section", "content", "summary"
- "title": slide heading
- "body": array of 6-8 detailed, informative bullet point strings. Each bullet should be a complete explanation (15-25 words) that teaches the concept clearly. NOT needed for title/section slides.
- "section_number": integer, only for "section" type slides

STRUCTURE RULES:
- Slide 1: type "title" (main title slide, body should have a one-line subtitle)
- Then organize into 4-6 logical sections based on the chapter content
- Each section starts with a "section" slide
- All other slides are "content" type — NO diagram slides. Every slide must have detailed text.
- Last slide: type "summary" with key revision points
- Make bullet points detailed and educational — students should be able to learn from reading the slides alone
- Cover definitions, processes, examples, comparisons, and important facts
- Include exam-relevant points, mnemonics, and key differences where applicable

Return ONLY the JSON array, no other text."""

# ==========================================
# NOTES PROMPT
# ==========================================
NOTES_PROMPT = """You are an expert {subject} teacher. Write comprehensive, well-structured study notes on "{chapter}" for Class {class_num} students.

The notes should include:
- Chapter overview
- All key concepts with clear explanations
- Important definitions
- Diagrams described in text (e.g. "Diagram: ...")
- Formulas and equations where applicable
- Comparisons and differences in table format (use markdown tables)
- Examples and solved problems where relevant
- Key points to remember / exam tips
- Summary at the end

Format the notes in clean Markdown with proper headings (#, ##, ###), bullet points, bold for key terms, and tables where useful.
Make it detailed enough that a student can use these notes alone to study for exams."""

# ==========================================
# PPT GENERATION
# ==========================================
def generate_ppt(class_num, subject, chapter, use_images):
    if use_images:
        prompt = GEMINI_PROMPT_TEMPLATE.format(
            class_num=class_num, subject=subject, chapter=chapter
        )
    else:
        prompt = GEMINI_PROMPT_TEXT_ONLY.format(
            class_num=class_num, subject=subject, chapter=chapter
        )

    response = gemini_generate(prompt)
    slides_data = parse_json_response(response.text)

    prs = Presentation()
    prs.slide_width, prs.slide_height = SLIDE_W, SLIDE_H

    slide_num = 0

    for data in slides_data:
        slide_type = data.get('type', 'content')
        title = data.get('title', '')
        body = data.get('body', [])
        if slide_type in ('content', 'diagram', 'summary') and not body:
            body = [f"Key points about {title}"]
        query = data.get('search_query', '')
        sec_num = data.get('section_number', '')

        if slide_type == 'title':
            subtitle = body[0] if isinstance(body, list) and body else f"Class {class_num} {subject}"
            build_title_slide(prs, title, subtitle)

        elif slide_type == 'section':
            build_section_slide(prs, sec_num, title)

        elif slide_type == 'diagram':
            slide_num += 1
            if use_images:
                build_diagram_slide(prs, title, body, query)
            else:
                build_content_slide(prs, title, body)

        elif slide_type == 'summary':
            items = body if isinstance(body, list) else [body]
            build_summary_slide(prs, title, items)

        else:
            slide_num += 1
            build_content_slide(prs, title, body)

    build_thank_you_slide(prs, class_num, subject, chapter)

    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)

    safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
    filename = f"Class{class_num}_{subject}_{safe_name}.pptx"
    return buf, filename

def generate_notes(class_num, subject, chapter):
    prompt = NOTES_PROMPT.format(
        class_num=class_num, subject=subject, chapter=chapter
    )
    notes_text = gemini_generate_text(prompt)
    safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
    filename = f"Class{class_num}_{subject}_{safe_name}_Notes.pdf"

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    # Use Windows Arial for full Unicode support
    win_font = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
    pdf.add_font('Arial', '', os.path.join(win_font, 'arial.ttf'), uni=True)
    pdf.add_font('Arial', 'B', os.path.join(win_font, 'arialbd.ttf'), uni=True)
    pdf.add_page()
    pdf.set_font('Arial', size=11)

    for line in notes_text.split('\n'):
        stripped = line.strip()
        if stripped.startswith('### '):
            pdf.set_font('Arial', 'B', 13)
            pdf.multi_cell(0, 7, stripped[4:])
            pdf.ln(2)
            pdf.set_font('Arial', size=11)
        elif stripped.startswith('## '):
            pdf.set_font('Arial', 'B', 15)
            pdf.multi_cell(0, 8, stripped[3:])
            pdf.ln(2)
            pdf.set_font('Arial', size=11)
        elif stripped.startswith('# '):
            pdf.set_font('Arial', 'B', 18)
            pdf.multi_cell(0, 10, stripped[2:])
            pdf.ln(3)
            pdf.set_font('Arial', size=11)
        elif stripped.startswith('- ') or stripped.startswith('* '):
            pdf.set_x(15)
            # Handle **bold** in bullet text
            bullet_text = '  \u2022  ' + stripped[2:]
            clean_text = re.sub(r'\*\*(.+?)\*\*', r'\1', bullet_text)
            pdf.multi_cell(0, 6, clean_text)
            pdf.ln(1)
        elif stripped.startswith('|') and '|' in stripped[1:]:
            # Table row
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            if all(set(c) <= {'-', ':', ' '} for c in cells):
                continue  # skip separator rows
            col_w = (pdf.w - 20) / max(len(cells), 1)
            for cell in cells:
                clean = re.sub(r'\*\*(.+?)\*\*', r'\1', cell)
                pdf.cell(col_w, 7, clean, border=1)
            pdf.ln()
        elif stripped == '':
            pdf.ln(3)
        else:
            clean_text = re.sub(r'\*\*(.+?)\*\*', r'\1', stripped)
            pdf.multi_cell(0, 6, clean_text)
            pdf.ln(1)

    buf = BytesIO()
    buf.write(pdf.output())
    buf.seek(0)
    return buf, filename

# ==========================================
# STREAMLIT UI
# ==========================================
st.set_page_config(page_title="AI Presentation Maker", page_icon="📚", layout="centered")

st.markdown("""
<style>
    .stApp { background-color: #03002e; }
    h1, h2, h3 { color: #ffc43d; }
    .stMarkdown p { color: #c8d2e1; }
</style>
""", unsafe_allow_html=True)

# Logo at top
if os.path.exists(LOGO_PATH):
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.image(LOGO_PATH, width=200)

st.title("📚 AI Presentation Maker")
st.markdown("Generate professional 30-slide presentations + study notes powered by AI")

st.divider()

col1, col2 = st.columns(2)
with col1:
    class_num = st.text_input("Class", placeholder="e.g. 10")
    subject = st.text_input("Subject", placeholder="e.g. Biology")
with col2:
    chapter = st.text_input("Chapter", placeholder="e.g. Human Life Processes")
    use_images = st.checkbox("Include images/diagrams", value=False)

st.divider()

if st.button("🚀 Generate Presentation & Notes", type="primary", use_container_width=True):
    if not class_num or not subject or not chapter:
        st.error("Please fill in all fields.")
    elif not API_KEY:
        st.error("GEMINI_API_KEY environment variable not set.")
    else:
        with st.status("Generating your content...", expanded=True) as status:
            # --- PPT ---
            st.write("🤖 Generating 30-slide presentation...")
            try:
                ppt_buf, ppt_filename = generate_ppt(class_num, subject, chapter, use_images)
            except Exception as e:
                status.update(label="❌ PPT generation failed", state="error")
                st.error(f"PPT Error: {e}")
                st.stop()

            # --- Notes ---
            st.write("📝 Generating study notes...")
            try:
                notes_buf, notes_filename = generate_notes(class_num, subject, chapter)
            except Exception as e:
                status.update(label="❌ Notes generation failed", state="error")
                st.error(f"Notes Error: {e}")
                st.stop()

            status.update(label="✅ Everything ready!", state="complete")

        st.success("Your presentation and notes are ready!")

        # Bundle PPT + Notes PDF into a single zip
        zip_buf = BytesIO()
        with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(ppt_filename, ppt_buf.getvalue())
            zf.writestr(notes_filename, notes_buf.getvalue())
        zip_buf.seek(0)

        safe_name = re.sub(r'[^\w\s-]', '', chapter).strip().replace(' ', '_')
        zip_name = f"Class{class_num}_{subject}_{safe_name}.zip"

        st.download_button(
            label="📥 Download Presentation & Notes",
            data=zip_buf,
            file_name=zip_name,
            mime="application/zip",
            use_container_width=True
        )
