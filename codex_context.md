# GroceryAgent Context

## Project Goal

Build a local Flask-based Grocery Agent bridge that connects ChatGPT-generated grocery plans to the Kroger / King Soopers API.

The final workflow should be:

User says: "Weekly groceries"

System:
1. Generates a weekly grocery plan for 2 people.
2. Maintains permanent household staples.
3. Searches Kroger products using the user's preferred King Soopers store.
4. Selects best matching products.
5. Adds selected items to the user's real Kroger / King Soopers cart.
6. User manually reviews and checks out.

The app must never place the final grocery order. The user always manually reviews and approves checkout inside King Soopers.

---

## Current Working Features

The following features are already confirmed working and should be preserved:

- Flask app runs locally on port 3000.
- OAuth works with Kroger.
- `.env` stores `CLIENT_ID`, `CLIENT_SECRET`, and `REDIRECT_URI`.
- `tokens.json` stores OAuth tokens locally.
- Refresh token flow works.
- `/stores` works and returns live Kroger / King Soopers store data.
- `/search?term=...` works and returns live product data.
- `/cart/add?upc=...&quantity=1` works.
- `/cart/add_many` POST endpoint is implemented.
- `/cart/add_many` supports `dry_run=true` and `dry_run=false`.
- `dry_run=true` previews selected products without touching the real cart.
- `dry_run=false` adds only confident matches to the real cart.
- Low-confidence matches go to `needs_review`.
- Invalid items go to `failed`.
- A live `/cart/add_many` test with Cherry Coke Zero was confirmed in the real King Soopers cart.
- The verified Cherry Coke Zero product was Coca-Cola® Zero Sugar Cherry Soda Cans, UPC `0004900004751`, `cart_status` `204`.
- Phase 4 Weekly Cart MVP is complete and committed in `711ba49 Add weekly cart MVP dry run`.
- `/build_weekly_cart?dry_run=true` exists and was dry-run tested successfully.
- `/build_weekly_cart?dry_run=true` generated a 4-meal weekly cart plan and routed it through the same product-selection/cart processing flow as `/cart/add_many`.
- The verified Phase 4 dry run selected 22 of 27 items, with 5 `needs_review`, 0 `failed`, and 0 `added`.
- `/build_weekly_cart?dry_run=false` exists but has intentionally not been live-tested yet.
- Real cart insertion into the user's actual King Soopers cart has been confirmed.

Do not break these behaviors during refactors.

---

## Verified Phase 3 Behavior

- Endpoint: `POST /cart/add_many`
- Accepts JSON with `dry_run` and `items`.
- Response includes `dry_run`, `attempted_count`, `selected`, `added`, `needs_review`, and `failed`.
- `dry_run=true` previews selected products without touching the real cart.
- `dry_run=false` adds only confident matches to the real cart.
- Low-confidence matches go to `needs_review`.
- Invalid items go to `failed`.
- Live mode must never checkout or place an order.
- The user manually verifies the real King Soopers cart after live tests.
- Live verification confirmed Cherry Coke Zero selected Coca-Cola® Zero Sugar Cherry Soda Cans, UPC `0004900004751`, with `cart_status` `204`.

---

## Verified Phase 4 Dry Run Behavior

- Endpoint: `GET /build_weekly_cart?dry_run=true`
- Does not touch the real cart.
- Returns `meal_plan`, `cart_items_count`, `selected`, `added`, `needs_review`, `failed`, and `notes`.
- Dry-run summary from committed Phase 4 MVP:
  - meal count: 4
  - cart item count: 27
  - selected count: 22
  - needs_review count: 5
  - failed count: 0
  - added count: 0
- Current known `needs_review` terms:
  1. Liquid Death Severed Lime
  2. Arizona Green Tea Zero Sugar Jug
  3. Dave's Killer Bread
  4. Boar's Head Ovengold Turkey
  5. Tillamook Havarti sliced cheese
- `/build_weekly_cart?dry_run=false` must not be run unless the user explicitly requests it after inspecting an acceptable dry run.
- Live weekly cart adding must remain blocked until standing staples resolution improves and a dry run has acceptable `selected` / `needs_review` results.

---

## Current Technical Stack

- Python
- Flask
- requests
- python-dotenv
- Kroger Public APIs
- Local development server on `localhost:3000`

Current rough folder state:

```text
GroceryAgent/
  app.py
  .env
  tokens.json
  venv/
```

Target project structure:

```text
GroceryAgent/
  app.py
  config.py
  kroger_client.py
  grocery_profile.py
  product_selector.py
  weekly_cart_builder.py
  pantry.json
  grocery_profile.json
  meal_templates.json
  substitution_rules.json
  meal_history.json
  tokens.json
  .env
  codex_context.md
  README.md
```

