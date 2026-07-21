"""multi_agent_system — 多智能體虛擬投研與自動交易系統。

對接三個在地化 SQLite 資料庫（stock.db / fund.db / news.db），
以 1 個資料代理人 + 5 個虛擬專家（總經 / 技術 / 配置 / 策略融合 / 系統整合）
產出五大交易行動訊號。

**惰性載入（PEP 562）**：公開 API 只有被存取到的名字才 import 對應子模組。這讓輕量 caller
（NAS webhook bot：只要 `make_subscriber_store` / `WatchItem`）**不必連帶載入 agents / pandas /
numpy** —— 可在 NAS 用內建 python **零安裝**執行。cron / dashboard 用到重物件時才實際載入。

公開 API 見下方 __all__。
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = [
    # agents
    "DataAggregationAgent",
    "MacroeconomicAgent",
    "TechnicalAnalysisAgent",
    "FundamentalAgent",
    "AssetAllocationAgent",
    "StrategyAgent",
    "WorkflowOrchestrator",
    # providers / broker
    "MacroDataProvider",
    "StaticMacroProvider",
    "SimulatedMacroProvider",
    "DbMacroProvider",
    "FredMacroProvider",
    "BrokerAPI",
    "MockBrokerAPI",
    # contracts
    "Action",
    "AgentVerdict",
    "DataPacket",
    "FinalDecision",
    "MacroReading",
    "TwMacroReading",
    "NewsItem",
    "PortfolioState",
    "TechnicalSnapshot",
    "FinancialsSnapshot",
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

# 公開名 → 所在子模組（相對）。存取到才 import（見 __getattr__）。
_LAZY = {
    "AssetAllocationAgent": "allocation_agent",
    "default_portfolio_state": "allocation_agent",
    "Action": "contracts",
    "AgentVerdict": "contracts",
    "DataPacket": "contracts",
    "FinalDecision": "contracts",
    "MacroReading": "contracts",
    "TwMacroReading": "contracts",
    "NewsItem": "contracts",
    "PortfolioState": "contracts",
    "TechnicalSnapshot": "contracts",
    "FinancialsSnapshot": "contracts",
    "UsLinkSnapshot": "contracts",
    "DataAggregationAgent": "data_agent",
    "DataSourceError": "data_agent",
    "FundamentalAgent": "fundamental_agent",
    "BrokerAPI": "integration_agent",
    "CycleResult": "integration_agent",
    "MockBrokerAPI": "integration_agent",
    "OrderReceipt": "integration_agent",
    "ResearchRequest": "integration_agent",
    "WorkflowOrchestrator": "integration_agent",
    "LineNotifier": "line_push",
    "LinePusher": "line_push",
    "MacroeconomicAgent": "macro_agent",
    "DbMacroProvider": "macro_providers",
    "FredMacroProvider": "macro_providers",
    "MacroDataProvider": "macro_providers",
    "SimulatedMacroProvider": "macro_providers",
    "StaticMacroProvider": "macro_providers",
    "ConsoleNotifier": "notifications",
    "Notifier": "notifications",
    "format_notification": "notifications",
    "should_notify": "notifications",
    "annualized_sharpe": "numerics",
    "clamp": "numerics",
    "linear_map": "numerics",
    "StrategyAgent": "strategy_agent",
    "TechnicalAnalysisAgent": "technical_agent",
}


def __getattr__(name: str):
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(f".{mod}", __name__), name)
    globals()[name] = value  # 快取：之後直接命中,不再走 __getattr__
    return value


def __dir__():
    return sorted(__all__)
