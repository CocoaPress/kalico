"""
Singleton plugin for multi-toolhead Cocoa Press 2
"""

from __future__ import annotations

import typing

from klippy.kinematics.extruder import DummyExtruder

from ..offset_move_transform import OffsetMoveTransform

if typing.TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper
    from klippy.gcode import GCodeCommand, GCodeDispatch
    from klippy.printer import Printer
    from klippy.toolhead import ToolHead

    from ..cocoa_toolhead.toolhead import CocoaToolheadControl
    from ..gcode_macro import PrinterGCodeMacro, Template
    from ..gcode_move import GCodeMove
    from ..virtual_sdcard import VirtualSD


class CocoaToolchanging:
    config: ConfigWrapper
    printer: Printer
    gcode: GCodeDispatch
    gcode_move: GCodeMove
    gcode_macro: PrinterGCodeMacro
    virtual_sdcard: VirtualSD
    move_transform: OffsetMoveTransform

    tools: dict[int, CocoaToolheadControl]

    _templates: dict[str, Template]

    def __init__(self, config: ConfigWrapper):
        self.config = config
        self.printer = config.get_printer()

        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_move = self.printer.load_object(config, "gcode_move")
        self.gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.virtual_sdcard = self.printer.load_object(config, "virtual_sdcard")

        self.move_transform = self.printer.load_object(
            config, "offset_move_transform"
        )

        self.dummy_extruder = DummyExtruder(self.printer)

        self._templates = {
            name: self.gcode_macro.load_template(config, f"{name}_gcode", "")
            for name in [
                "before_change_tool",
                "after_change_tool",
                "before_activate_tool",
                "after_activate_tool",
            ]
        }

        self._enabled = True

        # status flags
        self._is_toolchanging = False
        self._active_tool = None
        self._last_tool = None
        self._next_tool = None
        self._toolchange_count = 0

        self.tools = {}
        tool_names = config.getlist("tools")
        for tool_index, tool_name in enumerate(tool_names):
            tool = self.printer.load_object(config, tool_name)
            self.register_toolhead(tool_index, tool)

        # Setup gcode commands
        self.gcode.register_command(
            "SET_TOOLCHANGING_ENABLED", self.cmd_SET_TOOLCHANGING_ENABLED
        )
        self.gcode.register_command("CHANGE_TOOL", self.cmd_CHANGE_TOOL)
        self.gcode.register_command("ACTIVATE_TOOL", self.cmd_ACTIVATE_TOOL)
        self.gcode.register_command("DEACTIVATE_TOOL", self.cmd_DEACTIVATE_TOOL)

        # Clear toolchange count on print stat reset
        self.printer.register_event_handler(
            "print_status:reset", self._reset_print_stats
        )

    def _on_connect(self):
        toolhead: ToolHead = self.printer.lookup_object("toolhead")

        # Set the initial extruder to a dummy
        toolhead.set_extruder(self.dummy_extruder, 0.0)
        self.move_transform.set_offsets(None)

    def _reset_print_stats(self):
        self._toolchange_count = 0

    def register_toolhead(self, index: int, toolhead: CocoaToolheadControl):
        self.tools[index] = toolhead
        self._active_tool

    def deactivate_tool(self):
        toolhead: ToolHead = self.printer.lookup_object("toolhead")

        self._active_tool = None
        self.move_transform.set_offsets(None)

        current_extruder = toolhead.get_extruder()
        if not isinstance(current_extruder, DummyExtruder):
            toolhead.flush_step_generation()
            toolhead.set_extruder(self.dummy_extruder, 0.0)

    def activate_tool(self, tool_index: int):
        toolhead: ToolHead = self.printer.lookup_object("toolhead")
        tool = self.tools[tool_index]

        current_extruder = toolhead.get_extruder()
        if current_extruder is not tool.extruder:
            toolhead.flush_step_generation()
            toolhead.set_extruder(tool.extruder, tool.extruder.last_position)

        self.move_transform.set_offsets(tool.nozzle_offsets)
        self._active_tool = tool_index

    def get_status(self, _eventtime=None):
        return {
            "enabled": self._enabled,
            "is_toolchanging": self._is_toolchanging,
            "active_tool": self._active_tool,
            "next_tool": self._next_tool,
            "last_tool": self._last_tool,
            "toolchange_count": self._toolchange_count,
        }

    def _call_template(self, name, *, gcmd: GCodeCommand = None):
        template = self._templates[name]

        context = {"toolchanger": self.get_status(None)}
        if self._active_tool is not None:
            context["tool"] = self.tools[self._active_tool].get_status(None)
        if self._next_tool is not None:
            context["next_tool"] = self.tools[self._next_tool].get_status(None)
        if self._last_tool is not None:
            context["last_tool"] = self.tools[self._last_tool].get_status(None)
        context.update(template.create_template_context())
        if gcmd:
            context.update(
                {
                    "params": gcmd.get_command_parameters(),
                    "rawparams": gcmd.get_raw_command_parameters(),
                }
            )

        template.run_gcode_from_command(context)

    def cmd_CHANGE_TOOL(self, gcmd: GCodeCommand):
        """
        `CHANGE_TOOL TOOL=`

        Start a toolchange.
        """

        active_tool = self._active_tool
        next_tool = gcmd.get_int("TOOL", minval=0, maxval=max(self.tools))

        if not self._enabled or next_tool == self._active_tool:
            return

        self._call_template("before_change_tool", gcmd=gcmd)

        self._is_toolchanging = True
        self._last_tool = self._active_tool
        self._next_tool = next_tool

        self.deactivate_tool()
        self._active_tool = None

        self._call_template("after_change_tool", gcmd=gcmd)

    def cmd_ACTIVATE_TOOL(self, gcmd: GCodeCommand):
        """
        `ACTIVATE_TOOL [TOOL=]`
        """

        next_tool = gcmd.get_int(
            "TOOL",
            self._next_tool if self._next_tool is not None else gcmd.sentinel,
            minval=min(self.tools),
            maxval=max(self.tools),
        )
        self._next_tool = next_tool

        self._call_template("before_activate_tool", gcmd=gcmd)

        if self._next_tool != self._last_tool:
            self._toolchange_count += 1

        self.activate_tool(next_tool)

        self._is_toolchanging = False
        self._active_tool = next_tool
        self._next_tool = None

        self._call_template("after_activate_tool", gcmd=gcmd)

    def cmd_DEACTIVATE_TOOL(self, _gcmd: GCodeCommand):
        """
        Deactivate the current active tool
        """

        if not self._active_tool:
            _gcmd.respond_info("No current tool to deactivate")
            return

        self.deactivate_tool()
        self._last_tool = self._active_tool
        self._next_tool = None
        self._active_tool = None
        self._is_toolchanging = False

    def cmd_SET_TOOLCHANGING_ENABLED(self, gcmd: GCodeCommand):
        """
        `SET_TOOLCHANGING_ENABLED [ENABLE=1]
        """

        enabled = bool(gcmd.get_int("ENABLE", 1))

        if self._is_toolchanging and not enabled:
            if self._last_tool is None:
                raise gcmd.error(
                    "Unable to disable toolchanging while selecting an initial tool"
                )

            self._enabled = enabled
            self.gcode.run_script_from_command(
                f"ACTIVATE_TOOL TOOL={self._last_tool}"
            )

        else:
            self._enabled = enabled