This target structure is aspirational. Refactor gradually and preserve working behavior.

---

## Security Rules

- Do not hardcode secrets.
- Keep `CLIENT_ID`, `CLIENT_SECRET`, and `REDIRECT_URI` in `.env`.
- Keep `tokens.json` local and private.
- Never print `access_token` or `refresh_token` in normal app output.
- Do not commit `.env`, `tokens.json`, or `venv/`.
- Add or maintain a `.gitignore` that excludes:
  - `.env`
  - `tokens.json`
  - `venv/`
  - `__pycache__/`
  - `.DS_Store`
- Use automatic token refresh before Kroger API requests.
- If an API request fails due to auth, return a clean error message and avoid exposing tokens.
- The app should never submit or finalize checkout.

---

## Kroger / King Soopers API Behavior

Known working flows:

1. OAuth authorization:
   - User opens `http://localhost:3000`
   - User clicks connect link
   - User logs into actual Kroger / King Soopers customer account
   - Tokens are saved locally to `tokens.json`

2. Store lookup:
   - `/stores`
   - Uses authenticated API request
   - Returns nearby store data

3. Product search:
   - `/search?term=Cherry Coke Zero`
   - Uses authenticated API request
   - Uses preferred location/store filtering when available
   - Returns product JSON containing UPCs

4. Cart add:
   - `/cart/add?upc=<UPC>&quantity=1`
   - Sends `PUT` request to Kroger cart add endpoint
   - Payload uses pickup modality
   - Confirmed status `204` can mean successful cart insertion

Important:
- Cart insertion should be checked in the user's normal King Soopers customer account, not the Kroger developer portal account.
- The OAuth login must use the user's real grocery shopping account.

---

## Household Grocery Profile

Household:
- 2 people
- Weekly haul
- Budget target: $100–150
- Prefer name brands and quality over aggressive bargain optimization
- Store: closest/preferred King Soopers
- Mode: semi-automatic; user approves final checkout manually

Weekly structure:
- 4 dinners
- Sandwich/lunch support
- Drinks/staples
- Mostly 30-minute meals
- 30–45 minutes occasionally okay

Meal preferences:
- Rice-based dishes
- Pasta/noodle-based dishes
- Protein/carb/veggie trio
- Fun flavorful dinners
- Broad palate
- Few dislikes

Permanent drinks:
1. Coke Zero or Coke Zero flavor variant
2. Liquid Death flavored water
3. Arizona Green Tea jug, preferably zero sugar

Drink slot examples:
- Coke Zero
- Cherry Coke Zero
- Vanilla Coke Zero
- Liquid Death Severed Lime
- Liquid Death Berry It Alive
- Liquid Death Mango Chainsaw
- Liquid Death Convicted Melon
- Arizona Green Tea Zero Sugar Jug
- Arizona Diet Green Tea

Sandwich core:
- Dave's Killer Bread
- Tillamook sliced cheese
- Boar's Head deli meat

Sandwich pairing examples:
- Boar's Head Ovengold Turkey + Tillamook Havarti
- Roast beef + Tillamook Sharp Cheddar
- Ham + Swiss
- Chicken + Pepper Jack
- Italian meats + Provolone

---

## Product Selection Rules

When selecting products from Kroger search results, prefer:

1. Correct product category.
2. Strong match to search term.
3. Preferred brand.
4. Pickup availability at the preferred store.
5. Reasonable package size for a weekly household haul.
6. Name-brand/quality option when it materially matters.
7. Avoid irrelevant products even if they match a keyword.

Examples:

For `Dave's Killer Bread`, prefer an actual Dave's Killer Bread loaf over:
- bread crumbs
- buns, unless specifically requested
- protein bars
- unrelated bakery items

For `Cherry Coke Zero`, prefer:
- Coca-Cola Zero Sugar Cherry Cola cans
over:
- regular Cherry Coke
- Diet Coke
- unrelated cherry beverages

For `Liquid Death Severed Lime`, prefer:
- Liquid Death Severed Lime sparkling water
over:
- unrelated lime sparkling water
unless fallback/substitution is needed.

For `Boar's Head Turkey`, prefer:
- Boar's Head turkey deli meat
over:
- packaged non-deli turkey
- unrelated turkey products

---

## Substitution Philosophy

Use substitutions when the ideal item is unavailable, not as a first choice.

Substitution examples:

Coke Zero slot:
1. Coke Zero
2. Cherry Coke Zero
3. Vanilla Coke Zero
4. Dr Pepper Zero as occasional alternate

Liquid Death slot:
1. Liquid Death Severed Lime
2. Liquid Death Berry It Alive
3. Liquid Death Mango Chainsaw
4. Liquid Death Convicted Melon
5. Other Liquid Death flavored water

