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

        self._variable_name = f"z_offset_{self.name}"
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

        if self.mux_name is None:
            self.printer.register_event_handler(
                "klippy:ready", self._on_ready_PROBE_OFFSET_HACK
            )

    def _on_ready_PROBE_OFFSET_HACK(self):
        pconfig: PrinterConfig = self.printer.lookup_object("configfile")
        probe: PrinterProbe = self.printer.lookup_object("probe")
        probe_config = self.config.getsection("probe")

        if (z_offset := probe_config.getfloat("z_offset")) != 0.0:
            # A probe z_offset of N is equivalent to a gcode z offset of -N
            self.save_variables.save(self._variable_name, -z_offset)
            probe.mcu_probe.position_endstop = 0.0
            pconfig.set("probe", "z_offset", "0.0")
            self.gcode.run_script_from_command("SAVE_CONFIG RELOAD=0")

        elif z_offset := self.save_variables.allVariables.pop(
            "z_offset_generic", None
        ):
            self.save_variables.save(self._variable_name, z_offset)

    def _on_attach(self):
        self._current_offset = self.save_variables.allVariables.get(
            self._variable_name, 0.0
        )
        self.gcode.run_script_from_command(
            f"SET_GCODE_OFFSET Z_ADJUST={self._current_offset}"
        )

    def _on_detach(self):
        self.gcode.run_script_from_command(
            f"SET_GCODE_OFFSET Z_ADJUST={-self._current_offset}"
        )
        self._current_offset = 0.0

    def get_status(self, _eventtime):
        gcode_offset = self.gcode_move.homing_position[2]
        return {
            "babystep": gcode_offset - self._current_offset,
            "current": self._current_offset,
            "saved": self.save_variables.allVariables.get(
                self._variable_name, 0.0
            ),
        }

    def cmd_SET_NOZZLE_OFFSET(self, cmd: GCodeCommand):
        offset = cmd.get_float("OFFSET", self.gcode_move.homing_position[2])

        self.save_variables.save(f"{self._variable_name}", round(offset, 4))
        self._current_offset = offset
