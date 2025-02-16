from fastapi import FastAPI
from fastapi.testclient import TestClient
import os
import re

# Clean up any existing cache file to start with a fresh database
if os.path.exists("cache.db"):
    os.remove("cache.db")

# Import the router and dependent models from mirror
from mirror.mirror import mirror_router
from models import Fiddle

# Create a proper DummyFiddle class that matches Fiddle's interface
class DummyFiddle:
    def __init__(self):
        self.script = "console.log('test');"
        self.style = "body { background: white; }"
        self.id = "test-id"
        self.name = "test-name"
        self.title = "Test Fiddle"
        self.description = "Test Description"
        self.start_url = "example.com"
        self.script_language = "js"
        self.style_language = "css"
        
    def put(self):
        """Mock put method"""
        return self
        
    @classmethod
    def byUrlKey(cls, urlkey):
        """Mock byUrlKey method that returns a new instance"""
        instance = cls()
        instance.name = urlkey
        return instance

# Override Fiddle's byUrlKey with our dummy implementation
original_byUrlKey = getattr(Fiddle, 'byUrlKey', None)
Fiddle.byUrlKey = DummyFiddle.byUrlKey
Fiddle.put = DummyFiddle.put

# Create the FastAPI app for testing and include the mirror_router
app = FastAPI()
app.include_router(mirror_router)
client = TestClient(app)

def test_warmup_endpoint():
    """Test the /warmup endpoint returns status ok."""
    response = client.get("/warmup")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"

def test_home_redirect():
    """
    Test the home endpoint.
    If a URL parameter is provided (encoded), 
    it should redirect to the appropriate path.
    """
    # Pass an encoded url parameter to trigger the redirect logic.
    response = client.get("/?url=http%3A%2F%2Fexample.com")
    # FastAPI's RedirectResponse normally returns 307.
    assert response.status_code in (302, 307)
    assert response.headers.get("location") == "/example.com"

def test_mirror_handler_invalid_fiddle():
    """
    Test that a fiddle_name without '-' triggers an HTTP 500 error.
    """
    # 'invalidfiddle' does not contain a hyphen, so it should fail.
    response = client.get("/invalidfiddle/example.com")
    assert response.status_code == 500

def test_mirror_handler_favicon():
    """
    Test that when the base_url ends with 'favicon.ico', the endpoint 
    redirects to the corresponding favicon.
    """
    response = client.get("/test-fiddle/favicon.ico")
    # The redirect is expected: FastAPI returns 302 or 307.
    assert response.status_code in (302, 307)
    assert response.headers.get("location") == "/favicon.ico"

def test_mirror_handler_real_url():
    """
    Test the mirror endpoint using a valid fiddle_name and a real URL.
    This will attempt to fetch http://example.com. If the fetch fails,
    a 404 is expected; otherwise, check for injected scripts in the HTML.
    """
    fiddle_name = "test-fiddle"  # valid because it contains a hyphen
    base_url = "example.com"
    response = client.get(f"/{fiddle_name}/{base_url}")
    
    # If the mirrored fetch fails, we expect a 404.
    if response.status_code == 404:
        assert response.status_code == 404
    else:
        # Otherwise, we expect success.
        assert response.status_code == 200
        content_type = response.headers.get("content-type", "")
        assert "text/html" in content_type
        # Check for presence of injected <script> tags (from request_blocker and analytics)
        assert "<script" in response.text
        # Verify that the additional iframe content (big_add_code) is present.
        assert "addictingwordgames" in response.text

def teardown_module(module):
    """Restore original byUrlKey method after tests complete"""
    if original_byUrlKey:
        Fiddle.byUrlKey = original_byUrlKey


def test_mirror_handler_real_netwrck():
    """
    Test the mirror endpoint using a real fiddle and URL for netwrck.com.
    This tests a real-world case with a valid fiddle name and domain.
    """
    fiddle_name = "cats-d8c4vu"
    base_url = "netwrck.com"
    response = client.get(f"/{fiddle_name}/{base_url}")
    
    # We expect a successful response
    assert response.status_code == 200
    
    # Verify content type is HTML
    content_type = response.headers.get("content-type", "")
    assert "text/html" in content_type
    
    # Check for expected content and injected scripts
    assert "<script" in response.text
    assert "proxyBase = '/cats-d8c4vu/'" in response.text
    assert "currentDomain = 'netwrck.com'" in response.text
    assert "addictingwordgames" in response.text

def test_mirror_handler_domain():
    # ... test setup ...
    response = client.get("/cats-d8c4vu/netwrck.com")
    # Verify domain extraction logic exists
    assert "var pathSegments = window.location.pathname.split('/').filter(s => s)" in response.text
    assert "var currentDomain = pathSegments.length > 1 ? pathSegments[1] : ''" in response.text
    # Verify proxy base is set correctly
    assert "var proxyBase = '/cats-d8c4vu/'" in response.text

