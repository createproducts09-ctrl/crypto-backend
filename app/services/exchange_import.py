"""Import holdings from exchanges / wallets into a thesis basket."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlencode

import httpx

from app.extensions import db

logger = logging.getLogger(__name__)

# Fiat / non-crypto ledger codes we never treat as holdings
SKIP_ASSETS = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "AUD",
    "CAD",
    "CHF",
    "SGD",
    "HKD",
    "TRY",
    "BRL",
    "INR",
    "KRW",
    "ZAR",
    "MXN",
    "NGN",
    "AED",
    "RUB",
    "CNY",
    "CNH",
    "ZEUR",
    "ZGBP",
    "ZJPY",
    "ZAUD",
    "ZCAD",
    "CHF.HOLD",
}

_KEY_SECRET = [
    {"key": "api_key", "label": "API key", "secret": False},
    {"key": "api_secret", "label": "API secret", "secret": True},
]
_KEY_SECRET_PASS = [
    {"key": "api_key", "label": "API key", "secret": False},
    {"key": "api_secret", "label": "Secret key", "secret": True},
    {"key": "passphrase", "label": "Passphrase", "secret": True},
]


def _cex(
    pid: str,
    name: str,
    *,
    blurb: str,
    hint: str,
    fields: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": pid,
        "name": name,
        "kind": "cex",
        "status": "live",
        "blurb": blurb,
        "fields": fields or list(_KEY_SECRET),
        "hint": hint,
    }


PLATFORMS: list[dict[str, Any]] = [
    _cex(
        "binance",
        "Binance",
        blurb="Import spot balances with a read-only API key.",
        hint="Binance → API Management. Read-only, disable withdrawals.",
    ),
    _cex(
        "okx",
        "OKX",
        blurb="Import funding / trading balances with read-only API credentials.",
        hint="OKX → API → Create V5 key with Read permission only.",
        fields=_KEY_SECRET_PASS,
    ),
    _cex(
        "bybit",
        "Bybit",
        blurb="Import unified account spot balances.",
        hint="Bybit → API → Create key → Read-Only only.",
    ),
    _cex(
        "coinbase",
        "Coinbase Exchange",
        blurb="Import Coinbase Exchange balances with a read-only API key.",
        hint="Coinbase Exchange → API → New key with View permission only.",
        fields=_KEY_SECRET_PASS,
    ),
    _cex(
        "kraken",
        "Kraken",
        blurb="Import spot balances with a Query funds key.",
        hint="Kraken → Security → API → Query funds only.",
        fields=[
            {"key": "api_key", "label": "API key", "secret": False},
            {"key": "api_secret", "label": "Private key", "secret": True},
        ],
    ),
    _cex(
        "kucoin",
        "KuCoin",
        blurb="Import trade / main account balances.",
        hint="KuCoin → API Management → General / Spot read.",
        fields=_KEY_SECRET_PASS,
    ),
    _cex(
        "gate",
        "Gate.io",
        blurb="Import spot balances with a read-only API key.",
        hint="Gate.io → API Keys → Spot read only.",
    ),
    _cex(
        "bitget",
        "Bitget",
        blurb="Import spot account assets.",
        hint="Bitget → API → Create key with Read permission + passphrase.",
        fields=_KEY_SECRET_PASS,
    ),
    _cex(
        "mexc",
        "MEXC",
        blurb="Import spot balances with a read-only API key.",
        hint="MEXC → API Management → Read-only, no withdrawals.",
    ),
    _cex(
        "htx",
        "HTX",
        blurb="Import spot balances (formerly Huobi).",
        hint="HTX → API Management → Read-only key.",
    ),
    _cex(
        "bitfinex",
        "Bitfinex",
        blurb="Import wallet balances with a read-only key.",
        hint="Bitfinex → API → Create key with account read only.",
    ),
    _cex(
        "gemini",
        "Gemini",
        blurb="Import account balances.",
        hint="Gemini → Settings → API → Auditor / read-only.",
    ),
    _cex(
        "cryptocom",
        "Crypto.com",
        blurb="Import Exchange wallet balances.",
        hint="Crypto.com Exchange → API → Create key with balance read.",
    ),
    _cex(
        "bingx",
        "BingX",
        blurb="Import spot account balances.",
        hint="BingX → API Management → Read permission only.",
    ),
    _cex(
        "bitstamp",
        "Bitstamp",
        blurb="Import account balances.",
        hint="Bitstamp → Settings → API Access → Read only.",
    ),
    {
        "id": "watchlist",
        "name": "Alphora watchlist",
        "kind": "internal",
        "status": "live",
        "blurb": "Seed a thesis from coins you already watch.",
        "fields": [],
        "hint": "Quantities start at 0 — set buy size after import.",
    },
    {
        "id": "manual",
        "name": "Start empty",
        "kind": "manual",
        "status": "live",
        "blurb": "Create a blank thesis and add coins yourself.",
        "fields": [],
        "hint": "Best when you want full control over the basket.",
    },
]


def list_platforms() -> list[dict[str, Any]]:
    return PLATFORMS


def _lookup_symbol(sym: str) -> str | None:
    docs = list(
        db.coins.find({"symbol": sym}, {"id": 1, "market_cap_rank": 1})
        .sort("market_cap_rank", 1)
        .limit(5)
    )
    if not docs:
        docs = list(
            db.coins.find(
                {"symbol": sym.lower()},
                {"id": 1, "market_cap_rank": 1},
            )
            .sort("market_cap_rank", 1)
            .limit(5)
        )
    if not docs:
        return None
    docs.sort(key=lambda d: d.get("market_cap_rank") or 99999)
    return docs[0].get("id")


def _resolve_symbol(symbol: str) -> str | None:
    sym = (symbol or "").strip().upper().replace(" ", "")
    if not sym or sym in SKIP_ASSETS:
        return None
    # Drop trailing .S / .M staking suffixes (Kraken etc.)
    for suffix in (".S", ".M", ".HOLD", ".F"):
        if sym.endswith(suffix) and len(sym) > len(suffix) + 1:
            nested = _resolve_symbol(sym[: -len(suffix)])
            if nested:
                return nested

    aliases = {
        "WBTC": "bitcoin",
        "WETH": "ethereum",
        "BETH": "ethereum",
        "STETH": "staked-ether",
        "WSTETH": "wrapped-steth",
        "BTCB": "bitcoin",
        "XXBT": "bitcoin",
        "XBT": "bitcoin",
        "XETH": "ethereum",
        "ZUSD": "usd-coin",
        "USDTZ": "tether",
        "USDC": "usd-coin",
        "USDT": "tether",
        "BUSD": "binance-usd",
        "FDUSD": "first-digital-usd",
        "TUSD": "true-usd",
        "DAI": "dai",
        "MATIC": "matic-network",
        "POL": "polygon-ecosystem-token",
        "BNB": "binancecoin",
        "AVAX": "avalanche-2",
        "SOL": "solana",
        "DOT": "polkadot",
        "LINK": "chainlink",
        "ARB": "arbitrum",
        "OP": "optimism",
        "ATOM": "cosmos",
        "NEAR": "near",
        "APT": "aptos",
        "SUI": "sui",
        "TON": "the-open-network",
        "TRX": "tron",
        "DOGE": "dogecoin",
        "SHIB": "shiba-inu",
        "PEPE": "pepe",
        "WLD": "worldcoin-wld",
        "RENDER": "render-token",
        "FET": "fetch-ai",
    }
    if sym in aliases:
        return aliases[sym]

    hit = _lookup_symbol(sym)
    if hit:
        return hit

    # Kraken style XETH / XXBT
    if len(sym) == 4 and sym.startswith(("X", "Z")):
        hit = _resolve_symbol(sym[1:])
        if hit:
            return hit
    if sym.startswith("XX") and len(sym) >= 4:
        hit = _resolve_symbol(sym[1:])
        if hit:
            return hit

    # Binance Flexible Earn etc. LDUSDT → USDT
    if sym.startswith("LD") and len(sym) > 3:
        hit = _resolve_symbol(sym[2:])
        if hit:
            return hit

    return None


def _balance_row(symbol: str, amount: float, avg_price: float = 0.0) -> dict[str, Any]:
    asset = (symbol or "").strip().upper()
    return {
        "coin_id": _resolve_symbol(asset),
        "symbol": asset,
        "amount": float(amount or 0),
        "avg_price": float(avg_price or 0),
    }


def _binance_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    if not api_key or not api_secret:
        raise ValueError("Binance API key and secret are required")

    params = {"timestamp": int(time.time() * 1000), "recvWindow": 10000}
    query = urlencode(params)
    sig = hmac.new(
        api_secret.encode("utf-8"),
        query.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    url = f"https://api.binance.com/api/v3/account?{query}&signature={sig}"
    with httpx.Client(timeout=20.0) as client:
        res = client.get(url, headers={"X-MBX-APIKEY": api_key})
    if res.status_code == 401 or res.status_code == 403:
        raise ValueError("Binance rejected the API key — use a read-only key")
    if res.status_code >= 400:
        detail = res.json() if res.headers.get("content-type", "").startswith("application/json") else {}
        msg = detail.get("msg") if isinstance(detail, dict) else None
        raise ValueError(msg or f"Binance error ({res.status_code})")
    data = res.json()
    out: list[dict[str, Any]] = []
    for bal in data.get("balances") or []:
        free = float(bal.get("free") or 0)
        locked = float(bal.get("locked") or 0)
        amount = free + locked
        if amount <= 0:
            continue
        asset = str(bal.get("asset") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _okx_balances(api_key: str, api_secret: str, passphrase: str) -> list[dict[str, Any]]:
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    passphrase = passphrase.strip()
    if not api_key or not api_secret or not passphrase:
        raise ValueError("OKX API key, secret, and passphrase are required")

    import base64
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    path = "/api/v5/account/balance"
    prehash = f"{ts}GET{path}"
    sign = base64.b64encode(
        hmac.new(api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.get(f"https://www.okx.com{path}", headers=headers)
    if res.status_code >= 400:
        raise ValueError("OKX rejected the credentials — check key permissions")
    payload = res.json()
    if str(payload.get("code")) not in {"0", "0.0"}:
        raise ValueError(payload.get("msg") or "OKX balance fetch failed")
    out: list[dict[str, Any]] = []
    for acct in payload.get("data") or []:
        for det in acct.get("details") or []:
            amount = float(det.get("eq") or det.get("availBal") or 0)
            if amount <= 0:
                continue
            asset = str(det.get("ccy") or "").upper()
            out.append(_balance_row(asset, amount))
    return out


def _bybit_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    if not api_key or not api_secret:
        raise ValueError("Bybit API key and secret are required")

    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "accountType=UNIFIED"
    payload = f"{ts}{api_key}{recv}{query}"
    sign = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.get(
            f"https://api.bybit.com/v5/account/wallet-balance?{query}",
            headers=headers,
        )
    data = res.json()
    if str(data.get("retCode")) not in {"0", "0.0"}:
        raise ValueError(data.get("retMsg") or "Bybit balance fetch failed")
    out: list[dict[str, Any]] = []
    for acct in ((data.get("result") or {}).get("list") or []):
        for coin in acct.get("coin") or []:
            amount = float(coin.get("walletBalance") or 0)
            if amount <= 0:
                continue
            asset = str(coin.get("coin") or "").upper()
            out.append(_balance_row(asset, amount))
    return out


def _coinbase_balances(api_key: str, api_secret: str, passphrase: str) -> list[dict[str, Any]]:
    """Coinbase Exchange (pro) accounts endpoint."""
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    passphrase = passphrase.strip()
    if not api_key or not api_secret or not passphrase:
        raise ValueError("Coinbase API key, secret, and passphrase are required")

    ts = str(time.time())
    method = "GET"
    path = "/accounts"
    message = f"{ts}{method}{path}".encode()
    try:
        secret = base64.b64decode(api_secret)
    except Exception as exc:
        raise ValueError("Coinbase API secret must be base64-encoded") from exc
    sign = base64.b64encode(hmac.new(secret, message, hashlib.sha256).digest()).decode()
    headers = {
        "CB-ACCESS-KEY": api_key,
        "CB-ACCESS-SIGN": sign,
        "CB-ACCESS-TIMESTAMP": ts,
        "CB-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.get("https://api.exchange.coinbase.com/accounts", headers=headers)
    if res.status_code >= 400:
        raise ValueError("Coinbase rejected the credentials — use an Exchange API key with view permission")
    data = res.json()
    if not isinstance(data, list):
        raise ValueError("Unexpected Coinbase response")
    out: list[dict[str, Any]] = []
    for acct in data:
        amount = float(acct.get("balance") or acct.get("available") or 0)
        if amount <= 0:
            continue
        asset = str(acct.get("currency") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _kraken_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    if not api_key or not api_secret:
        raise ValueError("Kraken API key and private key are required")

    path = "/0/private/Balance"
    nonce = str(int(time.time() * 1000))
    postdata = urlencode({"nonce": nonce})
    encoded = (nonce + postdata).encode()
    message = path.encode() + hashlib.sha256(encoded).digest()
    try:
        secret = base64.b64decode(api_secret)
    except Exception as exc:
        raise ValueError("Kraken private key must be base64-encoded") from exc
    signature = base64.b64encode(
        hmac.new(secret, message, hashlib.sha512).digest()
    ).decode()
    headers = {"API-Key": api_key, "API-Sign": signature}
    with httpx.Client(timeout=20.0) as client:
        res = client.post(
            f"https://api.kraken.com{path}",
            content=postdata,
            headers={**headers, "Content-Type": "application/x-www-form-urlencoded"},
        )
    data = res.json()
    if data.get("error"):
        raise ValueError("; ".join(data["error"]) or "Kraken balance fetch failed")
    out: list[dict[str, Any]] = []
    for asset, bal in (data.get("result") or {}).items():
        amount = float(bal or 0)
        if amount <= 0:
            continue
        out.append(_balance_row(str(asset).upper(), amount))
    return out


def _kucoin_balances(api_key: str, api_secret: str, passphrase: str) -> list[dict[str, Any]]:
    api_key = api_key.strip()
    api_secret = api_secret.strip()
    passphrase = passphrase.strip()
    if not api_key or not api_secret or not passphrase:
        raise ValueError("KuCoin API key, secret, and passphrase are required")

    ts = str(int(time.time() * 1000))
    method = "GET"
    path = "/api/v1/accounts"
    str_to_sign = f"{ts}{method}{path}"
    sign = base64.b64encode(
        hmac.new(api_secret.encode(), str_to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    # KuCoin v2 passphrase encryption
    passphrase_sign = base64.b64encode(
        hmac.new(api_secret.encode(), passphrase.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "KC-API-KEY": api_key,
        "KC-API-SIGN": sign,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": passphrase_sign,
        "KC-API-KEY-VERSION": "2",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.get(f"https://api.kucoin.com{path}", headers=headers)
    data = res.json()
    if data.get("code") not in {"200000", 200000, "200"}:
        raise ValueError(data.get("msg") or "KuCoin balance fetch failed")
    out: list[dict[str, Any]] = []
    for acct in data.get("data") or []:
        amount = float(acct.get("balance") or acct.get("available") or 0)
        if amount <= 0:
            continue
        asset = str(acct.get("currency") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _gate_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("Gate.io API key and secret are required")
    method = "GET"
    path = "/api/v4/spot/accounts"
    query = ""
    body = ""
    ts = str(time.time())
    payload = f"{method}\n{path}\n{query}\n{hashlib.sha512(body.encode()).hexdigest()}\n{ts}"
    sign = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha512).hexdigest()
    headers = {
        "KEY": api_key,
        "Timestamp": ts,
        "SIGN": sign,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.get(f"https://api.gateio.ws{path}", headers=headers)
    try:
        data = res.json()
    except Exception as exc:
        raise ValueError("Gate.io balance fetch failed") from exc
    if res.status_code >= 400 or (isinstance(data, dict) and data.get("label")):
        raise ValueError(
            (data.get("message") if isinstance(data, dict) else None)
            or "Gate.io balance fetch failed"
        )
    out: list[dict[str, Any]] = []
    for row in data if isinstance(data, list) else []:
        amount = float(row.get("available") or 0) + float(row.get("locked") or 0)
        if amount <= 0:
            continue
        asset = str(row.get("currency") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _bitget_balances(api_key: str, api_secret: str, passphrase: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret or not passphrase:
        raise ValueError("Bitget API key, secret, and passphrase are required")
    ts = str(int(time.time() * 1000))
    method = "GET"
    path = "/api/v2/spot/account/assets"
    prehash = f"{ts}{method}{path}"
    sign = base64.b64encode(
        hmac.new(api_secret.encode(), prehash.encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": sign,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/json",
        "locale": "en-US",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.get(f"https://api.bitget.com{path}", headers=headers)
    data = res.json()
    if str(data.get("code")) not in {"00000", "0"}:
        raise ValueError(data.get("msg") or "Bitget balance fetch failed")
    out: list[dict[str, Any]] = []
    for row in data.get("data") or []:
        amount = float(row.get("available") or 0) + float(row.get("frozen") or 0) + float(
            row.get("locked") or 0
        )
        if amount <= 0:
            continue
        asset = str(row.get("coin") or row.get("coinName") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _mexc_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("MEXC API key and secret are required")
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    query = urlencode(params)
    sign = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    headers = {"X-MEXC-APIKEY": api_key}
    with httpx.Client(timeout=20.0) as client:
        res = client.get(
            f"https://api.mexc.com/api/v3/account?{query}&signature={sign}",
            headers=headers,
        )
    data = res.json()
    if data.get("code") and data.get("code") != 200:
        raise ValueError(data.get("msg") or "MEXC balance fetch failed")
    if "balances" not in data:
        raise ValueError(data.get("msg") or "MEXC balance fetch failed")
    out: list[dict[str, Any]] = []
    for bal in data.get("balances") or []:
        amount = float(bal.get("free") or 0) + float(bal.get("locked") or 0)
        if amount <= 0:
            continue
        asset = str(bal.get("asset") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _htx_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("HTX API key and secret are required")

    def _sign(method: str, host: str, path: str, params: dict[str, str]) -> str:
        qs = urlencode(sorted(params.items()))
        payload = f"{method}\n{host}\n{path}\n{qs}"
        return base64.b64encode(
            hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).digest()
        ).decode()

    host = "api.huobi.pro"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    base_params = {
        "AccessKeyId": api_key,
        "SignatureMethod": "HmacSHA256",
        "SignatureVersion": "2",
        "Timestamp": ts,
    }
    path_accounts = "/v1/account/accounts"
    params_a = dict(base_params)
    params_a["Signature"] = _sign("GET", host, path_accounts, params_a)
    with httpx.Client(timeout=20.0) as client:
        res = client.get(f"https://{host}{path_accounts}", params=params_a)
    data = res.json()
    if data.get("status") != "ok":
        raise ValueError(data.get("err-msg") or "HTX account list failed")
    spot = next(
        (a for a in (data.get("data") or []) if a.get("type") == "spot" and a.get("state") == "working"),
        None,
    )
    if not spot:
        raise ValueError("No working HTX spot account found")
    acct_id = spot["id"]
    path_bal = f"/v1/account/accounts/{acct_id}/balance"
    params_b = dict(base_params)
    params_b["Timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    params_b["Signature"] = _sign("GET", host, path_bal, params_b)
    with httpx.Client(timeout=20.0) as client:
        res = client.get(f"https://{host}{path_bal}", params=params_b)
    data = res.json()
    if data.get("status") != "ok":
        raise ValueError(data.get("err-msg") or "HTX balance fetch failed")
    out: list[dict[str, Any]] = []
    for row in (data.get("data") or {}).get("list") or []:
        if row.get("type") != "trade":
            continue
        amount = float(row.get("balance") or 0)
        if amount <= 0:
            continue
        asset = str(row.get("currency") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _bitfinex_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("Bitfinex API key and secret are required")
    path = "/v2/auth/r/wallets"
    nonce = str(int(time.time() * 1_000_000))
    body = "{}"
    signature_payload = f"/api{path}{nonce}{body}"
    sign = hmac.new(api_secret.encode(), signature_payload.encode(), hashlib.sha384).hexdigest()
    headers = {
        "bfx-nonce": nonce,
        "bfx-apikey": api_key,
        "bfx-signature": sign,
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.post(f"https://api.bitfinex.com{path}", headers=headers, content=body)
    data = res.json()
    if isinstance(data, dict) and data.get("error"):
        raise ValueError(str(data.get("error") or data)[:200])
    if not isinstance(data, list):
        raise ValueError("Bitfinex balance fetch failed")
    out: list[dict[str, Any]] = []
    for row in data:
        # [WALLET_TYPE, CURRENCY, BALANCE, UNSETTLED_INTEREST, BALANCE_AVAILABLE, ...]
        if not isinstance(row, list) or len(row) < 3:
            continue
        amount = float(row[2] or 0)
        if amount <= 0:
            continue
        asset = str(row[1] or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _gemini_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("Gemini API key and secret are required")
    path = "/v1/balances"
    nonce = int(time.time() * 1000)
    payload_obj = {"request": path, "nonce": nonce}
    b64 = base64.b64encode(json.dumps(payload_obj).encode()).decode()
    sign = hmac.new(api_secret.encode(), b64.encode(), hashlib.sha384).hexdigest()
    headers = {
        "Content-Type": "text/plain",
        "Content-Length": "0",
        "X-GEMINI-APIKEY": api_key,
        "X-GEMINI-PAYLOAD": b64,
        "X-GEMINI-SIGNATURE": sign,
        "Cache-Control": "no-cache",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.post(f"https://api.gemini.com{path}", headers=headers)
    data = res.json()
    if isinstance(data, dict) and data.get("result") == "error":
        raise ValueError(data.get("reason") or data.get("message") or "Gemini balance fetch failed")
    if not isinstance(data, list):
        raise ValueError("Gemini balance fetch failed")
    out: list[dict[str, Any]] = []
    for row in data:
        amount = float(row.get("amount") or row.get("available") or 0)
        if amount <= 0:
            continue
        asset = str(row.get("currency") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _cryptocom_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("Crypto.com API key and secret are required")
    nonce = int(time.time() * 1000)
    method = "private/user-balance"
    params: dict[str, Any] = {}
    param_str = "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    # method + id + api_key + params + nonce
    payload = f"{method}{nonce}{api_key}{param_str}{nonce}"
    sign = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    body = {
        "id": nonce,
        "method": method,
        "api_key": api_key,
        "params": params,
        "nonce": nonce,
        "sig": sign,
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.post(
            f"https://api.crypto.com/exchange/v1/{method}",
            headers={"Content-Type": "application/json"},
            content=json.dumps(body),
        )
    data = res.json()
    if data.get("code") != 0:
        raise ValueError(data.get("message") or "Crypto.com balance fetch failed")
    result = data.get("result") or {}
    out: list[dict[str, Any]] = []
    for row in result.get("data") or []:
        for bal in row.get("position_balances") or []:
            amount = float(bal.get("quantity") or 0)
            if amount <= 0:
                continue
            asset = str(bal.get("instrument_name") or "").upper()
            out.append(_balance_row(asset, amount))
    return out


def _bingx_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("BingX API key and secret are required")
    ts = int(time.time() * 1000)
    params = {"timestamp": ts}
    query = urlencode(params)
    sign = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BX-APIKEY": api_key}
    with httpx.Client(timeout=20.0) as client:
        res = client.get(
            f"https://open-api.bingx.com/openApi/spot/v1/account/balance?{query}&signature={sign}",
            headers=headers,
        )
    data = res.json()
    if str(data.get("code")) not in {"0", "00"}:
        raise ValueError(data.get("msg") or "BingX balance fetch failed")
    balances = (data.get("data") or {}).get("balances") or data.get("data") or []
    out: list[dict[str, Any]] = []
    for bal in balances if isinstance(balances, list) else []:
        amount = float(bal.get("free") or 0) + float(bal.get("locked") or 0)
        if amount <= 0:
            continue
        asset = str(bal.get("asset") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def _bitstamp_balances(api_key: str, api_secret: str) -> list[dict[str, Any]]:
    if not api_key or not api_secret:
        raise ValueError("Bitstamp API key and secret are required")
    ts = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4())
    content_type = ""
    payload = ""
    # Bitstamp v2 auth: KEY + METHOD + HOST + PATH + CONTENT_TYPE + NONCE + TIMESTAMP + VERSION + BODY
    message = (
        f"BITSTAMP {api_key}"
        f"POST"
        f"www.bitstamp.net"
        f"/api/v2/account_balances/"
        f"{content_type}"
        f"{nonce}"
        f"{ts}"
        f"v2"
        f"{payload}"
    )
    sign = hmac.new(api_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-Auth": f"BITSTAMP {api_key}",
        "X-Auth-Signature": sign,
        "X-Auth-Nonce": nonce,
        "X-Auth-Timestamp": ts,
        "X-Auth-Version": "v2",
    }
    with httpx.Client(timeout=20.0) as client:
        res = client.post("https://www.bitstamp.net/api/v2/account_balances/", headers=headers)
    data = res.json()
    if isinstance(data, dict) and data.get("status") == "error":
        raise ValueError(str(data.get("reason") or data)[:200])
    if not isinstance(data, list):
        raise ValueError("Bitstamp balance fetch failed")
    out: list[dict[str, Any]] = []
    for row in data:
        amount = float(row.get("total") or row.get("available") or 0)
        if amount <= 0:
            continue
        asset = str(row.get("currency") or "").upper()
        out.append(_balance_row(asset, amount))
    return out


def fetch_holdings(platform: str, credentials: dict[str, str] | None = None) -> list[dict[str, Any]]:
    platform = (platform or "").strip().lower()
    creds = credentials or {}
    key = creds.get("api_key", "")
    secret = creds.get("api_secret", "")
    passphrase = creds.get("passphrase", "")
    dispatch = {
        "binance": lambda: _binance_balances(key, secret),
        "okx": lambda: _okx_balances(key, secret, passphrase),
        "bybit": lambda: _bybit_balances(key, secret),
        "coinbase": lambda: _coinbase_balances(key, secret, passphrase),
        "kraken": lambda: _kraken_balances(key, secret),
        "kucoin": lambda: _kucoin_balances(key, secret, passphrase),
        "gate": lambda: _gate_balances(key, secret),
        "bitget": lambda: _bitget_balances(key, secret, passphrase),
        "mexc": lambda: _mexc_balances(key, secret),
        "htx": lambda: _htx_balances(key, secret),
        "bitfinex": lambda: _bitfinex_balances(key, secret),
        "gemini": lambda: _gemini_balances(key, secret),
        "cryptocom": lambda: _cryptocom_balances(key, secret),
        "bingx": lambda: _bingx_balances(key, secret),
        "bitstamp": lambda: _bitstamp_balances(key, secret),
        "watchlist": lambda: [],
        "manual": lambda: [],
    }
    fn = dispatch.get(platform)
    if not fn:
        raise ValueError("Unknown platform")
    return fn()


def import_thesis(
    user_id: str,
    *,
    platform: str,
    name: str,
    note: str = "",
    credentials: dict[str, str] | None = None,
) -> dict[str, Any]:
    from app.services import billing_service, portfolio_service

    platform = (platform or "").strip().lower()
    meta = next((p for p in PLATFORMS if p["id"] == platform), None)
    if not meta:
        raise ValueError("Unknown platform")
    if meta.get("status") == "soon":
        raise ValueError(f"{meta['name']} import is coming soon")

    billing_service.assert_can_create_basket(user_id)

    clean_name = (name or "").strip()[:60] or f"{meta['name']} thesis"
    clean_note = (note or "").strip()[:200]
    if not clean_note and platform not in {"manual", "watchlist"}:
        clean_note = f"Imported from {meta['name']}"

    holdings: list[dict[str, Any]] = []
    if platform == "watchlist":
        basket = portfolio_service.create_basket(
            user_id,
            name=clean_name,
            import_watchlist=True,
            note=clean_note or "Seeded from watchlist",
        )
        return {
            "basket": basket,
            "imported": len(basket.get("assets") or []),
            "unmapped": 0,
            "platform": platform,
        }

    if platform == "manual":
        basket = portfolio_service.create_basket(
            user_id,
            name=clean_name,
            note=clean_note,
        )
        return {"basket": basket, "imported": 0, "unmapped": 0, "platform": platform}

    raw = fetch_holdings(platform, credentials)
    mapped: dict[str, dict[str, Any]] = {}
    unmapped_merge: dict[str, dict[str, Any]] = {}
    for row in raw:
        amount = float(row.get("amount") or 0)
        if amount <= 0:
            continue
        symbol = str(row.get("symbol") or "").upper()
        cid = row.get("coin_id")
        if cid:
            if cid in mapped:
                mapped[cid]["amount"] += amount
            else:
                mapped[cid] = {
                    "coin_id": cid,
                    "symbol": symbol,
                    "amount": amount,
                    "avg_price": float(row.get("avg_price") or 0),
                }
        elif symbol and symbol not in SKIP_ASSETS:
            if symbol in unmapped_merge:
                unmapped_merge[symbol]["amount"] += amount
            else:
                unmapped_merge[symbol] = {
                    "symbol": symbol,
                    "amount": amount,
                    "avg_price": float(row.get("avg_price") or 0),
                }

    holdings = list(mapped.values())
    unmapped_assets = list(unmapped_merge.values())
    if not holdings and not unmapped_assets:
        raise ValueError(
            f"No balances found on {meta['name']}. "
            "Check the key has spot/wallet read access (not just trade), "
            "IP whitelist allows this server, and funds are in a spot account — "
            "or add coins manually."
        )

    coin_ids = [h["coin_id"] for h in holdings]
    basket = portfolio_service.create_basket(
        user_id,
        name=clean_name,
        coin_ids=coin_ids,
        note=clean_note,
        unmapped_assets=unmapped_assets,
        import_platform=platform,
    )
    basket_id = basket["id"]
    for h in holdings:
        try:
            portfolio_service.set_holding(
                user_id,
                basket_id,
                h["coin_id"],
                amount=float(h["amount"]),
                avg_price=float(h.get("avg_price") or 0),
            )
        except Exception as exc:
            logger.warning("set_holding failed for %s: %s", h.get("coin_id"), exc)

    basket = portfolio_service.get_basket(user_id, basket_id) or basket
    return {
        "basket": basket,
        "imported": len(holdings),
        "unmapped": len(unmapped_assets),
        "unmapped_assets": unmapped_assets,
        "platform": platform,
    }
