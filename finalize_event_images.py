import argparse
import html as html_lib
import json
import mimetypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

from app import app
from extensions import db
from models.event import Event


BASE_DIR = Path(app.root_path)
STATIC_DIR = Path(app.static_folder)
EVENT_IMAGE_DIR = STATIC_DIR / "images" / "events"
BACKUP_DIR = BASE_DIR / "backups"
MAPPING_FILE = BASE_DIR / "curated_event_image_sources.json"

TIMEOUT = 25
MAX_IMAGE_BYTES = 15 * 1024 * 1024
VALID_LOCAL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".svg"}

CATEGORY_VISUALS = {
    "sport": ("#223248", "#b8925a"),
    "entertain": ("#352c3f", "#b8925a"),
    "festival": ("#46362f", "#b8925a"),
    "culture": ("#2f3d38", "#b8925a"),
    "art": ("#3f3040", "#b8925a"),
    "tech": ("#243941", "#b8925a"),
    "conference": ("#303842", "#b8925a"),
    "exhibition": ("#3b3730", "#b8925a"),
    "business": ("#34302a", "#b8925a"),
    "default": ("#34302a", "#b8925a"),
}


def load_mapping():
    if not MAPPING_FILE.exists():
        raise FileNotFoundError(
            f"Missing {MAPPING_FILE.name}. Put it next to app.py."
        )
    data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    curated = {int(k): v for k, v in data.get("curated", {}).items()}
    rejected = {int(x) for x in data.get("reject_local_ids", [])}
    return curated, rejected


def local_files_for_event(event_id):
    files = []
    for path in EVENT_IMAGE_DIR.glob(f"event_{event_id}.*"):
        if path.is_file() and path.suffix.lower() in VALID_LOCAL_EXTENSIONS:
            files.append(path)
    return sorted(files, key=lambda p: p.name.lower())


def delete_local_files(event_id):
    removed = []
    for path in local_files_for_event(event_id):
        path.unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def extension_from_response(response):
    content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()
    mapping = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/avif": ".avif",
        "image/svg+xml": ".svg",
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = Path(urlparse(response.url).path).suffix.lower()
    if suffix in VALID_LOCAL_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix
    guessed = mimetypes.guess_extension(content_type)
    return guessed if guessed in VALID_LOCAL_EXTENSIONS else ".jpg"


def download_image(session, event_id, url):
    if not url or not url.startswith(("http://", "https://")):
        return None, "invalid_url"
    try:
        response = session.get(
            url,
            timeout=TIMEOUT,
            allow_redirects=True,
            stream=True,
            headers={
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
            },
        )
        if response.status_code >= 400:
            return None, f"http_{response.status_code}"
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].lower()
        if not content_type.startswith("image/"):
            return None, f"not_image:{content_type or 'unknown'}"

        chunks = []
        total = 0
        for chunk in response.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                return None, "too_large"
            chunks.append(chunk)

        data = b"".join(chunks)
        if len(data) < 1024:
            return None, "too_small"

        extension = extension_from_response(response)
        file_path = EVENT_IMAGE_DIR / f"event_{event_id}{extension}"
        file_path.write_bytes(data)
        return file_path, "ok"
    except requests.RequestException as exc:
        return None, type(exc).__name__


def choose_visual_palette(category):
    cat = (category or "").lower()
    for key, palette in CATEGORY_VISUALS.items():
        if key != "default" and key in cat:
            return palette
    return CATEGORY_VISUALS["default"]


def wrap_title(title, max_chars=28, max_lines=3):
    words = (title or "Event").split()
    lines = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(words)) > len(" ".join(lines)):
        lines[-1] = lines[-1][: max(0, max_chars - 1)].rstrip() + "..."
    return lines or ["Event"]


