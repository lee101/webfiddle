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
import requests

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from models import Fiddle
from mirror.transform_content import TransformContent, transform_content
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
        try:
            async with httpx.AsyncClient(max_redirects=3) as client:
                response = await client.get(mirrored_url, follow_redirects=True)
                final_url = str(response.url)
                
                # Prevent proxy path duplication
                parsed = urllib.parse.urlparse(final_url)
                if parsed.path.startswith(f"/{base_url}"):
                    # Path already contains proxy segments
                    translated_address = parsed.path.lstrip('/')
                    base_url = base_url.split('/')[0]  # Reset to original fiddle
                    
                # Process response headers
                adjusted_headers = {}
                for key, value in response.headers.items():
                    if key.lower() == 'location':
                        parsed = urllib.parse.urlparse(value)
                        adjusted_value = f"/{base_url}{parsed.path}"
                        if parsed.query:
                            adjusted_value += f"?{parsed.query}"
                        adjusted_headers['location'] = adjusted_value
                    elif key.lower() not in IGNORE_HEADERS:
                        adjusted_headers[key.lower()] = value

                content = response.content
                page_content_type = adjusted_headers.get("content-type", "")
                for content_type in TRANSFORMED_CONTENT_TYPES:
                    # startswith() because there could be a 'charset=UTF-8' in the header.
                    if page_content_type.startswith(content_type):
                        content = TransformContent(base_url, mirrored_url, content)
                        break

                # If the transformed content is over MAX_CONTENT_SIZE, truncate it (yikes!)
                if len(content) > MAX_CONTENT_SIZE:
                    logging.warning("Content is over MAX_CONTENT_SIZE; truncating")
                    content = content[:MAX_CONTENT_SIZE]

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
        except httpx.HTTPError as e:
            logging.exception("Could not fetch URL: %s", e)
            return None


@mirror_router.get("/warmup")
async def warmup_handler():
    return {"status": "ok"}


@mirror_router.get("/", response_class=HTMLResponse)
async def home_route(request: Request):
    """Handle the home endpoint."""
    user_agent = request.headers.get("user-agent", "")
    if "AppEngine-Google" in user_agent:
        raise HTTPException(status_code=404)

    url = request.query_params.get("url")
    if url:
        try:
            # Parse the URL and create a fiddle
            parsed = urllib.parse.urlparse(url)
            if not parsed.netloc:
                if url.startswith(HTTP_PREFIX):
                    url = url[len(HTTP_PREFIX):]
                parsed = urllib.parse.urlparse(HTTP_PREFIX + url)
            # Create a new fiddle without ndb
            fiddle = Fiddle()
            fiddle.name = f"test-{hashlib.md5(str(time.time()).encode()).hexdigest()[:6]}"
            fiddle.put()
            # Construct the redirect URL with the fiddle name and domain
            redirect_url = f"/{fiddle.name}/{parsed.netloc}"
            if parsed.path and parsed.path != "/":
                redirect_url += parsed.path
            if parsed.query:
                redirect_url += f"?{parsed.query}"
            if parsed.fragment:
                redirect_url += f"#{parsed.fragment}"
            return RedirectResponse(url=redirect_url, status_code=302)
        except Exception as e:
            logging.error(f'Error in home route: {e}')
            raise HTTPException(status_code=500, detail="Internal server error")
    return templates.TemplateResponse("index.html", {"request": request})


add_code = """"""

big_add_code = """<iframe style="min-width:600px;min-height:800px;width:100%;border:none" src="http://www.addictingwordgames.com">
    </iframe>"""