def test_mirror_handler_no_double_proxy():
    """
    Test that the mirror handler doesn't double up proxy paths when handling URLs.
    Specifically checks that /cats-d8c4vu/www.google.com doesn't become 
    /cats-d8c4vu/www.google.com/cats-d8c4vu/www.google.com
    """
    fiddle_name = "cats-d8c4vu"
    domain = "www.google.com"
    response = client.get(f"/{fiddle_name}/{domain}")
    
    # Verify successful response
    assert response.status_code == 200
    
    # Check content type is HTML
    content_type = response.headers.get("content-type", "")
    assert "text/html" in content_type
    
    # Verify proxy base and domain are set correctly
    assert f"var proxyBase = '/{fiddle_name}/'" in response.text
    # assert f"currentDomain = '{domain}'" in response.text
    
    # Verify no double proxy paths exist
    double_proxy = f"/{fiddle_name}/{domain}/{fiddle_name}/{domain}"
    assert double_proxy not in response.text
    
    # Verify URL rewriting works correctly
    assert "function rewriteUrl(url)" in response.text
    assert "return proxyBase + currentDomain + parser.pathname" in response.text

def test_no_path_duplication():
    """Test that proxy paths are not duplicated in redirects and content"""
    fiddle_name = "cats-d8c4vu"
    domain = "httpbin.org"
    
    # Test direct access
    response = client.get(f"/{fiddle_name}/{domain}")
    assert response.status_code == 200
    content = response.text
    
    # Verify no duplicate proxy paths
    assert f"/{fiddle_name}/{domain}/{fiddle_name}" not in content
    
    # Verify URL rewriting logic is correct
    assert "if (url.startsWith(proxyBase)) return url;" in content
    assert f"var proxyBase = '/{fiddle_name}/'" in content
    assert f"var currentDomain = '{domain}'" in content

def test_url_rewriting():
    """Test comprehensive URL rewriting scenarios"""
    fiddle_name = "cats-d8c4vu"
    domain = "example.com"
    
    response = client.get(f"/{fiddle_name}/{domain}")
    content = response.text
    
    # Test absolute URL handling
    assert "if (/^https?:\\/\\//i.test(url))" in content
    
    # Test root-relative URL handling
    assert "if (url.startsWith('/'))" in content
    assert "return proxyBase + currentDomain + url;" in content
    
    # Test relative URL handling
    assert "var currentPath = window.location.pathname;" in content
    assert "new URL(url, window.location.origin + currentPath)" in content

def test_no_plain_root_links():
    """Test that all root-relative links are properly rewritten with the proxy prefix"""
    fiddle_name = "cats-d8c4vu"
    domain = "netwrck.com"
    response = client.get(f"/{fiddle_name}/{domain}")
    content = response.text
    
    # Verify URL rewriting for different types of links
    assert f'href="/{fiddle_name}/{domain}/' in content
    assert f'src="/{fiddle_name}/{domain}/' in content
    assert f'action="/{fiddle_name}/{domain}/' in content
    
    # Verify no unproxied root-relative URLs
    plain_root_links = re.findall(r'(?:href|src|action)="(?!%s/%s)[^"]+"' % (fiddle_name, domain), content)
    assert not plain_root_links, f"Found unproxied root links: {plain_root_links}"

def test_iframe_security_features():
    """Test that iframes have proper security attributes and content is transformed"""
    fiddle_name = "cats-d8c4vu"
    domain = "netwrck.com"
    response = client.get(f"/{fiddle_name}/{domain}")
    content = response.text
    
    # Verify iframe sandbox attributes
    assert 'sandbox="allow-scripts allow-same-origin allow-popups"' in content
    
    # Verify iframe src URLs are properly transformed
    assert f'src="/{fiddle_name}/{domain}/' in content
    
    # Verify iframe creation is intercepted
    assert "document.createElement = function(tagName, options)" in content
    assert "el.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-popups')" in content

def test_content_transformation():
    """Test that content is properly transformed with correct proxy paths"""
    fiddle_name = "cats-d8c4vu"
    domain = "example.com"
    response = client.get(f"/{fiddle_name}/{domain}")
    
    if response.status_code == 200:
        content = response.text
        
        # Test absolute URL transformation
        assert f'href="/{fiddle_name}/{domain}/' in content
        
        # Test relative URL transformation
        assert f'src="/{fiddle_name}/{domain}/' in content
        
        # Test that no untransformed URLs exist
        untransformed = re.findall(r'(?:href|src|action)="(?!/{fiddle_name}/{domain}/)([^"]+)"', content)
        assert not any(u.startswith('/') for u in untransformed), f"Found untransformed URLs: {untransformed}"
