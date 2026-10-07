import os
from dotenv import load_dotenv
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_core.tools import tool

load_dotenv(override=True)

@tool
def search_company_news(query: str) -> str:
    """Searches recent web news and corporate announcements."""
    tavily_key = os.getenv("TAVILY_API_KEY")
    if not tavily_key:
        return f"OSINT Warning: TAVILY_API_KEY missing. Evaluated query: '{query}'."

    try:
        search_tool = TavilySearchResults(max_results=3, tavily_api_key=tavily_key)
        results = search_tool.invoke({"query": f"{query} controversy legal sanctions financial risk"})
        return str(results)
    except Exception as e:
        return f"News search failed: {str(e)}"