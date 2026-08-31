from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...configfile import ConfigWrapper, PrinterConfig
    from ...gcode import GCodeCommand, GCodeDispatch
    from ...printer import Printer
    from ..gcode_move import GCodeMove
    from ..probe import PrinterProbe
    from ..save_variables import SaveVariables
    from .toolhead import CocoaToolheadControl


class CocoaNozzleOffsets:
    cocoa_toolhead: CocoaToolheadControl
    printer: Printer

    gcode: GCodeDispatch
    gcode_move: GCodeMove
    save_variables: SaveVariables

    def __init__(
        self, cocoa_toolhead: CocoaToolheadControl, config: ConfigWrapper
    ):
        self.cocoa_toolhead = cocoa_toolhead
        self.name = cocoa_toolhead.name
        self.mux_name = cocoa_toolhead.mux_name
        self.logger = cocoa_toolhead.logger.getChild("offsets")

        self.printer = config.get_printer()
        self.config = config

        self.gcode = self.printer.lookup_object("gcode")
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self.save_variables = self.printer.load_object(config, "save_variables")

        self._prefix = (
            f"z_offset_{self.mux_name}_" if self.mux_name else "z_offset_"
        )
        self._current_tool = None
        self._current_offset = 0.0

        self.gcode.register_mux_command(
            "SET_NOZZLE_OFFSET",
            "TOOL",
            self.mux_name,
            self.cmd_SET_NOZZLE_OFFSET,
        )
        self.printer.register_event_handler(
            f"cocoa_toolhead:{self.name}:detached", self._on_detach
        )
        self.printer.register_event_handler(
            f"cocoa_toolhead:{self.name}:attached", self._on_attach
        )

        self.printer.register_event_handler(
            "klippy:ready", self._on_ready_PROBE_OFFSET_HACK
        )

    def _on_ready_PROBE_OFFSET_HACK(self):
        pconfig: PrinterConfig = self.printer.lookup_object("configfile")
        probe: PrinterProbe = self.printer.lookup_object("probe")
        probe_config = self.config.getsection("probe")

        if (z_offset := probe_config.getfloat("z_offset")) != 0.0:
            self.save_variables.save(f"{self._prefix}generic", z_offset)
            probe.mcu_probe.position_endstop = 0.0
            pconfig.set("probe", "z_offset", "0.0")
            self.gcode.run_script_from_command("SAVE_CONFIG RELOAD=0")

    def _on_attach(self):
        self._current_tool = "generic"
        self._current_offset = self.save_variables.allVariables.get(
            f"{self._prefix}{self._current_tool}", 0.0
        )
        self.gcode.run_script_from_command(
            f"SET_GCODE_OFFSET Z_ADJUST={self._current_offset}"
        )

    def _on_detach(self):
        self.gcode.run_script_from_command(
            f"SET_GCODE_OFFSET Z_ADJUST={-self._current_offset}"
        )
        self._current_offset = 0.0
        self._current_tool = None

    def get_status(self, _eventtime):
        gcode_offset = self.gcode_move.homing_position[2]
        return {
            "babystep": gcode_offset - self._current_offset,
            "current": self._current_offset,
            "saved": {
                k.removeprefix(self._prefix): v
                for k, v in self.save_variables.allVariables.items()
                if k.startswith(self._prefix)
            },
        }

    def cmd_SET_NOZZLE_OFFSET(self, cmd: GCodeCommand):
        uid = cmd.get("UID", "generic")
        offset = cmd.get_float("OFFSET", self.gcode_move.homing_position[2])

        if uid == "current":
            if self._current_tool is None:
                raise cmd.error(
                    "Unable to set offset for current tool when no tool is attached"
                )
            uid = self._current_tool

        self.save_variables.save(f"{self._prefix}{uid}", round(offset, 4))
        if self._current_tool == uid:
            self._current_offset = offset