Arizona tea slot:
1. Arizona Green Tea Zero Sugar Jug
2. Arizona Diet Green Tea
3. Arizona Green Tea Jug
4. Gold Peak Zero Sugar Tea
5. Pure Leaf Zero Sugar Tea

Sandwich meat:
1. Boar's Head Ovengold Turkey
2. Boar's Head Honey Maple Turkey
3. Boar's Head ham
4. Boar's Head roast beef
5. Private Selection deli meat only if Boar's Head is unavailable

Cheese:
1. Tillamook Havarti
2. Tillamook Sharp Cheddar
3. Tillamook Swiss
4. Tillamook Pepper Jack
5. Other quality sliced cheese if Tillamook unavailable

If no confident substitution exists, put the item in a manual review bucket instead of adding a bad item.

---

## Planned Feature Roadmap

### Phase 1: Stabilize Local Project

Goals:
- Make the project Codex-friendly.
- Refactor without breaking confirmed working routes.
- Add safer debug output.
- Add `.gitignore`.
- Keep secrets private.

Recommended actions:
1. Preserve current working `app.py`.
2. Move repeated Kroger API logic into `kroger_client.py`.
3. Move configuration loading into `config.py`.
4. Keep Flask routes simple.
5. Add `README.md` with local run steps.
6. Add `.gitignore`.

Do not implement the whole dream system at once.

---

### Phase 2: Add `/cart/add_by_term`

Goal:
Stop manually copying UPCs.

Endpoint:

```text
/cart/add_by_term?term=Cherry Coke Zero&quantity=1
```

Behavior:
1. Accept `term` and optional `quantity`.
2. Search Kroger products.
3. Rank/select best product.
4. Add selected UPC to cart.
5. Return clean summary.

Example response:

```json
{
  "status": "added",
  "term": "Cherry Coke Zero",
  "selected_product": "Coca-Cola Zero Sugar Cherry Cola Soda Cans",
  "upc": "000490000...",
  "quantity": 1,
  "cart_status": 204
}
```

If selection confidence is low, do not add automatically. Return manual review.

---

### Phase 3: Add `/cart/add_many` - Complete

Goal:
Batch-add multiple grocery items.

Status:
- Complete and live verified against the real King Soopers cart.
- Verified live item: Cherry Coke Zero selected Coca-Cola® Zero Sugar Cherry Soda Cans, UPC `0004900004751`, with `cart_status` `204`.

Endpoint accepts JSON payload like:

```json
{
  "dry_run": true,
  "items": [
    {"term": "Cherry Coke Zero 12 pack", "quantity": 1},
    {"term": "Liquid Death Severed Lime", "quantity": 1},
    {"term": "Dave's Killer Bread", "quantity": 1}
  ]
}
```

Behavior:
1. Loop through items.
2. Search.
3. Rank/select.
4. In `dry_run=true`, preview selected products without touching the real cart.
5. In `dry_run=false`, add only confident matches to the real cart.
6. Put low-confidence matches in `needs_review`.
7. Put invalid items or errors in `failed`.
8. Return `selected`, `added`, `needs_review`, and `failed` summary.

Live mode must never checkout or place an order. The user manually reviews and approves checkout inside King Soopers.

---

### Phase 4: Weekly Cart MVP - Complete

Goal:
Build a weekly cart from a reusable grocery profile.

Status:
- Complete and committed in `711ba49 Add weekly cart MVP dry run`.
- Dry-run verified only; live weekly cart adding has not been tested.
- The dry run generated a 4-meal weekly cart plan and routed items through the same product-selection/cart processing flow as `/cart/add_many`.
- Current dry-run result: 22 selected of 27 cart items, 5 `needs_review`, 0 `failed`, 0 `added`.

Files:
- `grocery_profile.json`
- `meal_templates.json`

Endpoint:

```text
/build_weekly_cart?dry_run=true
```

Behavior:
1. Load grocery profile.
2. Select 4 dinners.
3. Add sandwich core.
4. Add drink slots.
5. Add staples.
6. Return structured cart plan.
7. In dry-run mode, do not add to cart.

Live mode exists but is blocked pending safer dry-run quality:

```text
/build_weekly_cart?dry_run=false
```

Do not run live weekly cart mode unless the user explicitly asks for it after dry-run inspection. It should add only confident matches and must never checkout or place an order.

---

### Phase 4.1: Standing Staples Resolution Fix - Next Active Phase

Goal:
Improve product search and conservative fallback handling for standing staples that currently land in `needs_review`.

Problem terms:
1. Liquid Death Severed Lime
2. Arizona Green Tea Zero Sugar Jug
3. Dave's Killer Bread
4. Boar's Head Ovengold Turkey
5. Tillamook Havarti sliced cheese

