import os
import asyncio
import traceback
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_from_directory
from telethon import TelegramClient, errors
import threading
import time
from datetime import datetime, timedelta
import hashlib
import json
from werkzeug.utils import secure_filename

# Configuration
API_ID = 24965492
API_HASH = "84e38b9c84687d7a795b65e4f2c9ad19"
TARGET_BOT = "android_protect_bot"
UPLOAD_FOLDER = 'uploads/'
DOWNLOAD_FOLDER = 'static/downloads/'
SESSION_FILE = "userbot_session.session"
SIGNING_TIME = 300  # 5 minutes for signing (300 seconds)
PROCESSING_TIMEOUT = 480  # 8 minutes total timeout (including signing + download)
LICENSE_KEYS_FILE = 'templates/license_keys.json'
ADMIN_PASSWORD = hashlib.sha256("admin123".encode()).hexdigest()
CHECK_INTERVAL = 10  # Check every 10 seconds
MAX_APK_PER_LICENSE = 5

# Create necessary directories
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs('templates', exist_ok=True)

app = Flask(__name__)
app.secret_key = 'your_secret_key_here_change_this_to_something_secure'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# Initialize license keys file
if not os.path.exists(LICENSE_KEYS_FILE):
    with open(LICENSE_KEYS_FILE, 'w') as f:
        json.dump({"keys": {}}, f)

# Global state
client = None
loop = None
telegram_thread = None
activity_log = []
active_sessions = {}
telegram_login_data = {
    'phone_number': None,
    'otp_requested': False,
    'otp_verified': False
}

class APKProcessor:
    def __init__(self):
        self.processing = False
        self.processed_count = 0
        self.current_status = "Idle"
        self.current_progress = 0
        self.current_stage = ""
        self.download_ready = False
        self.download_filename = None
        self.error = None
        self.last_update = time.time()
        self.signing_start_time = None
        self.upload_message_id = None
        self.request_id = None
        self.lock = threading.Lock()

processor = APKProcessor()

# ========== HELPER FUNCTIONS ==========
def validate_license(key):
    try:
        with open(LICENSE_KEYS_FILE) as f:
            data = json.load(f)
            if key in data['keys']:
                if data['keys'][key].get('count', 0) >= MAX_APK_PER_LICENSE:
                    return "expired"
                return "valid"
            return False
    except Exception as e:
        log_activity(f"License validation error: {str(e)}")
        return False

def add_license(key):
    try:
        with open(LICENSE_KEYS_FILE, 'r+') as f:
            data = json.load(f)
            if key not in data['keys']:
                data['keys'][key] = {
                    "count": 0,
                    "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "last_used": None
                }
                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()
                return True
        return False
    except Exception as e:
        log_activity(f"Error adding license: {str(e)}")
        return False

