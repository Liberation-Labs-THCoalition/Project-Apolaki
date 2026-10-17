"""Kintsugi core loader: the real engine first, the vendored copy as fallback (Rivet's pattern).

Resolution order:
  1. `KINTSUGI_PATH` pointing at a Project-Kintsugi checkout
  2. an installed, importable `kintsugi` package
  3. the vendored verbatim copy in apolaki/kintsugi_vendor/

Everything in Apolaki imports Kintsugi through this module, so the same code runs against the real engine on a dev
box and the vendored copy on a bare deployment. KINTSUGI_SOURCE says which one loaded.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_VENDOR = Path(__file__).parent / "kintsugi_vendor"

KINTSUGI_SOURCE = "unresolved"


def _resolve() -> None:
    global KINTSUGI_SOURCE
    env_path = os.environ.get("KINTSUGI_PATH")
    if env_path and (Path(env_path) / "kintsugi" / "__init__.py").exists():
        sys.path.insert(0, env_path)
        KINTSUGI_SOURCE = f"checkout:{env_path}"
        return
    try:
        import kintsugi  # noqa: F401
        KINTSUGI_SOURCE = "installed"
        return
    except ImportError:
        pass
    sys.path.insert(0, str(_VENDOR))
    KINTSUGI_SOURCE = f"vendor:{_VENDOR}"


_resolve()

try:
    from kintsugi.bdi import (BDIBelief, BDIDesire, BDIIntention, BDISnapshot, BDIStore,  # noqa: E402,F401
                              BeliefStatus, DesireStatus, IntentionStatus)
    from kintsugi.skills.base import BaseSkillChip, SkillContext, SkillDomain, SkillRequest, SkillResponse  # noqa: E402,F401
    from kintsugi.skills.registry import SkillRegistry  # noqa: E402,F401
except ImportError as e:                                       # say what's missing and why Apolaki wants it
    raise ImportError(f"Apolaki's engine wiring needs Kintsugi's BDI and skill modules ({KINTSUGI_SOURCE}): {e}. "
                      f"Set KINTSUGI_PATH to a Project-Kintsugi checkout, or restore apolaki/kintsugi_vendor/.") from e
