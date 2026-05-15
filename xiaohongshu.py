#!/usr/bin/env python3
"""小红书知识库 Workflow — 主入口"""

# Fix PaddleOCR protobuf compatibility (must precede all other imports)
import os as _os
_os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

from src.cli import main

if __name__ == "__main__":
    main()
