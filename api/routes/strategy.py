"""Strategy management routes."""
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_current_user, get_db_session
from api.celery_client import get_active_workers, send_run_strategy, revoke_task
from shared.core.redis_client import get_redis_client
from shared.db.crud import StrategyCRUD, AccountCRUD
from shared.db.models import Strategy

router = APIRouter()


class StrategyCreate(BaseModel):
    account_id: int
    name: str
    symbol: str
    base_order_size: Decimal
    buy_price_deviation: Decimal
    sell_price_deviation: Decimal
    grid_levels: int = 3
    polling_interval: Decimal = Decimal("1.0")
    price_tolerance: Decimal = Decimal("0.5")
    stop_loss: Optional[Decimal] = None
    stop_loss_delay: Optional[int] = None
    max_open_positions: int = 10
    max_daily_drawdown: Optional[Decimal] = None
    worker_name: Optional[str] = None


class StrategyUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    base_order_size: Optional[Decimal] = None
    buy_price_deviation: Optional[Decimal] = None
    sell_price_deviation: Optional[Decimal] = None
    grid_levels: Optional[int] = None
    polling_interval: Optional[Decimal] = None
    price_tolerance: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    stop_loss_delay: Optional[int] = None
    max_open_positions: Optional[int] = None
    max_daily_drawdown: Optional[Decimal] = None
    worker_name: Optional[str] = None


class StrategyResponse(BaseModel):
    id: int
    account_id: int
    name: str
    symbol: str
    base_order_size: Decimal
    buy_price_deviation: Decimal
    sell_price_deviation: Decimal
    grid_levels: int
    polling_interval: Decimal
    price_tolerance: Decimal
    stop_loss: Optional[Decimal]
    stop_loss_delay: Optional[int]
    max_open_positions: int
    max_daily_drawdown: Optional[Decimal]
    worker_name: Optional[str]
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class StrategyStatusResponse(BaseModel):
    strategy_id: int
    is_running: bool
    task_id: Optional[str] = None
    worker_ip: Optional[str] = None
    worker_hostname: Optional[str] = None
    current_price: Optional[float] = None
    pending_buys: int = 0
    pending_sells: int = 0
    position_count: int = 0
    started_at: Optional[int] = None
    updated_at: Optional[int] = None


class RunningStrategyResponse(BaseModel):
    strategy_id: int
    task_id: str
    worker_ip: str
    worker_hostname: str
    status: str
    current_price: float
    pending_buys: int
    pending_sells: int
    position_count: int
    started_at: int
    updated_at: int


def strategy_to_response(strategy: Strategy) -> StrategyResponse:
    return StrategyResponse(
        id=strategy.id,
        account_id=strategy.account_id,
        name=strategy.name,
        symbol=strategy.symbol,
        base_order_size=strategy.base_order_size,
        buy_price_deviation=strategy.buy_price_deviation,
        sell_price_deviation=strategy.sell_price_deviation,
        grid_levels=strategy.grid_levels,
        polling_interval=strategy.polling_interval,
        price_tolerance=strategy.price_tolerance,
        stop_loss=strategy.stop_loss,
        stop_loss_delay=strategy.stop_loss_delay,
        max_open_positions=strategy.max_open_positions,
        max_daily_drawdown=strategy.max_daily_drawdown,
        worker_name=strategy.worker_name,
        created_at=strategy.created_at.isoformat(),
        updated_at=strategy.updated_at.isoformat(),
    )


def _is_strategy_running(strategy_id: int) -> bool:
    """Check if a strategy is running via Redis."""
    redis_client = get_redis_client()
    return redis_client.is_strategy_running(strategy_id)


def _ensure_worker_available(worker_name: Optional[str]) -> None:
    """Validate selected worker is currently online."""
    if not worker_name:
        return

    active_worker_names = {worker["name"] for worker in get_active_workers()}
    if worker_name not in active_worker_names:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"指定 Worker 不在线: {worker_name}",
        )


