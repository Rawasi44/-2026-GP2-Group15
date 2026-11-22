import argparse
import html
import ipaddress
import json
import mimetypes
import re
import socket
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from app import app
from extensions import db
from models.event import Event


OUTPUT_DIR = Path(app.static_folder) / "images" / "events"
BACKUP_DIR = Path(app.root_path) / "backups"

TIMEOUT = 20
MAX_IMAGE_BYTES = 12 * 1024 * 1024

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}

BAD_HINTS = (
    "favicon", "sprite", "logo", "icon", "avatar", "profile",
    "qr", "loader", "loading", "placeholder", "blank", "pixel",
    "tracking", "badge", "emoji", "flag", "social",
)

GOOD_HINTS = (
    "banner", "hero", "cover", "event", "thumbnail", "poster",
    "desktop", "mobile", "card", "listing", "ticket", "show",
    "experience", "activity",
)


def safe_slug(text):
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower())
    return text.strip("-")


def title_tokens(title):
    stop = {
        "the", "and", "for", "with", "from", "saudi", "arabia",
        "riyadh", "event", "expo", "conference", "summit",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (title or "").lower())
        if len(token) >= 4 and token not in stop
    }


def is_public_http_url(url):
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False

    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return True

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue

        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            return False

    return True


def normalize_candidate(raw, base_url):
    if not raw:
        return None

    raw = html.unescape(raw.strip().strip("'\""))
    raw = raw.replace("\\/", "/")

    if raw.startswith("data:"):
        return None

    absolute = urljoin(base_url, raw)

    if not is_public_http_url(absolute):
        return None

    return absolute


def extract_srcset_urls(value, base_url):
    urls = []

    if not value:
        return urls

    for part in value.split(","):
        candidate = part.strip().split()[0] if part.strip() else ""
        normalized = normalize_candidate(candidate, base_url)
        if normalized:
            urls.append(normalized)

    return urls


def extract_candidate_urls(page_html, base_url):
    candidates = []

    def add(url, source_kind):
        normalized = normalize_candidate(url, base_url)
        if normalized:
            candidates.append((normalized, source_kind))

    # 1) Meta image tags.
    for pattern in (
        r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image)["\']',
    ):
        for match in re.finditer(pattern, page_html, flags=re.I):
            add(match.group(1), "meta")

    # 2) img / source common lazy attributes.
    for tag_match in re.finditer(r'<(?:img|source)\b[^>]*>', page_html, flags=re.I):
        tag = tag_match.group(0)

        for attr in ("src", "data-src", "data-lazy-src", "data-original"):
            match = re.search(
                rf'\b{attr}\s*=\s*["\']([^"\']+)["\']',
                tag,
                flags=re.I,
            )
            if match:
                add(match.group(1), attr)

        for attr in ("srcset", "data-srcset"):
            match = re.search(
                rf'\b{attr}\s*=\s*["\']([^"\']+)["\']',
                tag,
                flags=re.I,
            )
            if match:
                for url in extract_srcset_urls(match.group(1), base_url):
                    candidates.append((url, attr))

    # 3) CSS background-image url(...).
    for match in re.finditer(
        r'url\(\s*["\']?([^)"\']+)["\']?\s*\)',
        page_html,
        flags=re.I,
    ):
        add(match.group(1), "css")

    # 4) JSON / hydrated framework data with image-looking URLs.
    # This catches Next.js / Vue / inline state where there is no <img> yet.
    for match in re.finditer(
        r'["\'](?:image|imageUrl|image_url|thumbnail|thumbnailUrl|cover|banner|poster|desktopImage|mobileImage)["\']\s*:\s*["\']([^"\']+)["\']',
        page_html,
        flags=re.I,
    ):
        add(match.group(1), "json-key")

    # 5) Broad quoted URLs/paths ending in an image extension.
    for match in re.finditer(
        r'["\']([^"\']+\.(?:jpg|jpeg|png|webp|gif|avif)(?:\?[^"\']*)?)["\']',
        page_html,
        flags=re.I,
    ):
        add(match.group(1), "quoted-image")

    # Deduplicate while keeping strongest source kind encountered first.
    seen = set()
    result = []
    for url, kind in candidates:
        if url in seen:
            continue
        seen.add(url)
        result.append((url, kind))

    return result


def score_candidate(url, source_kind, event_title, official_link):
    lower = url.lower()
    score = 0

    source_scores = {
        "meta": 100,
        "json-key": 85,
        "srcset": 70,
        "data-srcset": 70,
        "data-src": 65,
        "data-lazy-src": 65,
        "data-original": 65,
        "src": 55,
        "css": 45,
        "quoted-image": 35,
    }
    score += source_scores.get(source_kind, 20)

    if any(hint in lower for hint in BAD_HINTS):
        score -= 90

    for hint in GOOD_HINTS:
        if hint in lower:
            score += 12

    tokens = title_tokens(event_title)
    url_words = set(re.findall(r"[a-z0-9]+", lower))

    overlap = len(tokens & url_words)
    score += overlap * 18

    official_host = (urlparse(official_link or "").hostname or "").lower()
    candidate_host = (urlparse(url).hostname or "").lower()

    if official_host and candidate_host:
        if candidate_host == official_host:
            score += 20
        elif candidate_host.endswith("." + official_host):
            score += 15

    # Prefer URLs that look like larger/resized presentation assets.
    if re.search(r'(?:1280|1200|1024|800|750|630|550)', lower):
        score += 8

    # Tiny-looking explicit dimensions are usually icons/logos.
    if re.search(r'(?:32x32|48x48|64x64|96x96|128x128)', lower):
        score -= 35

    return score


