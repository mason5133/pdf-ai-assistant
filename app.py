import os
import shutil
import time
import uuid
from flask import Flask, request, jsonify, render_template
import opendataloader_pdf
from google import genai
from google.genai import types

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Directories
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), 'outputs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload

# In-memory store: { session_id: { "markdown", "history", "doc_context", "filename" } }
chat_sessions = {}

MODEL_ID = "gemini-2.5-flash"

# Context management constants
MAX_CONTEXT_CHARS = 200_000       # ~50K tokens for document context
MAX_HISTORY_TURNS = 20            # Keep last N turn-pairs before summarizing
SUMMARY_TRIGGER = 16              # Summarize when history reaches this many turn-pairs

SYSTEM_INSTRUCTION = """You are an intelligent document assistant.
You have been given a document that was converted from PDF to structured Markdown.
Your job is to help the user understand, learn from, and work with this document — regardless of its topic.

Key behaviors:
- When first presented with a document, provide a clear and thorough summary.
- Identify and highlight key information: dates, deadlines, figures, names, definitions, formulas, citations, or any structured data.
- Present structured information (timelines, comparisons, data points) in markdown tables when it helps clarity.
- Use organized headers and formatting for readability.
- Answer follow-up questions using the document as context.
- If the user asks about something not covered in the document, say so honestly.
- Be conversational, helpful, and thorough — the user is here to learn and work with this content.
- Adapt your tone and depth to match the subject matter (technical, casual, academic, legal, etc.).
- If the document was truncated due to size, mention that only a portion was loaded and suggest the user ask about specific sections or topics."""


def get_gemini_client():
    """Initialize and return a Gemini client."""
    return genai.Client()


def build_genai_history(history_list):
    """Convert stored history into google-genai Content objects."""
    contents = []
    for entry in history_list:
        contents.append(
            types.Content(
                role=entry["role"],
                parts=[types.Part.from_text(text=entry["text"])]
            )
        )
    return contents


def truncate_content(text, max_chars=MAX_CONTEXT_CHARS):
    """Truncate document text to stay within token limits.
    Tries to break at a paragraph boundary for cleaner context."""
    if len(text) <= max_chars:
        return text, False
    truncated = text[:max_chars]
    last_break = truncated.rfind('\n\n')
    if last_break > max_chars * 0.8:
        truncated = truncated[:last_break]
    return truncated, True


def summarize_history(client, history, doc_context_preview):
    """Summarize older conversation turns to keep context window manageable.
    Returns a condensed summary string of the conversation so far."""
    # Take all but the last 4 turn-pairs to summarize
    to_summarize = history[:-8] if len(history) > 8 else history
    conversation_text = ""
    for entry in to_summarize:
        role_label = "User" if entry["role"] == "user" else "Assistant"
        # Truncate individual messages in the summary input
        msg_text = entry["text"][:2000] + "..." if len(entry["text"]) > 2000 else entry["text"]
        conversation_text += f"{role_label}: {msg_text}\n\n"

    summary_prompt = f"""Summarize this conversation about a document concisely.
Capture: key topics discussed, questions asked, important findings, and any conclusions.
Keep it under 500 words. This summary will be used as context for continuing the conversation.

Conversation:
{conversation_text}"""

    try:
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=summary_prompt
        )
        return response.text
    except Exception:
        # If summarization fails, just return a truncated version
        return conversation_text[:3000]


def manage_history(client, session_data):
    """Check if history needs summarization and handle it.
    Returns the effective history list to use for the next API call."""
    history = session_data["history"]
    turn_pairs = len(history) // 2

    if turn_pairs >= SUMMARY_TRIGGER:
        # Summarize older turns
        doc_preview = session_data.get("doc_context", "")[:1000]
        summary = summarize_history(client, history, doc_preview)

        # Replace history: summary as first exchange + recent turns
        summary_entry = {
            "role": "user",
            "text": f"[Previous conversation summary]: {summary}"
        }
        ack_entry = {
            "role": "model",
            "text": "I have the context from our previous discussion. Please continue with your questions."
        }

        # Keep the last 8 entries (4 turn-pairs)
        recent = history[-8:]
        session_data["history"] = [summary_entry, ack_entry] + recent

    return session_data["history"]


