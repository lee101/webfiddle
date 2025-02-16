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

# Build uncompiled regexes with updated replacement strings to produce expected output
UNCOMPILED_REGEXES = [
    # For tags with absolute URLs
    (TAG_START + ABSOLUTE_URL_REGEX,
        r"\g<1>\g<2>\g<3>/%(fiddle)s/\g<6>\g<7>"),  # Use group numbers from combined regex

    # For CSS imports
    (CSS_IMPORT_START + ABSOLUTE_URL_REGEX,
        r"@import\1\2/%(fiddle)s/\g<5>\g<6>';"),  # Fix group references

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
    accessed_dir = os.path.dirname(url_obj.path).lstrip('/')
    if accessed_dir and not accessed_dir.endswith("/"):
        accessed_dir += "/"

    # Extract domain components more safely
    base_parts = base_url.split('/', 1)
    fiddle_name = base_parts[0]
    current_domain = base_parts[1].split('/')[0] if len(base_parts) > 1 else ""

    sub_dict = {
        "fiddle": fiddle_name,
        "base": current_domain,
        "accessed_dir": accessed_dir
    }

    # Add validation for substitution patterns
    for pattern, replacement in REPLACEMENT_REGEXES:
        try:
            rep_string = replacement % sub_dict
            content = pattern.sub(rep_string, content)
        except KeyError as err:
            print(f"Missing key in substitution: {err}")
            continue
        except Exception as err:
            print(f"Error processing regex pattern {pattern.pattern}: {err}")
            continue

    # Enhanced URL cleanup
    content = re.sub(r'(?<!:)/{2,}', '/', content)
    # Fix duplicate fiddle names in path
    content = re.sub(r'/([^/]+?)(/\1)+/', r'/\1/', content)
    
    # Additional cleanup for manifest paths
    content = re.sub(r'/(\w+-\w+?)/.*?/\1/', r'/\1/', content)
    
    content = content.replace(MARKER, "")

    # Final validation before return
    if not isinstance(content, str):
        content = content.decode('utf-8', errors='replace')
    
    # Ensure proper encoding
    return content.encode('utf-8').decode('utf-8')  # Normalize encoding
