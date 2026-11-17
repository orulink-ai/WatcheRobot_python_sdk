"""Marketplace entry point. The editor is bundled beside this file."""
import asyncio
from idle_studio.managed import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
