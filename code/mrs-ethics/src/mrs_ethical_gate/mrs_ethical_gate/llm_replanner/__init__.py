"""LLM-based replanner for multi-robot ethical coordination.

This module replaces the scripted FakePlanner with an actual LLM (Phi-3.5-mini via Ollama)
that can understand constraint feedback and generate modified plans.

Components:
    - ollama_client: Low-level Ollama API wrapper with timeout/retry
    - prompt_builder: Constructs prompts from plan + constraints
    - replanner: Main replanning logic (replaces scripted_repair)
"""

from .replanner import LLMReplanner
from .ollama_client import OllamaClient
from .prompt_builder import build_replan_prompt

__all__ = ["LLMReplanner", "OllamaClient", "build_replan_prompt"]
