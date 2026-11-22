import argparse
import html
import ipaddress
import json
import mimetypes
import re
import socket
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests

from app import app
from extensions import db
from models.event import Event


OUTPUT_DIR = Path(app.static_folder) / "images" / "events"
BACKUP_DIR = Path(app.root_path) / "backups"

TIMEOUT = 20
MAX_IMAGE_BYTES = 12 * 1024 * 1024

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
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
        # DNS failure will be reported by requests later.
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


def source_variants(original_url):
    """Return safe variants for old Riyadh image URLs."""
    if not original_url or not is_public_http_url(original_url):
        return []

    urls = [original_url]

    try:
        parsed = urlparse(original_url)
        host = (parsed.hostname or "").lower()

        if host == "api.riyadh.sa":
            for alt_host in ("www.riyadh.sa", "riyadh.sa"):
                urls.append(urlunparse(parsed._replace(netloc=alt_host)))
    except Exception:
        pass

    result = []
    seen = set()
    for url in urls:
        if url and url not in seen and is_public_http_url(url):
            seen.add(url)
            result.append(url)

    return result


def extension_from_response(response):
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()

    extension = CONTENT_TYPE_EXTENSIONS.get(content_type)
    if extension:
        return extension

    suffix = Path(urlparse(response.url).path).suffix.lower()
    if suffix in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        return ".jpg" if suffix == ".jpeg" else suffix

    guessed = mimetypes.guess_extension(content_type)
    return guessed or ".jpg"


def download_image(session, url):
    if not is_public_http_url(url):
        return None, None, "rejected_non_public_url"

    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"

    try:
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
            status = response.status_code
            content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()

            if status >= 400:
                return None, None, f"http_{status}"

            if not content_type.startswith("image/"):
                return None, None, f"not_image:{content_type or 'unknown'}"

            chunks = []
            total = 0

            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue

                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return None, None, "image_too_large"

                chunks.append(chunk)

            data = b"".join(chunks)

            if len(data) < 256:
                return None, None, "image_too_small"

            return data, extension_from_response(response), "ok"

    except requests.exceptions.SSLError as exc:
        return None, None, f"ssl_error:{type(exc).__name__}"
    except requests.exceptions.ConnectionError as exc:
        return None, None, f"connection_error:{type(exc).__name__}"
    except requests.exceptions.Timeout:
        return None, None, "timeout"
    except requests.RequestException as exc:
        return None, None, f"request_error:{type(exc).__name__}"


def extract_meta_image(page_html, base_url):
    patterns = [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if not match:
            continue

        candidate = html.unescape(match.group(1).strip())
        absolute = urljoin(base_url, candidate)

        if is_public_http_url(absolute):
            return absolute

    return None


def official_page_image(session, official_link):
    if not official_link or not is_public_http_url(official_link):
        return None, "no_public_official_link"

    try:
        response = session.get(
            official_link,
            timeout=TIMEOUT,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        if response.status_code >= 400:
            return None, f"official_page_http_{response.status_code}"

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type:
            return None, f"official_page_not_html:{content_type or 'unknown'}"

        image_url = extract_meta_image(response.text, response.url)
        if not image_url:
            return None, "official_page_no_meta_image"

        return image_url, "ok"

    except requests.exceptions.ConnectionError:
        return None, "official_page_connection_error"
    except requests.exceptions.Timeout:
        return None, "official_page_timeout"
    except requests.RequestException as exc:
        return None, f"official_page_request_error:{type(exc).__name__}"


def save_local_image(event_id, data, extension):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    filename = f"event_{event_id}{extension}"
    path = OUTPUT_DIR / filename
    path.write_bytes(data)

    return f"/static/images/events/{filename}"


def localize_event_image(session, event):
    if event.image and (
        event.image.startswith("/static/")
        or event.image.startswith("static/")
    ):
        return {
            "status": "already_local",
            "local_path": event.image,
            "source": event.image,
            "attempts": [],
        }

    attempts = []

    # A) Existing image URL and Riyadh host variants.
    for url in source_variants(event.image):
        data, extension, reason = download_image(session, url)
        attempts.append({"url": url, "reason": reason})

        if data:
            local_path = save_local_image(event.id, data, extension)
            return {
                "status": "downloaded",
                "local_path": local_path,
                "source": url,
                "attempts": attempts,
            }

    # B) Official event page -> og:image / twitter:image.
    meta_image, page_reason = official_page_image(session, event.official_link)

    if meta_image:
        data, extension, reason = download_image(session, meta_image)
        attempts.append({"url": meta_image, "reason": reason})

        if data:
            local_path = save_local_image(event.id, data, extension)
            return {
                "status": "downloaded",
                "local_path": local_path,
                "source": meta_image,
                "attempts": attempts,
            }
    else:
        attempts.append({
            "url": event.official_link or "",
            "reason": page_reason,
        })

    return {
        "status": "failed",
        "local_path": None,
        "source": None,
        "attempts": attempts,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Cache AFAQ event images locally with detailed diagnostics."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit successful local image paths into Event.image.",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Inspect active events only.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Inspect only the first N events. 0 means all.",
    )
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

        if args.limit and args.limit > 0:
            events = events[:args.limit]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        backup_path = BACKUP_DIR / f"event_images_before_cache_{timestamp}.json"
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

        diagnostics = []
        downloaded = 0
        already_local = 0
        failed = 0

        print(f"Events to inspect: {len(events)}")
        print(f"Backup: {backup_path}")
        print(f"Mode: {'APPLY' if args.apply else 'PREVIEW (DB not changed)'}")
        print("-" * 80)

        for index, event in enumerate(events, start=1):
            result = localize_event_image(session, event)

            diagnostics.append({
                "event_id": event.id,
                "title": event.title,
                "status": result["status"],
                "old_image": event.image,
                "official_link": event.official_link,
                "local_path": result["local_path"],
                "source": result["source"],
                "attempts": result["attempts"],
            })

            if result["status"] == "already_local":
                already_local += 1
                print(f"[{index}/{len(events)}] LOCAL   #{event.id} {event.title}")
                continue

            if result["status"] == "downloaded":
                downloaded += 1
                print(f"[{index}/{len(events)}] SAVED   #{event.id} {event.title}")
                print(f"    source: {result['source']}")
                print(f"    local : {result['local_path']}")

                if args.apply:
                    event.image = result["local_path"]

                continue

            failed += 1
            print(f"[{index}/{len(events)}] FAILED  #{event.id} {event.title}")

            # Print only the last few failure reasons to keep terminal readable.
            for attempt in result["attempts"][-3:]:
                print(f"    {attempt['reason']} -> {attempt['url']}")

        report_path = BACKUP_DIR / f"event_image_diagnostics_{timestamp}.json"
        report_path.write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2),
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

        print("-" * 80)
        print(f"Downloaded:    {downloaded}")
        print(f"Already local: {already_local}")
        print(f"Failed:        {failed}")
        print(f"Diagnostics:   {report_path}")

        if not args.apply:
            print("\nPreview mode: database was NOT changed.")
            print("Do NOT use --apply unless the downloaded count is greater than 0.")


if __name__ == "__main__":
    main()
