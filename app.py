import json

from flask import Flask, jsonify, request

from config import load_config
from kroger_client import KrogerApiError, KrogerClient
from product_selector import select_best_product
from weekly_cart_builder import WeeklyCartBuilderError, build_weekly_cart_plan


settings = load_config()
kroger_client = KrogerClient(settings)

KNOWN_ACCEPTABLE_MANUAL_REVIEW_TERMS = {
    "liquid death severed lime",
}
KNOWN_ACCEPTABLE_MANUAL_REVIEW_STAPLE_IDS = {
    "liquid_death_flavored_water",
}
CORE_STANDING_STAPLE_IDS = {
    "liquid_death_flavored_water",
    "arizona_green_tea",
    "daves_killer_bread",
    "boars_head_deli_meat",
    "tillamook_sliced_cheese",
}
MANUAL_CHECKOUT_REMINDER = (
    "Review the real King Soopers cart manually before checkout. "
    "This app never checks out or places an order."
)


def create_app(client=None):
    client = client or kroger_client
    app = Flask(__name__)

    @app.route("/")
    def login():
        return f"""
        <h2>Grocery Agent Login</h2>
        <a href="{client.authorization_url()}">
        Connect King Soopers / Kroger Account
        </a>
        """

    @app.route("/callback")
    def callback():
        code = request.args.get("code")
        if not code:
            return "<h2>Missing OAuth code.</h2>", 400

        try:
            client.exchange_code_for_tokens(code)
        except KrogerApiError as exc:
            return _plain_error(exc), 502

        return "<h2>Success.</h2><p>Tokens saved locally to tokens.json</p>"

    @app.route("/stores")
    def stores():
        try:
            response = client.get_locations()
        except KrogerApiError as exc:
            return _plain_error(exc), 502

        return _pre_response(response)

    @app.route("/search")
    def search():
        term = request.args.get("term", "Cherry Coke Zero")

        try:
            response = client.search_products(
                term,
                limit=client.config.product_search_limit,
            )
        except KrogerApiError as exc:
            return _plain_error(exc), 502

        return _pre_response(response)

    @app.route("/cart/add")
    def add_to_cart():
        upc = request.args.get("upc")
        quantity = _parse_quantity(request.args.get("quantity", 1))
        if quantity is None:
            return "<pre>Status: 400\n\nquantity must be a positive integer.</pre>", 400

        try:
            response, payload = client.add_to_cart(upc, quantity)
        except KrogerApiError as exc:
            return _plain_error(exc), 502

        return (
            f"<pre>Status: {response.status_code}\n\n"
            f"Payload sent:\n{json.dumps(payload, indent=2)}\n\n"
            f"Response:\n{response.text}</pre>"
        )

    @app.route("/cart/add_by_term")
    def add_to_cart_by_term():
        term = request.args.get("term", "").strip()
        if not term:
            return jsonify(
                {
                    "status": "error",
                    "message": "Missing required query parameter: term",
                }
            ), 400

        quantity = _parse_quantity(request.args.get("quantity", 1))
        if quantity is None:
            return jsonify(
                {
                    "status": "error",
                    "message": "quantity must be a positive integer",
                }
            ), 400

        try:
            search_response = client.search_products(
                term,
                limit=client.config.add_by_term_search_limit,
            )
        except KrogerApiError as exc:
            return jsonify({"status": "search_error", "message": str(exc)}), 502

        if search_response.status_code >= 400:
            return jsonify(
                {
                    "status": "search_error",
                    "term": term,
                    "search_status": search_response.status_code,
                    "message": "Kroger product search failed.",
                }
            ), 502

        try:
            products = search_response.json().get("data", [])
        except ValueError:
            return jsonify(
                {
                    "status": "search_error",
                    "term": term,
                    "message": "Kroger product search returned non-JSON data.",
                }
            ), 502

        selection = select_best_product(term, products)
        if selection["status"] != "selected":
            return jsonify(
                {
                    "status": "needs_review",
                    "term": term,
                    "quantity": quantity,
                    "confidence": selection["confidence"],
                    "reason": selection["reason"],
                    "candidates": selection["candidates"],
                }
            )

        selected_product = selection["product"]
        upc = selected_product.get("upc")

        try:
            cart_response, _payload = client.add_to_cart(upc, quantity)
        except KrogerApiError as exc:
            return jsonify({"status": "cart_error", "message": str(exc)}), 502

        result = {
            "status": "added" if cart_response.status_code in {200, 201, 204} else "cart_error",
            "term": term,
            "selected_product": selected_product.get("description"),
            "brand": selected_product.get("brand"),
            "upc": upc,
            "quantity": quantity,
            "confidence": selection["confidence"],
            "cart_status": cart_response.status_code,
        }

        if cart_response.text:
            result["cart_response"] = cart_response.text

        return jsonify(result), 200 if result["status"] == "added" else 502

    @app.route("/cart/add_many", methods=["POST"])
    def add_many_to_cart():
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(
                {
                    "status": "error",
                    "message": "Expected a JSON object with dry_run and items.",
                }
            ), 400

        dry_run = payload.get("dry_run", True)
        if not isinstance(dry_run, bool):
            return jsonify(
                {
                    "status": "error",
                    "message": "dry_run must be true or false.",
                }
            ), 400

        items = payload.get("items")
        if not isinstance(items, list):
            return jsonify(
                {
                    "status": "error",
                    "message": "items must be a list of objects with term and quantity.",
                }
            ), 400

        return jsonify(_process_cart_items(client, items, dry_run))

    @app.route("/build_weekly_cart")
    def build_weekly_cart():
        dry_run, dry_run_error = _parse_dry_run_param(request.args.get("dry_run", "true"))
        if dry_run_error:
            return jsonify(dry_run_error), 400

        require_ready, require_ready_error = _parse_optional_bool_param(
            request.args.get("require_ready"),
            "require_ready",
        )
        if require_ready_error:
            return jsonify(require_ready_error), 400

        response = _build_weekly_cart_response(client, dry_run, require_ready)
        status_code = response.pop("_status_code", 200)
        return jsonify(response), status_code

    @app.route("/weekly_cart_preview")
    def weekly_cart_preview():
        response = _build_weekly_cart_response(client, dry_run=True, require_ready=None)
        status_code = response.pop("_status_code", 200)
        return jsonify(response), status_code

    return app


