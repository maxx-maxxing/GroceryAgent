import json

from flask import Flask, jsonify, request

from config import load_config
from kroger_client import KrogerApiError, KrogerClient
from product_selector import select_best_product
from weekly_cart_builder import WeeklyCartBuilderError, build_weekly_cart_plan


settings = load_config()
kroger_client = KrogerClient(settings)


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

        try:
            plan = build_weekly_cart_plan()
        except WeeklyCartBuilderError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 500

        cart_items = plan["cart_items"]
        summary = _process_cart_items(client, cart_items, dry_run)

        return jsonify(
            {
                "dry_run": dry_run,
                "meal_plan": plan["meal_plan"],
                "cart_items": cart_items,
                "cart_items_count": len(cart_items),
                "attempted_count": summary["attempted_count"],
                "selected": summary["selected"],
                "added": summary["added"],
                "needs_review": summary["needs_review"],
                "failed": summary["failed"],
                "notes": plan["notes"],
            }
        )

    return app


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

        selection_result = _select_product_for_term(
            client,
            parsed_item["term"],
            parsed_item["quantity"],
            index,
        )

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

    return summary


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

    return {"index": index, "term": term, "quantity": quantity}, None


def _select_product_for_term(client, term, quantity, index):
    try:
        search_response = client.search_products(
            term,
            limit=client.config.add_by_term_search_limit,
        )
    except KrogerApiError as exc:
        return {
            "status": "failed",
            "item": {
                "index": index,
                "term": term,
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
                "term": term,
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
                "term": term,
                "quantity": quantity,
                "status": "search_error",
                "message": "Kroger product search returned non-JSON data.",
            },
        }

    selection = select_best_product(term, products)
    if selection["status"] != "selected":
        return {
            "status": "needs_review",
            "item": {
                "index": index,
                "term": term,
                "quantity": quantity,
                "confidence": selection["confidence"],
                "reason": selection["reason"],
                "candidates": selection["candidates"],
            },
        }

    return {
        "status": "selected",
        "item": _selected_item_summary(index, term, quantity, selection),
    }


def _selected_item_summary(index, term, quantity, selection):
    selected_product = selection["product"]
    return {
        "index": index,
        "term": term,
        "quantity": quantity,
        "selected_product": selected_product.get("description"),
        "brand": selected_product.get("brand"),
        "upc": selected_product.get("upc"),
        "confidence": selection["confidence"],
        "reason": selection["reason"],
    }


def _pre_response(response):
    return f"<pre>Status: {response.status_code}\n\n{response.text}</pre>"


def _plain_error(error):
    return f"<pre>Status: 502\n\n{error}</pre>"


app = create_app()


if __name__ == "__main__":
    app.run(port=3000)