def increment_license_count(key):
    try:
        with open(LICENSE_KEYS_FILE, 'r+') as f:
            data = json.load(f)
            if key in data['keys']:
                data['keys'][key]['count'] = data['keys'][key].get('count', 0) + 1
                data['keys'][key]['last_used'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.seek(0)
                json.dump(data, f, indent=2)
                f.truncate()
                return True
        return False
    except Exception as e:
        log_activity(f"Error incrementing license count: {str(e)}")
        return False

def get_license_usage(key):
    try:
        with open(LICENSE_KEYS_FILE) as f:
            data = json.load(f)
            if key in data['keys']:
                return {
                    'used': data['keys'][key].get('count', 0),
                    'remaining': MAX_APK_PER_LICENSE - data['keys'][key].get('count', 0),
                    'created': data['keys'][key].get('created', 'Unknown'),
                    'last_used': data['keys'][key].get('last_used', 'Never')
                }
            return None
    except Exception as e:
        log_activity(f"Error getting license usage: {str(e)}")
        return None

def log_activity(event):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        activity_log.append(f"{timestamp} - {event}")
        if len(activity_log) > 100:
            activity_log.pop(0)
        print(f"[LOG] {event}")
    except Exception as e:
        print(f"Error logging activity: {str(e)}")

# ========== TELEGRAM FUNCTIONS ==========
async def initialize_telegram():
    global client
    try:
        log_activity("Initializing Telegram client...")
        client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        await client.connect()
        
        if not await client.is_user_authorized():
            log_activity("Telegram client not authorized")
            return False
            
        log_activity("Telegram client authorized successfully")
        return True
    except Exception as e:
        log_activity(f"Error initializing Telegram client: {str(e)}")
        traceback.print_exc()
        return False

async def request_otp(phone_number):
    global telegram_login_data
    try:
        if not client or not client.is_connected():
            await client.connect()
        
        await client.send_code_request(phone_number)
        telegram_login_data['phone_number'] = phone_number
        telegram_login_data['otp_requested'] = True
        telegram_login_data['otp_verified'] = False
        log_activity(f"OTP requested for {phone_number}")
        return True
    except Exception as e:
        log_activity(f"Error requesting OTP: {str(e)}")
        return False

async def verify_otp(otp_code):
    global telegram_login_data
    try:
        await client.sign_in(telegram_login_data['phone_number'], otp_code)
        telegram_login_data['otp_verified'] = True
        log_activity("OTP verified successfully")
        return True
    except errors.SessionPasswordNeededError:
        log_activity("2FA password required")
        return False
    except Exception as e:
        log_activity(f"OTP verification failed: {str(e)}")
        return False

async def process_apk(apk_path, license_key):
    try:
        with processor.lock:
            processor.processing = True
            processor.current_status = "Starting upload"
            processor.current_progress = 0
            processor.current_stage = "Uploading to bot"
            processor.download_ready = False
            processor.download_filename = None
            processor.error = None
            processor.last_update = time.time()
            processor.signing_start_time = None
            processor.upload_message_id = None
        
        # Upload APK
        filename = os.path.basename(apk_path)
        file_size = os.path.getsize(apk_path)
        unique_id = hashlib.md5(f"{filename}{time.time()}{license_key}".encode()).hexdigest()[:10]
        processor.request_id = f"SIGN_{unique_id}_{int(time.time())}"
        
        log_activity(f"📤 Uploading {filename} ({file_size/1024/1024:.2f}MB) - Request ID: {processor.request_id}")
        
        # Send file to bot
        sent_message = await client.send_file(
            TARGET_BOT, 
            apk_path,
            caption=f"Sign APK - Request ID: {processor.request_id} | License: {license_key}"
        )
        
        with processor.lock:
            processor.upload_message_id = sent_message.id
            processor.current_progress = 20
            processor.current_status = "APK uploaded, waiting for signing"
            processor.current_stage = "Bot is signing the APK"
            processor.signing_start_time = time.time()
            processor.last_update = time.time()
        
        log_activity(f"✅ APK uploaded successfully. Bot will sign it (takes 3-5 minutes)")
        
        # Wait for signing to complete (3-5 minutes)
        signing_duration = SIGNING_TIME
        elapsed_wait = 0
        
        while elapsed_wait < signing_duration and processor.processing:
            remaining = signing_duration - elapsed_wait
            minutes = remaining // 60
            seconds = remaining % 60
            
            with processor.lock:
                progress = 20 + int((elapsed_wait / signing_duration) * 40)
                processor.current_progress = progress
                processor.current_status = f"Bot signing APK... Please wait {minutes}m {seconds}s remaining"
                processor.last_update = time.time()
            
            await asyncio.sleep(10)
            elapsed_wait += 10
        
        # After signing time, start checking for signed APK
        log_activity("🔍 Signing time complete, checking for signed APK...")
        
        with processor.lock:
            processor.current_progress = 65
            processor.current_status = "Checking for signed APK from bot"
            processor.current_stage = "Downloading signed file"
            processor.last_update = time.time()
        
        # Look for signed APK
        download_deadline = time.time() + 180  # 3 minutes to find and download
        
        while time.time() < download_deadline and processor.processing:
            try:
                # Get recent messages from bot
                messages = await client.get_messages(TARGET_BOT, limit=30)
                
                for msg in messages:
                    # Skip our own upload message
                    if msg.id == processor.upload_message_id:
                        continue
                    
                    # Check if message contains APK file
                    if msg.file and msg.file.name and msg.file.name.lower().endswith('.apk'):
                        # Check if this is a response to our request
                        is_our_apk = False
                        
                        # Method 1: Check reply chain
                        if msg.reply_to_msg_id == processor.upload_message_id:
                            is_our_apk = True
                            log_activity(f"✓ Found signed APK via reply chain")
                        
                        # Method 2: Check text content for our request ID
                        elif msg.text and processor.request_id in msg.text:
                            is_our_apk = True
                            log_activity(f"✓ Found signed APK via request ID")
                        
                        # Method 3: Check if it's a signed file (contains 'signed' in name)
                        elif 'signed' in msg.file.name.lower():
                            is_our_apk = True
                            log_activity(f"✓ Found signed APK file: {msg.file.name}")
                        
                        # Method 4: Check if file size is different (signed APK usually different size)
                        elif abs((msg.file.size or 0) - file_size) > 10000:  # More than 10KB difference
                            is_our_apk = True
                            log_activity(f"✓ Found APK with different size (signed)")
                        
                        if is_our_apk:
                            # Download the signed APK
                            safe_filename = secure_filename(f"signed_{filename}")
                            signed_path = os.path.join(DOWNLOAD_FOLDER, safe_filename)
                            
                            # Remove if exists
                            if os.path.exists(signed_path):
                                os.remove(signed_path)
                            
                            with processor.lock:
                                processor.current_status = "Downloading signed APK..."
                                processor.current_progress = 85
                                processor.last_update = time.time()
                            
                            log_activity(f"📥 Downloading signed APK: {msg.file.name} ({msg.file.size/1024/1024:.2f}MB)")
                            
                            # Download the file
                            await msg.download_media(file=signed_path)
                            
                            # Verify download
                            if os.path.exists(signed_path) and os.path.getsize(signed_path) > 0:
                                downloaded_size = os.path.getsize(signed_path)
                                log_activity(f"✅ Download complete! Size: {downloaded_size/1024/1024:.2f}MB")
                                
                                with processor.lock:
                                    processor.download_ready = True
                                    processor.download_filename = safe_filename
                                    processor.current_status = "Complete! Ready for download"
                                    processor.current_progress = 100
                                    processor.current_stage = "Done"
                                    processor.last_update = time.time()
                                
                                # Update license usage
                                increment_license_count(license_key)
                                
                                # Clean up old messages
                                await clear_old_messages()
                                
                                log_activity(f"🎉 APK signing completed: {safe_filename}")
                                return signed_path
                            else:
                                log_activity(f"❌ Download failed - file corrupt or empty")
            
            except Exception as e:
                log_activity(f"Error checking messages: {str(e)}")
            
            # Wait before next check
            with processor.lock:
                elapsed_download = time.time() - download_deadline + 180
                remaining_download = max(0, 180 - elapsed_download)
                if remaining_download > 0:
                    processor.current_status = f"Searching for signed APK... ({int(remaining_download)}s remaining)"
                processor.last_update = time.time()
            
            await asyncio.sleep(CHECK_INTERVAL)
        
        # Final attempt - get all messages
        log_activity("⚠️ Performing final check for signed APK...")
        all_messages = await client.get_messages(TARGET_BOT, limit=50)
        
        for msg in all_messages:
            if msg.file and msg.file.name and msg.file.name.lower().endswith('.apk'):
                if msg.id != processor.upload_message_id:
                    log_activity(f"📁 Found APK file in final check: {msg.file.name}")
                    
                    safe_filename = secure_filename(f"signed_{filename}")
                    signed_path = os.path.join(DOWNLOAD_FOLDER, safe_filename)
                    
                    if os.path.exists(signed_path):
                        os.remove(signed_path)
                    
                    await msg.download_media(file=signed_path)
                    
                    if os.path.exists(signed_path) and os.path.getsize(signed_path) > 0:
                        with processor.lock:
                            processor.download_ready = True
                            processor.download_filename = safe_filename
                            processor.current_status = "Complete!"
                            processor.current_progress = 100
                            processor.last_update = time.time()
                        
                        increment_license_count(license_key)
                        log_activity(f"✅ Downloaded signed APK in final check")
                        return signed_path
        
        # If we reach here, no signed APK found
        raise Exception("No signed APK received from bot after 8 minutes. Please check if bot is working.")
    
    except Exception as e:
        error_msg = str(e)
        with processor.lock:
            processor.error = error_msg
            processor.current_status = f"Error: {error_msg}"
            processor.processing = False
            processor.last_update = time.time()
        log_activity(f"❌ APK processing error: {error_msg}")
        traceback.print_exc()
        raise
    finally:
        with processor.lock:
            processor.processing = False
            processor.last_update = time.time()
        
        # Cleanup uploaded file
        try:
            if os.path.exists(apk_path):
                os.remove(apk_path)
                log_activity(f"🧹 Cleaned up uploaded file: {apk_path}")
        except Exception as e:
            log_activity(f"Error cleaning up: {str(e)}")

async def clear_old_messages():
    """Clear old messages to keep chat clean"""
    try:
        messages = await client.get_messages(TARGET_BOT, limit=20)
        deleted = 0
        for msg in messages:
            try:
                # Delete messages older than 10 minutes
                if msg.date and (datetime.now().timestamp() - msg.date.timestamp()) > 600:
                    await msg.delete()
                    deleted += 1
            except:
                pass
        if deleted > 0:
            log_activity(f"🧹 Cleaned up {deleted} old messages")
    except Exception as e:
        log_activity(f"Error cleaning messages: {str(e)}")

# ========== FLASK ROUTES ==========
@app.route('/')
def index():
    if not telegram_login_data.get('otp_verified', False):
        return redirect(url_for('telegram_login'))
    
    session_id = request.cookies.get('session_id')
    if session_id in active_sessions:
        license_key = active_sessions[session_id]
        if license_key != "ADMIN":
            license_usage = get_license_usage(license_key)
            return render_template('dashboard.html', 
                                license_key=license_key,
                                license_usage=license_usage,
                                activity=activity_log[-10:][::-1],
                                processor=processor)
        else:
            return redirect(url_for('admin_dashboard'))
    return render_template('login.html')

@app.route('/telegram-login', methods=['GET', 'POST'])
def telegram_login():
    global telegram_login_data
    
    if telegram_login_data.get('otp_verified', False):
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        if not telegram_login_data.get('otp_requested', False):
            phone_number = request.form.get('phone_number')
            if phone_number:
                if not phone_number.startswith('+'):
                    phone_number = '+' + phone_number
                
                result = asyncio.run_coroutine_threadsafe(
                    request_otp(phone_number), loop
                ).result(timeout=30)
                
                if result:
                    flash('OTP sent successfully!', 'success')
                else:
                    flash('Failed to send OTP', 'error')
            else:
                flash('Please enter phone number', 'error')
        else:
            otp_code = request.form.get('otp_code')
            if otp_code:
                result = asyncio.run_coroutine_threadsafe(
                    verify_otp(otp_code), loop
                ).result(timeout=30)
                
                if result:
                    flash('OTP verified!', 'success')
                    return redirect(url_for('index'))
                else:
                    flash('Invalid OTP', 'error')
            else:
                flash('Please enter OTP', 'error')
    
    return render_template('telegram_login.html', 
                         otp_requested=telegram_login_data.get('otp_requested', False),
                         phone_number=telegram_login_data.get('phone_number', ''))

@app.route('/login', methods=['POST'])
def login():
    if not telegram_login_data.get('otp_verified', False):
        return redirect(url_for('telegram_login'))
    
    license_key = request.form.get('license_key')
    validation = validate_license(license_key)
    
    if validation == "valid":
        session_id = hashlib.sha256(os.urandom(64)).hexdigest()
        active_sessions[session_id] = license_key
        response = redirect(url_for('index'))
        response.set_cookie('session_id', session_id, max_age=86400)
        flash('Login successful!', 'success')
        return response
    elif validation == "expired":
        flash('License limit reached. Contact support.', 'error')
    else:
        flash('Invalid license key.', 'error')
    return redirect(url_for('contact_buy'))

@app.route('/logout')
def logout():
    session_id = request.cookies.get('session_id')
    if session_id in active_sessions:
        del active_sessions[session_id]
    response = redirect(url_for('index'))
    response.set_cookie('session_id', '', expires=0)
    flash('Logged out', 'success')
    return response

@app.route('/upload', methods=['POST'])
def handle_upload():
    if not telegram_login_data.get('otp_verified', False):
        return jsonify({'status': 'error', 'message': 'Telegram not authenticated'})
    
    session_id = request.cookies.get('session_id')
    if session_id not in active_sessions or active_sessions[session_id] == "ADMIN":
        return jsonify({'status': 'error', 'message': 'Unauthorized'})
    
    license_key = active_sessions[session_id]
    validation = validate_license(license_key)
    if validation != "valid":
        return jsonify({'status': 'error', 'message': 'License invalid or expired'})
    
    if 'apk_file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file selected'})
    
    file = request.files['apk_file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No file selected'})
    
    if not file.filename.lower().endswith('.apk'):
        return jsonify({'status': 'error', 'message': 'Only APK files allowed'})
    
    with processor.lock:
        if processor.processing:
            return jsonify({'status': 'error', 'message': 'Another file is being processed. Please wait.'})
    
    try:
        safe_filename = secure_filename(file.filename)
        filename = f"{license_key}_{int(time.time())}_{safe_filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        
        log_activity(f"File saved: {filename}")
        
        with processor.lock:
            processor.processing = True
            processor.current_status = "Starting..."
            processor.current_progress = 0
            processor.download_ready = False
            processor.download_filename = None
            processor.error = None
            processor.last_update = time.time()
        
        # Start processing
        asyncio.run_coroutine_threadsafe(process_apk(filepath, license_key), loop)
        
        return jsonify({
            'status': 'processing', 
            'message': 'APK upload started. Signing takes 3-5 minutes.',
            'progress': 0,
            'current_status': 'Upload started'
        })
    except Exception as e:
        with processor.lock:
            processor.error = str(e)
            processor.current_status = f"Error: {str(e)}"
            processor.processing = False
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/status')
def get_status():
    try:
        session_id = request.cookies.get('session_id')
        if session_id not in active_sessions:
            return jsonify({'status': 'error', 'message': 'Unauthorized'})
        
        license_key = active_sessions[session_id]
        license_usage = None if license_key == "ADMIN" else get_license_usage(license_key)
        
        with processor.lock:
            # Check for timeout
            if processor.processing and processor.signing_start_time:
                elapsed = time.time() - processor.signing_start_time
                if elapsed > PROCESSING_TIMEOUT:
                    processor.error = "Processing timeout"
                    processor.processing = False
                    processor.current_status = "Timeout error"
            
            return jsonify({
                'status': 'success',
                'processing': processor.processing,
                'current_status': processor.current_status,
                'current_stage': processor.current_stage,
                'progress': processor.current_progress,
                'download_ready': processor.download_ready,
                'download_filename': processor.download_filename,
                'error': processor.error,
                'license_usage': license_usage,
                'activity': activity_log[-10:][::-1] if activity_log else []
            })
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/download/<filename>')
def download_file(filename):
    session_id = request.cookies.get('session_id')
    if session_id not in active_sessions:
        flash('Please login', 'error')
        return redirect(url_for('index'))
    
    if not filename.startswith('signed_'):
        flash('Invalid file', 'error')
        return redirect(url_for('index'))
    
    try:
        return send_from_directory(
            DOWNLOAD_FOLDER, 
            filename, 
            as_attachment=True,
            mimetype='application/vnd.android.package-archive',
            download_name=filename.replace('signed_', '')
        )
    except Exception as e:
        flash(f"Download error: {str(e)}", 'error')
        return redirect(url_for('index'))

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        password = request.form.get('password')
        if hashlib.sha256(password.encode()).hexdigest() == ADMIN_PASSWORD:
            session_id = hashlib.sha256(os.urandom(64)).hexdigest()
            active_sessions[session_id] = "ADMIN"
            response = redirect(url_for('admin_dashboard'))
            response.set_cookie('session_id', session_id, max_age=3600)
            flash('Admin login successful', 'success')
            return response
        flash('Invalid password', 'error')
    return render_template('admin_login.html')

