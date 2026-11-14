"""Apply the reviewed AFAQ image preview using files already on disk."""

import argparse
import json
from datetime import datetime
from pathlib import Path, PurePosixPath

from app import app
from extensions import db
from models.event import Event


PLAN_FILENAME = "final_event_image_plan_20261114_130205.json"
REVIEWED_IMAGES = {
    69: "/static/images/events/event_69.jpg",
    98: "/static/images/events/event_98.jpg",
    106: "/static/images/events/event_106.jpg",
    148: "/static/images/events/event_148.jpg",
}
FOOD_FESTIVAL_ID = 105
FOOD_FESTIVAL_IMAGE = "/static/uploads/Picture3.png"


def local_image_path(static_dir, url):
    parts = PurePosixPath(url).parts
    if parts[:2] != ("/", "static") or len(parts) < 3 or ".." in parts:
        raise ValueError(f"Invalid local image URL: {url}")
    return static_dir.joinpath(*parts[2:])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Save the reviewed image URLs.")
    parser.add_argument(
        "--use-food-photo", action="store_true",
        help="Use static/uploads/Picture3.png after confirming it shows the Riyadh Food Festival.",
    )
    args = parser.parse_args()

    backup_dir = Path(app.root_path) / "backups"
    static_dir = Path(app.static_folder)
    plan = json.loads((backup_dir / PLAN_FILENAME).read_text(encoding="utf-8-sig"))
    replacements = dict(REVIEWED_IMAGES)
    if args.use_food_photo:
        replacements[FOOD_FESTIVAL_ID] = FOOD_FESTIVAL_IMAGE

    with app.app_context():
        plan_ids = {row["event_id"] for row in plan}
        if len(plan_ids) != len(plan) or not replacements.keys() <= plan_ids:
            raise SystemExit("The preview file is incomplete or contains duplicate event IDs.")
        events = {event.id: event for event in Event.query.filter(Event.id.in_(plan_ids)).all()}
        problems = []
        changes = []
        fallbacks = []

        for row in plan:
            event_id = row["event_id"]
            event = events.get(event_id)
            new_url = replacements.get(event_id, row["new_image"])
            if not event or event.title != row["title"]:
                problems.append(f"#{event_id}: event missing or title changed.")
                continue
            if event.image not in (row["old_image"], row["new_image"], new_url):
                problems.append(f"#{event_id}: current image differs from the reviewed preview.")
                continue
            if not local_image_path(static_dir, new_url).is_file():
                problems.append(f"#{event_id}: missing {new_url}")
                continue
            if new_url.endswith(".svg"):
                fallbacks.append(f"#{event_id} {event.title}")
            if event.image != new_url:
                changes.append((event, new_url))

        if problems:
            raise SystemExit("Resolve these entries before applying:\n" + "\n".join(problems))

        print(f"Events checked: {len(plan)}")
        for item in fallbacks:
            print(f"Title card: {item}")
        print(f"Image URLs to update: {len(changes)}")

        if not args.apply:
            print("Preview complete. Database was not changed.")
            return

        if changes:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup_file = backup_dir / f"reviewed_event_images_before_{timestamp}.json"
            previous = [{"id": event.id, "title": event.title, "image": event.image}
                        for event, _ in changes]
            with backup_file.open("x", encoding="utf-8") as output:
                json.dump(previous, output, ensure_ascii=False, indent=2)
            try:
                for event, new_url in changes:
                    event.image = new_url
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
            print(f"Backup: {backup_file}")
        print(f"Applied. DB updates: {len(changes)}")


if __name__ == "__main__":
    main()
