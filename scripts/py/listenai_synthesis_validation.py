#!/usr/bin/env python3
"""Compatibility CLI for synthesis-management validation.

Implementation lives in ``synthesis_management.validation`` so 合成管理
音频合成/播报合成验证 can evolve as an isolated skill module.
"""
from __future__ import annotations

from synthesis_management.validation import main


if __name__ == "__main__":
    raise SystemExit(main())