@app.route('/admin/dashboard')
def admin_dashboard():
    session_id = request.cookies.get('session_id')
    if session_id not in active_sessions or active_sessions[session_id] != "ADMIN":
        return redirect(url_for('admin_login'))
    
    try:
        with open(LICENSE_KEYS_FILE) as f:
            license_data = json.load(f)
    except:
        license_data = {"keys": {}}
    
    return render_template('admin_dashboard.html', 
                         licenses=license_data.get('keys', {}),
                         activity=activity_log[-20:][::-1],
                         max_apk=MAX_APK_PER_LICENSE)

@app.route('/admin/add_license', methods=['POST'])
def admin_add_license():
    session_id = request.cookies.get('session_id')
    if session_id not in active_sessions or active_sessions[session_id] != "ADMIN":
        return jsonify({'status': 'error', 'message': 'Unauthorized'})
    
    key = request.form.get('license_key')
    if key and add_license(key):
        log_activity(f"Admin added license: {key}")
        return jsonify({'status': 'success', 'message': 'License added'})
    return jsonify({'status': 'error', 'message': 'Invalid or exists'})

@app.route('/payment')
def payment():
    return render_template('payment.html')

@app.route('/contact')
def contact_buy():
    return render_template('contact.html')

@app.route('/debug')
def debug():
    session_id = request.cookies.get('session_id')
    if session_id not in active_sessions or active_sessions[session_id] != "ADMIN":
        return jsonify({'error': 'Unauthorized'}), 401
    
    async def get_bot_info():
        if not client:
            return {"error": "Client not initialized"}
        try:
            me = await client.get_me()
            messages = await client.get_messages(TARGET_BOT, limit=5)
            msg_list = []
            for msg in messages:
                msg_list.append({
                    'id': msg.id,
                    'date': msg.date.isoformat() if msg.date else None,
                    'text': msg.text[:200] if msg.text else None,
                    'has_file': msg.file is not None,
                    'file_name': msg.file.name if msg.file else None,
                    'file_size': msg.file.size if msg.file else None
                })
            return {
                "bot_username": TARGET_BOT,
                "telegram_user": me.username if me else None,
                "connected": client.is_connected(),
                "recent_messages": msg_list
            }
        except Exception as e:
            return {"error": str(e)}
    
    try:
        info = asyncio.run_coroutine_threadsafe(get_bot_info(), loop).result(timeout=10)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'telegram': client and client.is_connected() if client else False,
        'otp_verified': telegram_login_data.get('otp_verified', False),
        'sessions': len(active_sessions)
    })