def _build_weekly_cart_response(client, dry_run, require_ready):
    try:
        plan = build_weekly_cart_plan()
    except WeeklyCartBuilderError as exc:
        return {"status": "error", "message": str(exc), "_status_code": 500}

    cart_items = plan["cart_items"]
    selection_summary = _process_cart_items(client, cart_items, dry_run=True)
    readiness_summary = _build_readiness_summary(selection_summary)

    if dry_run:
        return _weekly_cart_response_payload(
            dry_run=True,
            plan=plan,
            cart_items=cart_items,
            summary=selection_summary,
            readiness_summary=readiness_summary,
            require_ready=require_ready,
        )

    if not readiness_summary["ready_for_live_run"]:
        blocked_summary = _summary_with_counts(
            {
                **selection_summary,
                "dry_run": False,
                "added": [],
            }
        )
        return _weekly_cart_response_payload(
            dry_run=False,
            plan=plan,
            cart_items=cart_items,
            summary=blocked_summary,
            readiness_summary=readiness_summary,
            status="blocked",
            message=(
                "Weekly cart live run blocked before adding anything because "
                "the readiness check is not acceptable."
            ),
            require_ready=require_ready,
        )

    live_summary = _add_selected_items_to_cart(client, selection_summary)
    live_summary["dry_run"] = False
    live_summary = _summary_with_counts(live_summary)

    return _weekly_cart_response_payload(
        dry_run=False,
        plan=plan,
        cart_items=cart_items,
        summary=live_summary,
        readiness_summary=readiness_summary,
        status="live_run_complete",
        message=MANUAL_CHECKOUT_REMINDER,
        require_ready=require_ready,
    )


