"""Run with python -m watcherobot.idle_studio."""
import argparse
import uvicorn
from .server import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Watcher 待机行为工作台")
    parser.add_argument("--port", type=int, default=18768)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
