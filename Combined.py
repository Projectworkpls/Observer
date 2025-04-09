import streamlit as st
import requests
import base64
import json
from supabase import create_client
from datetime import datetime
import re
import time
import google.generativeai as genai
import docx
from docx.shared import Pt
import io
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Set page config
st.set_page_config(
    page_title="Learning Observer",
    layout="wide",
    page_icon="📝"
)

# Load secrets
SUPABASE_URL = st.secrets.get("SUPABASE_URL", "")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", "")
GOOGLE_API_KEY = st.secrets.get("GOOGLE_API_KEY", "")
ASSEMBLYAI_API_KEY = st.secrets.get("ASSEMBLYAI_API_KEY", "")
OCR_API_KEY = st.secrets.get("OCR_API_KEY", "")
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "")
EMAIL_PASSWORD = st.secrets.get("EMAIL_PASSWORD", "")
ADMIN_USERNAME = st.secrets.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = st.secrets.get("ADMIN_PASS", "secureadmin123")

# Initialize Supabase
@st.cache_resource
def init_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()
genai.configure(api_key=GOOGLE_API_KEY)

class ObservationExtractor:
    def __init__(self):
        self.ocr_api_key = OCR_API_KEY
        self.groq_api_key = GROQ_API_KEY
        self.gemini_api_key = GOOGLE_API_KEY
        self.email_password = EMAIL_PASSWORD

    def image_to_base64(self, image_file):
        return base64.b64encode(image_file.read()).decode('utf-8')

    def extract_text_with_ocr(self, image_file):
        try:
            file_type = image_file.name.split('.')[-1].lower()
            if file_type == 'jpeg':
                file_type = 'jpg'

            base64_image = self.image_to_base64(image_file)
            base64_image_with_prefix = f"data:image/{file_type};base64,{base64_image}"

            payload = {
                'apikey': self.ocr_api_key,
                'language': 'eng',
                'OCREngine': 2,
                'base64Image': base64_image_with_prefix
            }

            response = requests.post(
                'https://api.ocr.space/parse/image',
                data=payload,
                headers={'apikey': self.ocr_api_key}
            )

            response.raise_for_status()
            data = response.json()

            if not data.get('ParsedResults'):
                raise Exception(data.get('ErrorMessage', 'OCR Error'))

            parsed_result = data['ParsedResults'][0]
            extracted_text = parsed_result['ParsedText']

            if not extracted_text.strip():
                raise Exception("No text detected")

            return extracted_text

        except Exception as e:
            st.error(f"OCR Error: {str(e)}")
            raise

    def process_with_groq(self, extracted_text):
        try:
            system_prompt = """You are an AI assistant for a learning observation system..."""  # Your original prompt

            response = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={'Authorization': f'Bearer {self.groq_api_key}', 'Content-Type': 'application/json'},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Extract and structure: {extracted_text}"}
                    ],
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"}
                }
            )

            response.raise_for_status()
            return json.loads(response.json()['choices'][0]['message']['content'])

        except Exception as e:
            st.error(f"Groq API Error: {str(e)}")
            raise

    def transcribe_with_assemblyai(self, audio_file):
        if not ASSEMBLYAI_API_KEY:
            return "Error: Missing AssemblyAI key"

        headers = {"authorization": ASSEMBLYAI_API_KEY, "content-type": "application/json"}
        
        try:
            upload_response = requests.post(
                "https://api.assemblyai.com/v2/upload",
                headers=headers,
                data=audio_file.getvalue()
            )
            upload_response.raise_for_status()
            upload_url = upload_response.json()["upload_url"]

            transcript_response = requests.post(
                "https://api.assemblyai.com/v2/transcript",
                json={"audio_url": upload_url, "language_code": "en"},
                headers=headers
            )
            transcript_response.raise_for_status()
            transcript_id = transcript_response.json()["id"]

            status = "processing"
            progress_bar = st.progress(0)
            while status not in ["completed", "error"]:
                polling_response = requests.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers)
                polling_data = polling_response.json()
                status = polling_data["status"]
                
                if status == "completed":
                    progress_bar.progress(100)
                    return polling_data["text"]
                elif status == "error":
                    return f"Error: {polling_data.get('error', 'Unknown error')}"
                
                progress = polling_data.get("percent_done", 0)
                progress_bar.progress(progress / 100)
                time.sleep(2)

            return "Error: Transcription timed out"

        except Exception as e:
            return f"Error: {str(e)}"

    def generate_report_from_text(self, text_content, user_info):
        try:
            model = genai.GenerativeModel('gemini-1.5-pro-002')
            response = model.generate_content([{"role": "user", "parts": [{"text": f"""..."""}]])  # Your original prompt
            return f"""Date: {user_info['session_date']}
Student Name: {user_info['student_name']}
Observer Name: {user_info['observer_name']}
Session Duration: {user_info['session_start']} - {user_info['session_end']}

{response.text}

Name of Observer: {user_info['observer_name']}"""
        except Exception as e:
            return f"Error: {str(e)}"

    def create_word_document(self, report_content):
        doc = docx.Document()
        report_content = report_content.replace('**', '')
        
        for line in report_content.split('\n'):
            line = line.strip()
            if not line: continue
            
            if line.startswith(('Date:', 'Student Name:', 'Observer Name:')):
                p = doc.add_paragraph()
                p.add_run(line).bold = True
            elif re.match(r'^\d+\.\s+', line):
                doc.add_heading(line, level=1)
            elif re.match(r'^\*\s*.+:\*\s*', line):
                parts = line.split(':', 1)
                p = doc.add_paragraph()
                p.add_run(parts[0].strip(' *') + ": ").bold = True
                p.add_run(parts[1].strip())
            elif line.startswith('* '):
                doc.add_paragraph(line.strip('* '), style='List Bullet')
            else:
                doc.add_paragraph(line)

        docx_bytes = io.BytesIO()
        doc.save(docx_bytes)
        docx_bytes.seek(0)
        return docx_bytes

    def send_email(self, recipient_email, subject, message):
        try:
            msg = MIMEMultipart()
            msg["From"] = "parth.workforai@gmail.com"
            msg["To"] = recipient_email
            msg["Subject"] = subject
            msg.attach(MIMEText(message, "html"))

            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(msg["From"], self.email_password)
                server.send_message(msg)
            return True, "Email sent successfully"
        except Exception as e:
            return False, f"Error: {str(e)}"

def admin_dashboard():
    st.title("Admin Dashboard")
    
    tab1, tab2 = st.tabs(["Mappings Management", "Activity Logs"])
    
    with tab1:
        st.subheader("Observer-Child Mappings")
        try:
            mappings = supabase.table('observer_child_mappings').select("*").execute().data
            if mappings:
                st.dataframe(mappings)
                
                with st.expander("Add New Mapping"):
                    with st.form("add_mapping"):
                        obs_id = st.text_input("Observer ID")
                        child_id = st.text_input("Child ID")
                        if st.form_submit_button("Add"):
                            supabase.table('observer_child_mappings').insert({
                                "observer_id": obs_id, 
                                "child_id": child_id
                            }).execute()
                            st.rerun()
                
                with st.expander("Remove Mapping"):
                    with st.form("remove_mapping"):
                        mapping_id = st.number_input("Mapping ID to remove", min_value=1)
                        if st.form_submit_button("Remove"):
                            supabase.table('observer_child_mappings').delete().eq("id", mapping_id).execute()
                            st.rerun()
            else:
                st.info("No mappings found")

        except Exception as e:
            st.error(f"Database error: {str(e)}")

    with tab2:
        st.subheader("Activity Logs")
        try:
            logs = supabase.table('observer_activity_logs').select("*").execute().data
            st.dataframe(logs if logs else [])
        except Exception as e:
            st.error(f"Database error: {str(e)}")

def parent_dashboard():
    st.title("Parent Portal")
    
    child_id = st.text_input("Enter your Child ID")
    if not child_id:
        return
    
    try:
        mapping = supabase.table('observer_child_mappings').select("*").eq("child_id", child_id).execute().data
        if not mapping:
            st.warning("No observer assigned to this child")
            return
        
        observer_id = mapping[0]['observer_id']
        logs = supabase.table('observer_activity_logs').select("*").eq("child_id", child_id).execute().data
        reports = supabase.table('observations').select("*").eq("child_id", child_id).execute().data
        
        col1, col2, col3 = st.columns(3)
        col1.metric("Assigned Observer ID", observer_id)
        col2.metric("Total Logins", len([log for log in logs if log['action'] == 'login']))
        col3.metric("Total Reports", len(reports))
        
        st.subheader("Recent Activity")
        st.dataframe(logs[-5:] if logs else [])
        
        st.subheader("Latest Reports")
        for report in reports[-3:]:
            with st.expander(f"Report {report['id']} - {report['date']}"):
                st.write(report['observations'])

    except Exception as e:
        st.error(f"Database error: {str(e)}")

def observer_dashboard(username):
    st.title(f"Observer Dashboard - ID: {username}")
    
    with st.sidebar:
        st.subheader("Session Information")
        user_info = {
            'student_name': st.text_input("Student Name"),
            'observer_name': st.text_input("Observer Name"),
            'session_date': st.date_input("Session Date").strftime('%d/%m/%Y'),
            'session_start': st.time_input("Start Time").strftime('%H:%M'),
            'session_end': st.time_input("End Time").strftime('%H:%M')
        }
    
    extractor = ObservationExtractor()
    processing_mode = st.radio("Processing Mode", ["OCR", "Audio"], horizontal=True)
    
    if processing_mode == "OCR":
        uploaded_file = st.file_uploader("Upload Observation Sheet", type=["jpg", "jpeg", "png"])
        if uploaded_file and st.button("Process"):
            with st.spinner("Processing..."):
                try:
                    text = extractor.extract_text_with_ocr(uploaded_file)
                    data = extractor.process_with_groq(text)
                    report = extractor.generate_report_from_text(data['observations'], user_info)
                    
                    supabase.table('observations').insert({
                        "username": username,
                        "student_name": data['studentName'],
                        "student_id": data['studentId'],
                        "child_id": data['studentId'],
                        "observer_id": username,
                        "observations": data['observations'],
                        "timestamp": datetime.now().isoformat(),
                        "full_data": json.dumps(data)
                    }).execute()
                    
                    st.session_state.report = report
                except Exception as e:
                    st.error(str(e))
    
    elif processing_mode == "Audio":
        uploaded_file = st.file_uploader("Upload Audio File", type=["wav", "mp3", "m4a"])
        if uploaded_file and st.button("Process"):
            with st.spinner("Transcribing..."):
                transcript = extractor.transcribe_with_assemblyai(uploaded_file)
                report = extractor.generate_report_from_text(transcript, user_info)
                
                supabase.table('observations').insert({
                    "username": username,
                    "student_name": user_info['student_name'],
                    "child_id": "AUDIO_SESSION",
                    "observer_id": username,
                    "observations": transcript,
                    "timestamp": datetime.now().isoformat(),
                    "full_data": json.dumps({"transcript": transcript})
                }).execute()
                
                st.session_state.report = report
    
    if 'report' in st.session_state:
        st.subheader("Generated Report")
        st.markdown(st.session_state.report)
        
        docx_file = extractor.create_word_document(st.session_state.report)
        st.download_button(
            "Download Report",
            data=docx_file,
            file_name="Observer_Report.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        
        with st.form("email_form"):
            email = st.text_input("Recipient Email")
            if st.form_submit_button("Send Email"):
                success, message = extractor.send_email(email, "Observer Report", st.session_state.report)
                if success:
                    st.success(message)
                else:
                    st.error(message)

def main():
    if 'auth' not in st.session_state:
        st.session_state.auth = {
            'logged_in': False,
            'role': None,
            'user_id': None
        }
    if 'report' not in st.session_state:
        st.session_state.report = None

    # Login Page
    if not st.session_state.auth['logged_in']:
        st.title("Learning Observer Login")
        
        with st.form("login_form"):
            role = st.selectbox("Role", ["Observer", "Parent"])
            username = st.text_input("Username/ID")
            password = st.text_input("Password", type="password")
            
            if st.form_submit_button("Login"):
                # Hidden admin login check
                if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                    st.session_state.auth = {
                        'logged_in': True,
                        'role': 'Admin',
                        'user_id': 'admin'
                    }
                else:
                    st.session_state.auth = {
                        'logged_in': True,
                        'role': role,
                        'user_id': username
                    }
                st.rerun()
        return

    # Admin Dashboard
    if st.session_state.auth['role'] == 'Admin':
        admin_dashboard()
        if st.button("Logout"):
            st.session_state.auth = {'logged_in': False, 'role': None, 'user_id': None}
            st.rerun()
        return

    # Parent Dashboard
    if st.session_state.auth['role'] == 'Parent':
        parent_dashboard()
        if st.button("Logout"):
            st.session_state.auth = {'logged_in': False, 'role': None, 'user_id': None}
            st.rerun()
        return

    # Observer Dashboard
    observer_dashboard(st.session_state.auth['user_id'])
    if st.button("Logout"):
        supabase.table('observer_activity_logs').insert({
            "observer_id": st.session_state.auth['user_id'],
            "action": "logout",
            "timestamp": datetime.now().isoformat()
        }).execute()
        st.session_state.auth = {'logged_in': False, 'role': None, 'user_id': None}
        st.rerun()

if __name__ == "__main__":
    main()
