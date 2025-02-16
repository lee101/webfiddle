import os
import re
from urllib.parse import urlparse, urljoin
import logging

# Unique marker to prevent re-transformation
MARKER = "###TRANSFORMED###"

# ###############################################################################

# Updated absolute URL regex to capture domain and path separately
ABSOLUTE_URL_REGEX = r"(https?:)?//([^/]+)(/[^\"'> \t\)]*)"

# URLs that are relative to the base of the current hostname.
# Updated to use non‐capturing groups for protocol to avoid unintended capturing.
BASE_RELATIVE_URL_REGEX = r"/(?!(?:(?:/)|(?:http(?:s)?://)|(?:url\(\))))([^\"'> \t\)]*)"

# URLs that have '../' or './' to start off their paths.
TRAVERSAL_URL_REGEX = r"(?:\.\./|\./)([^\"'> \t\)]*)"

# URLs that are in the same directory as the requested URL.
SAME_DIR_URL_REGEX = r"(?!(?:(?:/)|(?:http(?:s?)://)|(?:url\(\))))([^\"'> \t\)]+)"

# URL matches the root directory.
ROOT_DIR_URL_REGEX = r"(?!//(?!>))/(?=[ \t\n]*[\"'\)>/])"

# Start of a tag using 'src' or 'href'
TAG_START = r"(?i)(src|href|action|url|background)\s*=\s*([\"']?)"

# Start of a CSS import
CSS_IMPORT_START = r"(?i)@import\s+([\"']?)"

# CSS url() call
CSS_URL_START = r"(?i)url\(([\"']?)"

# Callable replacements for absolute URLs

def absolute_replacement_tag(match, fiddle_name):
    tag = match.group(1)      # Group 1 is the tag and attribute
    equals = match.group(2)   # Group 2 is any whitespace and equals
    quote = match.group(3)    # Group 3 is the quote
    domain = match.group(6)   # Group 6 is the domain
    path = match.group(7)     # Group 7 is the path
    # Preserve fiddle context by using it as the root of the path
    return f"{tag}{equals}{quote}/{fiddle_name}/{domain}{path}"

def absolute_replacement_css_import(match, fiddle_name):
    protocol = match.group(3) or "http:"  # Group 3 is the protocol
    domain = match.group(5)   # Group 5 is the domain
    path = match.group(6)     # Group 6 is the path
    # Preserve fiddle context
    return f"@import '/{fiddle_name}/{domain}{path}';"

def absolute_replacement_css_url(match, fiddle_name):
    protocol = match.group(2) or "http:"  # Group 2 is the protocol
    domain = match.group(4)   # Group 4 is the domain
    path = match.group(5)     # Group 5 is the path
    # Preserve fiddle context
    return f"url('/{fiddle_name}/{domain}{path}')"

def make_replacement(pattern_type, match, base, accessed_dir, fiddle_name, current_domain):
    """Helper function to handle both string and function replacements"""
    if callable(pattern_type):
        return pattern_type(match, fiddle_name)
    else:
        # Include fiddle context in the replacement
        formatted = pattern_type % {
            "fiddle": fiddle_name,
            "base": current_domain,
            "accessed_dir": accessed_dir
        }
        return match.expand(formatted)

def wrap_quotes(url, original_quotes):
    """Wrap a URL in quotes if it was originally quoted."""
    if original_quotes:
        return f'{original_quotes}{url}{original_quotes}'
    return url

def clean_path(path, fiddle_name=None, current_domain=None, accessed_dir=None):
    """Clean and normalize URL paths."""
    if not path:
        if fiddle_name and current_domain:
            return f"/{fiddle_name}/{current_domain}/"
        return "/"
        
    # Don't transform data URLs
    if path.startswith('data:'):
        return path
        
    # Remove extra quotes if present
    path = path.strip('"\'')
    
    # Prevent double proxying
    if fiddle_name and path.startswith(f"/{fiddle_name}/"):
        return path
        
    # Handle absolute URLs
    if path.startswith(('http://', 'https://')):
        parsed = urlparse(path)
        path_with_query = parsed.path
        if parsed.query:
            path_with_query += '?' + parsed.query
        if parsed.fragment:
            path_with_query += '#' + parsed.fragment
        if fiddle_name:
            return f"/{fiddle_name}/{parsed.netloc}{path_with_query}"
        return path
        
    # Handle protocol-relative URLs
    if path.startswith('//'):
        parts = path[2:].split('/', 1)
        domain = parts[0]
        path_part = '/' + parts[1] if len(parts) > 1 else '/'
        if fiddle_name:
            return f"/{fiddle_name}/{domain}{path_part}"
        return path
        
    # Handle root-relative URLs
    if path.startswith('/'):
        if fiddle_name and current_domain:
            # Remove any duplicate slashes
            clean_path = re.sub(r'//+', '/', path)
            return f"/{fiddle_name}/{current_domain}{clean_path}"
        return path
        
    # Handle relative URLs
    if accessed_dir:
        # Normalize path by resolving . and ..
        full_path = os.path.normpath(os.path.join(accessed_dir, path))
        if fiddle_name and current_domain:
            # Remove leading slash if present
            full_path = full_path.lstrip('/')
            return f"/{fiddle_name}/{current_domain}/{full_path}"
        return full_path
        
    # Default case: treat as relative to current domain
    if fiddle_name and current_domain:
        # Remove any leading slashes
        clean_path = path.lstrip('/')
        return f"/{fiddle_name}/{current_domain}/{clean_path}"
    return path