def extension_from_response(response):
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()

    extension = CONTENT_TYPE_EXTENSIONS.get(content_type)
    if extension:
        return extension

    suffix = Path(urlparse(response.url).path).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix

    guessed = mimetypes.guess_extension(content_type)
    return guessed or ".jpg"


def download_image(session, url):
    if not is_public_http_url(url):
        return None, None, "rejected"

    try:
        parsed = urlparse(url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"

        with session.get(
            url,
            timeout=TIMEOUT,
            stream=True,
            allow_redirects=True,
            headers={
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": referer,
            },
        ) as response:
            if response.status_code >= 400:
                return None, None, f"http_{response.status_code}"

            content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()
            if not content_type.startswith("image/"):
                return None, None, f"not_image:{content_type or 'unknown'}"

            chunks = []
            total = 0

            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return None, None, "too_large"
                chunks.append(chunk)

            data = b"".join(chunks)
            if len(data) < 1024:
                return None, None, "too_small"

            return data, extension_from_response(response), "ok"

    except requests.exceptions.Timeout:
        return None, None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, None, "connection_error"
    except requests.exceptions.SSLError:
        return None, None, "ssl_error"
    except requests.RequestException as exc:
        return None, None, f"request_error:{type(exc).__name__}"


def fetch_page(session, url):
    if not is_public_http_url(url):
        return None, "invalid_url"

    try:
        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        if response.status_code >= 400:
            return None, f"http_{response.status_code}"

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "html" not in content_type:
            return None, f"not_html:{content_type}"

        return (response.url, response.text), "ok"

    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "connection_error"
    except requests.exceptions.SSLError:
        return None, "ssl_error"
    except requests.RequestException as exc:
        return None, f"request_error:{type(exc).__name__}"


def existing_local_image(event_id):
    for path in OUTPUT_DIR.glob(f"event_{event_id}.*"):
        if path.suffix.lower() in IMAGE_EXTENSIONS and path.is_file():
            return path
    return None


def recover_event(session, event):
    local = existing_local_image(event.id)
    if local:
        return {
            "status": "already_local",
            "local_path": f"/static/images/events/{local.name}",
            "source": None,
            "page_reason": None,
            "attempts": [],
        }

    page, page_reason = fetch_page(session, event.official_link)
    if not page:
        return {
            "status": "failed",
            "local_path": None,
            "source": None,
            "page_reason": page_reason,
            "attempts": [],
        }

    final_page_url, page_html = page
    candidates = extract_candidate_urls(page_html, final_page_url)

    ranked = sorted(
        candidates,
        key=lambda item: score_candidate(
            item[0],
            item[1],
            event.title,
            event.official_link,
        ),
        reverse=True,
    )

    attempts = []

    # Try only the best candidates, not every icon on the page.
    for url, source_kind in ranked[:20]:
        score = score_candidate(
            url,
            source_kind,
            event.title,
            event.official_link,
        )

        # Clearly bad candidates are skipped.
        if score < 20:
            continue

        data, extension, reason = download_image(session, url)
        attempts.append({
            "url": url,
            "kind": source_kind,
            "score": score,
            "reason": reason,
        })

        if data:
            filename = f"event_{event.id}{extension}"
            output_path = OUTPUT_DIR / filename
            output_path.write_bytes(data)

            return {
                "status": "downloaded",
                "local_path": f"/static/images/events/{filename}",
                "source": url,
                "page_reason": page_reason,
                "attempts": attempts,
            }

    return {
        "status": "failed",
        "local_path": None,
        "source": None,
        "page_reason": page_reason,
        "attempts": attempts,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Second-pass recovery for AFAQ event images from official page HTML."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--active-only", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    })

    with app.app_context():
        query = Event.query.order_by(Event.id.asc())

        if args.active_only:
            query = query.filter(Event.is_active.is_(True))

        events = query.all()

        # Only process events that do not already have a locally cached image.
        events = [
            event
            for event in events
            if existing_local_image(event.id) is None
        ]

        if args.limit > 0:
            events = events[:args.limit]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        backup_path = BACKUP_DIR / f"event_images_second_pass_before_{timestamp}.json"
        backup_path.write_text(
            json.dumps(
                [
                    {
                        "id": event.id,
                        "title": event.title,
                        "image": event.image,
                        "official_link": event.official_link,
                    }
                    for event in events
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        report = []
        downloaded = 0
        failed = 0

        print(f"Missing-local-image events to inspect: {len(events)}")
        print(f"Mode: {'APPLY' if args.apply else 'PREVIEW (DB not changed)'}")
        print("-" * 84)

        for index, event in enumerate(events, start=1):
            result = recover_event(session, event)

            report.append({
                "event_id": event.id,
                "title": event.title,
                "official_link": event.official_link,
                **result,
            })

            if result["status"] == "downloaded":
                downloaded += 1
                print(f"[{index}/{len(events)}] SAVED  #{event.id} {event.title}")
                print(f"    source: {result['source']}")
                print(f"    local : {result['local_path']}")

                if args.apply:
                    event.image = result["local_path"]
            else:
                failed += 1
                print(f"[{index}/{len(events)}] FAILED #{event.id} {event.title}")
                print(f"    page: {result['page_reason']}")

                for attempt in result["attempts"][:3]:
                    print(
                        f"    {attempt['reason']} "
                        f"(score {attempt['score']}, {attempt['kind']}) "
                        f"-> {attempt['url']}"
                    )

        report_path = BACKUP_DIR / f"event_image_second_pass_{timestamp}.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if args.apply:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
        else:
            db.session.rollback()

        print("-" * 84)
        print(f"Downloaded this pass: {downloaded}")
        print(f"Still failed:         {failed}")
        print(f"Report:               {report_path}")

        if not args.apply:
            print("\nPreview only: database was NOT changed.")
            print("Do not use --apply until you review the downloaded count.")


if __name__ == "__main__":
    main()
