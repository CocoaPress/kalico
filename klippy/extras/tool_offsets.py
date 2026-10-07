"""Proxy object for calibrating active tool offsets"""

from __future__ import annotations

import typing

from klippy.extras import manual_probe

if typing.TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper
    from klippy.gcode import GCodeCommand, GCodeDispatch
    from klippy.printer import Printer

    from .cocoa_toolhead.toolhead import CocoaToolheadControl
    from .cocoa_toolhead.nozzle_offsets import CocoaNozzleOffsets
    from .gcode_move import GCodeMove
    from .offset_move_transform import OffsetMoveTransform


class ToolOffsetProxy:
    config: ConfigWrapper
    printer: Printer
    gcode: GCodeDispatch
    gcode_move: GCodeMove

    move_transform: OffsetMoveTransform
    active_offsets: None | CocoaNozzleOffsets

    def __init__(self, config: ConfigWrapper):
        self.config = config
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_move = self.printer.lookup_object("gcode_move")

        self.move_transform = self.printer.load_object(
            config, "offset_move_transform"
        )

        self.active_offsets = None

        self.printer.register_event_handler("klippy:connect", self._on_connect)
        self.printer.register_event_handler(
            "cocoa_toolchanging:tool_activated", self._tool_activated
        )

        self.gcode.register_command(
            "TOOL_OFFSET_CALIBRATE", self.cmd_TOOL_OFFSET_CALIBRATE
        )
        self.gcode.register_command(
            "Z_OFFSET_APPLY_TOOL", self.cmd_Z_OFFSET_APPLY_TOOL
        )
        self.gcode.register_command(
            "Z_OFFSET_APPLY_ALL_TOOLS", self.cmd_Z_OFFSET_APPLY_ALL_TOOLS
        )

    def _on_connect(self):
        if cocoa_toolchanging := self.printer.lookup_object(
            "cocoa_toolchanging", None
        ):
            self._tool_activated(cocoa_toolchanging.active_tool)

        # Single tool machines always activate the first toolhead found
        elif toolheads := self.printer.lookup_object("cocoa_toolhead"):
            _, cocoa_toolhead = next(toolheads)
            self._tool_activated(cocoa_toolhead)

    def _tool_activated(self, tool: None | CocoaToolheadControl):
        manual_probe.verify_no_manual_probe(self.printer)

        if tool is None:
            self.active_offsets = None
        else:
            self.active_offsets = tool.nozzle_offsets

        self.move_transform.set_offsets(self.active_offsets)

    def tool_offset_finalize(self, kin_pos):
        if kin_pos is None or self.active_offsets is None:
            return

        offsets = self.active_offsets.get_gcode_offsets()
        new_offsets = [offsets[0], offsets[1], kin_pos[2]]
        self.active_offsets.save_offsets(new_offsets)

        self.gcode.respond_info(
            f"tool_offsets: Saved z_offset={new_offsets[2]:.3f}"
        )

    def cmd_TOOL_OFFSET_CALIBRATE(self, gcmd: GCodeCommand):
        """
        Calibrate Z offset for the currently active tool
        """

        if self.active_offsets is None:
            raise gcmd.error(
                "Unable to calibrate tool offsets without active tool"
            )

        manual_probe.ManualProbeHelper(
            self.printer,
            gcmd,
            self.tool_offset_finalize,
        )

    def cmd_Z_OFFSET_APPLY_TOOL(self, gcmd: GCodeCommand):
        """
        Adjust the active tool z_offset to include the current GCode z_offset
        """

        if self.active_offsets is None:
            raise gcmd.error(
                "Unable to calibrate tool offsets without active tool"
            )

        gcode_z_offset = self.gcode_move.homing_position[2]

        offsets = self.active_offsets.get_gcode_offsets()
        new_offsets = [offsets[0], offsets[1], offsets[2] + gcode_z_offset]

        self.active_offsets.save_offsets(new_offsets)
        gcmd.respond_info(
            f"Saved {self.active_offsets.name} z_offset={new_offsets[2]:.3f}"
        )

        self.gcode.run_script_from_command("SET_GCODE_OFFSET Z=0.")

    def cmd_Z_OFFSET_APPLY_ALL_TOOLS(self, gcmd: GCodeCommand):
        """
        Adjust z_offset for all tools to include the current GCode z_offset
        """

        gcode_z_offset = self.gcode_move.homing_position[2]

        for _, tool in self.printer.lookup_objects("cocoa_toolhead"):
            offsets = tool.nozzle_offsets.get_gcode_offsets()
            new_offsets = [offsets[0], offsets[1], offsets[2] + gcode_z_offset]
            tool.nozzle_offsets.save_offsets(new_offsets)
            gcmd.respond_info(
                f"Saved {tool.name} z_offset={new_offsets[2]:.3f}"
            )

        self.gcode.run_script_from_command("SET_GCODE_OFFSET Z=0.")

    def get_status(self, _eventtime):
        return {
            "offsets": self.active_offsets.get_gcode_offsets()
            if self.active_offsets is not None
            else [0.0, 0.0, 0.0]
        }


def load_config(config: ConfigWrapper):
    return ToolOffsetProxy(config)
