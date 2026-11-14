import argparse
import html
import json
import mimetypes
import os
import re
import socket
import ipaddress
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin, urlunparse

import requests

from app import app
from extensions import db
from models.event import Event


OUTPUT_DIR = Path(app.static_folder) / "images" / "events"
BACKUP_DIR = Path(app.root_path) / "backups"

TIMEOUT = 18
MAX_IMAGE_BYTES = 12 * 1024 * 1024

ALLOWED_SOURCE_SUFFIXES = (
    "riyadh.sa",
    "webook.com",
)

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def is_allowed_public_url(url):
    """Allow only HTTPS/HTTP URLs on approved public source domains."""
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return False

    if not any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_SOURCE_SUFFIXES):
        return False

    # Basic SSRF safety: reject hosts resolving to private/loopback/link-local IPs.
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_reserved
                or ip.is_multicast
            ):
                return False
    except Exception:
        # DNS failures are handled later by requests.
        pass

    return True


def candidate_image_urls(original_url):
    """Try the stored URL first, then safe Riyadh host variants."""
    if not original_url:
        return []

    urls = [original_url]

    try:
        parsed = urlparse(original_url)
        host = (parsed.hostname or "").lower()

        if host == "api.riyadh.sa":
            for alt_host in ("www.riyadh.sa", "riyadh.sa"):
                alt = parsed._replace(netloc=alt_host)
                urls.append(urlunparse(alt))
    except Exception:
        pass

    # Preserve order, remove duplicates.
    result = []
    seen = set()
    for url in urls:
        if url and url not in seen and is_allowed_public_url(url):
            seen.add(url)
            result.append(url)

    return result


def extract_meta_image(page_html, base_url):
    """Extract og:image / twitter:image from an official event page."""
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]

    for pattern in patterns:
        match = re.search(pattern, page_html, flags=re.I)
        if match:
            value = html.unescape(match.group(1).strip())
            absolute = urljoin(base_url, value)
            if is_allowed_public_url(absolute):
                return absolute

    return None


def get_official_page_image(session, official_link):
    if not official_link or not is_allowed_public_url(official_link):
        return None

    try:
        response = session.get(
            official_link,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        response.raise_for_status()

        content_type = (response.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type:
            return None

        return extract_meta_image(response.text, response.url)
    except requests.RequestException:
        return None


def download_image(session, url):
    if not is_allowed_public_url(url):
        return None, None

    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
    }

    try:
        with session.get(
            url,
            headers=headers,
            timeout=TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()

            content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()
            if not content_type.startswith("image/"):
                return None, None

            chunks = []
            total = 0

            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    return None, None
                chunks.append(chunk)

            data = b"".join(chunks)
            if len(data) < 256:
                return None, None

            extension = CONTENT_TYPE_EXTENSIONS.get(content_type)
            if not extension:
                path_ext = Path(urlparse(response.url).path).suffix.lower()
                if path_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                    extension = ".jpg" if path_ext == ".jpeg" else path_ext
                else:
                    extension = mimetypes.guess_extension(content_type) or ".jpg"

            return data, extension

    except requests.RequestException:
        return None, None


def localize_event_image(session, event):
    # Already local: nothing to do.
    if event.image and (
        event.image.startswith("/static/")
        or event.image.startswith("static/")
    ):
        return "already_local", event.image, None

    attempted = []

    # 1) Stored image URL + safe host variants.
    for url in candidate_image_urls(event.image):
        attempted.append(url)
        data, extension = download_image(session, url)
        if data:
            filename = f"event_{event.id}{extension}"
            file_path = OUTPUT_DIR / filename
            file_path.write_bytes(data)
            return "downloaded", f"/static/images/events/{filename}", url

    # 2) Fallback: official event page's og:image / twitter:image.
    page_image = get_official_page_image(session, event.official_link)
    if page_image:
        attempted.append(page_image)
        data, extension = download_image(session, page_image)
        if data:
            filename = f"event_{event.id}{extension}"
            file_path = OUTPUT_DIR / filename
            file_path.write_bytes(data)
            return "downloaded", f"/static/images/events/{filename}", page_image

    return "failed", None, attempted


def main():
    parser = argparse.ArgumentParser(
        description="Cache AFAQ event images locally and optionally update Event.image."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit successful local image paths to the database.",
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        help="Process only active events.",
    )
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
    })

    with app.app_context():
        query = Event.query.order_by(Event.id.asc())
        if args.active_only:
            query = query.filter(Event.is_active.is_(True))

        events = query.all()

        backup_rows = [
            {
                "id": event.id,
                "title": event.title,
                "image": event.image,
                "official_link": event.official_link,
            }
            for event in events
        ]

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"event_images_before_cache_{timestamp}.json"
        backup_path.write_text(
            json.dumps(backup_rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        downloaded = 0
        already_local = 0
        failed = 0

        print(f"Events to inspect: {len(events)}")
        print(f"Backup: {backup_path}")
        print(f"Mode: {'APPLY' if args.apply else 'PREVIEW (DB not changed)'}")
        print("-" * 72)

        for index, event in enumerate(events, start=1):
            status, local_path, source = localize_event_image(session, event)

            if status == "already_local":
                already_local += 1
                print(f"[{index}/{len(events)}] LOCAL   #{event.id} {event.title}")
                continue

            if status == "downloaded":
                downloaded += 1
                print(f"[{index}/{len(events)}] SAVED   #{event.id} {event.title}")
                print(f"    source: {source}")
                print(f"    local : {local_path}")

                if args.apply:
                    event.image = local_path
                continue

            failed += 1
            print(f"[{index}/{len(events)}] FAILED  #{event.id} {event.title}")

        if args.apply:
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
        else:
            db.session.rollback()

        print("-" * 72)
        print(f"Downloaded:    {downloaded}")
        print(f"Already local: {already_local}")
        print(f"Failed:        {failed}")

        if not args.apply:
            print("\nPreview mode: database was NOT changed.")
            print("If the downloaded count looks good, run again with --apply.")
        else:
            print("\nDatabase updated for successfully cached images only.")
            print("Failed events were left unchanged and will still use the AFAQ fallback.")


if __name__ == "__main__":
    main()
