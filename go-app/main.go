package main

import (
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/flosch/pongo2/v6"
	_ "github.com/mattn/go-sqlite3"
	"github.com/valyala/fasthttp"
	"golang.org/x/crypto/bcrypt"
)

// ==================== Config ====================

type Config struct {
	Port                  string
	DataDir               string
	CacheDir              string
	Debug                 bool
	Domain                string
	MaxDiskGB             float64 // 4TB cap
	StaticURL             string
	SecretKey             string
	Concurrency           int
	OutboundConcurrency   int
	CacheWriteConcurrency int
}

func loadConfig() *Config {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8000"
	}
	dataDir := os.Getenv("DATA_DIR")
	if dataDir == "" {
		dataDir = "."
	}
	cacheDir := os.Getenv("CACHE_DIR")
	if cacheDir == "" {
		cacheDir = "cache"
	}
	domain := os.Getenv("DOMAIN")
	if domain == "" {
		domain = "webfiddle.net"
	}
	staticURL := "/static"
	return &Config{
		Port:                  port,
		DataDir:               dataDir,
		CacheDir:              cacheDir,
		Debug:                 os.Getenv("IS_DEVELOP") == "1",
		Domain:                domain,
		MaxDiskGB:             4096, // 4TB cap
		StaticURL:             staticURL,
		SecretKey:             getEnvOrDefault("SECRET_KEY", "changeme-webfiddle-secret"),
		Concurrency:           getEnvInt("WEBFIDDLE_CONCURRENCY", 256),
		OutboundConcurrency:   getEnvInt("WEBFIDDLE_OUTBOUND_CONCURRENCY", 64),
		CacheWriteConcurrency: getEnvInt("WEBFIDDLE_CACHE_WRITE_CONCURRENCY", 8),
	}
}

func getEnvOrDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func getEnvInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil && n > 0 {
			return n
		}
	}
	return def
}

func configureRuntime() {
	if os.Getenv("GOMAXPROCS") == "" {
		runtime.GOMAXPROCS(getEnvInt("WEBFIDDLE_GOMAXPROCS", 4))
	}
	log.Printf("GOMAXPROCS=%d", runtime.GOMAXPROCS(0))
}

// ==================== Fixtures ====================

var SCRIPT_TYPES = map[string]int{
	"js":         0,
	"javascript": 0,
	"coffee":     1,
}

var STYLE_TYPES = map[string]int{
	"css":  0,
	"less": 1,
	"sass": 2,
}

// ==================== Blacklist ====================

var BLACKLISTED_DOMAINS = map[string]bool{
	"www.ebay.com":     true,
	"signin.ebay.com":  true,
	"cgi4.ebay.com":    true,
	"my.ebay.com":      true,
	"reg.ebay.com":     true,
	"ocsnext.ebay.com": true,
	"pages.ebay.com":   true,
	"www.facebook.com": true,
	"www.linkedin.com": true,
}

// ==================== Fiddle ====================

type Fiddle struct {
	ID             string `json:"id"`
	Title          string `json:"title"`
	Description    string `json:"description"`
	StartURL       string `json:"start_url"`
	Script         string `json:"script"`
	Style          string `json:"style"`
	ScriptLanguage int    `json:"script_language"`
	StyleLanguage  int    `json:"style_language"`
}

var defaultFiddle = Fiddle{
	ID:             "d8c4vu",
	Title:          "cats",
	Description:    "cats via the cat api",
	StartURL:       "www.google.com",
	ScriptLanguage: 0,
	StyleLanguage:  0,
	Script: `// replace the first image we see with a cat
document.images[0].src = 'http://thecatapi.com/api/images/get?format=src&type=gif';

// replace the google logo with a cat
document.getElementById('lga').innerHTML = '<a href="http://thecatapi.com"><img src="http://thecatapi.com/api/images/get?format=src&type=gif"></a>';
`,
	Style: "body {\n    background-color: skyblue;\n}\n",
}

// ==================== Database ====================

type Database struct {
	db *sql.DB
	mu sync.RWMutex
}

func NewDatabase(dataDir string) (*Database, error) {
	dbPath := filepath.Join(dataDir, "webfiddle.db")
	db, err := sql.Open("sqlite3", dbPath+"?_journal_mode=WAL&_busy_timeout=5000")
	if err != nil {
		return nil, err
	}
	d := &Database{db: db}
	if err := d.init(); err != nil {
		return nil, err
	}
	return d, nil
}

func (d *Database) init() error {
	_, err := d.db.Exec(`
		CREATE TABLE IF NOT EXISTS fiddles (
			id TEXT PRIMARY KEY,
			title TEXT,
			description TEXT,
			start_url TEXT,
			script TEXT,
			style TEXT,
			script_language INTEGER DEFAULT 0,
			style_language INTEGER DEFAULT 0,
			created TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
			updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
		);
		CREATE TABLE IF NOT EXISTS users (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			username TEXT UNIQUE NOT NULL,
			password_hash TEXT NOT NULL
		);
		CREATE TABLE IF NOT EXISTS cache_meta (
			key_name TEXT PRIMARY KEY,
			original_url TEXT,
			translated_address TEXT,
			status_code INTEGER,
			headers TEXT,
			content_type TEXT,
			file_path TEXT,
			base_url TEXT,
			size INTEGER DEFAULT 0,
			expiry INTEGER
		);
		CREATE TABLE IF NOT EXISTS sessions (
			id TEXT PRIMARY KEY,
			username TEXT NOT NULL,
			expires_at TIMESTAMP NOT NULL
		);
	`)
	return err
}