def request_blocker(fiddle_name: str, base_domain: str) -> str:
    """Generate the JavaScript code for URL rewriting and security features."""
    return f"""
<script>
var proxyBase = '/{fiddle_name}/';
var currentDomain = '{base_domain}';

function rewriteUrl(url) {{
    try {{
        // Don't transform data URLs
        if (url.startsWith('data:')) return url;
        
        // Prevent double proxying
        if (url.startsWith(proxyBase)) return url;

        // Handle absolute URLs
        if (/^https?:\\/\\//i.test(url)) {{
            var parser = document.createElement('a');
            parser.href = url;
            return proxyBase + parser.hostname + parser.pathname + (parser.search || '') + (parser.hash || '');
        }}
        
        // Handle protocol-relative URLs
        if (url.startsWith('//')) {{
            var parts = url.substring(2).split('/', 1);
            var domain = parts[0];
            var path = url.substring(2 + domain.length) || '/';
            return proxyBase + domain + path;
        }}
        
        // Handle root-relative URLs
        if (url.startsWith('/')) {{
            return proxyBase + currentDomain + url;
        }}
        
        // Handle relative URLs
        var currentPath = window.location.pathname;
        var basePath = currentPath.substring(0, currentPath.lastIndexOf('/') + 1);
        var resolved = new URL(url, window.location.origin + basePath).pathname;
        return proxyBase + currentDomain + resolved;
    }} catch (e) {{
        console.warn('Error rewriting URL:', url, e);
        return url;
    }}
}}

// Override window.open
const originalWindowOpen = window.open;
window.open = function(url, target, features) {{
    if (url) {{
        url = rewriteUrl(url);
        window.location.href = url;
        return null;
    }}
    return originalWindowOpen.apply(this, arguments);
}};

// Add sandbox attributes to iframes and rewrite their src
const originalCreateElement = document.createElement;
document.createElement = function(tagName, options) {{
    const el = originalCreateElement.call(this, tagName, options);
    if (tagName.toLowerCase() === 'iframe') {{
        el.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups');
        const originalSetAttribute = el.setAttribute;
        el.setAttribute = function(name, value) {{
            if (name.toLowerCase() === 'src') {{
                value = rewriteUrl(value);
            }}
            return originalSetAttribute.call(this, name, value);
        }};
    }}
    return el;
}};

// Initial DOM rewrite
document.addEventListener('DOMContentLoaded', () => {{
    // Rewrite URLs in attributes
    ['href', 'src', 'action'].forEach(attr => {{
        document.querySelectorAll(`[${{attr}}]`).forEach(el => {{
            const value = el.getAttribute(attr);
            if (value) {{
                el.setAttribute(attr, rewriteUrl(value));
            }}
        }});
    }});

    // Add sandbox to existing iframes
    document.querySelectorAll('iframe').forEach(iframe => {{
        iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups');
        const src = iframe.getAttribute('src');
        if (src) {{
            iframe.setAttribute('src', rewriteUrl(src));
        }}
    }});
}});

// Remove CSP meta tags
document.addEventListener('DOMContentLoaded', () => {{
    document.querySelectorAll('meta[http-equiv="Content-Security-Policy"]').forEach(el => el.remove());
}});
</script>
"""


@mirror_router.get("/{fiddle_name}/{path:path}", response_class=HTMLResponse)
async def proxy_route(request: Request, fiddle_name: str, path: str):
    """Handle mirroring requests."""
    try:
        # Check for recursive requests
        user_agent = request.headers.get("user-agent", "")
        if "AppEngine-Google" in user_agent:
            raise HTTPException(status_code=404)
            
        # Validate fiddle name
        if '-' not in fiddle_name:
            raise HTTPException(status_code=400, detail="Invalid fiddle name")
            
        # Handle favicon requests
        if path.endswith("favicon.ico"):
            return RedirectResponse(url="/static/favicon.ico", status_code=302)
            
        # Check for blacklisted URLs
        domain = path.split('/')[0].lower()
        if domain in BLACKLISTED_URLS:
            raise HTTPException(status_code=403, detail="Access to this URL is not allowed")
            
        # Prepare URLs for mirroring
        proxy_base = f"{fiddle_name}/{domain}"
        mirrored_url = HTTP_PREFIX + path
        
        # Fetch content from cache or remote
        key_name = get_url_key_name(mirrored_url)
        content = MirroredContent.get_by_key_name(key_name)
        if content is None:
            content = await MirroredContent.fetch_and_store(key_name, proxy_base, path, mirrored_url)
        if content is None:
            raise HTTPException(status_code=404)
            
        # Prepare response headers
        headers = dict(content.headers)
        if not DEBUG:
            headers["cache-control"] = f"max-age={EXPIRATION_DELTA_SECONDS}"
            
        # Handle HTML content
        if content.headers.get('content-type', '').startswith('text/html'):
            headers.pop('content-length', None)
            headers.pop('content-encoding', None)
            
            # Ensure content is str
            content_str = content.data.decode('utf-8') if isinstance(content.data, bytes) else content.data
            
            # Add request blocker script
            request_blocker = """
<script>
(function() {
    var proxyBase = '/%s/';
    var currentDomain = '%s';
    
    function rewriteUrl(url) {
        if (!url) return url;
        if (url.startsWith('data:')) return url;
        if (url.startsWith(proxyBase)) return url;
        
        try {
            var parser = document.createElement('a');
            parser.href = url;
            
            if (url.startsWith('//')) {
                return proxyBase + url.substring(2);
            }
            
            if (url.startsWith('http://') || url.startsWith('https://')) {
                return proxyBase + parser.host + parser.pathname + (parser.search || '') + (parser.hash || '');
            }
            
            if (url.startsWith('/')) {
                return proxyBase + currentDomain + url;
            }
            
            return proxyBase + currentDomain + '/' + url;
        } catch (e) {
            console.error('Error rewriting URL:', e);
            return url;
        }
    }
    
    // Intercept fetch/XHR requests
    var originalFetch = window.fetch;
    window.fetch = function(url, options) {
        return originalFetch(rewriteUrl(url), options);
    };
    
    var originalXHROpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...args) {
        return originalXHROpen.call(this, method, rewriteUrl(url), ...args);
    };
    
    // Create MutationObserver to rewrite URLs in DOM mutations
    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            mutation.addedNodes.forEach(function(node) {
                if (node.nodeType === 1) {  // ELEMENT_NODE
                    // Handle <a> tags
                    if (node.tagName === 'A' && node.href) {
                        node.href = rewriteUrl(node.href);
                    }
                    
                    // Handle <img> tags
                    if (node.tagName === 'IMG' && node.src) {
                        node.src = rewriteUrl(node.src);
                    }
                    
                    // Handle <iframe> tags
                    if (node.tagName === 'IFRAME') {
                        if (node.src) {
                            node.src = rewriteUrl(node.src);
                        }
                        node.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups');
                    }
                    
                    // Handle <form> tags
                    if (node.tagName === 'FORM' && node.action) {
                        node.action = rewriteUrl(node.action);
                    }
                    
                    // Handle <link> tags
                    if (node.tagName === 'LINK' && node.href) {
                        node.href = rewriteUrl(node.href);
                    }
                    
                    // Handle <script> tags
                    if (node.tagName === 'SCRIPT' && node.src) {
                        node.src = rewriteUrl(node.src);
                    }
                    
                    // Handle inline styles
                    if (node.style && node.style.cssText) {
                        node.style.cssText = node.style.cssText.replace(
                            /url\(['"]?([^'")]+)['"]?\)/g,
                            function(match, url) {
                                return 'url(' + rewriteUrl(url) + ')';
                            }
                        );
                    }
                }
            });
        });
    });
    
    observer.observe(document.documentElement, {
        childList: true,
        subtree: true
    });
    
    // Add iframe content
    var iframeContent = '<div style="position:fixed;bottom:0;right:0;width:300px;height:250px;z-index:9999;"><iframe src="//addictingwordgames.com" width="300" height="250" frameborder="0" scrolling="no" sandbox="allow-scripts allow-same-origin allow-popups"></iframe></div>';
    document.body.insertAdjacentHTML('beforeend', iframeContent);
})();
</script>
""" % (fiddle_name, domain)
            
            # Add request blocker script and transform content
            content_with_script = transform_content(content_str, fiddle_name, domain)
            insert_pos = content_with_script.find('</head>')
            if insert_pos == -1:
                insert_pos = content_with_script.find('<body')
                if insert_pos == -1:
                    insert_pos = 0
            content_with_script = (
                content_with_script[:insert_pos] +
                request_blocker +
                content_with_script[insert_pos:]
            )
            
            # Add fiddle content if available
            fiddle = Fiddle.byUrlKey(fiddle_name)
            if fiddle:
                script = str(fiddle.script) if fiddle.script is not None else ""
                style = str(fiddle.style) if fiddle.style is not None else ""
                extra_js = f'<script id="webfiddle-js">{script}</script>'
                extra_css = f'<style id="webfiddle-css">{style}</style>'
                content_with_script += extra_js + extra_css
                
            return HTMLResponse(content=content_with_script, status_code=content.status, headers=headers)
        else:
            return HTMLResponse(content=content.data, status_code=content.status, headers=headers)
            
    except Exception as e:
        logging.error(f'Error in proxy route: {e}')
        raise HTTPException(status_code=500, detail=str(e))

