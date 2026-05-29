import json
import re
from functools import lru_cache
from pathlib import Path


STOPWORDS = {"a", "an", "and", "for", "of", "or", "the", "with"}
DEFAULT_QUALITY_RULES_PATH = Path(__file__).with_name("product_quality_rules.json")


def select_best_product(term, products, quality_rules=None):
    quality_rules = quality_rules if quality_rules is not None else load_product_quality_rules()
    scored = [_score_product(term, product, quality_rules) for product in products]
    scored = [candidate for candidate in scored if candidate["upc"]]
    _apply_relative_price_rules(term, scored, quality_rules)

    selectable = [
        candidate for candidate in scored if not candidate["quality_rejections"]
    ]
    rejected = [
        candidate for candidate in scored if candidate["quality_rejections"]
    ]
    selectable.sort(key=lambda candidate: candidate["confidence"], reverse=True)
    rejected.sort(key=lambda candidate: candidate["base_confidence"], reverse=True)

    if not scored:
        return {
            "status": "needs_review",
            "confidence": 0,
            "reason": "No searchable products with UPCs were returned.",
            "product": None,
            "candidates": [],
            "rejected_candidates": [],
            "quality_notes": [],
        }

    if not selectable:
        return {
            "status": "needs_review",
            "confidence": rejected[0]["base_confidence"] if rejected else 0,
            "reason": "All searchable products were rejected by product quality rules.",
            "product": None,
            "candidates": [],
            "rejected_candidates": _candidate_summaries(rejected),
            "quality_notes": _quality_notes_for_candidates(rejected),
        }

    best = selectable[0]
    if best["coverage"] < 1 or best["confidence"] < 0.72:
        return {
            "status": "needs_review",
            "confidence": best["confidence"],
            "reason": "No product matched the term confidently enough to add.",
            "product": None,
            "candidates": _candidate_summaries(selectable),
            "rejected_candidates": _candidate_summaries(rejected),
            "quality_notes": _quality_notes_for_candidates([best] + rejected),
        }

    return {
        "status": "selected",
        "confidence": best["confidence"],
        "reason": "Selected the highest-confidence product match.",
        "product": best["product"],
        "candidates": _candidate_summaries(selectable),
        "rejected_candidates": _candidate_summaries(rejected),
        "quality_notes": best["quality_notes"],
    }


def load_product_quality_rules(path=DEFAULT_QUALITY_RULES_PATH):
    return _load_product_quality_rules_cached(str(path))


@lru_cache(maxsize=8)
def _load_product_quality_rules_cached(path):
    try:
        with open(path) as rules_file:
            rules = json.load(rules_file)
    except FileNotFoundError:
        return _empty_quality_rules()

    if not isinstance(rules, dict):
        return _empty_quality_rules()

    return {
        "reject_rules": _list_value(rules.get("reject_rules")),
        "score_adjustment_rules": _list_value(rules.get("score_adjustment_rules")),
        "relative_price_rules": _list_value(rules.get("relative_price_rules")),
    }


def _empty_quality_rules():
    return {
        "reject_rules": [],
        "score_adjustment_rules": [],
        "relative_price_rules": [],
    }


def _list_value(value):
    return value if isinstance(value, list) else []


