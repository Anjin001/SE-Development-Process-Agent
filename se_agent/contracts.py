"""Shared v1 contract. All records use JSON-compatible fields."""

class Conflict(ValueError):
    """The current state or revision does not allow this operation."""
