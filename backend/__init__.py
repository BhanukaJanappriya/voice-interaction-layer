"""The backend modules import each other by plain module name (import
config, from agent import Orchestrator), the way they have since the
prototype, so each one stays runnable standalone from inside backend/.
Adding this directory to sys.path when the package itself is imported
makes the same imports work for package style entry points run from the
repository root, such as python -m backend or
python -m backend.knowledge.build."""

import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
