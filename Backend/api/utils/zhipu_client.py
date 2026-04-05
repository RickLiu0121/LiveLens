import os
import io
import re
import json
import logging
import httpx
import boto3
from botocore.config import Config
from typing import List, Optional

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None

try:
    from zhipuai import ZhipuAI
except ImportError:
    ZhipuAI = None

logger = logging.getLogger(__name__)

# We will initialize the client lazily inside the function 
# to ensure load_dotenv() has already populated os.environ.
_client = None

def get_client():
    global _client
    if _client is None:
        if ZhipuAI is None:
            print("ZhipuAI package is not installed.")
            return None
        
        api_key = os.environ.get("ZHIPUAI_API_KEY")
        if not api_key:
            print("ERROR: ZHIPUAI_API_KEY environment variable is missing!")
            return None
            
        try:
            _client = ZhipuAI(api_key=api_key)
        except Exception as e:
            print(f"Failed to initialize ZhipuAI client: {e}")
            return None
    return _client

def extract_tags(review_text: str) -> List[str]:
    """
    Extracts 1 to 5 concise tags from the given review text using Zhipu AI.
    """
    client = get_client()
    if not client:
        return []

    if not review_text or not review_text.strip():
        return []

    prompt = (
        "You are an AI assistant for a live event review platform. "
        "Your task is to analyze the following review text and extract 1 to 5 highly relevant, "
        "concise tags (1-3 words each) that summarize the key aspects of the user's experience "
        "(e.g., 'Great View', 'Loud Sound', 'Expensive', 'Friendly Staff', 'Comfortable Seats').\n\n"
        "Return ONLY a valid JSON array of strings containing the tags, and nothing else. "
        "Example output: [\"Great View\", \"Loud Sound\"]\n\n"
        f"Review text:\n{review_text}"
    )

    try:
        print(f"DEBUG: Starting ZhipuAI request (review length: {len(review_text)} characters)...")
        response = client.chat.completions.create(
            model="glm-4",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            timeout=60, # Increased timeout for cloud environment
        )
        print("DEBUG: ZhipuAI response received.")
        
        # Extract the content from the response
        content = response.choices[0].message.content
        
        # Clean the response in case the model adds markdown formatting (e.g. ```json ... ```)
        content = content.replace("```json", "").replace("```", "").strip()
        
        # Parse the JSON string into a Python list
        try:
            tags = json.loads(content)
        except json.JSONDecodeError as e:
            print(f"DEBUG: Failed to parse AI response as JSON. Content: {content}")
            return []
        
        if isinstance(tags, list):
            # Limit to max 5 tags just in case
            return tags[:5]
        else:
             print(f"DEBUG: ZhipuAI returned non-list format: {content}")
             return []
             
    except Exception as e:
        print(f"CRITICAL: extract_tags failed. Error type: {type(e).__name__}, Message: {str(e)}")
        import traceback
        traceback.print_exc()
        return []


_TM_HEADERS = {"User-Agent": "LiveLens/1.0"}

def _get_tm_seatmap_urls(venue_name: str):
    """Fetch Ticketmaster venue_id, PNG seatmap URL and SVG seatmap URL for a venue."""
    tm_key = os.environ.get("TICKETMASTER_API_KEY")
    if not tm_key:
        return None, None, None
    try:
        # 1. Find venue
        r = httpx.get(
            "https://app.ticketmaster.com/discovery/v2/venues.json",
            params={"keyword": venue_name, "apikey": tm_key, "size": 1},
            headers=_TM_HEADERS,
            timeout=10,
        )
        venues = r.json().get("_embedded", {}).get("venues", [])
        if not venues:
            return None, None, None
        venue_id = venues[0]["id"]

        # 2. Get an upcoming event for that venue
        r2 = httpx.get(
            "https://app.ticketmaster.com/discovery/v2/events.json",
            params={"venueId": venue_id, "apikey": tm_key, "size": 1},
            headers=_TM_HEADERS,
            timeout=10,
        )
        events = r2.json().get("_embedded", {}).get("events", [])
        if not events:
            return venue_id, None, None
        # Get seatmap URL from event response (contains the correct internal event ID)
        png_url = events[0].get("seatmap", {}).get("staticUrl")
        if not png_url:
            return venue_id, None, None
        # Derive SVG URL from PNG URL by replacing type=png with type=svg
        svg_url = png_url.replace("type=png", "type=svg")
        return venue_id, png_url, svg_url
    except Exception as e:
        logger.error(f"TM seatmap lookup failed: {e}")
        return None, None, None