def _weekly_cart_response_payload(
    dry_run,
    plan,
    cart_items,
    summary,
    readiness_summary,
    status=None,
    message=None,
    require_ready=None,
):
    notes = list(plan["notes"])
    notes.append(readiness_summary["live_run_guidance"])
    if not dry_run:
        notes.append(MANUAL_CHECKOUT_REMINDER)

    payload = {
        "dry_run": dry_run,
        "require_ready": require_ready,
        "meal_plan": plan["meal_plan"],
        "cart_items": cart_items,
        "cart_items_count": len(cart_items),
        "attempted_count": summary["attempted_count"],
        "selected_count": summary["selected_count"],
        "added_count": summary["added_count"],
        "needs_review_count": summary["needs_review_count"],
        "failed_count": summary["failed_count"],
        "not_added_count": summary["not_added_count"],
        "selected": summary["selected"],
        "added": summary["added"],
        "needs_review": summary["needs_review"],
        "failed": summary["failed"],
        "manual_review_items": _manual_review_items(summary["needs_review"]),
        "audit_notes": _audit_notes(plan, summary),
        "product_quality_notes": _product_quality_notes(summary),
        "notes": notes,
        "readiness_summary": readiness_summary,
    }

    if status:
        payload["status"] = status
    if message:
        payload["message"] = message
    if not dry_run:
        payload["live_run_summary"] = {
            "selected_count": summary["selected_count"],
            "added_count": summary["added_count"],
            "needs_review_count": summary["needs_review_count"],
            "failed_count": summary["failed_count"],
            "not_added_count": summary["not_added_count"],
            "manual_review_items": _manual_review_items(summary["needs_review"]),
            "message": MANUAL_CHECKOUT_REMINDER,
        }

    return payload


def _process_cart_items(client, items, dry_run):
    summary = {
        "dry_run": dry_run,
        "attempted_count": len(items),
        "selected": [],
        "added": [],
        "needs_review": [],
        "failed": [],
    }

    for index, raw_item in enumerate(items):
        parsed_item, validation_error = _parse_add_many_item(index, raw_item)
        if validation_error:
            summary["failed"].append(validation_error)
            continue

        selection_result = _select_product_for_item(client, parsed_item)

        if selection_result["status"] == "failed":
            summary["failed"].append(selection_result["item"])
            continue

        if selection_result["status"] == "needs_review":
            summary["needs_review"].append(selection_result["item"])
            continue

        selected_item = selection_result["item"]
        summary["selected"].append(selected_item)

        if dry_run:
            continue

        upc = selected_item["upc"]
        quantity = selected_item["quantity"]
        try:
            cart_response, _payload = client.add_to_cart(upc, quantity)
        except KrogerApiError as exc:
            failed_item = {
                **selected_item,
                "status": "cart_error",
                "message": str(exc),
            }
            summary["failed"].append(failed_item)
            continue

        added_item = {
            **selected_item,
            "cart_status": cart_response.status_code,
        }

        if cart_response.status_code in {200, 201, 204}:
            summary["added"].append(added_item)
        else:
            failed_item = {
                **added_item,
                "status": "cart_error",
                "message": "Kroger cart add failed.",
            }
            if cart_response.text:
                failed_item["cart_response"] = cart_response.text
            summary["failed"].append(failed_item)

    return _summary_with_counts(summary)


def _add_selected_items_to_cart(client, selection_summary):
    summary = {
        "dry_run": False,
        "attempted_count": selection_summary["attempted_count"],
        "selected": list(selection_summary["selected"]),
        "added": [],
        "needs_review": list(selection_summary["needs_review"]),
        "failed": list(selection_summary["failed"]),
    }

    for selected_item in selection_summary["selected"]:
        upc = selected_item["upc"]
        quantity = selected_item["quantity"]
        try:
            cart_response, _payload = client.add_to_cart(upc, quantity)
        except KrogerApiError as exc:
            failed_item = {
                **selected_item,
                "status": "cart_error",
                "message": str(exc),
            }
            summary["failed"].append(failed_item)
            continue

        added_item = {
            **selected_item,
            "cart_status": cart_response.status_code,
        }

        if cart_response.status_code in {200, 201, 204}:
            summary["added"].append(added_item)
        else:
            failed_item = {
                **added_item,
                "status": "cart_error",
                "message": "Kroger cart add failed.",
            }
            if cart_response.text:
                failed_item["cart_response"] = cart_response.text
            summary["failed"].append(failed_item)

    return _summary_with_counts(summary)


