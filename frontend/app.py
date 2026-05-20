from flask import Flask, request, redirect, url_for, render_template, send_from_directory, make_response  
import os
import json  # Add this import
from dotenv import load_dotenv
from datetime import datetime
import requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')
groq_api_key = os.getenv('GROQ_API_KEY')

VOICE_BOT_URL = os.getenv("VOICE_BOT_URL")
DOC_GEN_URL = os.getenv("DOC_GEN_URL")

if not app.secret_key:
    raise ValueError("SECRET_KEY not found in environment variables")
if not groq_api_key:
    raise ValueError("GROQ_API_KEY not found in environment variables")

print("✅ All environment variables loaded successfully!")

# === Default route redirects to homepage ===
@app.route('/')
def index_redirect():
    return redirect(url_for('homepage'))

# === Public landing page ===
@app.route('/homepage')
def homepage():
    return render_template('homepage.html')
# === Login route ===
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    username = request.form.get('username')
    password = request.form.get('password')

    try:
        response = requests.post("http://127.0.0.1:8000/login", data={
            "username": username,
            "password": password
        })

        if response.status_code == 200:
            data = response.json()
            userid = data.get("userid")

            if userid:
                print("✅ Login successful for userid:", userid)

                session_response = requests.post(
                    "http://127.0.0.1:8000/create-session",
                    data={"userid": userid},
                    headers={"User-Agent": request.headers.get("User-Agent")}
                )

                if session_response.status_code == 200:
                    session_id = session_response.json().get("session_id")
                    print("🆔 Session created:", session_id)

                    flask_response = make_response(redirect(url_for('index')))
                    flask_response.set_cookie(
                        key="login_session_id",  # ✅ Renamed here
                        value=session_id,
                        path="/",
                        samesite="Lax",
                        secure=False  # Set True in production
                    )
                    return flask_response
                else:
                    return render_template('login.html', error="Session creation failed.")
            else:
                return render_template('login.html', error="Login succeeded but no user ID returned.")
        else:
            error_msg = response.json().get("message", "Invalid username or password")
            print("❌ Login failed:", error_msg)
            return render_template('login.html', error=error_msg)

    except Exception as e:
        print("❌ Backend error:", str(e))
        return render_template('login.html', error="Backend error: " + str(e))


# === Authenticated dashboard (index.html) ===
@app.route('/index')
def index():
    login_session_id = request.cookies.get("login_session_id")
    print("🍪 Cookie contents at index:", request.cookies)

    if not login_session_id:
        print("❌ No login_session_id cookie found")
        return redirect(url_for('login'))

    try:
        response = requests.get(f"http://127.0.0.1:8000/session-user/{login_session_id}")
        if response.status_code == 200:
            userid = response.json().get("userid")
            print("✅ Logged in as userid:", userid)
            return render_template(
                'index.html', 
                userid=userid, 
                session_id=login_session_id,
                voice_bot_url=VOICE_BOT_URL,
                doc_gen_url=DOC_GEN_URL
                )
        else:
            print("❌ Invalid or expired session")
            return redirect(url_for('login'))
    except Exception as e:
        print("❌ Error fetching user:", str(e))
        return redirect(url_for('login'))


# === Signup route ===
@app.route('/signup')
def signup():
    return render_template('signup.html')


# === Logout route ===
@app.route('/logout')
def logout():
    login_session_id = request.cookies.get("login_session_id")  # ✅ Renamed here

    if login_session_id:
        try:
            requests.post("http://127.0.0.1:8000/logout-session", data={"session_id": login_session_id})
        except:
            pass

    resp = make_response(redirect(url_for('login')))
    resp.set_cookie("login_session_id", "", expires=0)  # ✅ Clear renamed cookie
    return resp

# === Serve static icons ===
@app.route('/static/icons/<filename>')
def serve_icons(filename):
    return send_from_directory('static/icons', filename)

# === Health check ===
@app.route('/health')
def health_check():
    return {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'frontend': 'Flask',
        'backend': 'FastAPI'
    }

if __name__ == '__main__':
    app.run(debug=True, port=5000)