def generate_title_card(event):
    bg, accent = choose_visual_palette(getattr(event, "category", None))
    title_lines = wrap_title(event.title)
    category = html_lib.escape((getattr(event, "category", None) or "Riyadh Event").strip())

    line_svgs = []
    start_y = 250
    for idx, line in enumerate(title_lines):
        escaped = html_lib.escape(line)
        y = start_y + idx * 62
        line_svgs.append(
            f'<text x="64" y="{y}" fill="#f6f1e6" '
            f'font-family="Arial, sans-serif" font-size="42" font-weight="700">'
            f'{escaped}</text>'
        )

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="650" viewBox="0 0 1000 650">
  <defs>
    <linearGradient id="bg" x1="0" x2="1" y1="0" y2="1">
      <stop offset="0%" stop-color="{bg}"/>
      <stop offset="100%" stop-color="#1f1d1a"/>
    </linearGradient>
    <radialGradient id="glow" cx="82%" cy="18%" r="70%">
      <stop offset="0%" stop-color="{accent}" stop-opacity=".52"/>
      <stop offset="100%" stop-color="{accent}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1000" height="650" fill="url(#bg)"/>
  <rect width="1000" height="650" fill="url(#glow)"/>
  <circle cx="820" cy="155" r="118" fill="none" stroke="{accent}" stroke-width="5" opacity=".75"/>
  <circle cx="820" cy="155" r="78" fill="none" stroke="{accent}" stroke-width="2" opacity=".45"/>
  <circle cx="820" cy="155" r="28" fill="{accent}" opacity=".75"/>
  <text x="64" y="100" fill="{accent}" font-family="Arial, sans-serif" font-size="26" font-weight="700" letter-spacing="5">
    AFAQ - RIYADH EVENTS
  </text>
  {''.join(line_svgs)}
  <rect x="64" y="500" width="350" height="2" fill="{accent}" opacity=".8"/>
  <text x="64" y="548" fill="#d9d1c5" font-family="Arial, sans-serif" font-size="24">{category}</text>
  <text x="64" y="590" fill="#9e9589" font-family="Arial, sans-serif" font-size="18">
    Local event visual - not an official event photo
  </text>
</svg>'''

    path = EVENT_IMAGE_DIR / f"event_{event.id}.svg"
    path.write_text(svg, encoding="utf-8")
    return path


def local_url(path):
    return f"/static/images/events/{path.name}"


def main():
    parser = argparse.ArgumentParser(description="Finalize AFAQ event images safely.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--active-only", action="store_true")
    args = parser.parse_args()

    EVENT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    curated, rejected = load_mapping()

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0 Safari/537.36"
        )
    })

    with app.app_context():
        query = Event.query.order_by(Event.id.asc())
        if args.active_only:
            query = query.filter(Event.is_active.is_(True))
        events = query.all()

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"final_event_images_before_{timestamp}.json"
        backup_path.write_text(
            json.dumps(
                [{"id": e.id, "title": e.title, "image": e.image} for e in events],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        stats = {
            "kept_existing": 0,
            "curated_downloaded": 0,
            "generated_visual": 0,
            "curated_failed": 0,
            "db_updates": 0,
        }
        plan = []

        print(f"Events: {len(events)}")
        print(f"Backup: {backup_path}")
        print(f"Mode: {'APPLY' if args.apply else 'PREVIEW'}")
        print("-" * 80)

        for event in events:
            if event.id in rejected:
                delete_local_files(event.id)

            chosen_path = None
            basis = None
            curated_entry = curated.get(event.id)

            if curated_entry:
                delete_local_files(event.id)
                chosen_path, reason = download_image(
                    session, event.id, curated_entry.get("image_url")
                )
                if chosen_path:
                    basis = "curated"
                    stats["curated_downloaded"] += 1
                else:
                    stats["curated_failed"] += 1
                    print(f"CURATED FAIL #{event.id} {event.title}: {reason}")

            if chosen_path is None:
                existing = local_files_for_event(event.id)
                if existing:
                    chosen_path = existing[0]
                    basis = "existing-local"
                    stats["kept_existing"] += 1

            if chosen_path is None:
                chosen_path = generate_title_card(event)
                basis = "generated-title-card"
                stats["generated_visual"] += 1

            new_url = local_url(chosen_path)
            plan.append({
                "event_id": event.id,
                "title": event.title,
                "basis": basis,
                "old_image": event.image,
                "new_image": new_url,
                "source": curated_entry.get("source_page") if curated_entry else None,
            })

            print(f"#{event.id:<3} {basis:<20} {event.title}")

            if args.apply and event.image != new_url:
                event.image = new_url
                stats["db_updates"] += 1

        plan_path = BACKUP_DIR / f"final_event_image_plan_{timestamp}.json"
        plan_path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
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
        print("RESULT")
        print(f"Kept existing local: {stats['kept_existing']}")
        print(f"Curated downloaded:  {stats['curated_downloaded']}")
        print(f"Generated visuals:   {stats['generated_visual']}")
        print(f"Curated failures:    {stats['curated_failed']}")
        print(f"DB updates:          {stats['db_updates'] if args.apply else 0}")
        print(f"Plan:                {plan_path}")

        if not args.apply:
            print("\nPreview complete. Database was NOT changed.")
        else:
            print("\nDone. Every processed event now points to a local image/visual.")


if __name__ == "__main__":
    main()
