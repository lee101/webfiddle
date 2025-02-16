#!/usr/bin/env python
import hashlib
import logging
import os
import urllib.parse
from pathlib import Path
from typing import Optional

import httpx
import sqlite3
import json
import time
import re

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from models import Fiddle
from mirror.transform_content import TransformContent
from blacklist import BLACKLISTED_URLS

mirror_router = APIRouter()
templates = Jinja2Templates(directory=".")

DEBUG = False
EXPIRATION_DELTA_SECONDS = 3600 * 24 * 30

HTTP_PREFIX = "http://"

IGNORE_HEADERS = frozenset([
    "set-cookie",
    "expires",
    "cache-control",

    # Ignore hop-by-hop headers
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "x-frame-options",
    "content-security-policy",
    "x-xss-protection",
])

TRANSFORMED_CONTENT_TYPES = frozenset([
    "text/html",
    "text/css",
])

MAX_CONTENT_SIZE = 10 ** 64

# Initialize SQLite database and table for caching mirrored content.
def init_db():
    conn = sqlite3.connect('cache.db')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS mirrored_content (
            key_name TEXT PRIMARY KEY,
            original_address TEXT,
            translated_address TEXT,
            status INTEGER,
            headers TEXT,
            data BLOB,
            base_url TEXT,
            expiry INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def get_url_key_name(url):
    url_hash = hashlib.sha256()
    url_hash.update(url.encode('utf-8'))
    return "hash_" + url_hash.hexdigest()


class MirroredContent(object):
    def __init__(self, original_address, translated_address,
                 status, headers, data, base_url):
        self.original_address = original_address
        self.translated_address = translated_address
        self.status = status
        self.headers = headers
        self.data = data
        self.base_url = base_url

    @staticmethod
    def get_by_key_name(key_name):
        conn = sqlite3.connect('cache.db')
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM mirrored_content WHERE key_name = ?", (key_name,))
        row = cursor.fetchone()
        if row is None:
            conn.close()
            return None
        current_time = int(time.time())
        if row['expiry'] < current_time:
            conn.execute("DELETE FROM mirrored_content WHERE key_name = ?", (key_name,))
            conn.commit()
            conn.close()
            return None
        headers = json.loads(row['headers'])
        new_content = MirroredContent(
            original_address=row['original_address'],
            translated_address=row['translated_address'],
            status=row['status'],
            headers=headers,
            data=row['data'],
            base_url=row['base_url']
        )
        conn.close()
        return new_content

    @staticmethod
    async def fetch_and_store(key_name, base_url, translated_address, mirrored_url):
        """Fetch and cache a page with redirect handling"""
        final_url = mirrored_url
        try:
            # Add headers to prevent compressed responses
            async with httpx.AsyncClient(
                max_redirects=3,
                headers={'Accept-Encoding': 'identity'}  # Disable compression
            ) as client:
                # First HEAD request to check for redirects
                head_resp = await client.head(mirrored_url, follow_redirects=True)
                final_url = str(head_resp.url)
                
                # If redirected, update the mirrored URL
                if final_url != mirrored_url:
                    translated_address = final_url[len(HTTP_PREFIX):]
                    mirrored_url = final_url
                    key_name = get_url_key_name(mirrored_url)
                    
                    # Check cache again with new URL
                    existing = MirroredContent.get_by_key_name(key_name)
                    if existing:
                        return existing

                # Now get the full content
                response = await client.get(mirrored_url, follow_redirects=True)
                
        except httpx.HTTPError as e:
            logging.exception("Could not fetch URL: %s", e)
            return None

        # Process response as before...
        adjusted_headers = {}
        for key, value in response.headers.items():
            if key.lower() == 'location':
                # Rewrite Location header to maintain proxy context
                parsed = urllib.parse.urlparse(value)
                adjusted_value = f"/{base_url}/{parsed.netloc}{parsed.path}"
                adjusted_headers['location'] = adjusted_value
            elif key.lower() not in IGNORE_HEADERS:
                adjusted_headers[key.lower()] = value

        content = response.content
        page_content_type = adjusted_headers.get("content-type", "")
        for content_type in TRANSFORMED_CONTENT_TYPES:
            # startswith() because there could be a 'charset=UTF-8' in the header.
            if page_content_type.startswith(content_type):
                # Transform and encode to bytes before storage
                content_str = TransformContent(base_url, mirrored_url, content)
                content = content_str.encode('utf-8')
                break

        # Ensure proper byte handling
        if isinstance(content, str):
            content = content.encode('utf-8')
        
        # Add safety checks
        content = content[:MAX_CONTENT_SIZE]  # Direct slice instead of conditional
        if len(content) < int(response.headers.get("content-length", 0)):
            logging.warning("Truncated content from %s", mirrored_url)
        
        # Store actual content length
        adjusted_headers["content-length"] = str(len(content))
        if "content-encoding" in adjusted_headers:
            del adjusted_headers["content-encoding"]

        new_content = MirroredContent(
            base_url=base_url,
            original_address=mirrored_url,
            translated_address=translated_address,
            status=response.status_code,
            headers=adjusted_headers,
            data=content
        )
        try:
            conn = sqlite3.connect('cache.db')
            conn.execute(
                "INSERT OR REPLACE INTO mirrored_content "
                "(key_name, original_address, translated_address, status, headers, data, base_url, expiry) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key_name, new_content.original_address, new_content.translated_address, new_content.status,
                 json.dumps(new_content.headers), new_content.data, new_content.base_url,
                 int(time.time()) + EXPIRATION_DELTA_SECONDS)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logging.error('SQLite insert failed: key_name = "%s", original_url = "%s", error: %s',
                          key_name, mirrored_url, e)

        return new_content


@mirror_router.get("/warmup")
async def warmup_handler():
    return {"status": "ok"}


@mirror_router.get("/", response_class=HTMLResponse)
@mirror_router.get("/main", response_class=HTMLResponse)
async def home_handler(request: Request):
    # Mimic the original home handler form logic.
    user_agent = request.headers.get("user-agent", "")
    if "AppEngine-Google" in user_agent:
        raise HTTPException(status_code=404)

    form_url = request.query_params.get("url")
    if form_url:
        inputted_url = urllib.parse.unquote(form_url)
        if inputted_url.startswith(HTTP_PREFIX):
            inputted_url = inputted_url[len(HTTP_PREFIX):]
        return RedirectResponse(url=f"/{inputted_url}", status_code=302)
    
    secure_url = None
    if request.url.scheme == "http":
        host = request.headers.get("host", "")
        secure_url = f"https://{host}{request.url.path}"
    context = {
        "request": request,
        "secure_url": secure_url,
    }
    return templates.TemplateResponse("main.html", context)


add_code = """"""

big_add_code = """<iframe style="min-width:600px;min-height:800px;width:100%;border:none" src="http://textadventure.v5games.com">
    </iframe>"""

def request_blocker(fiddle_name, nonce):
    return f"""
<script nonce="{nonce}">
var proxyBase = '/{fiddle_name}/';
var currentDomain = window.location.pathname.split('/')[2];
function rewriteUrl(url) {{
    // Handle absolute URLs
    if (/^https?:\\/\\//.test(url)) {{
        const parser = document.createElement('a');
        parser.href = url;
        return proxyBase + parser.hostname + parser.pathname;
    }}
    // Handle root-relative URLs
    if (url.startsWith('/')) {{
        return proxyBase + currentDomain + url;
    }}
    // Handle relative URLs
    const currentPath = window.location.pathname.split('/').slice(0, 4).join('/');
    const baseUrl = new URL(window.location.origin + currentPath + '/');
    return new URL(url, baseUrl).pathname;
}}
// Remove target="_blank" from all links and handle window.open
document.addEventListener('DOMContentLoaded', () => {{
    // Remove target="_blank" from all links
    const links = document.querySelectorAll('a[target="_blank"]');
    links.forEach(link => {{
        link.removeAttribute('target');
    }});
}});

// Override window.open to open in same window
const originalWindowOpen = window.open;
window.open = function(url, target, features) {{
    if (url) {{
        window.location.href = rewriteUrl(url);
        return null;
    }}
    return originalWindowOpen.apply(this, arguments);
}};

// Override XMLHttpRequest and fetch
const originalOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url) {{
    arguments[1] = rewriteUrl(url);
    originalOpen.apply(this, arguments);
}};

const originalFetch = window.fetch;
window.fetch = function(input, init) {{
    if (typeof input === 'string') {{
        input = rewriteUrl(input);
    }} else if (input instanceof URL) {{
        input = rewriteUrl(input.href);
    }}
    return originalFetch(input, init);
}};

// Rewrite all DOM elements
document.addEventListener('DOMContentLoaded', () => {{
    const attributes = ['href', 'src', 'action', 'data-src'];
    const elements = document.querySelectorAll([...attributes].map(attr => `[${{attr}}]`).join(','));
    
    elements.forEach(element => {{
        attributes.forEach(attr => {{
            if (element.hasAttribute(attr)) {{
                const url = element.getAttribute(attr);
                element.setAttribute(attr, rewriteUrl(url));
            }}
        }});
    }});
}});
</script>
"""


@mirror_router.get("/{fiddle_name}/{base_url:path}", response_class=HTMLResponse)
async def mirror_handler(request: Request, fiddle_name: str, base_url: str):
    # Check for recursive requests.
    user_agent = request.headers.get("user-agent", "")
    if "AppEngine-Google" in user_agent:
        raise HTTPException(status_code=404)
    if '-' not in fiddle_name:
        raise HTTPException(status_code=500)
    if base_url.endswith("favicon.ico"):
        return RedirectResponse(url="/favicon.ico", status_code=302)
    
    # Check if the URL is blacklisted
    domain = base_url.split('/')[0].lower()  # Get the domain part
    if domain in BLACKLISTED_URLS:
        raise HTTPException(status_code=403, detail="Access to this URL is not allowed")
    
    # Parse base_url as domain/path without fiddle prefix
    domain_part = base_url.split('/', 1)[0]
    proxy_base = f"{fiddle_name}/{domain_part}"
    
    # Ensure translated_address includes the full path
    translated_address = base_url
    mirrored_url = HTTP_PREFIX + translated_address

    # Use sha256 hash of the mirrored_url for the cache key.
    key_name = get_url_key_name(mirrored_url)
    content = MirroredContent.get_by_key_name(key_name)
    if content is None:
        content = await MirroredContent.fetch_and_store(key_name, proxy_base, translated_address, mirrored_url)
    if content is None:
        raise HTTPException(status_code=404)
    
    headers = dict(content.headers)
    if not DEBUG:
        headers["cache-control"] = "max-age=%d" % EXPIRATION_DELTA_SECONDS

    if content.headers.get('content-type', '').startswith('text/html'):
        # Transform content and handle size limits
        content_str = content.data.decode('utf-8') if isinstance(content.data, bytes) else content.data
        content_str = TransformContent(proxy_base, mirrored_url, content_str)
        if len(content_str) > MAX_CONTENT_SIZE:
            logging.warning("Content is over MAX_CONTENT_SIZE; truncating") 
            content_str = content_str[:MAX_CONTENT_SIZE]

        # Generate unique nonce for each request
        nonce = hashlib.sha256(os.urandom(32)).hexdigest()
        # Update CSP header with both hashes from errors
        csp_policy = (
            f"default-src * 'unsafe-inline' 'unsafe-eval' data: blob:; "
            f"script-src * 'unsafe-inline' 'unsafe-eval' data: blob: 'nonce-{nonce}'; "
            "style-src * 'unsafe-inline' data:; "
            "img-src * data: blob:; "
            "font-src * data:; "
            "connect-src *; "
            "media-src *; "
            "object-src *;"
        )
        headers["content-security-policy"] = csp_policy
        
        # Use the properly converted content_str
        request_blocked_data = re.sub(r'(?i)<head[^>]*>', 
            lambda m: m.group() + request_blocker(fiddle_name, nonce), 
            content_str,
            1)
        add_data = re.sub(r'(?P<tag><body[\w\W]*?>)',
                          r'\g<tag>' + add_code,
                          request_blocked_data, 1)
        fiddle = Fiddle.byUrlKey(fiddle_name)
        if fiddle:
            script = str(fiddle.script) if fiddle.script is not None else ""
            style = str(fiddle.style) if fiddle.style is not None else ""
            extra_js = '<script id="webfiddle-js">' + script + '</script>'
            extra_css = '<style id="webfiddle-css">' + style + '</style>'
            analytics_and_add = f"""
<script nonce="{nonce}">
    (function (i, s, o, g, r, a, m) {{
        i['GoogleAnalyticsObject'] = r;
        i[r] = i[r] || function () {{
            (i[r].q = i[r].q || []).push(arguments)
        }}, i[r].l = 1 * new Date();
        a = s.createElement(o),
                m = s.getElementsByTagName(o)[0];
        a.async = 1;
        a.src = g;
        m.parentNode.insertBefore(a, m)
    }})(window, document, 'script', '//www.google-analytics.com/analytics.js', 'ga');

    ga('create', 'UA-57646272-1', 'auto');
    ga('require', 'displayfeatures');
    ga('send', 'pageview');
</script>
""" + big_add_code
            final_html = add_data + extra_js + extra_css + analytics_and_add
            # More robust script tag modification
            final_html = re.sub(
                r'(<script\b)(?![^>]*\snonce\s*=)(?![^>]*\bsrc\s*=)([^>]*)>',
                rf'\1 nonce="{nonce}"\2>',
                final_html,
                flags=re.IGNORECASE
            )
            
            # Calculate actual content length after transformations
            final_content = final_html.encode('utf-8')
            headers["content-length"] = str(len(final_content))
            return HTMLResponse(content=final_content, status_code=content.status, headers=headers)
        else:
            return HTMLResponse(content=add_data, status_code=content.status, headers=headers)
    else:
        # For non-HTML content, use original data but verify length
        content_data = content.data
        if len(content_data) != int(headers.get("content-length", 0)):
            headers["content-length"] = str(len(content_data))
        return Response(content=content_data, headers=headers, status_code=content.status)
