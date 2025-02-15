from fastapi import FastAPI
from fastapi.testclient import TestClient
import os

# Clean up any existing cache file to start with a fresh database
if os.path.exists("cache.db"):
    os.remove("cache.db")

# Import the router and dependent models from mirror
from mirror.mirror import mirror_router
from models import Fiddle

# Override Fiddle.byUrlKey with a dummy implementation so that the mirror handler works.
class DummyFiddle:
    script = ""
    style = ""

# This ensures that when mirror_handler calls Fiddle.byUrlKey, it gets a dummy instance.
Fiddle.byUrlKey = lambda fiddle_name: DummyFiddle()

# Create the FastAPI app for testing and include the mirror_router.
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
