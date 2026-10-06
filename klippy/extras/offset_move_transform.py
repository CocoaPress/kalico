from __future__ import annotations

import typing

from .gcode_move import MoveTransform

if typing.TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper
    from klippy.printer import Printer

    from .gcode_move import GCodeMove


class GCodeOffsets(typing.Protocol):
    def get_gcode_offsets(self) -> tuple[float, float, float]: ...


class OffsetMoveTransform(MoveTransform):
    offsets: GCodeOffsets | None
    next_transform: MoveTransform

    def __init__(self, config: ConfigWrapper):
        self.printer: Printer = config.get_printer()
        self.gcode_move: GCodeMove = self.printer.lookup_object("gcode_move")

        self.next_transform = None
        self.offsets = None

        self.printer.register_event_handler("klippy:connect", self._on_connect)

    def _on_connect(self):
        self.next_transform = self.gcode_move.set_move_transform(
            self, force=True
        )

    def get_status(self, _eventtime: None):
        offsets = [0.0, 0.0, 0.0]
        if self.offsets:
            offsets = self.offsets.get_gcode_offsets()

        return {"offsets": offsets}

    def set_offsets(self, offsets: typing.Optional[GCodeOffsets] = None):
        if self.offsets is not offsets:
            self.offsets = offsets
            self.gcode_move.reset_last_position()

    ## Move Transform interface
    def move(self, newpos, speed):
        if not self.offsets:
            return self.next_transform.move(newpos, speed)

        offsets = self.offsets.get_gcode_offsets()
        transformed_pos = [
            newpos[0] + offsets[0],
            newpos[1] + offsets[1],
            newpos[2] + offsets[2],
            newpos[3],
        ]
        return self.next_transform.move(transformed_pos, speed)

    def get_position(self):
        if not self.offsets:
            return self.next_transform.get_position()

        base_pos = self.next_transform.get_position()
        offsets = self.offsets.get_gcode_offsets()
        return [
            base_pos[0] - offsets[0],
            base_pos[1] - offsets[1],
            base_pos[2] - offsets[2],
            base_pos[3],
        ]


def load_config(config: ConfigWrapper):
    return OffsetMoveTransform(config)
