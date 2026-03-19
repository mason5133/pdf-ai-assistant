import os
import shutil
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

# In-memory store: { session_id: { "markdown": str, "history": [{role, text}] } }
chat_sessions = {}

MODEL_ID = "gemini-2.5-flash"

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
- Adapt your tone and depth to match the subject matter (technical, casual, academic, legal, etc.)."""


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
        # 1. Parse PDF with OpenDataLoader
        print(f"Parsing {filename} with OpenDataLoader PDF...")
        run_id = str(uuid.uuid4())
        out_dir = os.path.join(app.config['OUTPUT_FOLDER'], run_id)
        os.makedirs(out_dir, exist_ok=True)

        opendataloader_pdf.run(
            input_path=filepath,
            output_folder=out_dir,
            generate_markdown=True
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

        # 2. Create a new chat session with Gemini
        session_id = str(uuid.uuid4())

        initial_prompt = f"""Here is a document that was extracted from a PDF file named "{filename}".
Please analyze it and provide:
1. A clear summary of what this document is about
2. Key information worth highlighting (dates, figures, names, definitions, rules, formulas — whatever is relevant)
3. Any structured data presented clearly (tables, timelines, lists)
4. Suggested questions the user might want to explore

Document Content:
---
{markdown_content}
---"""

        chat = client.chats.create(
            model=MODEL_ID,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            )
        )
        response = chat.send_message(initial_prompt)

        # Store session
        chat_sessions[session_id] = {
            "markdown": markdown_content,
            "history": [
                {"role": "user", "text": initial_prompt},
                {"role": "model", "text": response.text}
            ]
        }

        return jsonify({
            'session_id': session_id,
            'markdown': markdown_content,
            'ai_response': response.text
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
        # Rebuild chat with existing history
        history_contents = build_genai_history(session_data["history"])

        chat = client.chats.create(
            model=MODEL_ID,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION
            ),
            history=history_contents
        )

        response = chat.send_message(message)

        # Append to stored history
        session_data["history"].append({"role": "user", "text": message})
        session_data["history"].append({"role": "model", "text": response.text})

        return jsonify({'ai_response': response.text})

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


if __name__ == '__main__':
    app.run(debug=True, port=5000)
