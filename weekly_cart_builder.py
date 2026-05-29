import json
import re
from pathlib import Path


DEFAULT_PROFILE_PATH = Path(__file__).with_name("grocery_profile.json")
DEFAULT_MEAL_TEMPLATES_PATH = Path(__file__).with_name("meal_templates.json")
DEFAULT_SUBSTITUTION_RULES_PATH = Path(__file__).with_name("substitution_rules.json")


class WeeklyCartBuilderError(Exception):
    pass


def build_weekly_cart_plan(
    profile_path=DEFAULT_PROFILE_PATH,
    meal_templates_path=DEFAULT_MEAL_TEMPLATES_PATH,
    substitution_rules_path=DEFAULT_SUBSTITUTION_RULES_PATH,
):
    profile = _load_json_object(profile_path, "grocery profile")
    meal_templates = _load_json_list(meal_templates_path, "meal templates")
    substitutions = _load_substitution_rules(substitution_rules_path)

    dinner_count = _requested_dinner_count(profile)
    selected_meals = _select_meals(meal_templates, dinner_count)

    raw_items = []
    for meal in selected_meals:
        _extend_cart_items(raw_items, meal.get("cart_items", []), f"meal {meal.get('name')}")

    _extend_cart_items(
        raw_items,
        profile.get("permanent_drink_slots", []),
        "permanent drink slots",
        substitutions,
    )
    _extend_cart_items(
        raw_items,
        profile.get("sandwich_core", []),
        "sandwich core",
        substitutions,
    )
    _extend_cart_items(
        raw_items,
        profile.get("weekly_staples", []),
        "weekly staples",
        substitutions,
    )

    cart_items, dedupe_notes = _deduplicate_cart_items(raw_items)
    notes = [
        f"Selected the first {len(selected_meals)} meal templates for the Phase 4 MVP.",
        "Permanent drinks and sandwich core were included from grocery_profile.json.",
        "Standing staples use ordered fallback search terms from substitution_rules.json.",
        "Live mode only adds confident product matches and never checks out or places an order.",
    ]

    if not profile.get("weekly_staples"):
        notes.append("No extra weekly staples are configured yet; pantry memory is not implemented.")

    notes.extend(dedupe_notes)

    return {
        "meal_plan": [_meal_summary(meal) for meal in selected_meals],
        "cart_items": cart_items,
        "cart_audit_notes": dedupe_notes,
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


def _load_substitution_rules(path):
    try:
        rules = _load_json_object(path, "substitution rules")
    except WeeklyCartBuilderError as exc:
        if str(exc).startswith("Missing substitution rules file"):
            return {}
        raise

    substitutions = {}
    standing_staples = rules.get("standing_staples", [])
    if not isinstance(standing_staples, list):
        raise WeeklyCartBuilderError("substitution_rules.standing_staples must be a list.")

    for rule in standing_staples:
        if not isinstance(rule, dict):
            raise WeeklyCartBuilderError("Each standing staple rule must be an object.")

        primary_term = rule.get("primary_term", "")
        if not isinstance(primary_term, str) or not primary_term.strip():
            raise WeeklyCartBuilderError("Each standing staple rule must include primary_term.")

        fallback_terms = rule.get("fallback_terms", [])
        if not isinstance(fallback_terms, list):
            raise WeeklyCartBuilderError("standing staple fallback_terms must be a list.")

        clean_fallbacks = []
        for fallback_term in fallback_terms:
            if not isinstance(fallback_term, str) or not fallback_term.strip():
                raise WeeklyCartBuilderError("standing staple fallback terms must be strings.")
            clean_fallbacks.append(fallback_term.strip())

        substitutions[_dedupe_key(primary_term)] = {
            "staple_id": rule.get("id"),
            "staple_name": rule.get("name") or primary_term.strip(),
            "fallback_terms": _unique_terms(clean_fallbacks),
        }

    return substitutions


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


def _extend_cart_items(destination, items, source_label, substitutions=None):
    if not isinstance(items, list):
        raise WeeklyCartBuilderError(f"{source_label} must be a list.")

    for item in items:
        destination.append(_parse_cart_item(item, source_label, substitutions or {}))


def _parse_cart_item(item, source_label, substitutions):
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

    parsed_item = {"term": term.strip(), "quantity": quantity}
    for optional_key in (
        "distinct_product_required",
        "separate_required",
        "separate_product_required",
    ):
        if item.get(optional_key) is True:
            parsed_item[optional_key] = True

    substitution = substitutions.get(_dedupe_key(parsed_item["term"]))
    if substitution:
        parsed_item.update(substitution)

    return parsed_item


def _deduplicate_cart_items(items):
    deduped = []
    index_by_key = {}
    notes = []

    for item in items:
        key = _dedupe_key_for_item(item)
        if key not in index_by_key:
            index_by_key[key] = len(deduped)
            deduped.append(dict(item))
            continue

        existing = deduped[index_by_key[key]]
        existing["quantity"] += item["quantity"]
        protein_group = _protein_group_key(item)
        if protein_group:
            existing["protein_deduped"] = True
            existing["protein_group"] = protein_group
            existing.setdefault("merged_terms", [existing["term"]])
            if item["term"] not in existing["merged_terms"]:
                existing["merged_terms"].append(item["term"])
            notes.append(
                "Merged chicken protein term "
                f"'{item['term']}' into '{existing['term']}' "
                f"for quantity {existing['quantity']}."
            )
            continue

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


def _dedupe_key_for_item(item):
    protein_key = _protein_dedupe_key(item)
    if protein_key:
        return protein_key
    return _dedupe_key(item["term"])


def _protein_dedupe_key(item):
    protein_group = _protein_group_key(item)
    if not protein_group:
        return None
    return f"protein:{protein_group}"


def _protein_group_key(item):
    if _requires_distinct_product(item):
        return None

    key = _dedupe_key(item["term"])
    tokens = set(key.split())

    if "chicken" not in tokens:
        return None

    excluded_tokens = {
        "bouillon",
        "breaded",
        "broth",
        "nugget",
        "nuggets",
        "rotisserie",
        "sausage",
        "seasoning",
        "soup",
        "stock",
    }
    if tokens.intersection(excluded_tokens):
        return None

    return "chicken"


def _requires_distinct_product(item):
    return any(
        item.get(key) is True
        for key in (
            "distinct_product_required",
            "separate_required",
            "separate_product_required",
        )
    )


def _dedupe_key(term):
    text = str(term or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"coca[\s-]?cola", "coke", text)
    text = re.sub(r"dave'?s", "dave", text)
    text = re.sub(r"boar'?s", "boar", text)
    text = re.sub(r"zero sugar", "zero", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _unique_terms(terms):
    unique = []
    seen = set()
    for term in terms:
        key = _dedupe_key(term)
        if key in seen:
            continue
        seen.add(key)
        unique.append(term)
    return unique


def _meal_summary(meal):
    return {
        "name": meal.get("name"),
        "style": meal.get("style") or meal.get("category"),
        "category": meal.get("category"),
        "approx_cook_time_minutes": meal.get("approx_cook_time_minutes"),
    }