# Build uncompiled regexes with updated replacement strings
UNCOMPILED_REGEXES = [
    # Absolute URLs in tags
    (TAG_START + ABSOLUTE_URL_REGEX,
     lambda m, fiddle: f'{m.group(1)}={wrap_quotes(clean_path(f"//{m.group(3)}{m.group(4)}", fiddle, m.group(3)), m.group(2))}'),

    # CSS imports
    (CSS_IMPORT_START + ABSOLUTE_URL_REGEX,
     lambda m, fiddle: f'@import {wrap_quotes(clean_path(f"//{m.group(2)}{m.group(3)}", fiddle, m.group(2)), m.group(1))}'),

    # CSS URLs
    (CSS_URL_START + ABSOLUTE_URL_REGEX,
     lambda m, fiddle: f'url({wrap_quotes(clean_path(f"//{m.group(2)}{m.group(3)}", fiddle, m.group(2)), m.group(1))})'),

    # Root-relative URLs
    (TAG_START + BASE_RELATIVE_URL_REGEX,
     lambda m, fiddle: f'{m.group(1)}={wrap_quotes(clean_path("/" + m.group(3), fiddle, m.group(2)), m.group(2))}'),

    # Relative paths with traversal
    (TAG_START + TRAVERSAL_URL_REGEX,
     lambda m, fiddle: f'{m.group(1)}={wrap_quotes(clean_path(m.group(3), fiddle, m.group(2)), m.group(2))}'),

    # Same directory relative paths
    (TAG_START + SAME_DIR_URL_REGEX,
     lambda m, fiddle: f'{m.group(1)}={wrap_quotes(clean_path(m.group(3), fiddle, m.group(2)), m.group(2))}'),
]

REPLACEMENT_REGEXES = []
for reg, replace in UNCOMPILED_REGEXES:
    try:
        REPLACEMENT_REGEXES.append((re.compile(reg), replace))
    except Exception as e:
        print(f"Failed to compile regex: {reg}")
        print(f"Error: {e}")
        raise

################################################################################

def TransformContent(base_url, accessed_url, content):
    """Transform URLs in content to be relative to the base_url."""
    if isinstance(content, bytes):
        content = content.decode('utf-8')
        
    url_obj = urlparse(accessed_url)
    accessed_dir = os.path.dirname(url_obj.path)
    if not accessed_dir.endswith("/"):
        accessed_dir += "/"
    if accessed_dir.startswith("/"):
        accessed_dir = accessed_dir[1:]

    # Handle base URL parsing for test cases
    if '/' in base_url:
        fiddle_name, current_domain = base_url.split('/', 1)
        current_domain = current_domain.split('/')[0]
    else:
        fiddle_name = None
        current_domain = base_url
        
    return transform_content(content, fiddle_name, current_domain, accessed_dir)

def transform_content(content, fiddle_name=None, current_domain=None, accessed_dir=None):
    """Transform URLs in content to use the proxy prefix."""
    if not content:
        return content
        
    try:
        # Transform href attributes
        content = re.sub(
            r'href=(["\']?)([^"\'\s>]+)(["\']?)',
            lambda m: f'href={m.group(1)}{clean_path(m.group(2), fiddle_name, current_domain, accessed_dir)}{m.group(3)}',
            content
        )
        
        # Transform src attributes
        content = re.sub(
            r'src=(["\']?)([^"\'\s>]+)(["\']?)',
            lambda m: f'src={m.group(1)}{clean_path(m.group(2), fiddle_name, current_domain, accessed_dir)}{m.group(3)}',
            content
        )
        
        # Transform CSS @import rules
        content = re.sub(
            r'(@import\s+["\']?)([^"\'\s;]+)(["\']?)',
            lambda m: f'{m.group(1)}{clean_path(m.group(2), fiddle_name, current_domain, accessed_dir)}{m.group(3)}',
            content
        )
        
        # Transform CSS url() functions
        content = re.sub(
            r'url\((["\']?)([^"\'\)]+)(["\']?)\)',
            lambda m: f'url({m.group(1)}{clean_path(m.group(2), fiddle_name, current_domain, accessed_dir)}{m.group(3)})',
            content
        )
        
        return content
    except Exception as e:
        logging.error(f"Error transforming content: {e}")
        return content
