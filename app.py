import json

from flask import Flask, jsonify, request

from config import load_config
from kroger_client import KrogerApiError, KrogerClient
from product_selector import select_best_product


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

    return app


def _parse_quantity(value):
    try:
        quantity = int(value)
    except (TypeError, ValueError):
        return None

    if quantity < 1:
        return None

    return quantity


def _pre_response(response):
    return f"<pre>Status: {response.status_code}\n\n{response.text}</pre>"


def _plain_error(error):
    return f"<pre>Status: 502\n\n{error}</pre>"


app = create_app()


if __name__ == "__main__":
    app.run(port=3000)
