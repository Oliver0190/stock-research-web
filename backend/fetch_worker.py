"""Isolate third-party adapters so a stalled source cannot block the web server."""
import contextlib
import sys

from backend import data, fundamentals
from backend.store import encode


def main():
    symbol, mode = sys.argv[1:3]
    with contextlib.redirect_stdout(sys.stderr):
        if mode == "fundamentals":
            result = fundamentals.fetch_fundamentals(symbol, configured_name=sys.argv[3])
        else:
            df = data.fetch_hk_daily(symbol, int(sys.argv[3]))
            source = df.attrs.get("source", "未知来源")
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
            result = {"rows": df.to_dict("records"), "source": source}
    print(encode(result))


if __name__ == "__main__":
    main()