def _summary_with_counts(summary):
    summary["selected_count"] = len(summary["selected"])
    summary["added_count"] = len(summary["added"])
    summary["needs_review_count"] = len(summary["needs_review"])
    summary["failed_count"] = len(summary["failed"])
    summary["not_added_count"] = max(
        summary["attempted_count"] - summary["added_count"],
        0,
    )
    return summary


def _build_readiness_summary(summary):
    selected_count = len(summary["selected"])
    needs_review = summary["needs_review"]
    failed = summary["failed"]
    blocking_issues = []
    warning_issues = []

    if failed:
        blocking_issues.append("One or more items failed during search or validation.")
    if selected_count == 0:
        blocking_issues.append("No confident product matches were selected.")

    core_manual_review_items = [
        item for item in needs_review if item.get("staple_id") in CORE_STANDING_STAPLE_IDS
    ]
    unknown_manual_review_items = [
        item for item in needs_review if not _is_known_acceptable_manual_review_item(item)
    ]

    if len(core_manual_review_items) > 1:
        blocking_issues.append(
            "Multiple core standing staples need manual review; inspect the dry run before live add."
        )
    elif unknown_manual_review_items:
        blocking_issues.append(
            "One or more manual-review items are not on the known acceptable skip list."
        )

    if needs_review:
        warning_issues.append(
            "Manual-review items will not be added during a live run."
        )

    ready_for_live_run = not blocking_issues
    if ready_for_live_run:
        if needs_review:
            guidance = (
                "Ready for a live run only for confident selected items. "
                "Manual-review items will be skipped and must be handled in King Soopers if desired. "
                f"{MANUAL_CHECKOUT_REMINDER}"
            )
        else:
            guidance = (
                "Ready for a live run. Only confident selected items will be added. "
                f"{MANUAL_CHECKOUT_REMINDER}"
            )
    else:
        guidance = (
            "Do not run the weekly cart live add yet. Inspect the dry-run output, "
            "resolve blocking issues, and retry dry_run=true first."
        )

    return {
        "ready_for_live_run": ready_for_live_run,
        "selected_count": selected_count,
        "needs_review_count": len(needs_review),
        "failed_count": len(failed),
        "blocking_issues": blocking_issues,
        "warnings": warning_issues,
        "manual_review_items": _manual_review_items(needs_review),
        "live_run_guidance": guidance,
    }


def _manual_review_items(needs_review):
    return [
        {
            "term": item.get("term"),
            "original_term": item.get("original_term") or item.get("term"),
            "conceptual_item": item.get("conceptual_item"),
            "conceptual_group": item.get("conceptual_group"),
            "search_term": item.get("search_term"),
            "fallback_used": item.get("fallback_used", False),
            "reason": item.get("reason"),
            "best_candidate_description": item.get("best_candidate_description"),
            "best_candidate_brand": item.get("best_candidate_brand"),
            "best_candidate_upc": item.get("best_candidate_upc"),
            "best_candidate_confidence": item.get("best_candidate_confidence"),
            "confidence": item.get("confidence"),
            "quality_notes": item.get("quality_notes", []),
            "rejected_candidates": item.get("rejected_candidates", []),
            "suggested_manual_action": item.get("suggested_manual_action"),
        }
        for item in needs_review
    ]


def _audit_notes(plan, summary):
    notes = []
    cart_audit_notes = plan.get("cart_audit_notes") or []
    notes.extend(cart_audit_notes)

    rejected_count = sum(
        len(item.get("rejected_candidates", []))
        for item in summary["selected"] + summary["needs_review"]
    )
    if rejected_count:
        notes.append(
            f"Product quality rules rejected {rejected_count} candidate product(s)."
        )

    fallback_count = sum(1 for item in summary["selected"] if item.get("fallback_used"))
    if fallback_count:
        notes.append(f"Used fallback search terms for {fallback_count} selected item(s).")

    if summary["needs_review"]:
        notes.append("Manual-review items were not included in selected products.")

    return notes


