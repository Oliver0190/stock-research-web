import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="市场观察 · 本机研究工作区")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    # The first release is deliberately bound to loopback; no public unauthenticated API.
    uvicorn.run("backend.app:create_app", factory=True, host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
