"""Exchange account management routes."""
import asyncio
import json
import logging
from collections import defaultdict
from functools import partial
from typing import Any, Dict, List, Optional

import ccxt

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db_session
from api.db.crud import AccountCRUD
from api.db.models import ExchangeAccount
from shared.core.redis_client import get_redis_client
from shared.utils.crypto import decrypt_api_secret, encrypt_api_secret

router = APIRouter()
logger = logging.getLogger(__name__)

SYMBOLS_CACHE_TTL_SECONDS = 600


class AccountCreate(BaseModel):
    exchange: str
    label: str
    api_key: str
    api_secret: str
    testnet: bool = False


class AccountUpdate(BaseModel):
    label: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    testnet: Optional[bool] = None
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    id: int
    exchange: str
    label: str
    api_key: str
    testnet: bool
    is_active: bool
    created_at: str

    model_config = {"from_attributes": True}


class TradingFeeResponse(BaseModel):
    symbol: str
    maker: float
    taker: float


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return api_key[:4] + "*" * (len(api_key) - 8) + api_key[-4:]


def account_to_response(account: ExchangeAccount) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        exchange=account.exchange,
        label=account.label,
        api_key=mask_api_key(account.api_key),
        testnet=account.testnet,
        is_active=account.is_active,
        created_at=account.created_at.isoformat(),
    )


def _get_symbols_cache_key(exchange: str, testnet: bool) -> str:
    normalized_exchange = exchange.lower().strip()
    return f"symbols:{normalized_exchange}:{str(bool(testnet)).lower()}"


def _create_ccxt_exchange(
    exchange: str,
    testnet: bool,
    api_key: Optional[str] = None,
    api_secret: Optional[str] = None,
) -> Any:
    exchange_class = getattr(ccxt, exchange.lower().strip(), None)
    if exchange_class is None:
        raise ValueError(f"Unsupported exchange: {exchange}")

    config: Dict[str, Any] = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
        },
    }
    if api_key:
        config["apiKey"] = api_key
    if api_secret:
        config["secret"] = api_secret

    exchange_client = exchange_class(config)

    if testnet:
        if hasattr(exchange_client, "set_sandbox_mode"):
            exchange_client.set_sandbox_mode(True)
        elif hasattr(exchange_client, "enable_demo_trading"):
            exchange_client.enable_demo_trading(True)

    return exchange_client


def _sort_symbols_by_quote(markets: Dict[str, Dict[str, Any]]) -> List[str]:
    grouped_symbols: Dict[str, List[str]] = defaultdict(list)

    for market in markets.values():
        symbol = market.get("symbol")
        if not isinstance(symbol, str) or "/" not in symbol:
            continue

        if market.get("active") is False:
            continue

        if not market.get("spot", False):
            continue

        quote = str(market.get("quote") or symbol.split("/")[-1]).upper()
        grouped_symbols[quote].append(symbol)

    sorted_quotes = sorted(grouped_symbols.keys(), key=lambda quote: (quote != "USDT", quote))

    symbols: List[str] = []
    for quote in sorted_quotes:
        quote_symbols = sorted(grouped_symbols[quote], key=lambda item: item.upper())
        symbols.extend(quote_symbols)

    return symbols


def _safe_close_exchange(exchange_client: Any) -> None:
    close_method = getattr(exchange_client, "close", None)
    if callable(close_method):
        close_method()


def _load_account_symbols_sync(exchange: str, testnet: bool) -> List[str]:
    exchange_client = _create_ccxt_exchange(exchange=exchange, testnet=testnet)
    try:
        markets = exchange_client.load_markets()
        return _sort_symbols_by_quote(markets)
    finally:
        _safe_close_exchange(exchange_client)


