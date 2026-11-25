"""Compatibility exports for the PyQt6 desktop interface."""

from gui_qt import (
    PetRespecApp,
    automatic_target_groups,
    build_wash_notification_metadata,
    format_map_location,
    pet_skill_rows,
    run,
)

__all__ = (
    "PetRespecApp",
    "automatic_target_groups",
    "build_wash_notification_metadata",
    "format_map_location",
    "pet_skill_rows",
    "run",
)


if __name__ == "__main__":
    run()
