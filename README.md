# PDF AI Assistant

Upload any PDF document, get structured Markdown extraction via [OpenDataLoader PDF](https://github.com/opendataloader-project/opendataloader-pdf), and chat interactively with Google Gemini to explore the content.

## Features

- **PDF to Markdown** — Extracts text, tables, headings, and structure from PDFs using OpenDataLoader (runs locally, no API calls)
- **AI Chat** — Multi-turn conversation powered by Google Gemini (`google-genai` SDK) with full document context
- **Any Topic** — Works with legal documents, research papers, textbooks, manuals, reports, or anything else
- **Split View** — Side-by-side extracted content and AI chat interface
- **Session Memory** — Chat maintains full conversation history within a session

## Requirements

- Python 3.10+
- Java 11+ (required by OpenDataLoader PDF)
- A [Google Gemini API key](https://aistudio.google.com/apikey)

## Setup

1. **Set your Gemini API key:**
   ```powershell
   $env:GEMINI_API_KEY="your_key_here"
   ```

2. **Run the app:**
   ```powershell
   .\run.ps1
   ```

   This creates a virtual environment, installs dependencies, and starts the Flask server.

3. **Open** `http://localhost:5000` in your browser.

## Tech Stack

- **Backend:** Flask (Python)
- **PDF Parsing:** [opendataloader-pdf](https://pypi.org/project/opendataloader-pdf/) (local, deterministic, no GPU)
- **AI:** [google-genai](https://pypi.org/project/google-genai/) 1.68.0 (Gemini 2.5 Flash)
- **Frontend:** Vanilla HTML/CSS/JS with [Marked.js](https://marked.js.org/) for Markdown rendering