def _score_product(term, product, quality_rules):
    term_tokens = _meaningful_tokens(term)
    product_text = _product_text(product)
    product_tokens = set(_tokenize(product_text))

    if not term_tokens:
        coverage = 0
        matched_tokens = []
        missing_tokens = []
    else:
        matched_tokens = [token for token in term_tokens if token in product_tokens]
        missing_tokens = [token for token in term_tokens if token not in product_tokens]
        coverage = len(matched_tokens) / len(term_tokens)

    normalized_term = _normalize_text(term)
    normalized_product_text = _normalize_text(product_text)

    confidence = coverage * 0.75
    if normalized_term and normalized_term in normalized_product_text:
        confidence += 0.12
    if _brand_matches(product, term_tokens):
        confidence += 0.06
    if _has_pickup_availability(product):
        confidence += 0.04
    if product.get("upc"):
        confidence += 0.03
    confidence += _package_fit_bonus(term, product_text)
    confidence += _format_fit_adjustment(term, product)

    quality_rejections = _quality_rejections(term, product, quality_rules)
    quality_adjustment, quality_notes = _quality_adjustments(term, product, quality_rules)
    base_confidence = confidence
    confidence += quality_adjustment

    return {
        "product": product,
        "upc": product.get("upc"),
        "description": product.get("description", ""),
        "brand": product.get("brand", ""),
        "confidence": _clamp_confidence(confidence),
        "base_confidence": _clamp_confidence(base_confidence),
        "coverage": round(coverage, 2),
        "matched_tokens": matched_tokens,
        "missing_tokens": missing_tokens,
        "price": _product_price(product),
        "has_sale_price": _has_sale_price(product),
        "quality_adjustment": round(quality_adjustment, 2),
        "quality_notes": quality_notes,
        "quality_rejections": quality_rejections,
    }


def _candidate_summaries(scored):
    summaries = []
    for candidate in scored[:5]:
        summary = {
            "description": candidate["description"],
            "brand": candidate["brand"],
            "upc": candidate["upc"],
            "confidence": candidate["confidence"],
            "missing_terms": candidate["missing_tokens"],
        }
        if candidate.get("base_confidence") != candidate.get("confidence"):
            summary["base_confidence"] = candidate["base_confidence"]
        if candidate.get("price") is not None:
            summary["price"] = candidate["price"]
        if candidate.get("quality_notes"):
            summary["quality_notes"] = candidate["quality_notes"]
        if candidate.get("quality_rejections"):
            summary["quality_rejections"] = candidate["quality_rejections"]
        summaries.append(summary)

    return summaries


def _quality_notes_for_candidates(candidates):
    notes = []
    seen = set()
    for candidate in candidates:
        for note in candidate.get("quality_notes", []):
            key = (
                note.get("rule_id"),
                note.get("note"),
                candidate.get("upc"),
            )
            if key in seen:
                continue
            seen.add(key)
            notes.append(
                {
                    **note,
                    "product": candidate.get("description"),
                    "brand": candidate.get("brand"),
                    "upc": candidate.get("upc"),
                }
            )
        for rejection in candidate.get("quality_rejections", []):
            key = (
                rejection.get("rule_id"),
                rejection.get("reason"),
                candidate.get("upc"),
            )
            if key in seen:
                continue
            seen.add(key)
            notes.append(
                {
                    "rule_id": rejection.get("rule_id"),
                    "note": rejection.get("reason"),
                    "product": candidate.get("description"),
                    "brand": candidate.get("brand"),
                    "upc": candidate.get("upc"),
                    "intervention": "rejected",
                }
            )

    return notes


def _meaningful_tokens(value):
    return [
        token
        for token in _tokenize(value)
        if token not in STOPWORDS and len(token) > 1
    ]


def _tokenize(value):
    return _normalize_text(value).split()


