"""
Lenskart Order + Tracking Wrapper — v3 (clean)
==============================================

Three endpoints, all thin pass-throughs of the upstream APIs:

  GET  /orders-by-phone?id=<phone>
       → Calls customer-order-details. Returns the original Sprinklr response
         + a small `order_ids` array extracted for convenience.

  POST /tracking-by-order-ids
       Body: ["1339167449", "1338720250"]
       → Calls fusion-tracking with identifierType="order_no". Returns the
         original fusion-tracking response untouched.

  GET  /full-tracking-by-phone?id=<phone>
       → Chains the above two: phone → order IDs → fusion-tracking.
         Returns both upstream responses side-by-side. Nothing invented.

The agent (Maya) reads `lkStatusCode` directly from the fusion-tracking events
to decide whether a delivery attempt failed (code "800"). No boolean fields
are added by this wrapper.
"""

import os
from typing import List, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


CUSTOMER_ORDERS_URL = os.getenv(
    "CUSTOMER_ORDERS_URL",
    "https://crm.scm.lenskart.com/sprinklr/order/customer-order-details",
)
FUSION_TRACKING_URL = os.getenv(
    "FUSION_TRACKING_URL",
    "https://tracking-middleware-ui-api.ces.lenskart.com/fusion-tracking",
)
SPRINKLR_AUTH = os.getenv(
    "SPRINKLR_AUTH",
    "sprinklr-lk-test-demo-23063cd492219ae07dbd2c9e335dbd3fcb2f6549d07ee8b90c2c97f40b4d36a6",
)
FUSION_COOKIE_DEFAULT = os.getenv("FUSION_COOKIE", "")
SPRINKLR_COOKIE_DEFAULT = os.getenv("SPRINKLR_COOKIE", "")
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "30"))


app = FastAPI(
    title="Lenskart Order + Tracking Wrapper",
    version="3.0.0",
    description=(
        "Thin pass-through wrapper. Phone → orders. Order IDs → tracking. "
        "Or both chained in one call. No boolean flags invented — the agent "
        "reads lkStatusCode directly from the fusion-tracking response."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def call_customer_orders(client: httpx.AsyncClient, phone: str, cookie: str = ""):
    headers = {"Authorization": SPRINKLR_AUTH}
    if cookie:
        headers["Cookie"] = cookie
    resp = await client.get(CUSTOMER_ORDERS_URL, params={"id": phone}, headers=headers)
    resp.raise_for_status()
    return resp.json()


async def call_fusion_tracking(client: httpx.AsyncClient, order_ids: List[str], cookie: str = ""):
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = cookie
    resp = await client.post(
        FUSION_TRACKING_URL,
        json={"identifierType": "order_no", "identifierValues": order_ids},
        headers=headers,
    )
    resp.raise_for_status()
    return resp.json()


@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


@app.get("/orders-by-phone")
async def orders_by_phone(
    id: str = Query(..., description="Customer 10-digit phone number"),
    x_sprinklr_cookie: Optional[str] = Header(default=None),
):
    """
    Returns the customer-order-details response as-is, plus a flat `order_ids`
    array for convenience.
    """
    cookie = x_sprinklr_cookie or SPRINKLR_COOKIE_DEFAULT
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            payload = await call_customer_orders(client, id, cookie)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"customer-order-details returned {exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"customer-order-details error: {exc}")

    orders = ((payload.get("result") or {}).get("orders")) or []
    order_ids = [str(o.get("id")) for o in orders if o.get("id")]

    return {
        "order_ids": order_ids,
        "customer_orders": payload,  # original Sprinklr response, untouched
    }


@app.post("/tracking-by-order-ids")
async def tracking_by_order_ids(
    order_ids: List[str],
    x_fusion_cookie: Optional[str] = Header(default=None),
):
    """
    Body: a JSON array of order IDs, e.g. ["1339167449", "1338720250"]
    Returns the fusion-tracking response as-is.
    """
    if not order_ids:
        raise HTTPException(status_code=400, detail="order_ids list cannot be empty")

    cookie = x_fusion_cookie or FUSION_COOKIE_DEFAULT
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            payload = await call_fusion_tracking(client, order_ids, cookie)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"fusion-tracking returned {exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"fusion-tracking error: {exc}")

    return payload  # original response, untouched


@app.get("/full-tracking-by-phone")
async def full_tracking_by_phone(
    id: str = Query(..., description="Customer 10-digit phone number"),
    max_orders: int = Query(5, ge=1, le=20),
    x_sprinklr_cookie: Optional[str] = Header(default=None),
    x_fusion_cookie: Optional[str] = Header(default=None),
):
    """
    Chains both APIs in one call:
      1. Fetch customer-order-details for the phone.
      2. Extract up to `max_orders` order IDs.
      3. Call fusion-tracking with those IDs.
      4. Return both responses side-by-side.

    Returns:
      {
        "order_ids": ["1339167449", ...],
        "customer_orders": { ...original Sprinklr response... },
        "fusion_tracking": { ...original fusion-tracking response... } | null,
        "fusion_tracking_error": null | "<error message>"
      }
    """
    sprinklr_cookie = x_sprinklr_cookie or SPRINKLR_COOKIE_DEFAULT
    fusion_cookie = x_fusion_cookie or FUSION_COOKIE_DEFAULT

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        # 1. customer orders
        try:
            customer_payload = await call_customer_orders(client, id, sprinklr_cookie)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"customer-order-details returned {exc.response.status_code}",
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"customer-order-details error: {exc}")

        orders = ((customer_payload.get("result") or {}).get("orders")) or []
        order_ids = [str(o.get("id")) for o in orders[:max_orders] if o.get("id")]

        # 2. fusion tracking (best-effort — don't fail the whole call if it errors)
        fusion_payload = None
        fusion_error = None
        if order_ids:
            try:
                fusion_payload = await call_fusion_tracking(client, order_ids, fusion_cookie)
            except httpx.HTTPStatusError as exc:
                fusion_error = (
                    f"fusion-tracking returned {exc.response.status_code}: "
                    f"{exc.response.text[:200]}"
                )
            except httpx.HTTPError as exc:
                fusion_error = f"fusion-tracking error: {exc}"

    return {
        "order_ids": order_ids,
        "customer_orders": customer_payload,
        "fusion_tracking": fusion_payload,
        "fusion_tracking_error": fusion_error,
    }