func (d *Database) SaveFiddle(f *Fiddle) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	_, err := d.db.Exec(`
		INSERT OR REPLACE INTO fiddles (id, title, description, start_url, script, style, script_language, style_language, updated)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)`,
		f.ID, f.Title, f.Description, f.StartURL, f.Script, f.Style, f.ScriptLanguage, f.StyleLanguage)
	return err
}

func (d *Database) GetFiddleByID(id string) *Fiddle {
	d.mu.RLock()
	defer d.mu.RUnlock()
	row := d.db.QueryRow("SELECT id, title, description, start_url, script, style, script_language, style_language FROM fiddles WHERE id=?", id)
	f := &Fiddle{}
	err := row.Scan(&f.ID, &f.Title, &f.Description, &f.StartURL, &f.Script, &f.Style, &f.ScriptLanguage, &f.StyleLanguage)
	if err != nil {
		return nil
	}
	return f
}

func (d *Database) GetFiddleByURLKey(urlkey string) *Fiddle {
	if urlkey == "" || strings.HasSuffix(urlkey, "d8c4vu") {
		return &defaultFiddle
	}
	pos := strings.LastIndex(urlkey, "-")
	if pos == -1 {
		return nil
	}
	fid := urlkey[pos+1:]
	return d.GetFiddleByID(fid)
}

// User auth
func (d *Database) CreateUser(username, password string) error {
	hash, err := bcrypt.GenerateFromPassword([]byte(password), bcrypt.DefaultCost)
	if err != nil {
		return err
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	_, err = d.db.Exec("INSERT INTO users (username, password_hash) VALUES (?, ?)", username, string(hash))
	return err
}

func (d *Database) CheckUser(username, password string) bool {
	d.mu.RLock()
	defer d.mu.RUnlock()
	var hash string
	err := d.db.QueryRow("SELECT password_hash FROM users WHERE username=?", username).Scan(&hash)
	if err != nil {
		return false
	}
	return bcrypt.CompareHashAndPassword([]byte(hash), []byte(password)) == nil
}

// Sessions
func (d *Database) CreateSession(username string) (string, error) {
	id := randomString(32)
	expires := time.Now().Add(30 * 24 * time.Hour)
	d.mu.Lock()
	defer d.mu.Unlock()
	_, err := d.db.Exec("INSERT INTO sessions (id, username, expires_at) VALUES (?, ?, ?)", id, username, expires)
	return id, err
}

func (d *Database) GetSession(id string) (string, bool) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	var username string
	var expiresAt time.Time
	err := d.db.QueryRow("SELECT username, expires_at FROM sessions WHERE id=?", id).Scan(&username, &expiresAt)
	if err != nil || time.Now().After(expiresAt) {
		return "", false
	}
	return username, true
}

func (d *Database) DeleteSession(id string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.db.Exec("DELETE FROM sessions WHERE id=?", id)
}

// Cache metadata
func (d *Database) GetCacheMeta(keyName string) (*CacheMeta, error) {
	d.mu.RLock()
	defer d.mu.RUnlock()
	row := d.db.QueryRow("SELECT key_name, original_url, translated_address, status_code, headers, content_type, file_path, base_url, size, expiry FROM cache_meta WHERE key_name=?", keyName)
	m := &CacheMeta{}
	err := row.Scan(&m.KeyName, &m.OriginalURL, &m.TranslatedAddress, &m.StatusCode, &m.HeadersJSON, &m.ContentType, &m.FilePath, &m.BaseURL, &m.Size, &m.Expiry)
	if err != nil {
		return nil, err
	}
	if time.Now().Unix() > m.Expiry {
		go d.DeleteCacheMeta(keyName)
		return nil, fmt.Errorf("expired")
	}
	return m, nil
}

