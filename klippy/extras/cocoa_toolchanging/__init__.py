from __future__ import annotations

import typing

from .toolchanging import CocoaToolchanging

if typing.TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper


def load_config(config: ConfigWrapper) -> CocoaToolchanging:
    return CocoaToolchanging(config)
