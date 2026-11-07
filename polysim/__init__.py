"""polysim – event-driven trade simulation for Polymarket outcome tokens."""

from .approval import Approval, ApprovalRequest, auto_approve, cli_approver, deny_all
from .audit import AuditLog, verify_file
from .basket import Basket, BasketLeg
from .engine import BacktestResult, Backtester, BookSynthesizer
from .events import BookEvent, Event, PriceEvent, ResolutionEvent
from .exchange import FillMode, SimExchange
from .fees import FeeModel
from .models import (
    Fill,
    Liquidity,
    Market,
    Order,
    OrderBook,
    OrderStatus,
    OrderType,
    PriceLevel,
    Side,
    TimeInForce,
    Token,
)
from .portfolio import InsufficientFunds, Portfolio, Position
from .risk import RiskLimits, RiskManager
from .strategy import Strategy, StrategyContext

__version__ = "0.1.0"

__all__ = [
    "Approval",
    "ApprovalRequest",
    "AuditLog",
    "Basket",
    "BasketLeg",
    "RiskLimits",
    "RiskManager",
    "auto_approve",
    "cli_approver",
    "deny_all",
    "verify_file",
    "BacktestResult",
    "Backtester",
    "BookEvent",
    "BookSynthesizer",
    "Event",
    "FeeModel",
    "Fill",
    "FillMode",
    "InsufficientFunds",
    "Liquidity",
    "Market",
    "Order",
    "OrderBook",
    "OrderStatus",
    "OrderType",
    "Portfolio",
    "Position",
    "PriceEvent",
    "PriceLevel",
    "ResolutionEvent",
    "Side",
    "SimExchange",
    "Strategy",
    "StrategyContext",
    "TimeInForce",
    "Token",
]