def _product_quality_notes(summary):
    quality_items = []
    for status_key in ("selected", "needs_review"):
        for item in summary[status_key]:
            quality_notes = item.get("quality_notes", [])
            rejected_candidates = item.get("rejected_candidates", [])
            if not quality_notes and not rejected_candidates:
                continue

            quality_items.append(
                {
                    "status": status_key,
                    "term": item.get("term"),
                    "search_term": item.get("search_term"),
                    "selected_product": item.get("selected_product"),
                    "brand": item.get("brand"),
                    "upc": item.get("upc"),
                    "quality_notes": quality_notes,
                    "rejected_candidates": rejected_candidates,
                }
            )

    return quality_items


def _is_known_acceptable_manual_review_item(item):
    term = _manual_review_key(item.get("term") or item.get("original_term"))
    return (
        term in KNOWN_ACCEPTABLE_MANUAL_REVIEW_TERMS
        or item.get("staple_id") in KNOWN_ACCEPTABLE_MANUAL_REVIEW_STAPLE_IDS
    )


def _manual_review_key(value):
    return " ".join(str(value or "").lower().split())


def _parse_dry_run_param(value):
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True, None
    if normalized in {"false", "0", "no", "n"}:
        return False, None

    return None, {
        "status": "error",
        "message": "dry_run must be true or false.",
    }


def _parse_optional_bool_param(value, name):
    if value is None:
        return None, None

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True, None
    if normalized in {"false", "0", "no", "n"}:
        return False, None

    return None, {
        "status": "error",
        "message": f"{name} must be true or false.",
    }


def _parse_quantity(value):
    if isinstance(value, bool):
        return None

    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None

    if quantity < 1:
        return None

    return quantity


def _parse_add_many_item(index, item):
    if not isinstance(item, dict):
        return None, {
            "index": index,
            "status": "invalid_item",
            "message": "Item must be an object with term and quantity.",
        }

    term_value = item.get("term", "")
    if not isinstance(term_value, str):
        return None, {
            "index": index,
            "status": "invalid_item",
            "message": "Item term must be a non-empty string.",
        }

    term = term_value.strip()
    quantity = _parse_quantity(item.get("quantity", 1))

    if not term:
        return None, {
            "index": index,
            "status": "invalid_item",
            "message": "Item term is required.",
        }

    if quantity is None:
        return None, {
            "index": index,
            "term": term,
            "status": "invalid_item",
            "message": "quantity must be a positive integer.",
        }

    fallback_terms, fallback_error = _parse_fallback_terms(item.get("fallback_terms", []))
    if fallback_error:
        return None, {
            "index": index,
            "term": term,
            "status": "invalid_item",
            "message": fallback_error,
        }

    parsed_item = {
        "index": index,
        "term": term,
        "quantity": quantity,
        "fallback_terms": fallback_terms,
    }

    for optional_key in ("staple_id", "staple_name", "protein_group"):
        optional_value = item.get(optional_key)
        if isinstance(optional_value, str) and optional_value.strip():
            parsed_item[optional_key] = optional_value.strip()

    for optional_key in ("protein_deduped",):
        if item.get(optional_key) is True:
            parsed_item[optional_key] = True

    merged_terms = item.get("merged_terms")
    if isinstance(merged_terms, list):
        clean_merged_terms = [
            term.strip()
            for term in merged_terms
            if isinstance(term, str) and term.strip()
        ]
        if clean_merged_terms:
            parsed_item["merged_terms"] = clean_merged_terms

    return parsed_item, None


