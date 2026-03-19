# PDF AI Assistant

Upload any PDF document, get structured Markdown extraction via [OpenDataLoader PDF](https://github.com/opendataloader-project/opendataloader-pdf), and chat interactively with Google Gemini to explore the content.

## Features

- **PDF to Markdown** — Extracts text, tables, headings, structure, and embedded images from PDFs using OpenDataLoader (runs locally, no API calls)
- **AI Chat** — Multi-turn conversation powered by Google Gemini (`google-genai` SDK) with full document context
- **Any Topic** — Works with legal documents, textbooks, research papers, manuals, equipment specs, or anything else
- **Responsive Split View** — Side-by-side extracted content and AI chat, fully resizable
- **Collapsible Panels** — Hide/show the extracted content pane to go full-screen chat (Ctrl+B toggle)
- **Draggable Splitter** — Resize panels by dragging the divider
- **Conversation Memory** — Auto-summarizes older turns to keep long conversations within token limits
- **Image Embedding** — Extracts and embeds images from PDFs as Base64 JPEG in the Markdown viewer
- **Rate Limit Handling** — Automatic retry with backoff on 429 errors
- **Mobile Responsive** — Stacked layout on small screens, touch-friendly
- **Copy Messages** — One-click copy on AI responses
- **Keyboard Shortcuts** — Ctrl+B toggle panels, Enter to send, Escape to focus chat

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

## Notes on Large Documents

- Documents exceeding ~200K characters are automatically truncated for AI context (the full text still shows in the viewer)
- The free Gemini API tier has a 250K token/minute limit — large textbooks may need a paid tier
- Long conversations auto-summarize older turns to keep context fresh
