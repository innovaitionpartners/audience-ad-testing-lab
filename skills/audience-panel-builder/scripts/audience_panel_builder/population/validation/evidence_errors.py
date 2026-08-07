"""Closed failures for producer-evidence authentication."""

from __future__ import annotations


class ProducerEvidenceError(Exception):
    """Base class for fail-closed producer-evidence failures."""


class ProducerAuthenticationError(ProducerEvidenceError):
    """Producer input cannot be authenticated as the supplied evidence."""


class ProducerOutputCollision(ProducerEvidenceError):
    """An immutable producer-evidence output path already exists."""


class ProducerPublicationIndeterminate(ProducerEvidenceError):
    """Canonical bytes exist, but publication durability is not established."""


class ProducerRuntimeUnavailable(ProducerEvidenceError):
    """The required isolated producer runtime is unavailable."""
