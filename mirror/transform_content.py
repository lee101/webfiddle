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
    # Keep both protocol and domain in the path
    return f"{tag}{equals}{quote}/{protocol}//{domain}{path}"

def absolute_replacement_css_import(match):
    protocol = match.group(3) or "http:"  # Group 3 is the protocol
    domain = match.group(5)  # Group 5 is the domain after CSS_IMPORT_START + ABSOLUTE_URL_REGEX
    path = match.group(6)    # Group 6 is the path
    # Keep both protocol and domain in the path
    return f"@import '/{protocol}//{domain}{path}';"

def absolute_replacement_css_url(match):
    protocol = match.group(2) or "http:"  # Group 2 is the protocol
    domain = match.group(4)  # Group 4 is the domain after CSS_URL_START + ABSOLUTE_URL_REGEX
    path = match.group(5)    # Group 5 is the path
    # Keep both protocol and domain in the path
    return f"url('/{protocol}//{domain}{path}')"

def make_replacement(pattern_type, match, base, accessed_dir):
    """Helper function to handle both string and function replacements"""
    if callable(pattern_type):
        return pattern_type(match)
    else:
        # Include the full domain path in the replacement
        formatted = pattern_type % {"base": base, "accessed_dir": accessed_dir}
        return match.expand(formatted)

# Build uncompiled regexes with updated replacement strings to produce expected output
UNCOMPILED_REGEXES = [
    # For tags with same-dir URLs (e.g. <img src=...>)
    (TAG_START + SAME_DIR_URL_REGEX,
        r"\g<1>\g<2>\g<3>/%(base)s/%(accessed_dir)s\g<4>"),

    # For tags with traversal URLs
    (TAG_START + TRAVERSAL_URL_REGEX,
        r"\g<1>\g<2>\g<3>/%(base)s/%(accessed_dir)s\g<4>/\g<5>"),

    # For tags with base-relative URLs
    (TAG_START + BASE_RELATIVE_URL_REGEX,
        r"\g<1>\g<2>\g<3>/%(base)s\g<4>"),

    # For tags with root directory URLs
    (TAG_START + ROOT_DIR_URL_REGEX,
        r"\g<1>\g<2>\g<3>/%(base)s"),

    # For tags with absolute URLs, use callable replacement
    (TAG_START + ABSOLUTE_URL_REGEX, absolute_replacement_tag),

    # CSS import: same directory
    (CSS_IMPORT_START + SAME_DIR_URL_REGEX,
        r"@import '/%(base)s/%(accessed_dir)s\g<3>';"),

    # CSS import: traversal
    (CSS_IMPORT_START + TRAVERSAL_URL_REGEX,
        r"@import '/%(base)s/%(accessed_dir)s\g<3>/\g<4>';"),

    # CSS import: base-relative
    (CSS_IMPORT_START + BASE_RELATIVE_URL_REGEX,
        r"@import '/%(base)s\g<3>';"),

    # CSS import: absolute, callable replacement
    (CSS_IMPORT_START + ABSOLUTE_URL_REGEX, absolute_replacement_css_import),

    # CSS url(): same directory
    (CSS_URL_START + SAME_DIR_URL_REGEX,
        r"url('/%(base)s/%(accessed_dir)s\g<2>')"),

    # CSS url(): traversal
    (CSS_URL_START + TRAVERSAL_URL_REGEX,
        r"url('/%(base)s/%(accessed_dir)s\g<2>/\g<3>')"),

    # CSS url(): base-relative
    (CSS_URL_START + BASE_RELATIVE_URL_REGEX,
        r"url('/%(base)s\g<2>')"),

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

    # Split base_url to handle protocol, domain and path separately
    base_parts = base_url.split('/', 1)
    base = base_parts[0]
    base_path = base_parts[1] if len(base_parts) > 1 else ""
    
    # Extract protocol from accessed_url
    protocol = url_obj.scheme + ":" if url_obj.scheme else "http:"
    
    # Ensure base_path ends with a slash if it's not empty
    if base_path and not base_path.endswith('/'):
        base_path += '/'
        
    # Include the protocol and full domain in the base path
    prefix = f"/{protocol}//{base}/{base_path}{accessed_dir}" if accessed_dir else f"/{protocol}//{base}/{base_path}"

    for pattern, replacement in REPLACEMENT_REGEXES:
        if callable(replacement):
            def safe_replace(m):
                try:
                    return replacement(m)
                except Exception as exc:
                    print(f"Error applying replacement for regex: {pattern.pattern} with match {m.group(0)}: {exc}")
                    return m.group(0)

            try:
                content = pattern.sub(safe_replace, content)
            except Exception as err:
                print(f"Error processing regex pattern {pattern.pattern}: {err}")
                continue
        else:
            try:
                rep_string = replacement % {"base": f"{protocol}//{base}", "accessed_dir": accessed_dir}
                content = pattern.sub(rep_string, content)
            except Exception as err:
                print(f"Error processing regex pattern {pattern.pattern}: {err}")
                continue

    # Remove the marker
    content = content.replace(MARKER, "")
    return content
