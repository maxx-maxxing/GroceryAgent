import re


STOPWORDS = {"a", "an", "and", "for", "of", "or", "the", "with"}


def select_best_product(term, products):
    scored = [_score_product(term, product) for product in products]
    scored = [candidate for candidate in scored if candidate["upc"]]
    scored.sort(key=lambda candidate: candidate["confidence"], reverse=True)

    if not scored:
        return {
            "status": "needs_review",
            "confidence": 0,
            "reason": "No searchable products with UPCs were returned.",
            "product": None,
            "candidates": [],
        }

    best = scored[0]
    if best["coverage"] < 1 or best["confidence"] < 0.72:
        return {
            "status": "needs_review",
            "confidence": best["confidence"],
            "reason": "No product matched the term confidently enough to add.",
            "product": None,
            "candidates": _candidate_summaries(scored),
        }

    return {
        "status": "selected",
        "confidence": best["confidence"],
        "reason": "Selected the highest-confidence product match.",
        "product": best["product"],
        "candidates": _candidate_summaries(scored),
    }


def _score_product(term, product):
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

    return {
        "product": product,
        "upc": product.get("upc"),
        "description": product.get("description", ""),
        "brand": product.get("brand", ""),
        "confidence": round(min(confidence, 1), 2),
        "coverage": round(coverage, 2),
        "matched_tokens": matched_tokens,
        "missing_tokens": missing_tokens,
    }


def _candidate_summaries(scored):
    return [
        {
            "description": candidate["description"],
            "brand": candidate["brand"],
            "upc": candidate["upc"],
            "confidence": candidate["confidence"],
            "missing_terms": candidate["missing_tokens"],
        }
        for candidate in scored[:5]
    ]


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
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _product_text(product):
    parts = [
        product.get("brand", ""),
        product.get("description", ""),
        " ".join(product.get("categories") or []),
    ]

    for item in product.get("items") or []:
        parts.append(str(item.get("size", "")))
        fulfillment = item.get("fulfillment")
        if isinstance(fulfillment, dict):
            parts.extend(str(key) for key, available in fulfillment.items() if available)

    return " ".join(parts)


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
        "bun",
        "buns",
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
            return -0.35
        if non_brand_tokens.intersection({"bread", "loaf"}):
            return 0.08

    return 0
