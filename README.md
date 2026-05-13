# Lenskart Order + Tracking Wrapper v3

A thin pass-through wrapper around two Lenskart APIs:

- **customer-order-details** (Sprinklr) — orders for a phone number
- **fusion-tracking** — courier tracking events for one or more order IDs

The wrapper does **not** add boolean flags or interpret data. It just forwards
the upstream responses untouched. The agent (Maya) reads `lkStatusCode` from
the fusion-tracking events directly:

- `lkStatusCode = "800"` → delivery attempt failed (play 48-hour message)
- anything else → no failure

## Endpoints

### `GET /orders-by-phone?id=<phone>`
Returns the customer-order-details response as-is, plus a convenience
`order_ids` array.

```json
{
  "order_ids": ["1339167449", "1338720250"],
  "customer_orders": { ...original Sprinklr response... }
}
```

### `POST /tracking-by-order-ids`
Body: a JSON array of order IDs.
```json
["1339167449", "1338720250"]
```
Returns the fusion-tracking response untouched.

### `GET /full-tracking-by-phone?id=<phone>` ⭐ main endpoint for Maya
Chains both calls. Pass the phone number, get back both responses together.

```json
{
  "order_ids": ["1339167449", ...],
  "customer_orders": { ...Sprinklr response... },
  "fusion_tracking": { ...fusion-tracking response... },
  "fusion_tracking_error": null
}
```

If fusion-tracking fails (e.g. Cloudflare cookie expired), `fusion_tracking`
will be `null` and `fusion_tracking_error` will explain why — but the
customer orders are still returned.

### `GET /health`
Liveness check.

## Optional headers

Both endpoints accept fresh cookies for the upstream APIs:

- `x-sprinklr-cookie` — for customer-order-details (rarely needed)
- `x-fusion-cookie` — for fusion-tracking (often needed; Cloudflare expires ~30 min)

Set them as defaults via env vars: `SPRINKLR_COOKIE`, `FUSION_COOKIE`.

## Deploy

Push the three files (`main.py`, `requirements.txt`, this README) to a GitHub repo.
On Render / Railway / Fly.io:

- **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
- **Python:** 3.10+

### Environment variables

| Name | Purpose | Default |
|------|---------|---------|
| `SPRINKLR_AUTH` | Auth header for customer-order-details | (test token baked in) |
| `FUSION_COOKIE` | Default cookie for fusion-tracking | empty |
| `SPRINKLR_COOKIE` | Default cookie for customer-order-details | empty |
| `REQUEST_TIMEOUT` | HTTP timeout seconds | `30` |

## How Maya uses the response

After calling `/full-tracking-by-phone`:

1. From `customer_orders.result.orders[]`, list the recent orders to the customer
   (order ID + item name from `items[0].name_en`).
2. When the customer picks an order, find its tracking in
   `fusion_tracking.data[]` by matching `orderNumber`.
3. Scan `trackingOrdersEvents[]` for any event with `lkStatusCode = "800"`.
4. If found → "Delivery attempt failed, we'll re-attempt within 48 hours."
   If not found → "Delivery is on track, current status: ..."

## Notes

- The fusion-tracking endpoint is behind Cloudflare. Cookies typically expire
  in ~30 minutes. For production, set up an UptimeRobot ping or get a proper
  server-to-server API key from the Lenskart backend team.
- Free Render tier spins down after 15 min of inactivity → first request takes
  30-60 seconds to wake up. Set an UptimeRobot ping on `/health` every 5 min to
  keep it warm, or upgrade to Starter ($7/mo).