def create_fiddle() -> str:
    """Create a new fiddle and return its name."""
    fiddle = Fiddle()
    fiddle.put()
    return fiddle.name

def rewrite_url(url: str, fiddle_name: str, current_domain: str, request: Optional[Request] = None) -> str:
    """Rewrite URLs to use the proxy prefix."""
    try:
        # Don't transform data URLs
        if url.startswith('data:'):
            return url
            
        # Prevent double proxying
        if url.startswith(f'/{fiddle_name}/'):
            return url
            
        # Handle absolute URLs
        if re.match(r'^https?://', url):
            parsed = urllib.parse.urlparse(url)
            path_with_query = parsed.path
            if parsed.query:
                path_with_query += '?' + parsed.query
            if parsed.fragment:
                path_with_query += '#' + parsed.fragment
            return f'/{fiddle_name}/{parsed.netloc}{path_with_query}'
            
        # Handle protocol-relative URLs
        if url.startswith('//'):
            parts = url[2:].split('/', 1)
            domain = parts[0]
            path = '/' + parts[1] if len(parts) > 1 else '/'
            return f'/{fiddle_name}/{domain}{path}'
            
        # Handle root-relative URLs
        if url.startswith('/'):
            return f'/{fiddle_name}/{current_domain}{url}'
            
        # Handle relative URLs
        if request:
            current_path = request.url.path
            base_url = f'/{fiddle_name}/{current_domain}'
            if not current_path.startswith(base_url):
                current_path = base_url
            return urllib.parse.urljoin(current_path, url)
        return url
    except Exception as e:
        logging.error(f'Error rewriting URL {url}: {e}')
        return url

@mirror_router.get("/favicon.ico")
async def favicon_route():
    """Handle favicon requests."""
    try:
        return RedirectResponse(url="/static/favicon.ico", status_code=302)
    except Exception as e:
        logging.error(f'Error handling favicon: {e}')
        raise HTTPException(status_code=404, detail="Favicon not found")
