#!/usr/bin/env python
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request, Response, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.wsgi import WSGIMiddleware
from starlette.middleware.sessions import SessionMiddleware

import sqlite3
import bcrypt

current_dir = Path(__file__).parent

import fixtures
from gameon_utils import GameOnUtils
from mirror.mirror import mirror_router
from models import Fiddle, default_fiddle, init_db, DATABASE_PATH

app = FastAPI()

app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "changeme"))

init_db()

templates = Jinja2Templates(directory=os.path.dirname(__file__))
debug = (
    os.environ.get("SERVER_SOFTWARE", "").startswith("Development")
    or os.environ.get("IS_DEVELOP", "") == "1"
    or Path(current_dir / "models/debug.env").exists()
)

GCLOUD_STATIC_BUCKET_URL = "/static" if debug else "https://static.netwrck.com/simstatic"

# Mount static files before any routes
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return RedirectResponse(url="/static/favicon.ico", status_code=302)

@app.get("/bingsiteauth.xml", include_in_schema=False)
async def bingsiteauth():
    xml_content = """<?xml version="1.0"?>
<users>
	<user>1A8F067A516D6AE82B1170B390ECD0EC</user>
</users>
"""
    return Response(content=xml_content, media_type="application/xml")

@app.get("/", response_class=HTMLResponse)
async def main_handler(request: Request):
    try:
        current_saved_fiddle = {
            "id": default_fiddle.id,
            "title": default_fiddle.title,
            "description": default_fiddle.description,
            "start_url": default_fiddle.start_url,
            "script": default_fiddle.script,
            "style": default_fiddle.style,
            "script_language": default_fiddle.script_language,
            "style_language": default_fiddle.style_language
        }
        return templates.TemplateResponse("templates/index.jinja2", {
            "request": request,
            "fiddle": default_fiddle,
            "current_saved_fiddle": json.dumps(current_saved_fiddle),
            "title": "WebSim by Netwrck!",
            "description": "AI Creator - Make CSS and JavaScript To Create any and every web page! Share the results!",
            "json": json,
            "fixtures": fixtures,
            "GameOnUtils": GameOnUtils,
            "static_url": GCLOUD_STATIC_BUCKET_URL,
            "url": request.url,
        })
    except Exception as e:
        # Log the error and return a simple error page
        print(f"Error in main_handler: {str(e)}")
        return HTMLResponse(content="<h1>Error loading page</h1><p>Please try again later.</p>", status_code=500)

@app.get("/_ah/warmup")
async def warmup_handler():
    return ""

@app.get("/createfiddle")
async def create_fiddle_handler(request: Request):
    fiddle = Fiddle()
    fiddle.id = request.query_params.get('id')
    fiddle.title = request.query_params.get('title')
    fiddle.description = request.query_params.get('description')
    fiddle.start_url = request.query_params.get('start_url')
    fiddle.script = request.query_params.get('script')
    fiddle.style = request.query_params.get('style')

    script_language = request.query_params.get('script_language')
    style_language = request.query_params.get('style_language')

    fiddle.script_language = fixtures.SCRIPT_TYPES[script_language] if script_language else fixtures.SCRIPT_TYPES['javascript']
    fiddle.style_language = fixtures.STYLE_TYPES[style_language] if style_language else fixtures.STYLE_TYPES['css']

    Fiddle.save(fiddle)
    return "success"

@app.get("/{fiddlekey}", response_class=HTMLResponse)
async def get_fiddle_handler(request: Request, fiddlekey: str):
    current_fiddle = Fiddle.byUrlKey(fiddlekey)
    if not current_fiddle:
        current_fiddle = default_fiddle

    current_saved_fiddle = {
        "id": current_fiddle.id,
        "title": current_fiddle.title,
        "description": current_fiddle.description,
        "start_url": current_fiddle.start_url,
        "script": current_fiddle.script,
        "style": current_fiddle.style,
        "script_language": current_fiddle.script_language,
        "style_language": current_fiddle.style_language
    }
    return templates.TemplateResponse("templates/index.jinja2", {
        "request": request,
        "fiddle": current_fiddle,
        "current_saved_fiddle": json.dumps(current_saved_fiddle),
        "title": current_fiddle.title,
        "description": current_fiddle.description,
        "json": json,
        "fixtures": fixtures,
        "GameOnUtils": GameOnUtils,
        "static_url": GCLOUD_STATIC_BUCKET_URL,
        "url": request.url,
    })


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(
        "templates/login.jinja2",
        {
            "request": request,
            "title": "Login",
            "action": "/login",
            "submit_label": "Login",
            "login": True,
        },
    )


@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    conn = sqlite3.connect(DATABASE_PATH)
    row = conn.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if row and bcrypt.checkpw(password.encode(), row[0].encode()):
        request.session["user"] = username
        return RedirectResponse("/", status_code=302)
    return HTMLResponse("Invalid credentials", status_code=400)


@app.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return templates.TemplateResponse(
        "templates/login.jinja2",
        {
            "request": request,
            "title": "Register",
            "action": "/register",
            "submit_label": "Register",
            "login": False,
        },
    )


@app.post("/register")
async def register(request: Request, username: str = Form(...), password: str = Form(...)):
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        with conn:
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)", (username, hashed))
    except sqlite3.IntegrityError:
        conn.close()
        return HTMLResponse("User already exists", status_code=400)
    conn.close()
    request.session["user"] = username
    return RedirectResponse("/", status_code=302)


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=302)

@app.get("/sitemap.xml", response_class=HTMLResponse)
async def sitemap_handler(request: Request):
    content = templates.get_template("sitemap.xml").render({"request": request})
    return Response(content=content, media_type="text/xml")

@app.get("/{url:path}/")
async def slash_murderer(url: str):
    return RedirectResponse(url=f"/{url}", status_code=302)

# Include the mirror router
app.include_router(mirror_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