Behavior:
- Try ordered fallback search terms for standing staples.
- Add at most one selected product per conceptual staple.
- Include the fallback search term used in the response when a fallback resolves an item.
- Prefer `needs_review` over a bad automatic selection.
- Preserve the Dave's Killer Bread safety rule: prefer loaf/sliced bread and reject bagels, buns, rolls, English muffins, breakfast bread, and similar non-loaf formats unless explicitly requested.

---

### Phase 5: Store and Availability Intelligence

Goals:
- Use preferred store ID consistently.
- Filter for pickup availability.
- Avoid products that are not available at the chosen store.
- Store preferred location info in a config or JSON file.

Possible file:

```json
{
  "preferred_store_id": "62000056",
  "preferred_store_name": "Preferred King Soopers"
}
```

Do not assume store ID permanently if a route can verify or update it.

---

### Phase 6: Substitution Engine

Files:
- `substitution_rules.json`

Behavior:
- Try preferred item first.
- Try ranked fallbacks.
- If none are confident, send to manual review.
- Never add weird low-confidence products automatically.

---

### Phase 7: Pantry Memory

File:
- `pantry.json`

Simple statuses:
- `out`
- `low`
- `half`
- `plenty`
- `unknown`

Example:

```json
{
  "rice": "plenty",
  "soy_sauce": "plenty",
  "eggs": "low",
  "coke_zero": "out",
  "liquid_death": "low",
  "daves_bread": "out",
  "tillamook_cheese": "half",
  "boars_head_meat": "out"
}
```

Use pantry memory to avoid buying unnecessary staples.

---

### Phase 8: Meal History and Preference Learning

File:
- `meal_history.json`

Track:
- meal name
- date
- rating
- notes

Rules:
- Avoid repeating the same dinner within 3–4 weeks unless requested.
- Increase frequency of liked meals.
- Decrease frequency of disliked/meh meals.
- Maintain weekly mix: rice, pasta/noodle, classic, fun.

---

### Phase 9: Budget Awareness

Goal:
Keep weekly haul near $100–150.

Behavior:
- Estimate product prices when available.
- If over $150, suggest trims before adding.
- Preserve core dinners, drinks, and sandwich essentials.
- Trim optional snacks/extras first.
- Consider protein swaps if needed.

Example:
- Swap salmon for chicken thighs if cart estimate is too high.
- Remove optional snacks before removing core meals.

---

### Phase 10: Simple Local Dashboard

Goal:
Make the app pleasant to use in a browser.

Homepage should eventually show:
- Auth status
- Preferred store
- Search test
- Add by term form
- Dry-run weekly cart button
- Live add weekly cart button
- Pantry editor
- Last cart build summary

Priority:
- Function first
- Nice UI later

---

## Immediate Next Codex Task

The next task for Codex should be limited and precise.

Prompt:

```text
Read codex_context.md.

Start Phase 4.1: Standing Staples Resolution Fix.

Requirements:
- Preserve OAuth login and callback.
- Preserve token refresh.
- Preserve /stores.
- Preserve /search?term=...
- Preserve /cart/add?upc=...&quantity=...
- Preserve /cart/add_by_term?term=...&quantity=...
- Preserve /cart/add_many dry-run and live behavior.
- Preserve /build_weekly_cart?dry_run=true.
- Do not run /build_weekly_cart?dry_run=false unless explicitly requested after dry-run inspection.
- Add ordered fallback search terms for standing staples that landed in needs_review.
- Add at most one selected product per conceptual staple.
- Product search should use existing preferred store filtering where available.
- Product selection should remain conservative.
- If confidence is low, send the item to needs_review.
- Live mode must never checkout or place an order.
- Never print access_token or refresh_token.
- After changes, provide exact test commands and expected outputs.

Do not add pantry memory yet.
Do not add meal history yet.
Do not add a dashboard yet.
Do not implement price optimization yet.
Do not change OAuth scopes unless absolutely necessary.
```

---

## Development Style

Use small, testable increments.

Preferred cycle:
1. Implement one feature.
2. Run Flask server.
3. Test exact URL.
4. Confirm cart behavior.
5. Only then proceed.

Avoid broad refactors and new features in the same step unless explicitly requested.

---

## Definition of Done for Next Milestone

The next milestone is complete when:

1. Existing routes still work.
2. `/cart/add_many` dry-run and live behavior still work.
3. `/build_weekly_cart?dry_run=true` still generates a structured weekly cart plan.
4. Standing staples use ordered fallback search terms.
5. Needs-review count improves if Kroger search returns confident candidates.
6. Low-confidence items are sent to `needs_review` instead of being added automatically.
7. Dave's Killer Bread does not select bagels, buns, rolls, or other non-loaf products.
8. No tokens or secrets are printed.
9. Live cart adds, if enabled later, never checkout or place an order.
