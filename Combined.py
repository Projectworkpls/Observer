import streamlit as st
import requests
import base64
import json
from supabase import create_client
from datetime import datetime
import re
import tempfile
import os
import time
import google.generativeai as genai
import docx
from docx.shared import Pt
import io

# Set page config (must be first Streamlit command)
st.set_page_config(
    page_title="Learning Observer",
    layout="wide",
    page_icon="📝"
)

# ==============================================
# SECRETS MANAGEMENT - ORDER INDEPENDENT
# ==============================================

def load_and_validate_secrets():
    """Load and validate secrets from environment or Streamlit secrets"""
    required_secrets = {
        "SUPABASE_URL": "",
        "SUPABASE_KEY": "",
        "GOOGLE_API_KEY": "",
        "ASSEMBLYAI_API_KEY": "",
        "OCR_API_KEY": "",
        "GROQ_API_KEY": ""
    }
    
    try:
        # Try environment variables first (for deployment)
        secret_content = os.getenv('SECRET_TRY', '')
        
        # Fall back to Streamlit secrets (for local development)
        if not secret_content:
            try:
                secret_content = st.secrets.get("SECRET_TRY", "")
            except Exception:
                pass
        
        # Parse key=value pairs regardless of order
        for line in secret_content.split('\n'):
            line = line.strip()
            if line and '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                if key in required_secrets:
                    required_secrets[key] = value.strip()
        
        # Validate required secrets
        missing = [k for k, v in required_secrets.items() if not v and k in ["SUPABASE_URL", "SUPABASE_KEY"]]
        if missing:
            st.error(f"Missing required secrets: {', '.join(missing)}")
            st.stop()
            
        return required_secrets
        
    except Exception as e:
        st.error(f"Error loading secrets: {str(e)}")
        st.stop()

# Load all secrets
secrets = load_and_validate_secrets()

# Assign to individual variables
SUPABASE_URL = secrets["SUPABASE_URL"]
SUPABASE_KEY = secrets["SUPABASE_KEY"]
GOOGLE_API_KEY = secrets["GOOGLE_API_KEY"]
ASSEMBLYAI_API_KEY = secrets["ASSEMBLYAI_API_KEY"]
OCR_API_KEY = secrets["OCR_API_KEY"]
GROQ_API_KEY = secrets["GROQ_API_KEY"]

# ==============================================
# SUPABASE INITIALIZATION WITH ERROR HANDLING
# ==============================================

@st.cache_resource
def init_supabase():
    """Initialize Supabase client with connection test"""
    try:
        if not SUPABASE_URL:
            raise ValueError("Supabase URL is required")
        if not SUPABASE_KEY:
            raise ValueError("Supabase Key is required")
        
        client = create_client(SUPABASE_URL, SUPABASE_KEY)
        
        # Test connection with simple query
        try:
            test = client.from_("observations").select("*").limit(1).execute()
            if hasattr(test, 'error') and test.error:
                raise Exception(f"Supabase test query failed: {test.error.message}")
        except Exception as test_error:
            raise Exception(f"Supabase connection test failed: {str(test_error)}")
        
        return client
        
    except Exception as e:
        st.error(f"Failed to initialize Supabase: {str(e)}")
        st.stop()

# Initialize Supabase
supabase = init_supabase()

# Configure Google AI API
try:
    genai.configure(api_key=GOOGLE_API_KEY)
except Exception as e:
    st.error(f"Failed to configure Google AI: {str(e)}")
    st.stop()

# ==============================================
# OBSERVATION EXTRACTOR CLASS
# ==============================================

