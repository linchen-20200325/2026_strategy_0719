"""multi_agent_system — 多智能體虛擬投研與自動交易系統。

對接三個在地化 SQLite 資料庫（stock.db / fund.db / news.db），
以 1 個資料代理人 + 5 個虛擬專家（總經 / 技術 / 配置 / 策略融合 / 系統整合）
產出五大交易行動訊號。

公開 API 見下方 __all__。
"""

from __future__ import annotations

from .allocation_agent import AssetAllocationAgent, default_portfolio_state
from .contracts import (
    Action,
    AgentVerdict,
    DataPacket,
    FinalDecision,
    MacroReading,
    NewsItem,
    PortfolioState,
    TechnicalSnapshot,
    UsLinkSnapshot,
)
from .data_agent import DataAggregationAgent, DataSourceError
from .integration_agent import (
    BrokerAPI,
    CycleResult,
    MockBrokerAPI,
    OrderReceipt,
    ResearchRequest,
    WorkflowOrchestrator,
)
from .line_push import LineNotifier, LinePusher
from .macro_agent import MacroeconomicAgent
from .macro_providers import (
    FredMacroProvider,
    MacroDataProvider,
    SimulatedMacroProvider,
    StaticMacroProvider,
)
from .notifications import (
    ConsoleNotifier,
    Notifier,
    format_notification,
    should_notify,
)
from .numerics import annualized_sharpe, clamp, linear_map
from .strategy_agent import StrategyAgent
from .technical_agent import TechnicalAnalysisAgent

__version__ = "0.1.0"

__all__ = [
    # agents
    "DataAggregationAgent",
    "MacroeconomicAgent",
    "TechnicalAnalysisAgent",
    "AssetAllocationAgent",
    "StrategyAgent",
    "WorkflowOrchestrator",
    # providers / broker
    "MacroDataProvider",
    "StaticMacroProvider",
    "SimulatedMacroProvider",
    "FredMacroProvider",
    "BrokerAPI",
    "MockBrokerAPI",
    # contracts
    "Action",
    "AgentVerdict",
    "DataPacket",
    "FinalDecision",
    "MacroReading",
    "NewsItem",
    "PortfolioState",
    "TechnicalSnapshot",
    "UsLinkSnapshot",
    "OrderReceipt",
    "ResearchRequest",
    "CycleResult",
    # notifications (streamlit-free core)
    "Notifier",
    "ConsoleNotifier",
    "LineNotifier",
    "LinePusher",
    "should_notify",
    "format_notification",
    # helpers
    "DataSourceError",
    "default_portfolio_state",
    "annualized_sharpe",
    "clamp",
    "linear_map",
]