def call_gemini_with_retry(func, max_retries=2, base_wait=50):
    """Call a Gemini API function with retry on rate limit (429)."""
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            error_str = str(e)
            if '429' in error_str and attempt < max_retries:
                wait_time = base_wait * (attempt + 1)
                print(f"Rate limited. Waiting {wait_time}s before retry {attempt + 1}/{max_retries}...")
                time.sleep(wait_time)
            else:
                raise


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_pdf():
    """Upload and parse a PDF, then get initial AI analysis."""
    try:
        client = get_gemini_client()
    except Exception as e:
        return jsonify({'error': f"Gemini API init failed. Is GEMINI_API_KEY set? {str(e)}"}), 500

    if 'pdf' not in request.files:
        return jsonify({'error': 'No file part in request'}), 400

    file = request.files['pdf']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Only PDF files are accepted'}), 400

    filename = file.filename
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    out_dir = None
    try:
        # 1. Parse PDF with OpenDataLoader (with image embedding)
        print(f"Parsing {filename} with OpenDataLoader PDF...")
        run_id = str(uuid.uuid4())
        out_dir = os.path.join(app.config['OUTPUT_FOLDER'], run_id)
        os.makedirs(out_dir, exist_ok=True)

        opendataloader_pdf.convert(
            input_path=filepath,
            output_dir=out_dir,
            format="markdown",
            image_output="embedded",
            image_format="jpeg"
        )

        # Read the generated Markdown
        markdown_content = ""
        base_name = os.path.splitext(filename)[0]
        md_file_path = os.path.join(out_dir, f"{base_name}.md")

        if os.path.exists(md_file_path):
            with open(md_file_path, 'r', encoding='utf-8') as f:
                markdown_content = f.read()
        else:
            md_files = [f for f in os.listdir(out_dir) if f.endswith('.md')]
            if md_files:
                with open(os.path.join(out_dir, md_files[0]), 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
            else:
                return jsonify({
                    'error': 'Markdown parsing failed. No output generated. Is Java 11+ installed?'
                }), 500

        if not markdown_content.strip():
            return jsonify({'error': 'PDF parsed but no text content was extracted.'}), 500

        # Full markdown for display; stripped version for AI (remove base64 images)
        full_markdown = markdown_content

        # Strip base64 image data from context sent to LLM (saves tokens)
        import re
        ai_text = re.sub(r'!\[([^\]]*)\]\(data:image[^\)]+\)', r'[Image: \1]', markdown_content)
        ai_context, was_truncated = truncate_content(ai_text)

        # 2. Create chat session
        session_id = str(uuid.uuid4())

        truncation_note = ""
        if was_truncated:
            total_chars = len(ai_text)
            loaded_chars = len(ai_context)
            truncation_note = (
                f"\n\nNOTE: This document is very large ({total_chars:,} characters). "
                f"Only the first ~{loaded_chars:,} characters are loaded. "
                f"Let the user know and offer to discuss specific sections or chapters.\n"
            )

        initial_prompt = f"""Here is a document extracted from the PDF "{filename}".{truncation_note}
Please analyze it and provide:
1. A clear summary of what this document covers
2. Key information worth highlighting (dates, figures, names, definitions, formulas, rules — whatever is relevant to this subject)
3. Any structured data presented clearly (tables, timelines, lists)
4. A few suggested questions the user might want to explore

Document Content:
---
{ai_context}
---"""

        chat = client.chats.create(
            model=MODEL_ID,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )

        response = call_gemini_with_retry(
            lambda: chat.send_message(initial_prompt)
        )

        # Store session with document context for future reference
        chat_sessions[session_id] = {
            "markdown": full_markdown,
            "doc_context": ai_context,
            "filename": filename,
            "history": [
                {"role": "user", "text": initial_prompt},
                {"role": "model", "text": response.text}
            ]
        }

        return jsonify({
            'session_id': session_id,
            'markdown': full_markdown,
            'ai_response': response.text,
            'truncated': was_truncated,
            'doc_chars': len(ai_text),
            'loaded_chars': len(ai_context)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)
        if out_dir and os.path.exists(out_dir):
            shutil.rmtree(out_dir)


@app.route('/chat', methods=['POST'])
def chat_message():
    """Send a follow-up message in an existing chat session."""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No JSON body provided'}), 400

    session_id = data.get('session_id')
    message = data.get('message', '').strip()

    if not session_id or session_id not in chat_sessions:
        return jsonify({'error': 'Invalid or expired session. Please upload a document first.'}), 400

    if not message:
        return jsonify({'error': 'Message cannot be empty'}), 400

    try:
        client = get_gemini_client()
    except Exception as e:
        return jsonify({'error': f"Gemini API init failed: {str(e)}"}), 500

    session_data = chat_sessions[session_id]

    try:
        # Manage history length (summarize if needed)
        effective_history = manage_history(client, session_data)
        history_contents = build_genai_history(effective_history)

        chat = client.chats.create(
            model=MODEL_ID,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            ),
            history=history_contents
        )

        response = call_gemini_with_retry(
            lambda: chat.send_message(message)
        )

        # Append to stored history
        session_data["history"].append({"role": "user", "text": message})
        session_data["history"].append({"role": "model", "text": response.text})

        return jsonify({
            'ai_response': response.text,
            'history_turns': len(session_data["history"]) // 2
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/reset', methods=['POST'])
def reset_session():
    """Clear a chat session."""
    data = request.get_json()
    session_id = data.get('session_id') if data else None
    if session_id and session_id in chat_sessions:
        del chat_sessions[session_id]
    return jsonify({'status': 'ok'})


@app.route('/session-info', methods=['POST'])
def session_info():
    """Return metadata about an active session."""
    data = request.get_json()
    session_id = data.get('session_id') if data else None
    if not session_id or session_id not in chat_sessions:
        return jsonify({'error': 'No active session'}), 404

    s = chat_sessions[session_id]
    return jsonify({
        'filename': s.get('filename', 'Unknown'),
        'history_turns': len(s["history"]) // 2,
        'doc_chars': len(s.get('doc_context', '')),
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
