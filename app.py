import os
import re
import shutil
import time
import uuid
import datetime

from flask import Flask, request, jsonify, render_template
import opendataloader_pdf
from google import genai
from google.genai import types

app = Flask(__name__)
app.secret_key = os.urandom(24)

# ─── Directories ──────────────────────────────────────────
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
OUTPUT_FOLDER = os.path.join(os.path.dirname(__file__), 'outputs')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload

# ─── In-memory session store ──────────────────────────────
# { session_id: { markdown, doc_context, filename, history, cache_name, model } }
chat_sessions = {}

# ─── Model & context config ──────────────────────────────
MODEL_ID = "gemini-2.5-flash"

# Context limits
MAX_CONTEXT_CHARS = 200_000       # ~50K tokens for document context
MAX_HISTORY_TURNS = 20            # Keep last N turn-pairs before summarizing
SUMMARY_TRIGGER = 16              # Summarize when history reaches this many turn-pairs

# Cache settings
CACHE_TTL_SECONDS = 3600          # 1 hour default TTL for context caches
CACHE_MIN_CHARS = 4000            # Minimum doc size to attempt caching (~1K tokens)

# ─── System instruction ──────────────────────────────────
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


# ─── Context caching helpers ─────────────────────────────

def create_context_cache(client, doc_text, model=MODEL_ID):
    """Create a Gemini context cache for the document text.
    Returns the cache name string, or None if caching fails/not applicable."""
    if len(doc_text) < CACHE_MIN_CHARS:
        print(f"Document too short for caching ({len(doc_text)} chars < {CACHE_MIN_CHARS})")
        return None

    try:
        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(
                            text=f"Here is the document content for reference:\n\n{doc_text}"
                        )]
                    ),
                    types.Content(
                        role="model",
                        parts=[types.Part.from_text(
                            text="I have loaded and analyzed the document. I'm ready to help you understand and work with this content. What would you like to know?"
                        )]
                    )
                ],
                ttl=f"{CACHE_TTL_SECONDS}s",
            )
        )
        print(f"Context cache created: {cache.name} (TTL: {CACHE_TTL_SECONDS}s)")
        return cache.name
    except Exception as e:
        print(f"Context caching failed (will use inline context): {e}")
        return None


def refresh_cache_ttl(client, cache_name):
    """Refresh the TTL on an existing cache. Returns True if successful."""
    try:
        client.caches.update(
            name=cache_name,
            config=types.UpdateCachedContentConfig(
                ttl=f"{CACHE_TTL_SECONDS}s"
            )
        )
        return True
    except Exception as e:
        print(f"Cache TTL refresh failed: {e}")
        return False


def delete_cache(client, cache_name):
    """Delete a context cache. Silently ignores errors."""
    try:
        client.caches.delete(cache_name)
        print(f"Cache deleted: {cache_name}")
    except Exception:
        pass


# ─── History & context helpers ───────────────────────────

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


def summarize_history(client, history, model=MODEL_ID):
    """Summarize older conversation turns to keep context window manageable."""
    to_summarize = history[:-8] if len(history) > 8 else history
    conversation_text = ""
    for entry in to_summarize:
        role_label = "User" if entry["role"] == "user" else "Assistant"
        msg_text = entry["text"][:2000] + "..." if len(entry["text"]) > 2000 else entry["text"]
        conversation_text += f"{role_label}: {msg_text}\n\n"

    summary_prompt = f"""Summarize this conversation about a document concisely.
Capture: key topics discussed, questions asked, important findings, and any conclusions.
Keep it under 500 words. This summary will be used as context for continuing the conversation.

Conversation:
{conversation_text}"""

    try:
        response = client.models.generate_content(
            model=model,
            contents=summary_prompt
        )
        return response.text
    except Exception:
        return conversation_text[:3000]


def manage_history(client, session_data):
    """Check if history needs summarization and handle it."""
    history = session_data["history"]
    turn_pairs = len(history) // 2

    if turn_pairs >= SUMMARY_TRIGGER:
        summary = summarize_history(client, history)
        summary_entry = {
            "role": "user",
            "text": f"[Previous conversation summary]: {summary}"
        }
        ack_entry = {
            "role": "model",
            "text": "I have the context from our previous discussion. Please continue with your questions."
        }
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


def strip_base64_images(markdown_text):
    """Strip base64 image data from markdown, keep descriptive placeholders."""
    return re.sub(r'!\[([^\]]*)\]\(data:image[^\)]+\)', r'[Image: \1]', markdown_text)


