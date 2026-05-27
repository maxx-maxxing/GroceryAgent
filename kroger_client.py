import base64
import json
from urllib.parse import urlencode

import requests


class KrogerApiError(Exception):
    pass


class KrogerClient:
    def __init__(self, config, session=None):
        self.config = config
        self.session = session or requests.Session()

    def authorization_url(self):
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "response_type": "code",
            "scope": "product.compact cart.basic:write",
        }
        return f"{self.config.auth_url}?{urlencode(params)}"

    def exchange_code_for_tokens(self, code):
        response = self.session.post(
            self.config.token_url,
            headers=self._basic_auth_headers(),
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            },
        )

        tokens = self._json_response(response, "token exchange")
        self._save_tokens(tokens)
        return tokens

    def refresh_access_token(self):
        tokens = self._load_tokens()
        refresh_token = tokens["refresh_token"]

        response = self.session.post(
            self.config.token_url,
            headers=self._basic_auth_headers(),
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )

        new_tokens = self._json_response(response, "token refresh")
        if "refresh_token" not in new_tokens:
            new_tokens["refresh_token"] = refresh_token

        if "access_token" not in new_tokens:
            raise KrogerApiError("Token refresh did not return an access token.")

        self._save_tokens(new_tokens)
        return new_tokens["access_token"]

    def get_locations(self):
        return self.session.get(
            f"{self.config.api_base_url}/locations",
            headers=self._bearer_headers(),
            params={
                "filter.zipCode.near": self.config.store_zip_code,
                "filter.radiusInMiles": self.config.store_radius_miles,
                "filter.limit": self.config.store_limit,
            },
        )

    def search_products(self, term, limit=None):
        params = {
            "filter.term": term,
            "filter.locationId": self.config.preferred_location_id,
            "filter.fulfillment": "csp",
            "filter.limit": limit or self.config.product_search_limit,
        }

        return self.session.get(
            f"{self.config.api_base_url}/products",
            headers=self._bearer_headers(),
            params=params,
        )

    def add_to_cart(self, upc, quantity):
        payload = {
            "items": [
                {
                    "upc": upc,
                    "quantity": quantity,
                    "modality": "PICKUP",
                }
            ]
        }

        response = self.session.put(
            f"{self.config.api_base_url}/cart/add",
            headers={
                **self._bearer_headers(),
                "Content-Type": "application/json",
            },
            json=payload,
        )
        return response, payload

    def _bearer_headers(self):
        return {"Authorization": f"Bearer {self.refresh_access_token()}"}

    def _basic_auth_headers(self):
        auth_string = f"{self.config.client_id}:{self.config.client_secret}"
        encoded = base64.b64encode(auth_string.encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    def _load_tokens(self):
        with open(self.config.token_file) as token_file:
            return json.load(token_file)

    def _save_tokens(self, tokens):
        with open(self.config.token_file, "w") as token_file:
            json.dump(tokens, token_file, indent=2)

    @staticmethod
    def _json_response(response, action):
        try:
            data = response.json()
        except ValueError as exc:
            raise KrogerApiError(f"Kroger {action} returned non-JSON data.") from exc

        if response.status_code >= 400:
            message = data.get("error_description") or data.get("error") or action
            raise KrogerApiError(f"Kroger {action} failed: {message}")

        return data
