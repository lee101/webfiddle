import os
import re
from urllib.parse import urlparse

# Unique marker to prevent re-transformation
MARKER = "###TRANSFORMED###"

# ###############################################################################

# Updated absolute URL regex to capture domain and path separately
ABSOLUTE_URL_REGEX = r"(http(s?):)?//([^/]+)(/[^\"'> \t\)]+)"

# URLs that are relative to the base of the current hostname.
# Fixed to use a non‐capturing negative lookahead with balanced parentheses.
BASE_RELATIVE_URL_REGEX = r"/(?!(?:(?:/)|(?:http(s?)://)|(?:url\(\))))([^\"'> \t\)]*)"

# URLs that have '../' or './' to start off their paths.
TRAVERSAL_URL_REGEX = r"((?:\.\./|\.\/))/(?!(?:(?:/)|(?:http(s?)://)|(?:url\(\))))([^\"'> \t\)]*)"

# URLs that are in the same directory as the requested URL.
SAME_DIR_URL_REGEX = r"(?!(?:(?:/)|(?:http(?:s?)://)|(?:url\(\))))([^\"'> \t\)]+)"

# URL matches the root directory.
ROOT_DIR_URL_REGEX = r"(?!//(?!>))/(?=[ \t\n]*[\"'\)>/])"

# Start of a tag using 'src' or 'href'
TAG_START = r"(?i)\b(src|href|action|url|background)([\t ]*=[\t ]*)([\"\']?)"

# Start of a CSS import
CSS_IMPORT_START = r"(?i)@import([\t ]+)([\"\']?)"

# CSS url() call
CSS_URL_START = r"(?i)\burl\(([\"\']?)"

# Callable replacements for absolute URLs

def absolute_replacement_tag(match):
    tag = match.group(1)
    equals = match.group(2)
    quote = match.group(3)
    domain = match.group(6)  # Group 6 is the domain after TAG_START + ABSOLUTE_URL_REGEX
    path = match.group(7)    # Group 7 is the path
    return f"{tag}{equals}{quote}{MARKER}/{domain}{path}"

def absolute_replacement_css_import(match):
    spacing = match.group(1)
    quote = match.group(2)
    domain = match.group(5)  # Group 5 is the domain after CSS_IMPORT_START + ABSOLUTE_URL_REGEX
    path = match.group(6)    # Group 6 is the path
    return f"@import{spacing}{quote}{MARKER}/{domain}{path}"

def absolute_replacement_css_url(match):
    quote = match.group(1)
    domain = match.group(4)  # Group 4 is the domain after CSS_URL_START + ABSOLUTE_URL_REGEX
    path = match.group(5)    # Group 5 is the path
    return f"url({quote}{MARKER}/{domain}{path})"  # Added closing parenthesis

def make_replacement(pattern_type, match, base, accessed_dir):
    """Helper function to handle both string and function replacements"""
    if callable(pattern_type):
        return pattern_type(match)
    else:
        return pattern_type % {
            "base": base,
            "accessed_dir": accessed_dir,
        }

# Build uncompiled regexes with marker inserted in the replacement strings for non-absolute cases
UNCOMPILED_REGEXES = [
    # For tags with same-dir URLs
    (TAG_START + SAME_DIR_URL_REGEX,
        r"\1\2\3{MARKER}/%(base)s/%(accessed_dir)s\4".format(MARKER=MARKER)),

    # For tags with traversal URLs
    (TAG_START + TRAVERSAL_URL_REGEX,
        r"\1\2\3{MARKER}/%(base)s/%(accessed_dir)s\4/\5".format(MARKER=MARKER)),

    # For tags with base-relative URLs
    (TAG_START + BASE_RELATIVE_URL_REGEX,
        r"\1\2\3{MARKER}/%(base)s/\4".format(MARKER=MARKER)),

    # For tags with root directory URLs
    (TAG_START + ROOT_DIR_URL_REGEX,
        r"\1\2\3{MARKER}/%(base)s/".format(MARKER=MARKER)),

    # For tags with absolute URLs, use callable replacement
    (TAG_START + ABSOLUTE_URL_REGEX, absolute_replacement_tag),

    # CSS import: same directory
    (CSS_IMPORT_START + SAME_DIR_URL_REGEX,
        r"@import\1\2{MARKER}/%(base)s/%(accessed_dir)s\3".format(MARKER=MARKER)),

    # CSS import: traversal
    (CSS_IMPORT_START + TRAVERSAL_URL_REGEX,
        r"@import\1\2{MARKER}/%(base)s/%(accessed_dir)s\3/\4".format(MARKER=MARKER)),

    # CSS import: base-relative
    (CSS_IMPORT_START + BASE_RELATIVE_URL_REGEX,
        r"@import\1\2{MARKER}/%(base)s/\3".format(MARKER=MARKER)),

    # CSS import: absolute, callable replacement
    (CSS_IMPORT_START + ABSOLUTE_URL_REGEX, absolute_replacement_css_import),

    # CSS url(): same directory
    (CSS_URL_START + SAME_DIR_URL_REGEX,
        r"url(\1{MARKER}/%(base)s/%(accessed_dir)s\2)".format(MARKER=MARKER)),

    # CSS url(): traversal
    (CSS_URL_START + TRAVERSAL_URL_REGEX,
        r"url(\1{MARKER}/%(base)s/%(accessed_dir)s\2/\3)".format(MARKER=MARKER)),

    # CSS url(): base-relative
    (CSS_URL_START + BASE_RELATIVE_URL_REGEX,
        r"url(\1{MARKER}/%(base)s/\2)".format(MARKER=MARKER)),

    # CSS url(): absolute, callable replacement
    (CSS_URL_START + ABSOLUTE_URL_REGEX, absolute_replacement_css_url),
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
    """Transform URLs in content to be relative to the base_url.
    
    Args:
        base_url: The base URL that all transformed URLs should be relative to.
        accessed_url: The URL that was accessed to get this content.
        content: The content to transform.
    
    Returns:
        The transformed content with all URLs made relative to base_url.
    """
    if isinstance(content, bytes):
        content = content.decode('utf-8')
        
    url_obj = urlparse(accessed_url)
    accessed_dir = os.path.dirname(url_obj.path)
    if not accessed_dir.endswith("/"):
        accessed_dir += "/"
    if accessed_dir.startswith("/"):
        accessed_dir = accessed_dir[1:]

    # Use the part of base_url before any slash as the base
    base = base_url.split('/')[0] if '/' in base_url else base_url
        
    for pattern, replacement in REPLACEMENT_REGEXES:
        def safe_replace(match):
            try:
                return make_replacement(replacement, match, base, accessed_dir)
            except Exception as exc:
                print(f"Error applying replacement for regex: {pattern.pattern} with match {match.group(0)}: {exc}")
                return match.group(0)

        try:
            content = pattern.sub(safe_replace, content)
        except Exception as err:
            print(f"Error processing regex pattern {pattern.pattern}: {err}")
            continue

    # Remove the marker
    content = content.replace(MARKER, "")
    return content