def _parse_fallback_terms(value):
    if value in (None, ""):
        return [], None

    if not isinstance(value, list):
        return None, "fallback_terms must be a list of strings."

    fallback_terms = []
    seen = set()
    for raw_term in value:
        if not isinstance(raw_term, str) or not raw_term.strip():
            return None, "fallback_terms must be a list of non-empty strings."
        fallback_term = raw_term.strip()
        dedupe_key = fallback_term.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        fallback_terms.append(fallback_term)

    return fallback_terms, None


def _select_product_for_item(client, parsed_item):
    attempts = []
    original_term = parsed_item["term"]
    quantity = parsed_item["quantity"]
    index = parsed_item["index"]

    for search_index, search_term in enumerate(_search_terms_for_item(parsed_item)):
        selection_result = _select_product_for_term(
            client,
            search_term,
            quantity,
            index,
            original_term,
            fallback_used=search_index > 0,
            item_metadata=parsed_item,
        )

        if selection_result["status"] == "selected":
            return selection_result

        if selection_result["status"] == "failed":
            return selection_result

        attempts.append(
            {
                "search_term": search_term,
                "fallback_used": search_index > 0,
                "confidence": selection_result["item"]["confidence"],
                "reason": selection_result["item"]["reason"],
                "candidates": selection_result["item"]["candidates"],
                "rejected_candidates": selection_result["item"].get(
                    "rejected_candidates",
                    [],
                ),
                "quality_notes": selection_result["item"].get("quality_notes", []),
            }
        )

    best_attempt = _best_review_attempt(attempts)
    review_item = {
        "index": index,
        "term": original_term,
        "quantity": quantity,
        "confidence": best_attempt["confidence"] if best_attempt else 0,
        "reason": "No primary or fallback search term matched confidently enough to add.",
        "candidates": best_attempt["candidates"] if best_attempt else [],
        "rejected_candidates": best_attempt["rejected_candidates"] if best_attempt else [],
        "quality_notes": best_attempt["quality_notes"] if best_attempt else [],
        "fallback_terms": parsed_item.get("fallback_terms", []),
        "fallback_attempts": attempts,
    }
    _enrich_needs_review_item(review_item, parsed_item, best_attempt)

    return {"status": "needs_review", "item": review_item}


def _search_terms_for_item(parsed_item):
    search_terms = [parsed_item["term"]]
    search_terms.extend(parsed_item.get("fallback_terms", []))

    unique_terms = []
    seen = set()
    for search_term in search_terms:
        dedupe_key = search_term.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique_terms.append(search_term)

    return unique_terms


def _best_review_attempt(attempts):
    if not attempts:
        return None

    return max(attempts, key=lambda attempt: attempt["confidence"])


def _select_product_for_term(
    client,
    search_term,
    quantity,
    index,
    original_term,
    fallback_used=False,
    item_metadata=None,
):
    try:
        search_response = client.search_products(
            search_term,
            limit=client.config.add_by_term_search_limit,
        )
    except KrogerApiError as exc:
        return {
            "status": "failed",
            "item": {
                "index": index,
                "term": original_term,
                "search_term": search_term,
                "quantity": quantity,
                "status": "search_error",
                "message": str(exc),
            },
        }

    if search_response.status_code >= 400:
        return {
            "status": "failed",
            "item": {
                "index": index,
                "term": original_term,
                "search_term": search_term,
                "quantity": quantity,
                "status": "search_error",
                "search_status": search_response.status_code,
                "message": "Kroger product search failed.",
            },
        }

    try:
        products = search_response.json().get("data", [])
    except ValueError:
        return {
            "status": "failed",
            "item": {
                "index": index,
                "term": original_term,
                "search_term": search_term,
                "quantity": quantity,
                "status": "search_error",
                "message": "Kroger product search returned non-JSON data.",
            },
        }

    selection = select_best_product(search_term, products)
    if selection["status"] != "selected":
        review_item = {
            "index": index,
            "term": original_term,
            "search_term": search_term,
            "fallback_used": fallback_used,
            "quantity": quantity,
            "confidence": selection["confidence"],
            "reason": selection["reason"],
            "candidates": selection["candidates"],
            "rejected_candidates": selection.get("rejected_candidates", []),
            "quality_notes": selection.get("quality_notes", []),
        }
        _enrich_needs_review_item(
            review_item,
            item_metadata,
            {
                "search_term": search_term,
                "fallback_used": fallback_used,
                "confidence": selection["confidence"],
                "candidates": selection["candidates"],
                "rejected_candidates": selection.get("rejected_candidates", []),
                "quality_notes": selection.get("quality_notes", []),
            },
        )
        return {
            "status": "needs_review",
            "item": review_item,
        }

    return {
        "status": "selected",
        "item": _selected_item_summary(
            index,
            original_term,
            search_term,
            quantity,
            selection,
            fallback_used,
            item_metadata,
        ),
    }