@router.get("", response_model=List[StrategyResponse])
async def list_strategies(
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    strategies = await StrategyCRUD.get_all(session, user_email)
    return [strategy_to_response(s) for s in strategies]


@router.post("", response_model=StrategyResponse, status_code=status.HTTP_201_CREATED)
async def create_strategy(
    data: StrategyCreate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    account = await AccountCRUD.get_by_id(session, data.account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    strategy = await StrategyCRUD.create(
        session,
        user_email=user_email,
        account_id=data.account_id,
        name=data.name,
        symbol=data.symbol,
        base_order_size=data.base_order_size,
        buy_price_deviation=data.buy_price_deviation,
        sell_price_deviation=data.sell_price_deviation,
        grid_levels=data.grid_levels,
        polling_interval=data.polling_interval,
        price_tolerance=data.price_tolerance,
        stop_loss=data.stop_loss,
        stop_loss_delay=data.stop_loss_delay,
        max_open_positions=data.max_open_positions,
        max_daily_drawdown=data.max_daily_drawdown,
        worker_name=data.worker_name,
    )
    return strategy_to_response(strategy)


@router.get("/running", response_model=List[RunningStrategyResponse])
async def get_running_strategies(
    user_email: str = Depends(get_current_user),
):
    """Get all running strategies from Redis."""
    redis_client = get_redis_client()
    running = redis_client.get_all_running_strategies()
    return [
        RunningStrategyResponse(
            strategy_id=info["strategy_id"],
            task_id=info["task_id"],
            worker_ip=info["worker_ip"],
            worker_hostname=info["worker_hostname"],
            status=info["status"],
            current_price=info["current_price"],
            pending_buys=info["pending_buys"],
            pending_sells=info["pending_sells"],
            position_count=info["position_count"],
            started_at=info["started_at"],
            updated_at=info["updated_at"],
        )
        for info in running
    ]


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    strategy = await StrategyCRUD.get_by_id(session, strategy_id, user_email)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return strategy_to_response(strategy)


@router.put("/{strategy_id}", response_model=StrategyResponse)
async def update_strategy(
    strategy_id: int,
    data: StrategyUpdate,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    strategy = await StrategyCRUD.get_by_id(session, strategy_id, user_email)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    if _is_strategy_running(strategy_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update running strategy. Stop it first.",
        )

    update_data = data.model_dump(exclude_unset=True)
    strategy = await StrategyCRUD.update(session, strategy, **update_data)
    return strategy_to_response(strategy)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(
    strategy_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    strategy = await StrategyCRUD.get_by_id(session, strategy_id, user_email)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    if _is_strategy_running(strategy_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete running strategy. Stop it first.",
        )

    await StrategyCRUD.delete(session, strategy)


class StartStrategyRequest(BaseModel):
    worker_name: Optional[str] = None


@router.post("/{strategy_id}/start", response_model=StrategyStatusResponse)
async def start_strategy(
    strategy_id: int,
    request: Optional[StartStrategyRequest] = None,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    strategy = await StrategyCRUD.get_by_id(session, strategy_id, user_email)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    account = await AccountCRUD.get_by_id(session, strategy.account_id, user_email)
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    if not account.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account is not active")

    if _is_strategy_running(strategy_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Strategy already running")

    # Prepare account data for Celery task
    account_data = {
        "api_key": account.api_key,
        "api_secret": account.api_secret,
        "testnet": account.testnet,
    }

    # Prepare strategy config for Celery task
    strategy_config = {
        "symbol": strategy.symbol,
        "base_order_size": str(strategy.base_order_size),
        "buy_price_deviation": str(strategy.buy_price_deviation),
        "sell_price_deviation": str(strategy.sell_price_deviation),
        "grid_levels": strategy.grid_levels,
        "polling_interval": str(strategy.polling_interval),
        "price_tolerance": str(strategy.price_tolerance),
        "stop_loss": str(strategy.stop_loss) if strategy.stop_loss else None,
        "stop_loss_delay": strategy.stop_loss_delay,
        "max_open_positions": strategy.max_open_positions,
        "max_daily_drawdown": str(strategy.max_daily_drawdown) if strategy.max_daily_drawdown else None,
    }

    # Submit Celery task - 优先使用请求中的 worker，其次使用策略保存的 worker
    worker_name = (request.worker_name if request and request.worker_name else None) or strategy.worker_name
    _ensure_worker_available(worker_name)

    task_id = send_run_strategy(
        strategy_id=strategy_id,
        account_data=account_data,
        strategy_config=strategy_config,
        worker_name=worker_name,
    )

    return StrategyStatusResponse(
        strategy_id=strategy_id,
        is_running=True,
        task_id=task_id,
    )


@router.post("/{strategy_id}/stop", response_model=StrategyStatusResponse)
async def stop_strategy(
    strategy_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    strategy = await StrategyCRUD.get_by_id(session, strategy_id, user_email)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    redis_client = get_redis_client()
    running_info = redis_client.get_running_info(strategy_id)

    if not running_info:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Strategy not running")

    task_id = running_info.get("task_id")
    if task_id:
        revoke_task(task_id)

    # Update status in Redis
    redis_client.update_running_status(strategy_id=strategy_id, status="stopping")

    # Note: The task's finally block will clean up Redis when it stops

    return StrategyStatusResponse(strategy_id=strategy_id, is_running=False)


@router.get("/{strategy_id}/status", response_model=StrategyStatusResponse)
async def get_strategy_status(
    strategy_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    strategy = await StrategyCRUD.get_by_id(session, strategy_id, user_email)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    redis_client = get_redis_client()
    info = redis_client.get_running_info(strategy_id)

    if not info:
        return StrategyStatusResponse(
            strategy_id=strategy_id,
            is_running=False,
        )

    return StrategyStatusResponse(
        strategy_id=strategy_id,
        is_running=True,
        task_id=info.get("task_id"),
        worker_ip=info.get("worker_ip"),
        worker_hostname=info.get("worker_hostname"),
        current_price=info.get("current_price"),
        pending_buys=info.get("pending_buys", 0),
        pending_sells=info.get("pending_sells", 0),
        position_count=info.get("position_count", 0),
        started_at=info.get("started_at"),
        updated_at=info.get("updated_at"),
    )


class BatchRequest(BaseModel):
    strategy_ids: List[int]


class BatchResult(BaseModel):
    success: List[int]
    failed: List[int]


@router.post("/batch/start", response_model=BatchResult)
async def batch_start_strategies(
    data: BatchRequest,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Batch start multiple strategies."""
    success, failed = [], []
    for sid in data.strategy_ids:
        try:
            strategy = await StrategyCRUD.get_by_id(session, sid, user_email)
            if not strategy:
                failed.append(sid)
                continue
            account = await AccountCRUD.get_by_id(session, strategy.account_id, user_email)
            if not account or not account.is_active or _is_strategy_running(sid):
                failed.append(sid)
                continue
            account_data = {"api_key": account.api_key, "api_secret": account.api_secret, "testnet": account.testnet}
            strategy_config = {
                "symbol": strategy.symbol,
                "base_order_size": str(strategy.base_order_size),
                "buy_price_deviation": str(strategy.buy_price_deviation),
                "sell_price_deviation": str(strategy.sell_price_deviation),
                "grid_levels": strategy.grid_levels,
                "polling_interval": str(strategy.polling_interval),
                "price_tolerance": str(strategy.price_tolerance),
                "stop_loss": str(strategy.stop_loss) if strategy.stop_loss else None,
                "stop_loss_delay": strategy.stop_loss_delay,
                "max_open_positions": strategy.max_open_positions,
                "max_daily_drawdown": str(strategy.max_daily_drawdown) if strategy.max_daily_drawdown else None,
            }
            _ensure_worker_available(strategy.worker_name)
            send_run_strategy(sid, account_data, strategy_config, strategy.worker_name)
            success.append(sid)
        except Exception:
            failed.append(sid)
    return BatchResult(success=success, failed=failed)


@router.post("/batch/stop", response_model=BatchResult)
async def batch_stop_strategies(
    data: BatchRequest,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Batch stop multiple strategies."""
    success, failed = [], []
    redis_client = get_redis_client()
    for sid in data.strategy_ids:
        try:
            strategy = await StrategyCRUD.get_by_id(session, sid, user_email)
            if not strategy:
                failed.append(sid)
                continue
            info = redis_client.get_running_info(sid)
            if not info:
                failed.append(sid)
                continue
            task_id = info.get("task_id")
            if task_id:
                revoke_task(task_id)
            redis_client.update_running_status(strategy_id=sid, status="stopping")
            success.append(sid)
        except Exception:
            failed.append(sid)
    return BatchResult(success=success, failed=failed)


@router.post("/batch/delete", response_model=BatchResult)
async def batch_delete_strategies(
    data: BatchRequest,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Batch delete multiple strategies."""
    success, failed = [], []
    for sid in data.strategy_ids:
        try:
            strategy = await StrategyCRUD.get_by_id(session, sid, user_email)
            if not strategy or _is_strategy_running(sid):
                failed.append(sid)
                continue
            await StrategyCRUD.delete(session, strategy)
            success.append(sid)
        except Exception:
            failed.append(sid)
    return BatchResult(success=success, failed=failed)


@router.post("/{strategy_id}/copy", response_model=StrategyResponse)
async def copy_strategy(
    strategy_id: int,
    user_email: str = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """Copy a strategy."""
    strategy = await StrategyCRUD.get_by_id(session, strategy_id, user_email)
    if not strategy:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")

    new_strategy = await StrategyCRUD.create(
        session,
        user_email=user_email,
        account_id=strategy.account_id,
        name=f"{strategy.name} (副本)",
        symbol=strategy.symbol,
        base_order_size=strategy.base_order_size,
        buy_price_deviation=strategy.buy_price_deviation,
        sell_price_deviation=strategy.sell_price_deviation,
        grid_levels=strategy.grid_levels,
        polling_interval=strategy.polling_interval,
        price_tolerance=strategy.price_tolerance,
        stop_loss=strategy.stop_loss,
        stop_loss_delay=strategy.stop_loss_delay,
        max_open_positions=strategy.max_open_positions,
        max_daily_drawdown=strategy.max_daily_drawdown,
        worker_name=strategy.worker_name,
    )
    return strategy_to_response(new_strategy)
