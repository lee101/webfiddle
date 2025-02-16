import os
import re
from urllib.parse import urlparse

# Unique marker to prevent re-transformation
MARKER = "###TRANSFORMED###"

# ###############################################################################

# Updated absolute URL regex to capture domain and path separately
ABSOLUTE_URL_REGEX = r"(http(s?):)?//([^/]+)(/[^\"'> \t\)]+)"

# URLs that are relative to the base of the current hostname.
# Updated to use non‐capturing groups for protocol to avoid unintended capturing.
BASE_RELATIVE_URL_REGEX = r"/(?!(?:(?:/)|(?:http(?:s)?://)|(?:url\(\))))([^\"'> \t\)]*)"

# URLs that have '../' or './' to start off their paths.
TRAVERSAL_URL_REGEX = r"((?:\.\./|\./))/(?!(?:(?:/)|(?:http(?:s)?://)|(?:url\(\))))([^\"'> \t\)]*)"

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
    protocol = match.group(4) or "http:"  # Group 4 is the protocol (http: or https:), default to http:
    domain = match.group(6)  # Group 6 is the domain after TAG_START + ABSOLUTE_URL_REGEX
    path = match.group(7)    # Group 7 is the path
    # Preserve fiddle context by using it as the root of the path
    return f"{tag}{equals}{quote}/{fiddle_name}/{domain}{path}"

def absolute_replacement_css_import(match):
    protocol = match.group(3) or "http:"  # Group 3 is the protocol
    domain = match.group(5)  # Group 5 is the domain after CSS_IMPORT_START + ABSOLUTE_URL_REGEX
    path = match.group(6)    # Group 6 is the path
    # Preserve fiddle context
    return f"@import '/{fiddle_name}/{domain}{path}';"

def absolute_replacement_css_url(match):
    protocol = match.group(2) or "http:"  # Group 2 is the protocol
    domain = match.group(4)  # Group 4 is the domain after CSS_URL_START + ABSOLUTE_URL_REGEX
    path = match.group(5)    # Group 5 is the path
    # Preserve fiddle context
    return f"url('/{fiddle_name}/{domain}{path}')"

def make_replacement(pattern_type, match, base, accessed_dir):
    """Helper function to handle both string and function replacements"""
    if callable(pattern_type):
        return pattern_type(match)
    else:
        # Include fiddle context in the replacement
        formatted = pattern_type % {
            "fiddle": fiddle_name,
            "base": current_domain,
            "accessed_dir": accessed_dir
        }
        return match.expand(formatted)

# Build uncompiled regexes with updated replacement strings to produce expected output
UNCOMPILED_REGEXES = [
    # For tags with absolute URLs
    (TAG_START + ABSOLUTE_URL_REGEX,
        r"\g<1>\g<2>\g<3>/%(fiddle)s/\g<6>\g<7>"),  # Directly use captured domain

    # For CSS imports
    (CSS_IMPORT_START + ABSOLUTE_URL_REGEX,
        r"@import '/%(fiddle)s/\g<5>\g<6>';"),

    # For CSS URLs
    (CSS_URL_START + ABSOLUTE_URL_REGEX,
        r"url('/%(fiddle)s/\g<4>\g<5>')"),
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
        base_url: The base URL that all transformed URLs should be relative to (fiddle/domain format).
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

    # Use single-level base parsing
    base_parts = base_url.split('/', 1)
    fiddle_name = base_parts[0]
    current_domain = base_parts[1].split('/')[0] if len(base_parts) > 1 else ""  # Get primary domain

    sub_dict = {
        "fiddle": fiddle_name,
        "base": current_domain,  # Just the domain without path
        "accessed_dir": accessed_dir
    }

    for pattern, replacement in REPLACEMENT_REGEXES:
        if callable(replacement):
            # Remove callable replacements as we're using string patterns now
            continue
        try:
            rep_string = replacement % sub_dict
            content = pattern.sub(rep_string, content)
        except Exception as err:
            print(f"Error processing regex pattern {pattern.pattern}: {err}")
            continue

    content = content.replace(MARKER, "")
    return content
