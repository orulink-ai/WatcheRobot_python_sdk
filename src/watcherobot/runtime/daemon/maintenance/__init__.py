"""Local serial maintenance jobs used by Watcher Desktop."""

from .service import MaintenanceError, MaintenanceService

__all__ = ["MaintenanceError", "MaintenanceService"]