def _get_section_center(svg_content: str, section: str):
    """Parse SVG and return (px_x, px_y) center of the given section. SVG viewBox is 10240x7680, image 1024x768."""
    SCALE = 10

    # Strategy 1: path with id="<section>"
    path_match = re.search(rf'<path[^>]*id="{re.escape(section)}"[^>]*/>', svg_content)
    if path_match:
        d_match = re.search(r'd="([^"]+)"', path_match.group(0))
        if d_match:
            nums = [float(n) for n in re.findall(r'[-+]?\d+\.?\d*', d_match.group(1))]
            coords = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
            if coords:
                return (
                    int(sum(c[0] for c in coords) / len(coords) / SCALE),
                    int(sum(c[1] for c in coords) / len(coords) / SCALE),
                )

    # Strategy 2: text element containing the section label
    text_match = re.search(
        rf'<text[^>]*x="([^"]+)"[^>]*y="([^"]+)"[^>]*>\s*{re.escape(section)}\s*</text>',
        svg_content,
    )
    if text_match:
        x = int(float(text_match.group(1)) / SCALE)
        y = int(float(text_match.group(2)) / SCALE)
        return x, y

    return None


def _upload_to_s3(img_bytes: bytes, s3_key: str) -> Optional[str]:
    """Upload PNG bytes to S3 and return public URL."""
    bucket = os.environ.get("S3_BUCKET_NAME", "livelens-images")
    region = os.environ.get("AWS_REGION", "us-east-2")
    try:
        s3 = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            aws_session_token=os.environ.get("AWS_SESSION_TOKEN"),
            config=Config(s3={"addressing_style": "virtual"}, signature_version="s3v4"),
        )
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=img_bytes,
            ContentType="image/png",
        )
        return f"https://{bucket}.s3.{region}.amazonaws.com/{s3_key}"
    except Exception as e:
        logger.error(f"S3 upload failed: {e}")
        return None


def generate_seat_view_image(
    venue_name: str,
    section: str,
    row: str,
    seat_number: str,
) -> Optional[str]:
    """
    Generate a seatmap image with a pin on the given section using Ticketmaster seatmap + PIL.
    Falls back to cogview-3-flash AI generation if Ticketmaster data is unavailable.
    Returns a public image URL on success, or None on failure.
    """
    if Image is not None:
        try:
            _, png_url, svg_url = _get_tm_seatmap_urls(venue_name)
            if png_url and svg_url:
                svg_content = httpx.get(svg_url, timeout=15, headers=_TM_HEADERS).text
                center = _get_section_center(svg_content, section)
                if center:
                    x, y = center
                    png_bytes = httpx.get(png_url, timeout=15, headers=_TM_HEADERS).content
                    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
                    draw = ImageDraw.Draw(img)
                    # Outer glow
                    draw.ellipse([x - 22, y - 22, x + 22, y + 22], fill=(220, 38, 38, 80))
                    # Red circle
                    draw.ellipse([x - 16, y - 16, x + 16, y + 16], fill=(220, 38, 38, 230))
                    # White center dot
                    draw.ellipse([x - 6, y - 6, x + 6, y + 6], fill=(255, 255, 255, 255))

                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    img_bytes = buf.getvalue()

                    safe_venue = re.sub(r"[^a-z0-9]", "_", venue_name.lower())
                    s3_key = f"seatmaps/{safe_venue}_sec{section}.png"
                    url = _upload_to_s3(img_bytes, s3_key)
                    if url:
                        logger.info(f"Pinned seatmap uploaded: {url}")
                        return url
        except Exception as e:
            logger.error(f"Seatmap pin generation failed, falling back to AI: {e}")

    # Fallback: AI image generation
    api_key = os.environ.get("ZHIPUAI_API_KEY")
    if not api_key:
        logger.error("ZHIPUAI_API_KEY not configured")
        return None

    prompt = (
        f"A clean, professional top-down 2D seat map diagram of {venue_name} arena. "
        f"The map shows the stage at the top center as a wide rectangle labeled 'STAGE'. "
        f"Sections are arranged in a semicircle around the stage, each clearly labeled with section numbers. "
        f"Section {section}, Row {row}, Seat {seat_number} is highlighted with a bright red/yellow marker and a label arrow pointing to it. "
        f"The highlighted seat stands out clearly against the other seats. "
        f"Clean vector-style infographic, dark background, modern design, labeled sections and rows, "
        f"professional venue floor plan illustration."
    )
    try:
        resp = httpx.post(
            "https://open.bigmodel.cn/api/paas/v4/images/generations",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": "cogview-3-flash", "prompt": prompt, "size": "1024x1024"},
            timeout=60.0,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["url"]
    except Exception as e:
        logger.error(f"AI image generation failed: {e}")
        return None

