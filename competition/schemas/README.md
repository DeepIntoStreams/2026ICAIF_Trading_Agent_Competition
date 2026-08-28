# What the files in this folder are (plain-language decoder)

These four `.json` files are written in **JSON Schema** - a language for describing "what a
valid message looks like." They are cryptic to read raw, so this page decodes each one. You do
**not** need to read the raw files; read this instead.

Think of each schema as a **blank form template**: it lists the boxes a message must have and
what kind of value goes in each box. The competition server and each team's bot both follow
these templates so they can exchange messages reliably.

Common keywords, once:
- `required` = boxes that MUST be filled in (else the message is rejected)
- `type: string / number / object / array / boolean` = the kind of value allowed
- `const` = must be exactly this fixed value
- `format: date` = a calendar date like `2026-06-05`; `date-time` = a timestamp
- `additionalProperties: false` = no unexpected extra boxes allowed

There are two messages exchanged each trading day, and two variants of the data block:

```
  SERVER  --  decision_request  -->  TEAM'S BOT      (contains an "observation")
  TEAM'S BOT -- decision_response -->  SERVER        (contains "target_weights")
```

---

## 1. `decision_request.schema.json` - server -> bot

"Here is today's market data; decide before the deadline."

| Field | Type | Meaning |
|---|---|---|
| `type` | must be `"decision_request"` | tells the receiver which message this is |
| `protocol_version` | text like `"0.1"` | which version of the rules |
| `run_id` | text | which competition run |
| `team_id` | text | which team this is addressed to |
| `session_date` | date | the trading day |
| `deadline_utc` | timestamp | submit before this instant |
| `observation` | object | the market data (see #3 / #4 below) |

## 2. `decision_response.schema.json` - bot -> server

"Here is my portfolio for today." **This is the one a team produces.**

| Field | Type | Meaning |
|---|---|---|
| `type` | must be `"decision_response"` | message type |
| `protocol_version` | text like `"0.1"` | rules version |
| `run_id` | text | echoes the request |
| `team_id` | text | the team's id |
| `session_date` | date | the trading day |
| `target_weights` | object of ticker -> number | **the portfolio**, e.g. `{"AAPL": 0.2, "MSFT": 0.15}` |
| `metadata` | object (optional) | extra info, e.g. `used_external_news: true` |

Cash is implicit: whatever is not allocated stays in cash (`1 - sum of weights`).

## 3. `observation_with_news.schema.json` - the data block, news version

The market snapshot placed inside a `decision_request`.

| Field | Type | Meaning |
|---|---|---|
| `session_date` | date | the trading day |
| `event_time_utc` | timestamp | the decision cutoff - the no-leakage boundary |
| `assets` | array | the tradable stocks: `ticker, company_name, sector, open_price` |
| `market_features` | ticker -> numbers | price/technical signals (returns, momentum, volatility, ...) |
| `fundamental_features` | ticker -> numbers | company fundamentals (margins, leverage, ...) |
| `portfolio` | object | current holdings: `weights, cash_ratio, nav` |
| `constraints` | object | the rules: `long_only, max_asset_weight, max_gross_exposure, fee_rate` |
| `news` | array | news items; **each carries `available_at_utc` <= `event_time_utc`** so no future news leaks |

## 4. `observation_without_news.schema.json` - the data block, no-news version

Identical to #3, except `news` must be an empty list (`maxItems: 0`). Kept as its own file so
a team's code always sees a `news` field and never has to branch on whether it exists.

---

## A complete valid response (copy-paste example)

```json
{
  "type": "decision_response",
  "protocol_version": "0.1",
  "run_id": "official_2026_live",
  "team_id": "team_001",
  "session_date": "2026-06-05",
  "target_weights": {"AAPL": 0.20, "MSFT": 0.15, "NVDA": 0.10},
  "metadata": {"agent_version": "v1", "used_external_news": false}
}
```

To check any message against these templates:

```bash
python scripts/validate_protocol.py --response my_response.json   # -> [] means valid
python scripts/validate_protocol.py --selftest                    # checks the schemas themselves
```