def _normalize_text(value):
    text = str(value or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"coca[\s-]?cola", "coke", text)
    text = re.sub(r"dave'?s", "dave", text)
    text = re.sub(r"boar'?s", "boar", text)
    text = re.sub(r"zero sugar", "zero", text)
    text = re.sub(r"\bpk\b", "pack", text)
    text = re.sub(r"\bsliced?\b|\bslices\b", "slice", text)
    text = re.sub(r"\bloaves\b", "loaf", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _clamp_confidence(value):
    return round(min(max(value, 0), 1), 2)


def _product_text(product):
    parts = [
        product.get("brand", ""),
        product.get("description", ""),
        " ".join(product.get("categories") or []),
    ]

    for item in product.get("items") or []:
        parts.append(str(item.get("size", "")))
        price = item.get("price")
        if isinstance(price, dict):
            for key in ("regular", "promo"):
                if price.get(key) is not None:
                    parts.append(str(price.get(key)))
        fulfillment = item.get("fulfillment")
        if isinstance(fulfillment, dict):
            parts.extend(str(key) for key, available in fulfillment.items() if available)

    return " ".join(parts)


def _quality_rejections(term, product, quality_rules):
    rejections = []
    for rule in quality_rules.get("reject_rules", []):
        if not _rule_applies(term, rule):
            continue

        reject_if = rule.get("reject_if")
        if reject_if and _condition_matches(reject_if, term, product):
            rejections.append(_quality_rejection(rule))
            continue

        reject_unless = rule.get("reject_unless")
        if reject_unless and not _condition_matches(reject_unless, term, product):
            rejections.append(_quality_rejection(rule))

    return rejections


def _quality_rejection(rule):
    return {
        "rule_id": rule.get("id", "product_quality_rule"),
        "reason": rule.get("reason", "Rejected by product quality rule."),
    }


def _quality_adjustments(term, product, quality_rules):
    adjustment_total = 0
    notes = []

    for rule in quality_rules.get("score_adjustment_rules", []):
        if not _rule_applies(term, rule):
            continue
        if not _condition_matches(rule.get("adjust_if", {}), term, product):
            continue

        adjustment = _numeric_value(rule.get("score_adjustment"), 0)
        if not adjustment:
            continue

        adjustment_total += adjustment
        notes.append(
            {
                "rule_id": rule.get("id", "product_quality_preference"),
                "adjustment": round(adjustment, 2),
                "note": rule.get("note", "Adjusted by product quality preference."),
            }
        )

    return adjustment_total, notes


def _apply_relative_price_rules(term, scored, quality_rules):
    if not scored:
        return

    for rule in quality_rules.get("relative_price_rules", []):
        if not _rule_applies(term, rule):
            continue

        eligible = [
            candidate
            for candidate in scored
            if not candidate["quality_rejections"] and candidate.get("price") is not None
        ]
        if len(eligible) < 2:
            continue

        prices = [candidate["price"] for candidate in eligible]
        minimum = min(prices)
        maximum = max(prices)
        if minimum == maximum:
            continue

        mode = rule.get("mode")
        max_adjustment = abs(_numeric_value(rule.get("max_adjustment"), 0))
        if not max_adjustment:
            continue

        if mode == "prefer_lower_price":
            for candidate in eligible:
                price_position = (candidate["price"] - minimum) / (maximum - minimum)
                adjustment = max_adjustment * (1 - (2 * price_position))
                _apply_quality_adjustment(candidate, adjustment, rule)
        elif mode == "avoid_highest_price":
            for candidate in eligible:
                if candidate["price"] == maximum:
                    _apply_quality_adjustment(candidate, -max_adjustment, rule)


def _apply_quality_adjustment(candidate, adjustment, rule):
    if not adjustment:
        return

    candidate["quality_adjustment"] = round(
        candidate.get("quality_adjustment", 0) + adjustment,
        2,
    )
    candidate["confidence"] = _clamp_confidence(candidate["confidence"] + adjustment)
    candidate["quality_notes"].append(
        {
            "rule_id": rule.get("id", "product_quality_preference"),
            "adjustment": round(adjustment, 2),
            "note": rule.get("note", "Adjusted by product quality preference."),
        }
    )


def _rule_applies(term, rule):
    return _condition_matches(rule.get("applies_to", {}), term, None)


def _condition_matches(condition, term, product):
    if not isinstance(condition, dict):
        return True

    term_text = _normalize_text(term)
    product_text = _normalize_text(_product_text(product)) if product else ""
    brand_text = _normalize_text(product.get("brand", "")) if product else ""
    description_text = _normalize_text(product.get("description", "")) if product else ""

    checks = [
        ("terms_any", _contains_any_phrase, term_text, False),
        ("terms_all", _contains_all_phrases, term_text, False),
        ("terms_none", _contains_any_phrase, term_text, True),
        ("text_any", _contains_any_phrase, product_text, False),
        ("text_all", _contains_all_phrases, product_text, False),
        ("text_none", _contains_any_phrase, product_text, True),
        ("brand_any", _contains_any_phrase, brand_text, False),
        ("brand_all", _contains_all_phrases, brand_text, False),
        ("description_any", _contains_any_phrase, description_text, False),
        ("description_all", _contains_all_phrases, description_text, False),
    ]

    for key, matcher, haystack, invert in checks:
        if key not in condition:
            continue
        matched = matcher(haystack, condition.get(key))
        if invert:
            matched = not matched
        if not matched:
            return False

    if "sale_price" in condition and product is not None:
        if _has_sale_price(product) is not bool(condition["sale_price"]):
            return False

    return True


def _contains_any_phrase(text, phrases):
    return any(_contains_phrase(text, phrase) for phrase in _list_value(phrases))


def _contains_all_phrases(text, phrases):
    phrases = _list_value(phrases)
    return bool(phrases) and all(_contains_phrase(text, phrase) for phrase in phrases)


def _contains_phrase(text, phrase):
    normalized_phrase = _normalize_text(phrase)
    if not normalized_phrase:
        return False
    return f" {normalized_phrase} " in f" {text} "


def _numeric_value(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _product_price(product):
    prices = []
    for item in product.get("items") or []:
        price = item.get("price")
        if not isinstance(price, dict):
            continue

        promo = _numeric_value(price.get("promo"), None)
        regular = _numeric_value(price.get("regular"), None)
        if promo and promo > 0:
            prices.append(promo)
        elif regular and regular > 0:
            prices.append(regular)

    return min(prices) if prices else None


def _has_sale_price(product):
    for item in product.get("items") or []:
        price = item.get("price")
        if not isinstance(price, dict):
            continue

        promo = _numeric_value(price.get("promo"), None)
        regular = _numeric_value(price.get("regular"), None)
        if promo and regular and 0 < promo < regular:
            return True

    return False


def _brand_matches(product, term_tokens):
    brand_tokens = set(_tokenize(product.get("brand", "")))
    return bool(brand_tokens.intersection(term_tokens))


def _has_pickup_availability(product):
    for item in product.get("items") or []:
        fulfillment = item.get("fulfillment")
        if isinstance(fulfillment, dict):
            pickup_values = [
                fulfillment.get("curbside"),
                fulfillment.get("pickup"),
                fulfillment.get("shipToStore"),
            ]
            if any(value is True for value in pickup_values):
                return True
        elif isinstance(fulfillment, str) and "pickup" in fulfillment.lower():
            return True

        inventory = item.get("inventory")
        if isinstance(inventory, dict) and inventory.get("stockLevel") in {"HIGH", "LOW"}:
            return True

    return False


def _package_fit_bonus(term, product_text):
    term_text = _normalize_text(term)
    candidate_text = _normalize_text(product_text)
    term_tokens = set(term_text.split())
    candidate_tokens = set(candidate_text.split())

    wants_bottle = bool(term_tokens.intersection({"bottle", "bottles", "liter", "liters"}))
    wants_cans = bool(term_tokens.intersection({"can", "cans", "pack"}))
    has_bottle = bool(candidate_tokens.intersection({"bottle", "bottles", "liter", "liters"}))
    has_cans = bool(candidate_tokens.intersection({"can", "cans", "pack"}))
    is_soda = bool(term_tokens.intersection({"coke", "cola", "soda"}))

    if wants_bottle:
        return 0.05 if has_bottle else -0.03

    if wants_cans:
        return 0.05 if has_cans else -0.03

    if is_soda and has_cans:
        return 0.04

    if is_soda and has_bottle:
        return -0.02

    return 0


def _format_fit_adjustment(term, product):
    term_tokens = set(_meaningful_tokens(term))
    description_tokens = set(_tokenize(product.get("description", "")))
    category_tokens = set(_tokenize(" ".join(product.get("categories") or [])))
    non_brand_tokens = description_tokens | category_tokens

    bread_alternates = {
        "bagel",
        "bagels",
        "breakfast",
        "bun",
        "buns",
        "flatbread",
        "flatbreads",
        "roll",
        "rolls",
        "english",
        "muffin",
        "muffins",
        "crumb",
        "crumbs",
    }

    if "bread" in term_tokens and not term_tokens.intersection(bread_alternates):
        if description_tokens.intersection(bread_alternates):
            return -1
        if non_brand_tokens.intersection({"bread", "loaf", "slice"}):
            return 0.08

    return 0