func (d *Database) SaveCacheMeta(m *CacheMeta) error {
	d.mu.Lock()
	defer d.mu.Unlock()
	_, err := d.db.Exec(`
		INSERT OR REPLACE INTO cache_meta (key_name, original_url, translated_address, status_code, headers, content_type, file_path, base_url, size, expiry)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		m.KeyName, m.OriginalURL, m.TranslatedAddress, m.StatusCode, m.HeadersJSON, m.ContentType, m.FilePath, m.BaseURL, m.Size, m.Expiry)
	return err
}

func (d *Database) DeleteCacheMeta(keyName string) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.db.Exec("DELETE FROM cache_meta WHERE key_name=?", keyName)
}

func (d *Database) GetTotalCacheSize() int64 {
	d.mu.RLock()
	defer d.mu.RUnlock()
	var total sql.NullInt64
	d.db.QueryRow("SELECT SUM(size) FROM cache_meta").Scan(&total)
	return total.Int64
}

func (d *Database) EvictOldCache(cutoffTime int64) (int64, error) {
	d.mu.Lock()
	defer d.mu.Unlock()
	result, err := d.db.Exec("DELETE FROM cache_meta WHERE expiry < ?", cutoffTime)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

func (d *Database) GetExpiredCacheFiles(cutoffTime int64) []string {
	d.mu.RLock()
	defer d.mu.RUnlock()
	rows, err := d.db.Query("SELECT file_path FROM cache_meta WHERE expiry < ?", cutoffTime)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var files []string
	for rows.Next() {
		var fp string
		rows.Scan(&fp)
		files = append(files, fp)
	}
	return files
}

type CacheMeta struct {
	KeyName           string
	OriginalURL       string
	TranslatedAddress string
	StatusCode        int
	HeadersJSON       string
	ContentType       string
	FilePath          string
	BaseURL           string
	Size              int64
	Expiry            int64
}

func (m *CacheMeta) Headers() map[string]string {
	var h map[string]string
	json.Unmarshal([]byte(m.HeadersJSON), &h)
	if h == nil {
		h = make(map[string]string)
	}
	return h
}

// ==================== Cache Manager ====================

type CacheManager struct {
	dir      string
	db       *Database
	maxBytes int64 // 4TB = 4 * 1024^4
	mu       sync.Mutex
}

func NewCacheManager(dir string, db *Database, maxGB float64) *CacheManager {
	if err := os.MkdirAll(dir, 0755); err != nil {
		log.Printf("Warning: could not create cache dir %s: %v", dir, err)
	}
	// Create subdirectories for hash-based storage
	for i := 0; i < 256; i++ {
		subdir := filepath.Join(dir, fmt.Sprintf("%02x", i))
		os.MkdirAll(subdir, 0755)
	}
	return &CacheManager{
		dir:      dir,
		db:       db,
		maxBytes: int64(maxGB * 1024 * 1024 * 1024),
	}
}

func (cm *CacheManager) HasSpace() bool {
	total := cm.db.GetTotalCacheSize()
	return total < cm.maxBytes
}

func (cm *CacheManager) Store(keyName string, data []byte) (string, error) {
	if !cm.HasSpace() {
		cm.Evict()
		if !cm.HasSpace() {
			return "", fmt.Errorf("cache full (4TB limit reached)")
		}
	}
	// Use first 2 chars of key as subdirectory
	subdir := keyName[:2]
	if len(keyName) > 5 && strings.HasPrefix(keyName, "hash_") {
		subdir = keyName[5:7]
	}
	fp := filepath.Join(cm.dir, subdir, keyName)
	if err := os.WriteFile(fp, data, 0644); err != nil {
		return "", err
	}
	return fp, nil
}

func (cm *CacheManager) Load(filePath string) ([]byte, error) {
	return os.ReadFile(filePath)
}

func (cm *CacheManager) Evict() {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	cutoff := time.Now().Add(7 * 24 * time.Hour).Unix()
	files := cm.db.GetExpiredCacheFiles(cutoff)
	for _, fp := range files {
		os.Remove(fp)
	}
	cm.db.EvictOldCache(cutoff)
	log.Printf("Evicted %d cache entries", len(files))
}

// ==================== URL Transform ====================

var (
	// Match absolute URLs in tags
	tagAbsoluteRe = regexp.MustCompile(`(?i)\b(src|href|action|url|background)([\t ]*=[\t ]*)(["']?)(https?:)?//([^/]+)(/[^"'> \t)]+)`)
	// Match absolute URLs in CSS imports
	cssImportAbsoluteRe = regexp.MustCompile(`(?i)@import([\t ]+)(["']?)(https?:)?//([^/]+)(/[^"'> \t)]+)`)
	// Match absolute URLs in CSS url()
	cssURLAbsoluteRe = regexp.MustCompile(`(?i)\burl\((["']?)(https?:)?//([^/]+)(/[^"'> \t)]+)`)
	// Clean duplicate slashes (but not in http://)
	dupSlashRe = regexp.MustCompile(`(?:^|[^:])/{2,}`)
)

func transformContent(baseURL, accessedURL, content string) string {
	parts := strings.SplitN(baseURL, "/", 2)
	fiddleName := parts[0]

	// Rewrite absolute URLs in tags
	content = tagAbsoluteRe.ReplaceAllStringFunc(content, func(match string) string {
		m := tagAbsoluteRe.FindStringSubmatch(match)
		if m == nil {
			return match
		}
		return fmt.Sprintf(`%s%s%s/%s/%s%s`, m[1], m[2], m[3], fiddleName, m[5], m[6])
	})

	// Rewrite CSS @import
	content = cssImportAbsoluteRe.ReplaceAllStringFunc(content, func(match string) string {
		m := cssImportAbsoluteRe.FindStringSubmatch(match)
		if m == nil {
			return match
		}
		return fmt.Sprintf(`@import%s%s/%s/%s%s%s`, m[1], m[2], fiddleName, m[4], m[5], m[2])
	})

	// Rewrite CSS url()
	content = cssURLAbsoluteRe.ReplaceAllStringFunc(content, func(match string) string {
		m := cssURLAbsoluteRe.FindStringSubmatch(match)
		if m == nil {
			return match
		}
		return fmt.Sprintf(`url('%s/%s/%s%s')`, "", fiddleName, m[3], m[4])
	})

	// Clean up duplicate slashes (not in protocol)
	content = regexp.MustCompile(`([^:])/{2,}`).ReplaceAllString(content, "${1}/")

	// Fix duplicate fiddle names in path
	re := regexp.MustCompile(`/` + regexp.QuoteMeta(fiddleName) + `(/` + regexp.QuoteMeta(fiddleName) + `)+/`)
	content = re.ReplaceAllString(content, "/"+fiddleName+"/")

	return content
}

// ==================== Direct Domains & Request Blocker ====================

var DIRECT_DOMAINS = []string{
	"ebank.nz",
	"netwrck.com",
	"text-generator.io",
	"bitbank.nz",
	"readingtime.app.nz",
	"rewordgame.com",
	"bigmultiplayerchess.com",
	"webfiddle.net",
	"how.nz",
	"helix.app.nz",
}

func requestBlockerScript(fiddleName string) string {
	domainsJSON, _ := json.Marshal(DIRECT_DOMAINS)
	return fmt.Sprintf(`
<script>
var proxyBase = '/%s/';
var currentDomain = window.location.pathname.split('/')[2];
var directDomains = %s;
function rewriteUrl(url) {
    if (/^https?:\/\//.test(url)) {
        const parser = document.createElement('a');
        parser.href = url;
        if (directDomains.includes(parser.hostname)) {
            return url;
        }
        return proxyBase + parser.hostname + parser.pathname;
    }
    if (url.startsWith('/')) {
        return proxyBase + currentDomain + url;
    }
    const currentPath = window.location.pathname.split('/').slice(0, 4).join('/');
    const baseUrl = new URL(window.location.origin + currentPath + '/');
    return new URL(url, baseUrl).pathname;
}
document.addEventListener('DOMContentLoaded', () => {
    const links = document.querySelectorAll('a[target="_blank"]');
    links.forEach(link => { link.removeAttribute('target'); });
});
const originalWindowOpen = window.open;
window.open = function(url, target, features) {
    if (url) { window.location.href = rewriteUrl(url); return null; }
    return originalWindowOpen.apply(this, arguments);
};
const originalOpen = XMLHttpRequest.prototype.open;
XMLHttpRequest.prototype.open = function(method, url) {
    arguments[1] = rewriteUrl(url);
    originalOpen.apply(this, arguments);
};
const originalFetch = window.fetch;
window.fetch = function(input, init) {
    if (typeof input === 'string') { input = rewriteUrl(input); }
    else if (input instanceof URL) { input = rewriteUrl(input.href); }
    return originalFetch(input, init);
};
document.addEventListener('DOMContentLoaded', () => {
    const attributes = ['href', 'src', 'action', 'data-src'];
    const elements = document.querySelectorAll([...attributes].map(attr => '['+attr+']').join(','));
    elements.forEach(element => {
        attributes.forEach(attr => {
            if (element.hasAttribute(attr)) {
                const url = element.getAttribute(attr);
                element.setAttribute(attr, rewriteUrl(url));
            }
        });
    });
});
</script>
`, fiddleName, string(domainsJSON))
}

var bigAddCode = `
<p style="text-align:center;padding:10px;background:#f5f5f5;margin:10px 0">
<a href="https://x.com/leeleepenkman" target="_blank">Follow Lee on X</a> - Father, Husband, Serial builder creating AI, crypto, games & web tools. We are friends :) AI Will Come To Life!
<br><br>
Check out: <a href="https://ebank.nz">eBank.nz</a> (best Art Generator) |
<a href="https://netwrck.com">Netwrck.com</a> (best character chat and creative AI Tools) |
<a href="https://text-generator.io">Text-Generator.io</a> (AI APIs) |
<a href="https://bitbank.nz">BitBank.nz</a> (Crypto AI forecasting and trading bot) |
<a href="https://readingtime.app.nz">ReadingTime</a> (Kids Reading) |
<a href="https://rewordgame.com">RewordGame</a> |
<a href="https://cutedsl.cc">CuTeDSL accelerated image generation</a> |
<a href="https://openpaths.io">OpenPaths AI model APIs</a> |
<a href="https://codex-infinity.com>CodexInfinity</a> |
<a href="https://bigmultiplayerchess.com">BigMultiplayerChess</a> |
<a href="https://helix.app.nz">Helix AI Assistant</a>
</p>`

// ==================== Utilities ====================

func randomString(n int) string {
	const chars = "abcdefghijklmnopqrstuvwxyz0123456789"
	b := make([]byte, n)
	for i := range b {
		idx, _ := rand.Int(rand.Reader, big.NewInt(int64(len(chars))))
		b[i] = chars[idx.Int64()]
	}
	return string(b)
}

func urlEncode(s string) string {
	s = strings.ToLower(s)
	// Remove non-ASCII
	clean := make([]byte, 0, len(s))
	for i := 0; i < len(s); i++ {
		if s[i] < 128 {
			clean = append(clean, s[i])
		}
	}
	s = string(clean)
	s = regexp.MustCompile(`[\s]`).ReplaceAllString(s, "-")
	s = regexp.MustCompile(`[.\t,:;()'@!\\?#/<>&]`).ReplaceAllString(s, "")
	return url.QueryEscape(s)
}

func getURLKeyName(rawURL string) string {
	h := sha256.Sum256([]byte(rawURL))
	return "hash_" + hex.EncodeToString(h[:])
}

// ==================== Ignore Headers ====================

var IGNORE_HEADERS = map[string]bool{
	"set-cookie":              true,
	"expires":                 true,
	"cache-control":           true,
	"connection":              true,
	"keep-alive":              true,
	"proxy-authenticate":      true,
	"proxy-authorization":     true,
	"te":                      true,
	"trailers":                true,
	"transfer-encoding":       true,
	"upgrade":                 true,
	"x-frame-options":         true,
	"content-security-policy": true,
	"x-xss-protection":        true,
}

// ==================== Handler ====================

type Handler struct {
	config        *Config
	db            *Database
	cache         *CacheManager
	templates     *pongo2.TemplateSet
	staticFS      fasthttp.RequestHandler
	httpClient    *http.Client
	outboundSem   chan struct{}
	cacheWriteSem chan struct{}
}

func NewHandler(cfg *Config, db *Database, cache *CacheManager) *Handler {
	loader := pongo2.MustNewLocalFileSystemLoader("templates")
	tplSet := pongo2.NewSet("webfiddle", loader)
	tplSet.Debug = cfg.Debug

	staticRoot := filepath.Join(cfg.DataDir, "..", "static")
	fs := &fasthttp.FS{
		Root:               staticRoot,
		IndexNames:         []string{"index.html"},
		GenerateIndexPages: false,
		AcceptByteRange:    true,
		Compress:           false,
		CompressBrotli:     false,
		CacheDuration:      24 * time.Hour,
		PathRewrite:        fasthttp.NewPathSlashesStripper(1), // strip /static prefix
	}

	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		MaxIdleConns:          cfg.OutboundConcurrency,
		MaxIdleConnsPerHost:   8,
		MaxConnsPerHost:       16,
		IdleConnTimeout:       30 * time.Second,
		TLSHandshakeTimeout:   10 * time.Second,
		ResponseHeaderTimeout: 15 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
	}

	return &Handler{
		config:    cfg,
		db:        db,
		cache:     cache,
		templates: tplSet,
		staticFS:  fs.NewRequestHandler(),
		httpClient: &http.Client{
			Timeout:   20 * time.Second,
			Transport: transport,
			CheckRedirect: func(req *http.Request, via []*http.Request) error {
				if len(via) >= 5 {
					return fmt.Errorf("too many redirects")
				}
				return nil
			},
		},
		outboundSem:   make(chan struct{}, cfg.OutboundConcurrency),
		cacheWriteSem: make(chan struct{}, cfg.CacheWriteConcurrency),
	}
}

func (h *Handler) ServeHTTP(ctx *fasthttp.RequestCtx) {
	path := string(ctx.Path())
	method := string(ctx.Method())

	// Static files
	if strings.HasPrefix(path, "/static/") {
		h.staticFS(ctx)
		return
	}

	switch {
	case path == "/favicon.ico":
		ctx.Redirect("/static/favicon.ico", 302)

	case path == "/bingsiteauth.xml":
		ctx.SetContentType("application/xml")
		fmt.Fprint(ctx, `<?xml version="1.0"?><users><user>1A8F067A516D6AE82B1170B390ECD0EC</user></users>`)

	case path == "/robots.txt":
		h.staticFS(ctx)

	case path == "/" && method == "GET":
		h.handleIndex(ctx)

	case path == "/createfiddle" && method == "GET":
		h.handleCreateFiddle(ctx)

	case path == "/login" && method == "GET":
		h.handleLoginPage(ctx)
	case path == "/login" && method == "POST":
		h.handleLogin(ctx)

	case path == "/register" && method == "GET":
		h.handleRegisterPage(ctx)
	case path == "/register" && method == "POST":
		h.handleRegister(ctx)

	case path == "/logout":
		h.handleLogout(ctx)

	case path == "/sitemap.xml":
		h.handleSitemap(ctx)

	case path == "/_ah/warmup":
		ctx.SetStatusCode(200)
		fmt.Fprint(ctx, "ok")

	default:
		// Check for trailing slash redirect
		if len(path) > 1 && strings.HasSuffix(path, "/") {
			ctx.Redirect(strings.TrimRight(path, "/"), 302)
			return
		}

		// Check if this is a mirror/proxy path (contains fiddle-id/domain pattern)
		parts := strings.SplitN(strings.TrimPrefix(path, "/"), "/", 2)
		if len(parts) >= 2 && strings.Contains(parts[0], "-") {
			h.handleMirror(ctx, parts[0], parts[1])
			return
		}

		// Try as fiddle key
		fiddleKey := strings.TrimPrefix(path, "/")
		if fiddleKey != "" {
			h.handleFiddlePage(ctx, fiddleKey)
			return
		}

		ctx.SetStatusCode(404)
		fmt.Fprint(ctx, "Not found")
	}
}

func (h *Handler) getSessionUser(ctx *fasthttp.RequestCtx) string {
	cookie := ctx.Request.Header.Cookie("session_id")
	if len(cookie) == 0 {
		return ""
	}
	username, ok := h.db.GetSession(string(cookie))
	if !ok {
		return ""
	}
	return username
}

func (h *Handler) render(ctx *fasthttp.RequestCtx, templateName string, pctx pongo2.Context) {
	tpl, err := h.templates.FromFile(templateName)
	if err != nil {
		log.Printf("Template error: %v", err)
		ctx.SetStatusCode(500)
		fmt.Fprint(ctx, "Template error")
		return
	}
	out, err := tpl.Execute(pctx)
	if err != nil {
		log.Printf("Template render error: %v", err)
		ctx.SetStatusCode(500)
		fmt.Fprint(ctx, "Render error")
		return
	}
	ctx.SetContentType("text/html; charset=utf-8")
	fmt.Fprint(ctx, out)
}

// ==================== Route Handlers ====================

func (h *Handler) handleIndex(ctx *fasthttp.RequestCtx) {
	fiddle := defaultFiddle
	fiddleJSON, _ := json.Marshal(fiddle)
	scriptTypesJSON, _ := json.Marshal(SCRIPT_TYPES)
	styleTypesJSON, _ := json.Marshal(STYLE_TYPES)

	h.render(ctx, "index.html", pongo2.Context{
		"fiddle":               fiddle,
		"current_saved_fiddle": string(fiddleJSON),
		"title":                "WebSim by Netwrck!",
		"description":          "AI Creator - Make CSS and JavaScript To Create any and every web page! Share the results!",
		"static_url":           h.config.StaticURL,
		"url":                  string(ctx.URI().FullURI()),
		"script_types":         string(scriptTypesJSON),
		"style_types":          string(styleTypesJSON),
		"user":                 h.getSessionUser(ctx),
	})
}

func (h *Handler) handleFiddlePage(ctx *fasthttp.RequestCtx, fiddleKey string) {
	fiddle := h.db.GetFiddleByURLKey(fiddleKey)
	if fiddle == nil {
		fiddle = &defaultFiddle
	}

	fiddleJSON, _ := json.Marshal(fiddle)
	scriptTypesJSON, _ := json.Marshal(SCRIPT_TYPES)
	styleTypesJSON, _ := json.Marshal(STYLE_TYPES)

	h.render(ctx, "index.html", pongo2.Context{
		"fiddle":               *fiddle,
		"current_saved_fiddle": string(fiddleJSON),
		"title":                fiddle.Title,
		"description":          fiddle.Description,
		"static_url":           h.config.StaticURL,
		"url":                  string(ctx.URI().FullURI()),
		"script_types":         string(scriptTypesJSON),
		"style_types":          string(styleTypesJSON),
		"user":                 h.getSessionUser(ctx),
	})
}

func (h *Handler) handleCreateFiddle(ctx *fasthttp.RequestCtx) {
	args := ctx.QueryArgs()
	id := string(args.Peek("id"))
	if id == "" {
		id = randomString(6)
	}

	scriptLang := 0
	if sl := string(args.Peek("script_language")); sl != "" {
		if v, ok := SCRIPT_TYPES[sl]; ok {
			scriptLang = v
		}
	}
	styleLang := 0
	if sl := string(args.Peek("style_language")); sl != "" {
		if v, ok := STYLE_TYPES[sl]; ok {
			styleLang = v
		}
	}

	fiddle := &Fiddle{
		ID:             id,
		Title:          string(args.Peek("title")),
		Description:    string(args.Peek("description")),
		StartURL:       string(args.Peek("start_url")),
		Script:         string(args.Peek("script")),
		Style:          string(args.Peek("style")),
		ScriptLanguage: scriptLang,
		StyleLanguage:  styleLang,
	}

	if err := h.db.SaveFiddle(fiddle); err != nil {
		ctx.SetStatusCode(500)
		fmt.Fprint(ctx, "error saving fiddle")
		return
	}
	ctx.SetContentType("text/plain")
	fmt.Fprint(ctx, "success")
}

func (h *Handler) handleLoginPage(ctx *fasthttp.RequestCtx) {
	h.render(ctx, "login.html", pongo2.Context{
		"title":        "Login",
		"action":       "/login",
		"submit_label": "Login",
		"is_login":     true,
	})
}

func (h *Handler) handleLogin(ctx *fasthttp.RequestCtx) {
	username := string(ctx.FormValue("username"))
	password := string(ctx.FormValue("password"))

	if !h.db.CheckUser(username, password) {
		ctx.SetStatusCode(400)
		ctx.SetContentType("text/html")
		fmt.Fprint(ctx, "Invalid credentials")
		return
	}

	sessionID, err := h.db.CreateSession(username)
	if err != nil {
		ctx.SetStatusCode(500)
		fmt.Fprint(ctx, "Session error")
		return
	}

	cookie := fasthttp.AcquireCookie()
	defer fasthttp.ReleaseCookie(cookie)
	cookie.SetKey("session_id")
	cookie.SetValue(sessionID)
	cookie.SetPath("/")
	cookie.SetHTTPOnly(true)
	cookie.SetSameSite(fasthttp.CookieSameSiteLaxMode)
	ctx.Response.Header.SetCookie(cookie)
	ctx.Redirect("/", 302)
}

func (h *Handler) handleRegisterPage(ctx *fasthttp.RequestCtx) {
	h.render(ctx, "login.html", pongo2.Context{
		"title":        "Register",
		"action":       "/register",
		"submit_label": "Register",
		"is_login":     false,
	})
}

func (h *Handler) handleRegister(ctx *fasthttp.RequestCtx) {
	username := string(ctx.FormValue("username"))
	password := string(ctx.FormValue("password"))

	if err := h.db.CreateUser(username, password); err != nil {
		ctx.SetStatusCode(400)
		ctx.SetContentType("text/html")
		fmt.Fprint(ctx, "User already exists")
		return
	}

	sessionID, _ := h.db.CreateSession(username)
	cookie := fasthttp.AcquireCookie()
	defer fasthttp.ReleaseCookie(cookie)
	cookie.SetKey("session_id")
	cookie.SetValue(sessionID)
	cookie.SetPath("/")
	cookie.SetHTTPOnly(true)
	cookie.SetSameSite(fasthttp.CookieSameSiteLaxMode)
	ctx.Response.Header.SetCookie(cookie)
	ctx.Redirect("/", 302)
}

func (h *Handler) handleLogout(ctx *fasthttp.RequestCtx) {
	cookie := ctx.Request.Header.Cookie("session_id")
	if len(cookie) > 0 {
		h.db.DeleteSession(string(cookie))
	}
	// Clear cookie
	c := fasthttp.AcquireCookie()
	defer fasthttp.ReleaseCookie(c)
	c.SetKey("session_id")
	c.SetValue("")
	c.SetPath("/")
	c.SetExpire(time.Unix(0, 0))
	ctx.Response.Header.SetCookie(c)
	ctx.Redirect("/", 302)
}

func (h *Handler) handleSitemap(ctx *fasthttp.RequestCtx) {
	ctx.SetContentType("text/xml; charset=utf-8")
	fmt.Fprint(ctx, `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://webfiddle.net/</loc><priority>1.0</priority></url>
</urlset>`)
}

// ==================== Mirror/Proxy Handler ====================

func (h *Handler) handleMirror(ctx *fasthttp.RequestCtx, fiddleName, baseURL string) {
	if strings.HasSuffix(baseURL, "favicon.ico") {
		ctx.Redirect("/static/favicon.ico", 302)
		return
	}

	// Check blacklist
	domain := strings.SplitN(baseURL, "/", 2)[0]
	domainLower := strings.ToLower(domain)
	if BLACKLISTED_DOMAINS[domainLower] {
		ctx.SetStatusCode(403)
		fmt.Fprint(ctx, "Access to this URL is not allowed")
		return
	}
	if !isProxyableHost(domainLower) {
		ctx.SetStatusCode(400)
		fmt.Fprintf(ctx, "Invalid proxy host: %s", domain)
		return
	}

	// Build proxy base and mirrored URL
	domainPart := strings.SplitN(baseURL, "/", 2)[0]
	proxyBase := fiddleName + "/" + domainPart
	translatedAddress := baseURL
	mirroredURL := "http://" + translatedAddress

	// Check cache
	keyName := getURLKeyName(mirroredURL)
	meta, err := h.db.GetCacheMeta(keyName)

	var contentData []byte
	var statusCode int
	var headers map[string]string
	var contentType string

	if err == nil && meta != nil {
		// Cache hit - load from filesystem
		contentData, err = h.cache.Load(meta.FilePath)
		if err != nil {
			log.Printf("Cache file read error for %s: %v", meta.FilePath, err)
			// Fall through to fetch
		} else {
			statusCode = meta.StatusCode
			headers = meta.Headers()
			contentType = meta.ContentType
		}
	}

	if contentData == nil {
		// Fetch from remote
		statusCode, headers, contentData, contentType, err = h.fetchURL(mirroredURL, proxyBase)
		if err != nil {
			log.Printf("Fetch error for %s: %v", mirroredURL, err)
			ctx.SetStatusCode(502)
			fmt.Fprintf(ctx, "Could not fetch: %s", mirroredURL)
			return
		}

		// Transform HTML/CSS content
		if strings.HasPrefix(contentType, "text/html") || strings.HasPrefix(contentType, "text/css") {
			transformed := transformContent(proxyBase, mirroredURL, string(contentData))
			contentData = []byte(transformed)
		}

		// Store to filesystem cache
		h.storeInCacheAsync(keyName, mirroredURL, translatedAddress, proxyBase, statusCode, headers, contentType, contentData)
	}

	// For HTML content, inject fiddle code
	if strings.HasPrefix(contentType, "text/html") {
		htmlStr := string(contentData)

		// Transform content for proxy context
		htmlStr = transformContent(proxyBase, mirroredURL, htmlStr)

		// Inject request blocker after <head>
		headRe := regexp.MustCompile(`(?i)<head[^>]*>`)
		htmlStr = headRe.ReplaceAllStringFunc(htmlStr, func(match string) string {
			return match + requestBlockerScript(fiddleName)
		})

		// Inject fiddle JS/CSS
		fiddle := h.db.GetFiddleByURLKey(fiddleName)
		if fiddle != nil {
			script := fiddle.Script
			style := fiddle.Style
			extraJS := `<script id="webfiddle-js">` + script + `</script>`
			extraCSS := `<style id="webfiddle-css">` + style + `</style>`
			htmlStr = htmlStr + extraJS + extraCSS + bigAddCode
		}

		// Set CSP to allow everything
		csp := "default-src * 'unsafe-inline' 'unsafe-eval' data: blob:; " +
			"script-src * 'unsafe-inline' 'unsafe-eval' data: blob:; " +
			"style-src * 'unsafe-inline' data:; " +
			"img-src * data: blob:; " +
			"font-src * data:; " +
			"connect-src *; media-src *; object-src *;"

		ctx.SetContentType("text/html; charset=utf-8")
		ctx.Response.Header.Set("Content-Security-Policy", csp)
		for k, v := range headers {
			kl := strings.ToLower(k)
			if kl != "content-type" && kl != "content-length" && kl != "content-encoding" && kl != "content-security-policy" {
				ctx.Response.Header.Set(k, v)
			}
		}
		ctx.Response.Header.Set("Cache-Control", "max-age=2592000")
		ctx.SetStatusCode(statusCode)
		fmt.Fprint(ctx, htmlStr)
		return
	}

	// Non-HTML content: serve as-is
	if contentType != "" {
		ctx.SetContentType(contentType)
	}
	for k, v := range headers {
		kl := strings.ToLower(k)
		if kl != "content-encoding" && kl != "content-length" {
			ctx.Response.Header.Set(k, v)
		}
	}
	ctx.Response.Header.Set("Cache-Control", "max-age=2592000")
	ctx.SetStatusCode(statusCode)
	ctx.SetBody(contentData)
}

func (h *Handler) fetchURL(targetURL, proxyBase string) (int, map[string]string, []byte, string, error) {
	h.outboundSem <- struct{}{}
	defer func() { <-h.outboundSem }()

	req, err := http.NewRequest("GET", targetURL, nil)
	if err != nil {
		// Try with https
		targetURL = strings.Replace(targetURL, "http://", "https://", 1)
		req, err = http.NewRequest("GET", targetURL, nil)
		if err != nil {
			return 0, nil, nil, "", err
		}
	}
	req.Header.Set("Accept-Encoding", "identity")
	req.Header.Set("User-Agent", "Mozilla/5.0 (compatible; WebFiddle/1.0)")

	resp, err := h.httpClient.Do(req)
	if err != nil {
		// Retry with https if http failed
		if strings.HasPrefix(targetURL, "http://") {
			targetURL = strings.Replace(targetURL, "http://", "https://", 1)
			req, _ = http.NewRequest("GET", targetURL, nil)
			req.Header.Set("Accept-Encoding", "identity")
			req.Header.Set("User-Agent", "Mozilla/5.0 (compatible; WebFiddle/1.0)")
			resp, err = h.httpClient.Do(req)
			if err != nil {
				return 0, nil, nil, "", err
			}
		} else {
			return 0, nil, nil, "", err
		}
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, 50*1024*1024)) // 50MB max
	if err != nil {
		return 0, nil, nil, "", err
	}

	adjustedHeaders := make(map[string]string)
	for k, vals := range resp.Header {
		kl := strings.ToLower(k)
		if IGNORE_HEADERS[kl] {
			continue
		}
		if kl == "location" {
			// Rewrite location header
			parsed, e := url.Parse(vals[0])
			if e == nil {
				adjustedHeaders["location"] = "/" + proxyBase + "/" + parsed.Host + parsed.Path
			}
		} else {
			adjustedHeaders[kl] = vals[0]
		}
	}

	contentType := resp.Header.Get("Content-Type")
	return resp.StatusCode, adjustedHeaders, body, contentType, nil
}

func isProxyableHost(host string) bool {
	if host == "" || strings.ContainsAny(host, " \t\r\n/:[]()\\") {
		return false
	}
	if ip := net.ParseIP(host); ip != nil {
		return true
	}
	if host == "localhost" {
		return true
	}
	labels := strings.Split(host, ".")
	if len(labels) < 2 {
		return false
	}
	for _, label := range labels {
		if label == "" || strings.HasPrefix(label, "-") || strings.HasSuffix(label, "-") {
			return false
		}
		for _, r := range label {
			if (r < 'a' || r > 'z') && (r < '0' || r > '9') && r != '-' {
				return false
			}
		}
	}
	switch labels[len(labels)-1] {
	case "css", "gif", "htm", "html", "ico", "jpg", "jpeg", "js", "php", "png", "svg", "webp":
		return false
	}
	return true
}

func (h *Handler) storeInCacheAsync(keyName, originalURL, translatedAddress, baseURL string, statusCode int, headers map[string]string, contentType string, data []byte) {
	select {
	case h.cacheWriteSem <- struct{}{}:
		go func() {
			defer func() { <-h.cacheWriteSem }()
			h.storeInCache(keyName, originalURL, translatedAddress, baseURL, statusCode, headers, contentType, data)
		}()
	default:
	}
}

func (h *Handler) storeInCache(keyName, originalURL, translatedAddress, baseURL string, statusCode int, headers map[string]string, contentType string, data []byte) {
	filePath, err := h.cache.Store(keyName, data)
	if err != nil {
		log.Printf("Cache store error: %v", err)
		return
	}

	headersJSON, _ := json.Marshal(headers)
	meta := &CacheMeta{
		KeyName:           keyName,
		OriginalURL:       originalURL,
		TranslatedAddress: translatedAddress,
		StatusCode:        statusCode,
		HeadersJSON:       string(headersJSON),
		ContentType:       contentType,
		FilePath:          filePath,
		BaseURL:           baseURL,
		Size:              int64(len(data)),
		Expiry:            time.Now().Add(30 * 24 * time.Hour).Unix(),
	}
	if err := h.db.SaveCacheMeta(meta); err != nil {
		log.Printf("Cache meta save error: %v", err)
	}
}

// ==================== Main ====================

func main() {
	configureRuntime()
	cfg := loadConfig()

	db, err := NewDatabase(cfg.DataDir)
	if err != nil {
		log.Fatalf("Database init failed: %v", err)
	}

	cache := NewCacheManager(cfg.CacheDir, db, cfg.MaxDiskGB)

	handler := NewHandler(cfg, db, cache)

	server := &fasthttp.Server{
		Handler:            handler.ServeHTTP,
		Name:               "WebFiddle",
		ReadTimeout:        30 * time.Second,
		WriteTimeout:       30 * time.Second,
		IdleTimeout:        120 * time.Second,
		MaxRequestBodySize: 10 * 1024 * 1024,
		Concurrency:        cfg.Concurrency,
	}

	// Graceful shutdown
	go func() {
		sigCh := make(chan os.Signal, 1)
		signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
		<-sigCh
		log.Println("Shutting down...")
		server.Shutdown()
	}()

	addr := ":" + cfg.Port
	log.Printf("WebFiddle starting on %s", addr)
	if err := server.ListenAndServe(addr); err != nil {
		log.Fatalf("Server error: %v", err)
	}
}
