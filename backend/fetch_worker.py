"""Isolate third-party adapters so a stalled source cannot block the web server."""
import contextlib
import sys

from backend import data, fundamentals
from backend.store import encode
from backend.watchlist import resolve_stock
from backend.news import fetch_news


def main():
    symbol, mode = sys.argv[1:3]
    market = sys.argv[4] if len(sys.argv) > 4 else "HK"
    with contextlib.redirect_stdout(sys.stderr):
        if mode == "lookup":
            try:
                result = resolve_stock(symbol, market)
            except ValueError as error:
                result = {"error": str(error)}
        elif mode == "news":
            result = fetch_news(symbol, sys.argv[3])
        elif mode == "fundamentals":
            result = fundamentals.fetch_fundamentals(symbol, configured_name=sys.argv[3], market=market)
        else:
            df = data.fetch_daily(symbol, int(sys.argv[3]), market=market)
            source = df.attrs.get("source", "未知来源")
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
            result = {"rows": df.to_dict("records"), "source": source}
    print(encode(result))


if __name__ == "__main__":
    main()
