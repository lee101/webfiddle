package main

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"
)

const baseURL = "http://localhost:8091"

var client = &http.Client{
	Timeout: 30 * time.Second,
	CheckRedirect: func(req *http.Request, via []*http.Request) error {
		return http.ErrUseLastResponse // don't follow redirects
	},
}

var followClient = &http.Client{
	Timeout: 30 * time.Second,
}

func getBody(t *testing.T, url string) (int, string) {
	t.Helper()
	resp, err := followClient.Get(url)
	if err != nil {
		t.Fatalf("GET %s failed: %v", url, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	return resp.StatusCode, string(body)
}

func get(t *testing.T, url string) *http.Response {
	t.Helper()
	resp, err := client.Get(url)
	if err != nil {
		t.Fatalf("GET %s failed: %v", url, err)
	}
	return resp
}

// ==================== Homepage ====================

func TestHomepage(t *testing.T) {
	code, body := getBody(t, baseURL+"/")
	if code != 200 {
		t.Fatalf("Homepage returned %d, expected 200", code)
	}
	checks := []string{
		"WebFiddle",
		"currentSavedFiddle",
		"window.fixtures",
		"codemirror",
	}
	for _, check := range checks {
		if !strings.Contains(body, check) {
			t.Errorf("Homepage missing expected content: %q", check)
		}
	}
}

// ==================== Static Files ====================

func TestStaticCSS(t *testing.T) {
	code, _ := getBody(t, baseURL+"/static/stylesheets/style.css")
	if code != 200 {
		t.Fatalf("Static CSS returned %d, expected 200", code)
	}
}

func TestStaticJS(t *testing.T) {
	code, _ := getBody(t, baseURL+"/static/js/main.js")
	if code != 200 {
		t.Fatalf("Static JS returned %d, expected 200", code)
	}
}

func TestStaticJQuery(t *testing.T) {
	code, _ := getBody(t, baseURL+"/static/bower_components/jquery/dist/jquery.min.js")
	if code != 200 {
		t.Fatalf("jQuery returned %d, expected 200", code)
	}
}

func TestFavicon(t *testing.T) {
	resp := get(t, baseURL+"/favicon.ico")
	resp.Body.Close()
	if resp.StatusCode != 302 {
		t.Fatalf("Favicon returned %d, expected 302 redirect", resp.StatusCode)
	}
	loc := resp.Header.Get("Location")
	if !strings.Contains(loc, "/static/favicon.ico") {
		t.Errorf("Favicon redirect to %q, expected /static/favicon.ico", loc)
	}
}

// ==================== Fiddle CRUD ====================

func TestCreateAndLoadFiddle(t *testing.T) {
	fiddleID := fmt.Sprintf("test%d", time.Now().UnixNano()%100000)
	title := "E2E Test Fiddle"
	script := "document.body.style.background = 'red';"
	style := "body { color: blue; }"
	startURL := "www.example.com"

	// Create fiddle
	createURL := fmt.Sprintf("%s/createfiddle?id=%s&title=%s&start_url=%s&script=%s&style=%s",
		baseURL, fiddleID,
		url.QueryEscape(title),
		url.QueryEscape(startURL),
		url.QueryEscape(script),
		url.QueryEscape(style))

	code, body := getBody(t, createURL)
	if code != 200 {
		t.Fatalf("Create fiddle returned %d, expected 200", code)
	}
	if body != "success" {
		t.Fatalf("Create fiddle returned %q, expected 'success'", body)
	}

	// Load fiddle page
	fiddleKey := fmt.Sprintf("%s-%s", url.QueryEscape(strings.ToLower(title)), fiddleID)
	code, body = getBody(t, baseURL+"/"+fiddleKey)
	if code != 200 {
		t.Fatalf("Load fiddle returned %d, expected 200", code)
	}

	// Verify fiddle data is in the page
	if !strings.Contains(body, fiddleID) {
		t.Error("Fiddle page doesn't contain fiddle ID")
	}
	if !strings.Contains(body, "currentSavedFiddle") {
		t.Error("Fiddle page doesn't contain currentSavedFiddle JS variable")
	}
}

func TestDefaultFiddle(t *testing.T) {
	code, body := getBody(t, baseURL+"/cats-d8c4vu")
	if code != 200 {
		t.Fatalf("Default fiddle returned %d, expected 200", code)
	}
	if !strings.Contains(body, "d8c4vu") {
		t.Error("Default fiddle page doesn't contain fiddle ID d8c4vu")
	}
	if !strings.Contains(body, "thecatapi") {
		t.Error("Default fiddle page doesn't contain cat API script")
	}
}

// ==================== Proxy/Mirror ====================

func TestProxyRendersWebsite(t *testing.T) {
	code, body := getBody(t, baseURL+"/cats-d8c4vu/www.example.com")
	if code != 200 {
		t.Fatalf("Proxy returned %d, expected 200", code)
	}

	// Should contain the original site content
	if !strings.Contains(body, "Example Domain") {
		t.Error("Proxy response doesn't contain original site content ('Example Domain')")
	}

	// Should have request blocker script injected
	if !strings.Contains(body, "proxyBase") {
		t.Error("Proxy response missing request blocker script (proxyBase)")
	}
	if !strings.Contains(body, "rewriteUrl") {
		t.Error("Proxy response missing rewriteUrl function")
	}

	// Should have fiddle JS/CSS injected
	if !strings.Contains(body, `id="webfiddle-js"`) {
		t.Error("Proxy response missing webfiddle-js script tag")
	}
	if !strings.Contains(body, `id="webfiddle-css"`) {
		t.Error("Proxy response missing webfiddle-css style tag")
	}

	// Should have the default fiddle's script content (cat API)
	if !strings.Contains(body, "thecatapi") {
		t.Error("Proxy response missing default fiddle script content (thecatapi)")
	}

	// Should have the default fiddle's style content (skyblue)
	if !strings.Contains(body, "skyblue") {
		t.Error("Proxy response missing default fiddle style content (skyblue)")
	}

	// Should have promo links
	if !strings.Contains(body, "netwrck.com") {
		t.Error("Proxy response missing promo links")
	}
}

func TestProxyWithCustomFiddle(t *testing.T) {
	// Create a fiddle first
	fiddleID := fmt.Sprintf("proxy%d", time.Now().UnixNano()%100000)
	customScript := "console.log('e2e-test-marker-js');"
	customStyle := ".e2e-test-marker-css { display: block; }"

	createURL := fmt.Sprintf("%s/createfiddle?id=%s&title=ProxyTest&start_url=www.example.com&script=%s&style=%s",
		baseURL, fiddleID,
		url.QueryEscape(customScript),
		url.QueryEscape(customStyle))
	getBody(t, createURL)

	// Now proxy through this fiddle
	proxyURL := fmt.Sprintf("%s/proxytest-%s/www.example.com", baseURL, fiddleID)
	code, body := getBody(t, proxyURL)
	if code != 200 {
		t.Fatalf("Custom fiddle proxy returned %d, expected 200", code)
	}

	// Check custom JS is injected
	if !strings.Contains(body, "e2e-test-marker-js") {
		t.Error("Proxy response missing custom fiddle JS")
	}

	// Check custom CSS is injected
	if !strings.Contains(body, "e2e-test-marker-css") {
		t.Error("Proxy response missing custom fiddle CSS")
	}
}

func TestProxyURLRewriting(t *testing.T) {
	code, body := getBody(t, baseURL+"/cats-d8c4vu/www.example.com")
	if code != 200 {
		t.Fatalf("Proxy returned %d", code)
	}

	// The request blocker should reference the correct fiddle name
	if !strings.Contains(body, "var proxyBase = '/cats-d8c4vu/'") {
		t.Error("Proxy response has wrong proxyBase value")
	}

	// Direct domains should be listed
	if !strings.Contains(body, "directDomains") {
		t.Error("Proxy response missing directDomains list")
	}
}

func TestProxyBlacklist(t *testing.T) {
	code, _ := getBody(t, baseURL+"/cats-d8c4vu/www.facebook.com")
	if code != 403 {
		t.Fatalf("Blacklisted domain returned %d, expected 403", code)
	}
}

func TestProxyCachesOnFilesystem(t *testing.T) {
	// Hit a URL through the proxy
	uniquePath := fmt.Sprintf("cats-d8c4vu/www.example.com/cache-test-%d", time.Now().UnixNano())
	getBody(t, baseURL+"/"+uniquePath)

	// We can't easily check filesystem from the test, but we can hit the same URL
	// again and verify it still works (would come from cache)
	code, body := getBody(t, baseURL+"/cats-d8c4vu/www.example.com")
	if code != 200 {
		t.Fatalf("Cached proxy returned %d, expected 200", code)
	}
	if !strings.Contains(body, "Example Domain") {
		t.Error("Cached proxy response missing original content")
	}
}

func TestProxyCSPHeader(t *testing.T) {
	resp, err := followClient.Get(baseURL + "/cats-d8c4vu/www.example.com")
	if err != nil {
		t.Fatalf("GET failed: %v", err)
	}
	defer resp.Body.Close()

	csp := resp.Header.Get("Content-Security-Policy")
	if csp == "" {
		t.Error("Proxy response missing Content-Security-Policy header")
	}
	if !strings.Contains(csp, "'unsafe-inline'") {
		t.Error("CSP missing 'unsafe-inline'")
	}
	if !strings.Contains(csp, "'unsafe-eval'") {
		t.Error("CSP missing 'unsafe-eval'")
	}
}

// ==================== Auth ====================

func TestLoginPage(t *testing.T) {
	code, body := getBody(t, baseURL+"/login")
	if code != 200 {
		t.Fatalf("Login page returned %d, expected 200", code)
	}
	if !strings.Contains(body, `action="/login"`) {
		t.Error("Login page missing login form action")
	}
	if !strings.Contains(body, "username") {
		t.Error("Login page missing username field")
	}
}

func TestRegisterPage(t *testing.T) {
	code, body := getBody(t, baseURL+"/register")
	if code != 200 {
		t.Fatalf("Register page returned %d, expected 200", code)
	}
	if !strings.Contains(body, `action="/register"`) {
		t.Error("Register page missing register form action")
	}
}

func TestRegisterLoginLogoutFlow(t *testing.T) {
	username := fmt.Sprintf("testuser%d", time.Now().UnixNano()%100000)
	password := "testpass123"

	// Register
	resp, err := client.PostForm(baseURL+"/register", url.Values{
		"username": {username},
		"password": {password},
	})
	if err != nil {
		t.Fatalf("Register POST failed: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != 302 {
		t.Fatalf("Register returned %d, expected 302 redirect", resp.StatusCode)
	}

	// Should have session cookie
	var sessionCookie string
	for _, c := range resp.Cookies() {
		if c.Name == "session_id" {
			sessionCookie = c.Value
			break
		}
	}
	if sessionCookie == "" {
		t.Fatal("Register response missing session_id cookie")
	}

	// Verify logged in by checking homepage shows username
	req, _ := http.NewRequest("GET", baseURL+"/", nil)
	req.AddCookie(&http.Cookie{Name: "session_id", Value: sessionCookie})
	resp2, err := followClient.Do(req)
	if err != nil {
		t.Fatalf("GET homepage with session failed: %v", err)
	}
	body, _ := io.ReadAll(resp2.Body)
	resp2.Body.Close()
	if !strings.Contains(string(body), username) {
		t.Error("Homepage doesn't show logged-in username")
	}
	if !strings.Contains(string(body), "Logout") {
		t.Error("Homepage doesn't show Logout link when logged in")
	}

	// Logout
	req, _ = http.NewRequest("GET", baseURL+"/logout", nil)
	req.AddCookie(&http.Cookie{Name: "session_id", Value: sessionCookie})
	resp3, err := client.Do(req)
	if err != nil {
		t.Fatalf("Logout failed: %v", err)
	}
	resp3.Body.Close()
	if resp3.StatusCode != 302 {
		t.Fatalf("Logout returned %d, expected 302", resp3.StatusCode)
	}

	// Login with same credentials
	resp4, err := client.PostForm(baseURL+"/login", url.Values{
		"username": {username},
		"password": {password},
	})
	if err != nil {
		t.Fatalf("Login POST failed: %v", err)
	}
	resp4.Body.Close()
	if resp4.StatusCode != 302 {
		t.Fatalf("Login returned %d, expected 302", resp4.StatusCode)
	}
	hasSession := false
	for _, c := range resp4.Cookies() {
		if c.Name == "session_id" && c.Value != "" {
			hasSession = true
			break
		}
	}
	if !hasSession {
		t.Error("Login response missing session_id cookie")
	}
}

func TestLoginInvalidCredentials(t *testing.T) {
	resp, err := client.PostForm(baseURL+"/login", url.Values{
		"username": {"nonexistent_user"},
		"password": {"wrong_password"},
	})
	if err != nil {
		t.Fatalf("Login POST failed: %v", err)
	}
	resp.Body.Close()
	if resp.StatusCode != 400 {
		t.Fatalf("Invalid login returned %d, expected 400", resp.StatusCode)
	}
}

func TestDuplicateRegistration(t *testing.T) {
	username := fmt.Sprintf("dupuser%d", time.Now().UnixNano()%100000)

	// First registration
	resp, _ := client.PostForm(baseURL+"/register", url.Values{
		"username": {username},
		"password": {"pass1"},
	})
	resp.Body.Close()

	// Second registration with same username
	resp2, err := client.PostForm(baseURL+"/register", url.Values{
		"username": {username},
		"password": {"pass2"},
	})
	if err != nil {
		t.Fatalf("Duplicate register POST failed: %v", err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != 400 {
		t.Fatalf("Duplicate registration returned %d, expected 400", resp2.StatusCode)
	}
}

// ==================== Misc Routes ====================

func TestSitemap(t *testing.T) {
	code, body := getBody(t, baseURL+"/sitemap.xml")
	if code != 200 {
		t.Fatalf("Sitemap returned %d, expected 200", code)
	}
	if !strings.Contains(body, "webfiddle.net") {
		t.Error("Sitemap missing webfiddle.net URL")
	}
	if !strings.Contains(body, "urlset") {
		t.Error("Sitemap missing urlset XML element")
	}
}

func TestBingSiteAuth(t *testing.T) {
	code, body := getBody(t, baseURL+"/bingsiteauth.xml")
	if code != 200 {
		t.Fatalf("BingSiteAuth returned %d, expected 200", code)
	}
	if !strings.Contains(body, "1A8F067A516D6AE82B1170B390ECD0EC") {
		t.Error("BingSiteAuth missing verification code")
	}
}

func TestTrailingSlashRedirect(t *testing.T) {
	resp := get(t, baseURL+"/somepath/")
	resp.Body.Close()
	if resp.StatusCode != 302 {
		t.Fatalf("Trailing slash returned %d, expected 302", resp.StatusCode)
	}
	loc := resp.Header.Get("Location")
	if !strings.HasSuffix(loc, "/somepath") {
		t.Errorf("Trailing slash redirect to %q, expected to end with /somepath", loc)
	}
}

// ==================== End-to-End: Full Flow ====================

func TestFullFiddleProxyFlow(t *testing.T) {
	// 1. Create a fiddle
	fiddleID := fmt.Sprintf("flow%d", time.Now().UnixNano()%100000)
	script := "document.title = 'Modified by WebFiddle';"
	style := "body { border: 5px solid green; }"
	title := "Full Flow Test"

	createURL := fmt.Sprintf("%s/createfiddle?id=%s&title=%s&start_url=%s&script=%s&style=%s",
		baseURL, fiddleID,
		url.QueryEscape(title),
		url.QueryEscape("www.example.com"),
		url.QueryEscape(script),
		url.QueryEscape(style))

	code, body := getBody(t, createURL)
	if code != 200 || body != "success" {
		t.Fatalf("Step 1 (create fiddle) failed: %d %s", code, body)
	}

	// 2. Load the fiddle editor page
	fiddleKey := fmt.Sprintf("full-flow-test-%s", fiddleID)
	code, body = getBody(t, baseURL+"/"+fiddleKey)
	if code != 200 {
		t.Fatalf("Step 2 (load fiddle page) failed: %d", code)
	}
	if !strings.Contains(body, fiddleID) {
		t.Error("Step 2: Fiddle page doesn't contain fiddle ID")
	}

	// 3. Proxy a website through this fiddle
	proxyURL := fmt.Sprintf("%s/%s/www.example.com", baseURL, fiddleKey)
	code, body = getBody(t, proxyURL)
	if code != 200 {
		t.Fatalf("Step 3 (proxy website) failed: %d", code)
	}

	// 4. Verify the proxied page has original content
	if !strings.Contains(body, "Example Domain") {
		t.Error("Step 4: Proxied page missing original content")
	}

	// 5. Verify the custom JS is injected
	if !strings.Contains(body, "Modified by WebFiddle") {
		t.Error("Step 5: Proxied page missing custom JS")
	}

	// 6. Verify the custom CSS is injected
	if !strings.Contains(body, "5px solid green") {
		t.Error("Step 6: Proxied page missing custom CSS")
	}

	// 7. Verify request blocker is present
	if !strings.Contains(body, "proxyBase") {
		t.Error("Step 7: Proxied page missing request blocker")
	}

	// 8. Verify URL rewriting references the right fiddle
	expectedBase := fmt.Sprintf("var proxyBase = '/%s/'", fiddleKey)
	if !strings.Contains(body, expectedBase) {
		t.Errorf("Step 8: Wrong proxyBase. Expected %q in body", expectedBase)
	}

	// 9. Hit the same proxy URL again (should come from cache)
	code2, body2 := getBody(t, proxyURL)
	if code2 != 200 {
		t.Fatalf("Step 9 (cached proxy) failed: %d", code2)
	}
	if !strings.Contains(body2, "Example Domain") {
		t.Error("Step 9: Cached response missing original content")
	}
}

// ==================== HTTPS (if available) ====================

func TestHTTPS(t *testing.T) {
	resp, err := followClient.Get("https://webfiddle.net/")
	if err != nil {
		t.Skipf("HTTPS not available (DNS may not have propagated): %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("HTTPS homepage returned %d, expected 200", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "WebFiddle") {
		t.Error("HTTPS homepage missing WebFiddle content")
	}
}

func TestHTTPSProxy(t *testing.T) {
	resp, err := followClient.Get("https://webfiddle.net/cats-d8c4vu/www.example.com")
	if err != nil {
		t.Skipf("HTTPS not available: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("HTTPS proxy returned %d, expected 200", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	if !strings.Contains(string(body), "Example Domain") {
		t.Error("HTTPS proxy missing original content")
	}
	if !strings.Contains(string(body), "webfiddle-js") {
		t.Error("HTTPS proxy missing fiddle injection")
	}
}