class ObservationExtractor:
    def __init__(self):
        self.ocr_api_key = OCR_API_KEY
        self.groq_api_key = GROQ_API_KEY
        self.gemini_api_key = GOOGLE_API_KEY

    def image_to_base64(self, image_file):
        """Convert image file to base64 string"""
        return base64.b64encode(image_file.read()).decode('utf-8')

    def extract_text_with_ocr(self, image_file):
        """Extract text from image using OCR.space API"""
        try:
            file_type = image_file.name.split('.')[-1].lower()
            if file_type == 'jpeg':
                file_type = 'jpg'

            base64_image = self.image_to_base64(image_file)
            base64_image_with_prefix = f"data:image/{file_type};base64,{base64_image}"

            payload = {
                'apikey': self.ocr_api_key,
                'language': 'eng',
                'isOverlayRequired': False,
                'iscreatesearchablepdf': False,
                'issearchablepdfhidetextlayer': False,
                'OCREngine': 2,
                'detectOrientation': True,
                'scale': True,
                'base64Image': base64_image_with_prefix
            }

            response = requests.post(
                'https://api.ocr.space/parse/image',
                data=payload,
                headers={'apikey': self.ocr_api_key}
            )

            response.raise_for_status()
            data = response.json()

            if not data.get('ParsedResults') or len(data['ParsedResults']) == 0:
                error_msg = data.get('ErrorMessage', 'No parsed results returned')
                raise Exception(f"OCR Error: {error_msg}")

            parsed_result = data['ParsedResults'][0]
            if parsed_result.get('ErrorMessage'):
                raise Exception(f"OCR Error: {parsed_result['ErrorMessage']}")

            extracted_text = parsed_result['ParsedText']

            if not extracted_text or not extracted_text.strip():
                raise Exception("No text was detected in the image")

            return extracted_text

        except Exception as e:
            st.error(f"OCR Error: {str(e)}")
            raise

    def process_with_groq(self, extracted_text):
        """Process extracted text with Groq AI"""
        try:
            system_prompt = """You are an AI assistant for a learning observation system..."""  # Your full prompt here

            response = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {self.groq_api_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt
                        },
                        {
                            "role": "user",
                            "content": f"Extract and structure the following text from an observation sheet: {extracted_text}"
                        }
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"}
                }
            )

            response.raise_for_status()
            data = response.json()
            ai_response = data['choices'][0]['message']['content']
            return json.loads(ai_response)

        except Exception as e:
            st.error(f"Groq API Error: {str(e)}")
            raise

    def transcribe_with_assemblyai(self, audio_file):
        """Transcribe audio using AssemblyAI API"""
        if not ASSEMBLYAI_API_KEY:
            return "Error: AssemblyAI API key is not configured."

        headers = {
            "authorization": ASSEMBLYAI_API_KEY,
            "content-type": "application/json"
        }

        try:
            st.write("Uploading audio file...")
            upload_response = requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers={"authorization": ASSEMBLYAI_API_KEY},
                data=audio_file.getvalue()
            )

            if upload_response.status_code != 200:
                return f"Error uploading audio: {upload_response.text}"

            upload_url = upload_response.json()["upload_url"]

            st.write("Processing transcription...")
            transcript_response = requests.post(
                "https://api.assemblyai.com/v2/transcript",
                json={"audio_url": upload_url, "language_code": "en"},
                headers=headers
            )

            if transcript_response.status_code != 200:
                return f"Error requesting transcription: {transcript_response.text}"

            transcript_id = transcript_response.json()["id"]
            status = "processing"
            progress_bar = st.progress(0)
            
            while status != "completed" and status != "error":
                polling_response = requests.get(
                    f"https://api.assemblyai.com/v2/transcript/{transcript_id}",
                    headers=headers
                )
                
                if polling_response.status_code != 200:
                    return f"Error checking transcription status: {polling_response.text}"
                
                polling_data = polling_response.json()
                status = polling_data["status"]
                
                if status == "completed":
                    progress_bar.progress(100)
                    return polling_data["text"]
                elif status == "error":
                    return f"Transcription error: {polling_data.get('error', 'Unknown error')}"
                
                progress = polling_data.get("percent_done", 0)
                if progress:
                    progress_bar.progress(progress / 100.0)
                time.sleep(2)
            
            return "Error: Transcription timed out or failed."
        except Exception as e:
            return f"Error during transcription: {str(e)}"

    def generate_report_from_text(self, text_content, user_info):
        """Generate a structured report from text using Google Gemini"""
        prompt = f"""Based on this text from a student observation..."""  # Your full prompt here

        try:
            model = genai.GenerativeModel('gemini-1.5-pro-002')
            response = model.generate_content([{"role": "user", "parts": [{"text": prompt}]}])
            
            complete_report = f"""Date: {user_info['session_date']}
Student Name: {user_info['student_name']}
Observer Name: {user_info['observer_name']}
Session Duration: {user_info['session_start']} - {user_info['session_end']}

{response.text}

Name of Observer: {user_info['observer_name']}
"""
            return complete_report
        except Exception as e:
            return f"Error generating report: {str(e)}"

    def create_word_document(self, report_content):
        """Create a Word document from the report content"""
        doc = docx.Document()
        doc.add_heading('The Observer Report', 0)
        report_content = report_content.replace('**', '')
        
        for line in report_content.split('\n'):
            line = line.strip()
            if not line:
                continue
            
            if line.startswith(('Date:', 'Student Name:', 'Observer Name:', 'Session Duration:', 'Name of Observer:')):
                p = doc.add_paragraph()
                p.add_run(line).bold = True
            elif re.match(r'^\d+\.\s+(.+)', line):
                doc.add_heading(line, level=1)
            elif re.match(r'^\*\s*(.+):\*\s*(.+)', line):
                match = re.match(r'^\*\s*(.+):\*\s*(.+)', line)
                p = doc.add_paragraph()
                p.add_run(f"{match.group(1)}: ").bold = True
                p.add_run(match.group(2))
            elif re.match(r'^\*\s+(.+)', line):
                doc.add_paragraph(re.match(r'^\*\s+(.+)', line).group(1), style='List Bullet')
            else:
                doc.add_paragraph(line)
        
        docx_bytes = io.BytesIO()
        doc.save(docx_bytes)
        docx_bytes.seek(0)
        return docx_bytes

