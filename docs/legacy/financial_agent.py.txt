from typing import Dict, Any
from sentinel.tools.finance import fetch_company_financials

def run_financial_agent(ticker: str) -> Dict[str, Any]:
    if not ticker:
        return {"status": "skipped", "reason": "No ticker supplied"}
    return fetch_company_financials.invoke({"ticker": ticker})