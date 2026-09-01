"""Shared Eventra URL authority backed by the embedded reusable contract."""

from tools.multica_delivery.metadata import _canonical_comment_url


def is_canonical_comment_url(value: object, comment_uuid: object) -> bool:
    """Return whether one product-neutral HTTPS URL identifies the UUID."""

    return _canonical_comment_url(value, comment_uuid)
