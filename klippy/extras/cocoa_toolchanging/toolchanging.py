"""
Singleton plugin for multi-toolhead Cocoa Press 2
"""

from __future__ import annotations

import typing

from klippy.kinematics.extruder import DummyExtruder

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

    tools: dict[int, CocoaToolheadControl]
    active_tool: CocoaToolheadControl

    _templates: dict[str, Template]

    _is_toolchanging: bool
    _toolchange_count: int
    _active_tool: int | None
    _last_tool: int | None
    _next_tool: int | None
    _tool_names: dict[int, str]

    def __init__(self, config: ConfigWrapper):
        self.config = config
        self.printer = config.get_printer()

        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_move = self.printer.load_object(config, "gcode_move")
        self.gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.virtual_sdcard = self.printer.load_object(config, "virtual_sdcard")

        self.printer.load_object(config, "tool_offsets")

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

        # status fields
        self._is_toolchanging = False
        self._active_tool = None
        self._last_tool = None
        self._next_tool = None
        self._toolchange_count = 0
        self._tool_names = {}

        self.tools = {}
        self.active_tool = None

        tool_names = config.getlist("tools")
        for tool_index, tool_name in enumerate(tool_names):
            cocoa_toolhead = self.printer.load_object(config, tool_name)
            self.tools[tool_index] = cocoa_toolhead
            self._tool_names[tool_index] = tool_name

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

    def _reset_print_stats(self):
        self._toolchange_count = 0

    def deactivate_tool(self):
        toolhead: ToolHead = self.printer.lookup_object("toolhead")

        if not self._active_tool:
            return

        self._last_tool = self._active_tool
        self._active_tool = None
        self.active_tool = None

        current_extruder = toolhead.get_extruder()
        if not isinstance(current_extruder, DummyExtruder):
            toolhead.flush_step_generation()
            toolhead.set_extruder(self.dummy_extruder, 0.0)

        self.printer.send_event("cocoa_toolchanging:tool_activated", None)

    def activate_tool(self, tool_index: int):
        toolhead: ToolHead = self.printer.lookup_object("toolhead")
        tool = self.tools[tool_index]

        current_extruder = toolhead.get_extruder()
        if current_extruder is not tool.extruder:
            toolhead.flush_step_generation()
            toolhead.set_extruder(tool.extruder, tool.extruder.last_position)

        self.active_tool = tool
        self._active_tool = tool_index

        self.printer.send_event("cocoa_toolchanging:tool_activated", tool)

    def get_status(self, _eventtime=None):
        return {
            "enabled": self._enabled,
            "is_toolchanging": self._is_toolchanging,
            "active_tool": self._active_tool,
            "next_tool": self._next_tool,
            "last_tool": self._last_tool,
            "toolchange_count": self._toolchange_count,
            "tools": self._tool_names,
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

        self._next_tool = None
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
