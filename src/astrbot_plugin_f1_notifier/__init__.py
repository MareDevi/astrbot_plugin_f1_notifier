"""F1 Notifier plugin package.

Public submodules:
  - models:    Pydantic data models for F1 API responses
  - api:       Async HTTP clients for Jolpica-F1 and OpenF1
  - formatter: Plain-text message formatters
  - scheduler: Background notification scheduler
"""

from . import models, api, formatter, scheduler

__all__ = ["models", "api", "formatter", "scheduler"]