# ─── Routes ──────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/upload', methods=['POST'])
def upload_pdf():
    """Upload and parse a PDF, create context cache, get initial AI analysis."""
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
    cache_name = None

    try:
        # ── 1. Parse PDF with OpenDataLoader ─────────────────
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

        # Full markdown for display panel
        full_markdown = markdown_content

        # AI context: strip base64 images (saves tokens), then truncate
        ai_text = strip_base64_images(markdown_content)
        ai_context, was_truncated = truncate_content(ai_text)

        # ── 2. Create context cache ──────────────────────────
        # Cache the OpenDataLoader-extracted text for efficient multi-turn chat
        cache_name = create_context_cache(client, ai_context)
        using_cache = cache_name is not None

        # ── 3. Initial AI analysis ───────────────────────────
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

        initial_question = (
            f'The user just uploaded "{filename}".{truncation_note}\n'
            f'Please analyze the document and provide:\n'
            f'1. A clear summary of what this document covers\n'
            f'2. Key information worth highlighting (dates, figures, names, definitions, formulas, rules — whatever is relevant)\n'
            f'3. Any structured data presented clearly (tables, timelines, lists)\n'
            f'4. A few suggested questions the user might want to explore'
        )

        if using_cache:
            # Use cached context — document is already in the cache
            response = call_gemini_with_retry(lambda: client.models.generate_content(
                model=MODEL_ID,
                contents=initial_question,
                config=types.GenerateContentConfig(
                    cached_content=cache_name,
                    temperature=0.4,
                    max_output_tokens=8192,
                )
            ))
        else:
            # Fallback: send doc inline (no cache available)
            full_prompt = f"""{initial_question}

Document Content:
---
{ai_context}
---"""
            response = call_gemini_with_retry(lambda: client.models.generate_content(
                model=MODEL_ID,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.4,
                    max_output_tokens=8192,
                )
            ))

        # ── 4. Store session ─────────────────────────────────
        chat_sessions[session_id] = {
            "markdown": full_markdown,
            "doc_context": ai_context,
            "filename": filename,
            "cache_name": cache_name,
            "model": MODEL_ID,
            "history": [
                {"role": "user", "text": initial_question},
                {"role": "model", "text": response.text}
            ]
        }

        return jsonify({
            'session_id': session_id,
            'markdown': full_markdown,
            'ai_response': response.text,
            'truncated': was_truncated,
            'doc_chars': len(ai_text),
            'loaded_chars': len(ai_context),
            'cached': using_cache,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        # Clean up cache on error
        if cache_name:
            delete_cache(get_gemini_client(), cache_name)
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
    cache_name = session_data.get("cache_name")

    try:
        # Manage history length (summarize if needed)
        effective_history = manage_history(client, session_data)

        if cache_name:
            # ── Cached path: refresh TTL and use cache ────────
            refresh_cache_ttl(client, cache_name)

            # Build history for the cached context chat
            history_contents = build_genai_history(effective_history)

            # Add the new user message
            history_contents.append(
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=message)]
                )
            )

            response = call_gemini_with_retry(lambda: client.models.generate_content(
                model=MODEL_ID,
                contents=history_contents,
                config=types.GenerateContentConfig(
                    cached_content=cache_name,
                    temperature=0.4,
                    max_output_tokens=8192,
                )
            ))
        else:
            # ── Fallback: inline context (no cache) ──────────
            history_contents = build_genai_history(effective_history)

            chat = client.chats.create(
                model=MODEL_ID,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.4,
                    max_output_tokens=8192,
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
            'history_turns': len(session_data["history"]) // 2,
            'cached': cache_name is not None,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()

        # If cache error, try to invalidate and fall back
        if cache_name and ('cache' in str(e).lower() or 'not found' in str(e).lower()):
            print("Cache appears invalid, clearing for next request")
            session_data["cache_name"] = None

        return jsonify({'error': str(e)}), 500


@app.route('/reset', methods=['POST'])
def reset_session():
    """Clear a chat session and delete its context cache."""
    data = request.get_json()
    session_id = data.get('session_id') if data else None
    if session_id and session_id in chat_sessions:
        session_data = chat_sessions.pop(session_id)
        # Clean up the context cache
        cache_name = session_data.get("cache_name")
        if cache_name:
            try:
                client = get_gemini_client()
                delete_cache(client, cache_name)
            except Exception:
                pass
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
        'cached': s.get('cache_name') is not None,
    })


if __name__ == '__main__':
    app.run(debug=True, port=5000)
