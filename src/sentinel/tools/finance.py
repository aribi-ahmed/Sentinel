import yfinance as yf
from typing import Dict, Any
from langchain_core.tools import tool


@tool
def fetch_company_financials(ticker: str) -> Dict[str, Any]:
    """
    Fetches real-time financial metrics for a target stock ticker (e.g., TSLA, AAPL, MSFT).
    Returns market cap, P/E ratio, total debt, cash flow, and profit margins.
    """
    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        return {
            "symbol": ticker.upper(),
            "company_name": info.get("longName", ticker),
            "market_cap": info.get("marketCap", "N/A"),
            "pe_ratio": info.get("trailingPE", "N/A"),
            "total_debt": info.get("totalDebt", "N/A"),
            "free_cashflow": info.get("freeCashflow", "N/A"),
            "profit_margins": info.get("profitMargins", "N/A"),
            "currency": info.get("currency", "USD"),
        }
    except Exception as e:
        return {"error": f"Failed to fetch data for ticker {ticker}: {str(e)}"}