# ========== BACKGROUND THREAD ==========
def run_telegram_loop():
    global loop, telegram_login_data, client
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        client = TelegramClient(SESSION_FILE, API_ID, API_HASH)
        loop.run_until_complete(client.connect())
        
        if loop.run_until_complete(client.is_user_authorized()):
            telegram_login_data['otp_verified'] = True
            log_activity("✓ Telegram client authorized")
        else:
            log_activity("⚠️ Telegram client not authorized - need login")
        
        loop.run_forever()
    except Exception as e:
        log_activity(f"❌ Fatal error: {str(e)}")
        traceback.print_exc()

def start_background_loop():
    thread = threading.Thread(target=run_telegram_loop, daemon=True)
    thread.start()
    time.sleep(2)
    log_activity("✓ Background thread started")

# ========== MAIN ==========
if __name__ == '__main__':
    print("=" * 70)
    print("🔐 APK SIGNING BOT - COMPLETE SOLUTION")
    print("=" * 70)
    print(f"📁 Upload folder: {UPLOAD_FOLDER}")
    print(f"📥 Download folder: {DOWNLOAD_FOLDER}")
    print(f"🔑 License file: {LICENSE_KEYS_FILE}")
    print(f"📊 Max APK per license: {MAX_APK_PER_LICENSE}")
    print(f"🤖 Bot username: {TARGET_BOT}")
    print(f"⏱️  Signing time: {SIGNING_TIME//60} minutes")
    print("=" * 70)
    print("🌐 Starting web server...")
    print("👉 Access at: http://localhost:5000")
    print("👉 Admin access: http://localhost:5000/admin")
    print("👉 Admin password: admin123")
    print("=" * 70)
    
    start_background_loop()
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)