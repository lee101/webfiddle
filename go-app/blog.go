package main

import (
	"embed"
	"fmt"
	"html"
	"strings"

	"github.com/valyala/fasthttp"
)

//go:embed blog/*.html
var blogFS embed.FS

type blogPost struct{ Slug, Title, Desc, Date string }

var blogPosts = []blogPost{
	{"in-browser-code-editors", "In Browser Code Editors: A Practical Guide", "How in browser code editors work, which features help most, and a first edit in WebFiddle you can finish in a minute.", "2026-10-22"},
	{"web-prototyping-in-minutes", "Web Prototyping in Minutes with Live Pages", "A simple loop for prototyping web page changes: start from a live page, edit JavaScript and CSS, and share a link.", "2026-10-22"},
	{"share-live-page-remixes", "How to Share Live Web Page Remixes", "Share runnable page experiments as links so teammates can read, tweak and build on your JavaScript and CSS.", "2026-10-22"},
}

const blogCSS = `*{box-sizing:border-box}body{margin:0;font:17px/1.65 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1b2733;background:#f6f9fc}header.b{background:#0d2a4a;padding:12px 16px}header.b a{color:#fff;text-decoration:none;font-weight:600;margin-right:18px}main{max-width:760px;margin:0 auto;padding:24px 16px 48px}h1{font-size:2rem;line-height:1.2;color:#0d2a4a}h2{font-size:1.35rem;color:#0d2a4a;margin-top:2rem}a{color:#0b5cad}.lead{font-size:1.15rem;color:#33475b}.cta{display:inline-block;background:#0b5cad;color:#fff;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600}code{background:#e6eef7;padding:1px 5px;border-radius:3px}.card{background:#fff;border:1px solid #d5e1ee;border-radius:8px;padding:16px;margin:14px 0}.card h2{margin:0 0 6px;font-size:1.2rem}.card p{margin:0}footer{max-width:760px;margin:0 auto;padding:16px;color:#4a5b6c;font-size:.95rem}footer a{margin-right:12px}`

func blogPage(title, desc, canonical, ogType, ld, body string) string {
	return fmt.Sprintf(`<!doctype html><html lang="en"><head><meta charset="utf-8"><title>%[1]s</title><meta name="description" content="%[2]s"><link rel="canonical" href="%[3]s"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="theme-color" content="#0d2a4a"><meta property="og:title" content="%[1]s"><meta property="og:description" content="%[2]s"><meta property="og:url" content="%[3]s"><meta property="og:type" content="%[4]s"><meta property="og:site_name" content="WebFiddle"><meta property="og:image" content="https://webfiddle.net/static/images/webfiddle-og-1200x630.png"><meta property="og:image:width" content="1200"><meta property="og:image:height" content="630"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="%[1]s"><meta name="twitter:description" content="%[2]s"><meta name="twitter:image" content="https://webfiddle.net/static/images/webfiddle-og-1200x630.png"><link rel="shortcut icon" href="/favicon.ico"><script type="application/ld+json">%[5]s</script><style>%[7]s</style></head><body><header class="b"><a href="/">WebFiddle</a><a href="/blog">Blog</a></header><main>%[6]s</main><footer><a href="/">Editor</a><a href="/blog">Blog</a><a href="https://sitesim.net">SitesSim</a><a href="https://chatgibidy.com">ChatGibidy</a><a href="https://simplexgen.com">SimplexGen</a></footer></body></html>`,
		html.EscapeString(title), html.EscapeString(desc), canonical, ogType, ld, body, blogCSS)
}

func jsonStr(s string) string { return `"` + strings.NewReplacer(`\`, `\\`, `"`, `\"`).Replace(s) + `"` }

func (h *Handler) serveBlog(ctx *fasthttp.RequestCtx, path string) bool {
	if path != "/blog" && !strings.HasPrefix(path, "/blog/") {
		return false
	}
	ctx.SetContentType("text/html; charset=utf-8")
	ctx.Response.Header.Set("Cache-Control", "public, max-age=300")
	slug := strings.Trim(strings.TrimPrefix(path, "/blog"), "/")
	if slug == "" {
		var b strings.Builder
		b.WriteString(`<h1>WebFiddle Blog</h1><p class="lead">Practical guides for editing, prototyping and sharing web pages in your browser.</p>`)
		for _, p := range blogPosts {
			fmt.Fprintf(&b, `<div class="card"><h2><a href="/blog/%s">%s</a></h2><p>%s</p></div>`, p.Slug, html.EscapeString(p.Title), html.EscapeString(p.Desc))
		}
		ld := `{"@context":"https://schema.org","@type":"Blog","name":"WebFiddle Blog","url":"https://webfiddle.net/blog"}`
		ctx.SetBodyString(blogPage("WebFiddle Blog: Editing and Prototyping Web Pages", "Practical guides for editing, prototyping and sharing web pages with JavaScript and CSS in your browser.", "https://webfiddle.net/blog", "website", ld, b.String()))
		return true
	}
	for _, p := range blogPosts {
		if p.Slug != slug {
			continue
		}
		raw, err := blogFS.ReadFile("blog/" + slug + ".html")
		if err != nil {
			break
		}
		u := "https://webfiddle.net/blog/" + slug
		ld := fmt.Sprintf(`{"@context":"https://schema.org","@type":"Article","headline":%s,"description":%s,"datePublished":"%s","dateModified":"%s","mainEntityOfPage":"%s","image":"https://webfiddle.net/static/images/webfiddle-og-1200x630.png","author":{"@type":"Organization","name":"WebFiddle"},"publisher":{"@type":"Organization","name":"App.nz","url":"https://app.nz"}}`, jsonStr(p.Title), jsonStr(p.Desc), p.Date, p.Date, u)
		body := `<p><a href="/blog">Blog</a></p><h1>` + html.EscapeString(p.Title) + `</h1>` + string(raw)
		ctx.SetBodyString(blogPage(p.Title+" | WebFiddle", p.Desc, u, "article", ld, body))
		return true
	}
	ctx.SetStatusCode(404)
	ctx.SetBodyString(blogPage("Not found | WebFiddle", "Page not found.", "https://webfiddle.net/blog", "website", "{}", `<h1>Not found</h1><p><a href="/blog">Back to the blog</a></p>`))
	return true
}

func blogSitemapEntries() string {
	var b strings.Builder
	b.WriteString("  <url><loc>https://webfiddle.net/blog</loc><lastmod>2026-10-22</lastmod><priority>0.7</priority></url>\n")
	for _, p := range blogPosts {
		fmt.Fprintf(&b, "  <url><loc>https://webfiddle.net/blog/%s</loc><lastmod>%s</lastmod><priority>0.6</priority></url>\n", p.Slug, p.Date)
	}
	return b.String()
}