def _copy_item_metadata(source, destination):
    if not source:
        return

    for optional_key in (
        "staple_id",
        "staple_name",
        "protein_group",
        "protein_deduped",
        "merged_terms",
    ):
        if optional_key in source:
            destination[optional_key] = source[optional_key]


def _enrich_needs_review_item(item, item_metadata=None, best_attempt=None):
    _copy_item_metadata(item_metadata, item)

    item["original_term"] = item.get("original_term") or item.get("term")
    if item.get("staple_name"):
        item["conceptual_item"] = item["staple_name"]
    if item.get("staple_id"):
        item["conceptual_group"] = "standing_staple"

    if best_attempt:
        item["search_term"] = best_attempt.get("search_term") or item.get("search_term")
        item["fallback_used"] = best_attempt.get("fallback_used", item.get("fallback_used", False))
        item["confidence"] = best_attempt.get("confidence", item.get("confidence", 0))
        if best_attempt.get("candidates") is not None:
            item["candidates"] = best_attempt["candidates"]
        if best_attempt.get("rejected_candidates") is not None:
            item["rejected_candidates"] = best_attempt["rejected_candidates"]
        if best_attempt.get("quality_notes") is not None:
            item["quality_notes"] = best_attempt["quality_notes"]
    else:
        item["fallback_used"] = item.get("fallback_used", False)

    best_candidate = _best_candidate(item.get("candidates", []))
    if best_candidate:
        item["best_candidate_description"] = best_candidate.get("description")
        item["best_candidate_brand"] = best_candidate.get("brand")
        item["best_candidate_upc"] = best_candidate.get("upc")
        item["best_candidate_confidence"] = best_candidate.get("confidence")

    item["suggested_manual_action"] = _suggested_manual_action(item)
    return item


def _best_candidate(candidates):
    if isinstance(candidates, list) and candidates:
        first_candidate = candidates[0]
        if isinstance(first_candidate, dict):
            return first_candidate
    return {}


def _suggested_manual_action(item):
    if _is_known_acceptable_manual_review_item(item):
        return (
            "Search/add Liquid Death flavored sparkling water manually in King Soopers "
            "if desired, or accept skipping this drink slot for this run."
        )

    return (
        "Review this item manually in King Soopers. Add a matching product only if it "
        "is clearly correct, otherwise skip it for this run."
    )


def _selected_item_summary(
    index,
    term,
    search_term,
    quantity,
    selection,
    fallback_used,
    item_metadata,
):
    selected_product = selection["product"]
    item = {
        "index": index,
        "term": term,
        "search_term": search_term,
        "fallback_used": fallback_used,
        "quantity": quantity,
        "selected_product": selected_product.get("description"),
        "brand": selected_product.get("brand"),
        "upc": selected_product.get("upc"),
        "confidence": selection["confidence"],
        "reason": selection["reason"],
    }
    if selection.get("quality_notes"):
        item["quality_notes"] = selection["quality_notes"]
    if selection.get("rejected_candidates"):
        item["rejected_candidates"] = selection["rejected_candidates"]
    _copy_item_metadata(item_metadata, item)
    return item


def _pre_response(response):
    return f"<pre>Status: {response.status_code}\n\n{response.text}</pre>"


def _plain_error(error):
    return f"<pre>Status: 502\n\n{error}</pre>"


app = create_app()


if __name__ == "__main__":
    app.run(port=3000)
