# 📚 AI Presentation & Notes Maker

Generate **professional 30-slide PowerPoint presentations** and **comprehensive PDF study notes** from just a class, subject, and chapter name — powered by Google Gemini AI.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Streamlit](https://img.shields.io/badge/Streamlit-Frontend-red?logo=streamlit)
![Gemini](https://img.shields.io/badge/Google%20Gemini-AI-yellow?logo=google)
![License](https://img.shields.io/badge/License-MIT-green)

---

## ✨ Features

- **AI-Generated Slides** — 30 structured slides with title, sections, content, diagrams, and summary
- **PDF Study Notes** — Detailed notes with headings, bullet points, tables, and formulas
- **Branded Output** — Your logo on every slide, locked so it can't be removed
- **Image Diagrams** — Optional auto-fetched diagrams for visual slides
- **Model Fallback** — Cycles through 5 Gemini models automatically if one hits rate limits
- **One-Click Download** — PPT + Notes bundled into a single `.zip` file
- **Dark Theme UI** — Professional Streamlit interface with custom branding

---

## 🖥️ Screenshots

### Web Interface
> Enter class, subject, and chapter — click Generate — download everything.

### Generated Presentation
> Clean dark-themed slides with gold accents, section dividers, and branded logo.

---

## 🚀 Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/AdarshXKumار/ai-ppt-maker.git
cd ai-ppt-maker
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set your Gemini API key

Create a `.env` file or set the environment variable:

```bash
# Windows
set GEMINI_API_KEY=your_api_key_here

# Linux/Mac
export GEMINI_API_KEY=your_api_key_here
```

Get a free API key from [Google AI Studio](https://aistudio.google.com/apikey).

### 4. Add your logo

Place a `logo.png` file in the project root directory. This will appear on every slide.

### 5. Run the app

```bash
# Streamlit web app
streamlit run app.py

# CLI version
python ppt_maker.py
```

---

## 📁 Project Structure

```
├── app.py              # Streamlit web application
├── ppt_maker.py        # CLI version
├── logo.png            # Your brand logo
├── requirements.txt    # Python dependencies
├── .env                # API key (not tracked by git)
└── .gitignore
```

---

## 🤖 Gemini Model Fallback

If one model hits rate limits, the system automatically tries the next:

| Priority | Model |
|----------|-------|
| 1 | `gemini-2.5-flash-lite` |
| 2 | `gemini-2.0-flash` |
| 3 | `gemini-2.0-flash-lite` |
| 4 | `gemini-1.5-flash` |
| 5 | `gemini-1.5-flash-8b` |

---

## 🛠️ Tech Stack

- **Frontend**: Streamlit
- **AI**: Google Gemini API
- **PPT Generation**: python-pptx
- **PDF Generation**: fpdf2
- **Image Scraping**: Bing Images (for diagram slides)

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

**Built with ❤️ by [SkillRev](https://github.com/AdarshXKumार)**
