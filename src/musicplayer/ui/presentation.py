"""Choose an input-oriented presentation independently of orientation."""

from enum import StrEnum


class PresentationKind(StrEnum):
    MOBILE = "mobile"
    DESKTOP = "desktop"


def presentation_kind(
    platform: object, *, native_mobile: bool = False
) -> PresentationKind:
    name = str(platform).casefold()
    if native_mobile or "android" in name or "ios" in name:
        return PresentationKind.MOBILE
    return PresentationKind.DESKTOP
