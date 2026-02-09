"""Exchange account management routes."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db_session
from api.db.crud import AccountCRUD
from api.db.models import ExchangeAccount
from shared.utils.crypto import encrypt_api_secret

router = APIRouter()


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