# ==============================================
# MAIN APPLICATION
# ==============================================

def main():
    extractor = ObservationExtractor()

    if 'authenticated' not in st.session_state:
        st.session_state.authenticated = False
    if 'username' not in st.session_state:
        st.session_state.username = ""
    if 'user_info' not in st.session_state:
        st.session_state.user_info = {
            'student_name': '',
            'observer_name': '',
            'session_date': datetime.now().strftime('%d/%m/%Y'),
            'session_start': '',
            'session_end': ''
        }
    if 'audio_transcription' not in st.session_state:
        st.session_state.audio_transcription = ""
    if 'report_generated' not in st.session_state:
        st.session_state.report_generated = None
    if 'show_edit_transcript' not in st.session_state:
        st.session_state.show_edit_transcript = False
    if 'processing_mode' not in st.session_state:
        st.session_state.processing_mode = None

    if not st.session_state.authenticated:
        st.title("Learning Observer Login")
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Login"):
                st.session_state.authenticated = True
                st.session_state.username = username
                st.rerun()
        return

    st.title(f"Welcome, {st.session_state.username}")
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.session_state.username = ""
        st.rerun()

    with st.sidebar:
        st.subheader("Session Information")
        st.session_state.user_info['student_name'] = st.text_input("Student Name:", value=st.session_state.user_info['student_name'])
        st.session_state.user_info['observer_name'] = st.text_input("Observer Name:", value=st.session_state.user_info['observer_name'])
        st.session_state.user_info['session_date'] = st.date_input("Session Date:").strftime('%d/%m/%Y')
        col1, col2 = st.columns(2)
        with col1:
            st.session_state.user_info['session_start'] = st.time_input("Start Time:").strftime('%H:%M')
        with col2:
            st.session_state.user_info['session_end'] = st.time_input("End Time:").strftime('%H:%M')

    st.subheader("Select Processing Mode")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("OCR Mode (Image Upload)"):
            st.session_state.processing_mode = "ocr"
            st.session_state.audio_transcription = ""
            st.session_state.report_generated = None
    with col2:
        if st.button("Audio Mode (Recording Upload)"):
            st.session_state.processing_mode = "audio"
            st.session_state.audio_transcription = ""
            st.session_state.report_generated = None

    if st.session_state.processing_mode == "ocr":
        st.info("OCR Mode: Upload an image of an observation sheet")
        uploaded_file = st.file_uploader("Upload Observation Sheet", type=["jpg", "jpeg", "png"])
        
        if uploaded_file and st.button("Process Observation"):
            with st.spinner("Processing..."):
                try:
                    extracted_text = extractor.extract_text_with_ocr(uploaded_file)
                    structured_data = extractor.process_with_groq(extracted_text)
                    observations_text = structured_data.get("observations", "")
                    
                    if observations_text:
                        report = extractor.generate_report_from_text(observations_text, st.session_state.user_info)
                        st.session_state.report_generated = report
                        
                        supabase.table('observations').insert({
                            "username": st.session_state.username,
                            "student_name": structured_data.get("studentName", ""),
                            "student_id": structured_data.get("studentId", ""),
                            "class_name": structured_data.get("className", ""),
                            "date": structured_data.get("date", ""),
                            "observations": observations_text,
                            "strengths": json.dumps(structured_data.get("strengths", [])),
                            "areas_of_development": json.dumps(structured_data.get("areasOfDevelopment", [])),
                            "recommendations": json.dumps(structured_data.get("recommendations", [])),
                            "timestamp": datetime.now().isoformat(),
                            "filename": uploaded_file.name,
                            "full_data": json.dumps(structured_data)
                        }).execute()
                        
                        st.success("Data processed and saved successfully!")
                    else:
                        st.error("No observations found in the extracted data")
                except Exception as e:
                    st.error(f"Processing error: {str(e)}")

    elif st.session_state.processing_mode == "audio":
        st.info("Audio Mode: Upload an audio recording of an observation session")
        uploaded_file = st.file_uploader("Choose an audio file", 
                                       type=["wav", "mp3", "m4a", "mpeg", "mpg", "ogg", "flac", "aac", "wma", "aiff"])
        
        if uploaded_file and st.button("Process & Generate Report"):
            if not ASSEMBLYAI_API_KEY:
                st.error("AssemblyAI API key is missing.")
            else:
                with st.spinner("Step 1/2: Transcribing audio..."):
                    transcript = extractor.transcribe_with_assemblyai(uploaded_file)
                    st.session_state.audio_transcription = transcript
                
                with st.spinner("Step 2/2: Generating report with Gemini AI..."):
                    report = extractor.generate_report_from_text(transcript, st.session_state.user_info)
                    st.session_state.report_generated = report
                    
                    supabase.table('observations').insert({
                        "username": st.session_state.username,
                        "student_name": st.session_state.user_info['student_name'],
                        "student_id": "",
                        "class_name": "",
                        "date": st.session_state.user_info['session_date'],
                        "observations": transcript,
                        "strengths": json.dumps([]),
                        "areas_of_development": json.dumps([]),
                        "recommendations": json.dumps([]),
                        "timestamp": datetime.now().isoformat(),
                        "filename": uploaded_file.name,
                        "full_data": json.dumps({"transcript": transcript, "report": report})
                    }).execute()

    if st.session_state.audio_transcription:
        if st.button("Edit Transcription" if not st.session_state.show_edit_transcript else "Hide Editor"):
            st.session_state.show_edit_transcript = not st.session_state.show_edit_transcript
        
        if st.session_state.show_edit_transcript:
            st.subheader("Edit Transcription")
            edited_transcript = st.text_area("Transcript:", value=st.session_state.audio_transcription, height=200)
            
            if edited_transcript != st.session_state.audio_transcription:
                st.session_state.audio_transcription = edited_transcript
            
            if st.button("Regenerate Report with Edited Transcript"):
                with st.spinner("Generating report..."):
                    report = extractor.generate_report_from_text(st.session_state.audio_transcription, st.session_state.user_info)
                    st.session_state.report_generated = report

    if st.session_state.report_generated:
        st.subheader("Generated Report")
        st.markdown(st.session_state.report_generated)
        
        docx_file = extractor.create_word_document(st.session_state.report_generated)
        student_name = st.session_state.user_info['student_name'].replace(" ", "_")
        date = st.session_state.user_info['session_date'].replace("/", "-")
        filename = f"Observer_Report_{student_name}_{date}.docx"
        
        st.download_button(
            label="Download as Word Document",
            data=docx_file,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    with st.expander("Use sample data for testing"):
        if st.button("Load sample transcript"):
            sample_transcript = """So today I'd like to talk about my day as a student..."""  # Your sample transcript
            st.session_state.audio_transcription = sample_transcript
            st.experimental_rerun()

    st.markdown("---")
    st.markdown("The Observer Report Generator")

if __name__ == "__main__":
    main()
