# backend/database/auth.py
from fastapi import APIRouter, Form, Request,Cookie, HTTPException
from fastapi.responses import HTMLResponse
from backend.database.db_manager import database
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse,RedirectResponse
from uuid import uuid4
from typing import Optional
router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    """Render the login page"""
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/login")
async def login_post(
    username: str = Form(...),
    password: str = Form(...)
):
    query = "SELECT * FROM users WHERE username = :username AND password = :password"
    user = await database.fetch_one(query=query, values={"username": username, "password": password})

    if user:
        return JSONResponse(
            content={"status": "success", "userid": user["userid"]},
            status_code=200
        )
    else:
        return JSONResponse(
            content={"status": "error", "message": "Invalid username or password"},
            status_code=401
        )
        
@router.post("/create-session")
async def create_session(userid: int = Form(...), request: Request = None):
    session_id = str(uuid4())
    user_agent = request.headers.get("user-agent")
    client_ip = request.client.host

    query = """
    INSERT INTO sessions (sessionid, userid, user_agent, ip_address)
    VALUES (:sessionid, :userid, :user_agent, :ip_address)
    """
    values = {
        "sessionid": session_id,
        "userid": userid,
        "user_agent": user_agent,
        "ip_address": client_ip,
    }

    await database.execute(query, values)
    return {"status": "created", "session_id": session_id}

@router.get("/session-user/{session_id}")
async def get_user_from_session(session_id: str):
    query = """
        SELECT userid FROM sessions
        WHERE sessionid = :session_id AND is_active = TRUE
    """
    user = await database.fetch_one(query=query, values={"session_id": session_id})

    if user:
        return {"status": "success", "userid": user["userid"]}
    else:
        return JSONResponse(
            content={"status": "error", "message": "Session not found or inactive"},
            status_code=404
        )
        
@router.get("/signup", response_class=HTMLResponse)
async def signup_get(request: Request):
    """Render the signup page"""
    return templates.TemplateResponse("signup.html", {"request": request})


@router.post("/signup")
async def signup_post(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    confirm_password: str = Form(...)
):
    if password != confirm_password:
        return JSONResponse(
            content={"status": "error", "message": "Passwords do not match"},
            status_code=400
        )

    # Check if user/email already exists
    check_query = "SELECT userid FROM users WHERE username = :username OR email = :email"
    existing_user = await database.fetch_one(check_query, {"username": username, "email": email})

    if existing_user:
        return JSONResponse(
            content={"status": "error", "message": "Username or email already exists"},
            status_code=409
        )

    # Insert into DB (plain-text password)
    insert_query = """
        INSERT INTO users (username, email, password)
        VALUES (:username, :email, :password)
    """
    await database.execute(insert_query, {
        "username": username,
        "email": email,
        "password": password
    })

    return JSONResponse(
        content={"status": "success", "message": "Account created. Please log in."},
        status_code=201
    )
        
        
@router.post("/logout-session")
async def logout_session(session_id: str = Form(...)):
    query = "UPDATE sessions SET is_active = FALSE WHERE sessionid = :session_id"
    await database.execute(query, {"session_id": session_id})
    return {"status": "terminated"}


async def get_session_id_from_cookie(session_id: str = Cookie(default=None)) -> str:
    if not session_id:
        raise HTTPException(status_code=401, detail="Session ID not found in cookies")
    return session_id

async def get_user_id_by_session(login_session_id: str) -> Optional[int]:
    query = "SELECT userid FROM sessions WHERE sessionid = :sessionid"
    result = await database.fetch_one(query=query, values={"sessionid": login_session_id})
    return result["userid"] if result else None