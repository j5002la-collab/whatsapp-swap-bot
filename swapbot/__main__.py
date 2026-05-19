"""Entry point: python -m swapbot"""
import asyncio
import sys
from swapbot.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSwapBot stopped.")
        sys.exit(0)