def _fetch_account_trading_fee_sync(
    exchange: str,
    testnet: bool,
    api_key: str,
    api_secret: str,
    symbol: str,
) -> TradingFeeResponse:
    exchange_client = _create_ccxt_exchange(
        exchange=exchange,
        testnet=testnet,
        api_key=api_key,
        api_secret=api_secret,
    )

    try:
        markets = exchange_client.load_markets()
        market = markets.get(symbol)
        if not isinstance(market, dict):
            raise ValueError(f"Unsupported symbol for {exchange}: {symbol}")

        maker = float(market.get("maker") or 0.0)
        taker = float(market.get("taker") or maker)

        has_fetch_trading_fee = bool(getattr(exchange_client, "has", {}).get("fetchTradingFee"))
        if has_fetch_trading_fee:
            try:
                fee_info = exchange_client.fetch_trading_fee(symbol)
            except Exception as err:
                logger.warning(
                    "fetch_trading_fee fallback to market fee exchange=%s symbol=%s error=%s",
                    exchange,
                    symbol,
                    err,
                )
            else:
                if isinstance(fee_info, dict):
                    if fee_info.get("maker") is not None:
                        maker = float(fee_info["maker"])
                    if fee_info.get("taker") is not None:
                        taker = float(fee_info["taker"])

        return TradingFeeResponse(symbol=symbol, maker=maker, taker=taker)
    finally:
        _safe_close_exchange(exchange_client)


@router.get("", response_model=List[AccountResponse])
async def list_accounts(
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    accounts = await AccountCRUD.get_all(session, user_email)
    return [account_to_response(acc) for acc in accounts]


@router.post("", response_model=AccountResponse, status_code=status.HTTP_201_CREATED)
async def create_account(
    data: AccountCreate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    encrypted_key = encrypt_api_secret(data.api_key)
    encrypted_secret = encrypt_api_secret(data.api_secret)

    account = await AccountCRUD.create(
        session,
        user_email=user_email,
        exchange=data.exchange,
        label=data.label,
        api_key=encrypted_key,
        api_secret=encrypted_secret,
        testnet=data.testnet,
    )
    return account_to_response(account)


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account_to_response(account)


@router.get("/{account_id}/symbols", response_model=List[str])
async def get_account_symbols(
    account_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    cache_key = _get_symbols_cache_key(account.exchange, account.testnet)
    redis_client = get_redis_client().client

    try:
        cached_symbols_raw = redis_client.get(cache_key)
        if cached_symbols_raw:
            cached_symbols = json.loads(cached_symbols_raw)
            if isinstance(cached_symbols, list):
                return [str(symbol) for symbol in cached_symbols]
    except Exception as err:
        logger.warning("read symbols cache failed key=%s error=%s", cache_key, err)

    try:
        loop = asyncio.get_running_loop()
        symbols = await loop.run_in_executor(
            None,
            partial(_load_account_symbols_sync, account.exchange, account.testnet),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except Exception as err:
        logger.exception(
            "load account symbols failed account_id=%s exchange=%s testnet=%s",
            account.id,
            account.exchange,
            account.testnet,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to load symbols: {err}",
        ) from err

    try:
        redis_client.setex(cache_key, SYMBOLS_CACHE_TTL_SECONDS, json.dumps(symbols))
    except Exception as err:
        logger.warning("write symbols cache failed key=%s error=%s", cache_key, err)

    return symbols


@router.get("/{account_id}/trading-fee", response_model=TradingFeeResponse)
async def fetch_trading_fee(
    account_id: int,
    symbol: str,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    try:
        api_key = decrypt_api_secret(account.api_key)
        api_secret = decrypt_api_secret(account.api_secret)
    except Exception as err:
        logger.exception("decrypt account credentials failed account_id=%s", account.id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Account credentials are invalid",
        ) from err

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            partial(
                _fetch_account_trading_fee_sync,
                account.exchange,
                account.testnet,
                api_key,
                api_secret,
                symbol,
            ),
        )
    except ValueError as err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(err)) from err
    except Exception as err:
        logger.exception(
            "fetch trading fee failed account_id=%s exchange=%s symbol=%s",
            account.id,
            account.exchange,
            symbol,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to fetch trading fee: {err}",
        ) from err


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    data: AccountUpdate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    update_data = data.model_dump(exclude_unset=True)
    if "api_key" in update_data and update_data["api_key"]:
        update_data["api_key"] = encrypt_api_secret(update_data["api_key"])
    if "api_secret" in update_data and update_data["api_secret"]:
        update_data["api_secret"] = encrypt_api_secret(update_data["api_secret"])

    account = await AccountCRUD.update(session, account, **update_data)
    return account_to_response(account)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    await AccountCRUD.delete(session, account)
