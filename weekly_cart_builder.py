import json
import re
from pathlib import Path


DEFAULT_PROFILE_PATH = Path(__file__).with_name("grocery_profile.json")
DEFAULT_MEAL_TEMPLATES_PATH = Path(__file__).with_name("meal_templates.json")


class WeeklyCartBuilderError(Exception):
    pass


def build_weekly_cart_plan(
    profile_path=DEFAULT_PROFILE_PATH,
    meal_templates_path=DEFAULT_MEAL_TEMPLATES_PATH,
):
    profile = _load_json_object(profile_path, "grocery profile")
    meal_templates = _load_json_list(meal_templates_path, "meal templates")

    dinner_count = _requested_dinner_count(profile)
    selected_meals = _select_meals(meal_templates, dinner_count)

    raw_items = []
    for meal in selected_meals:
        _extend_cart_items(raw_items, meal.get("cart_items", []), f"meal {meal.get('name')}")

    _extend_cart_items(
        raw_items,
        profile.get("permanent_drink_slots", []),
        "permanent drink slots",
    )
    _extend_cart_items(raw_items, profile.get("sandwich_core", []), "sandwich core")
    _extend_cart_items(raw_items, profile.get("weekly_staples", []), "weekly staples")

    cart_items, dedupe_notes = _deduplicate_cart_items(raw_items)
    notes = [
        f"Selected the first {len(selected_meals)} meal templates for the Phase 4 MVP.",
        "Permanent drinks and sandwich core were included from grocery_profile.json.",
        "Live mode only adds confident product matches and never checks out or places an order.",
    ]

    if not profile.get("weekly_staples"):
        notes.append("No extra weekly staples are configured yet; pantry memory is not implemented.")

    notes.extend(dedupe_notes)

    return {
        "meal_plan": [_meal_summary(meal) for meal in selected_meals],
        "cart_items": cart_items,
        "notes": notes,
    }


def _load_json_object(path, label):
    data = _load_json(path, label)
    if not isinstance(data, dict):
        raise WeeklyCartBuilderError(f"{label} must be a JSON object.")
    return data


def _load_json_list(path, label):
    data = _load_json(path, label)
    if not isinstance(data, list):
        raise WeeklyCartBuilderError(f"{label} must be a JSON list.")
    return data


def _load_json(path, label):
    try:
        with open(path) as data_file:
            return json.load(data_file)
    except FileNotFoundError as exc:
        raise WeeklyCartBuilderError(f"Missing {label} file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WeeklyCartBuilderError(f"Invalid JSON in {label} file: {path}") from exc


def _requested_dinner_count(profile):
    weekly_structure = profile.get("weekly_structure", {})
    dinner_count = weekly_structure.get("dinners", 4)
    try:
        dinner_count = int(dinner_count)
    except (TypeError, ValueError) as exc:
        raise WeeklyCartBuilderError("weekly_structure.dinners must be an integer.") from exc

    if dinner_count < 1:
        raise WeeklyCartBuilderError("weekly_structure.dinners must be at least 1.")

    return dinner_count


def _select_meals(meal_templates, dinner_count):
    if len(meal_templates) < dinner_count:
        raise WeeklyCartBuilderError(
            f"Need at least {dinner_count} meal templates; found {len(meal_templates)}."
        )

    selected_meals = meal_templates[:dinner_count]
    for meal in selected_meals:
        if not isinstance(meal, dict):
            raise WeeklyCartBuilderError("Each meal template must be an object.")
        if not meal.get("name"):
            raise WeeklyCartBuilderError("Each meal template must include a name.")
        if not isinstance(meal.get("cart_items"), list):
            raise WeeklyCartBuilderError(
                f"Meal template {meal.get('name')} must include cart_items."
            )

    return selected_meals


def _extend_cart_items(destination, items, source_label):
    if not isinstance(items, list):
        raise WeeklyCartBuilderError(f"{source_label} must be a list.")

    for item in items:
        destination.append(_parse_cart_item(item, source_label))


def _parse_cart_item(item, source_label):
    if not isinstance(item, dict):
        raise WeeklyCartBuilderError(f"Cart item in {source_label} must be an object.")

    term = item.get("term", "")
    if not isinstance(term, str) or not term.strip():
        raise WeeklyCartBuilderError(f"Cart item in {source_label} must have a term.")

    quantity = item.get("quantity", 1)
    if isinstance(quantity, bool):
        raise WeeklyCartBuilderError(f"Cart item quantity in {source_label} is invalid.")

    try:
        quantity = int(quantity)
    except (TypeError, ValueError) as exc:
        raise WeeklyCartBuilderError(
            f"Cart item quantity in {source_label} must be an integer."
        ) from exc

    if quantity < 1:
        raise WeeklyCartBuilderError(
            f"Cart item quantity in {source_label} must be positive."
        )

    return {"term": term.strip(), "quantity": quantity}


def _deduplicate_cart_items(items):
    deduped = []
    index_by_key = {}
    notes = []

    for item in items:
        key = _dedupe_key(item["term"])
        if key not in index_by_key:
            index_by_key[key] = len(deduped)
            deduped.append(dict(item))
            continue

        existing = deduped[index_by_key[key]]
        existing["quantity"] += item["quantity"]
        if existing["term"] == item["term"]:
            notes.append(
                f"Combined duplicate term '{item['term']}' to quantity {existing['quantity']}."
            )
        else:
            notes.append(
                "Merged near-identical term "
                f"'{item['term']}' into '{existing['term']}' "
                f"for quantity {existing['quantity']}."
            )

    return deduped, notes


def _dedupe_key(term):
    text = str(term or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"coca[\s-]?cola", "coke", text)
    text = re.sub(r"dave'?s", "dave", text)
    text = re.sub(r"boar'?s", "boar", text)
    text = re.sub(r"zero sugar", "zero", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _meal_summary(meal):
    return {
        "name": meal.get("name"),
        "style": meal.get("style") or meal.get("category"),
        "category": meal.get("category"),
        "approx_cook_time_minutes": meal.get("approx_cook_time_minutes"),
    }
