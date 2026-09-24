"""
Klipper plugin to monitor toolhead adc values
to detect the toolhead's attachment status
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .homing import CocoaHoming
from .load_wizard import CocoaLoadWizard
from .memory import CocoaMemory
from .nozzle_offsets import CocoaNozzleOffsets
from .preheater import CocoaPreheater
from .runout import CocoaRunout

if TYPE_CHECKING:
    from ...configfile import ConfigWrapper
    from ...gcode import GCodeCommand, GCodeDispatch
    from ...kinematics.extruder import PrinterExtruder
    from ...mcu import MCU_adc
    from ...printer import Printer
    from ...toolhead import ToolHead
    from ..adc_temperature import PrinterADCtoTemperature
    from ..gcode_macro import PrinterGCodeMacro
    from ..heaters import Heater


# Open circuits on the ADC cause a near 1.0 reading, typically above 0.998
OPEN_ADC_VALUE = 0.99


DEFAULT_EXTRUDER = "extruder"
DEFAULT_BODY_HEATER = "heater_generic Body_Heater"


class CocoaToolheadControl:
    printer: Printer
    config: ConfigWrapper

    runout: CocoaRunout
    memory: CocoaMemory
    load_wizard: CocoaLoadWizard

    def __init__(self, config: ConfigWrapper):
        self.printer = config.get_printer()
        self.config = config
        self.name = self.config.get_name().split()[-1]
        self.mux_name = self.name if self.name != "cocoa_toolhead" else None

        self.logger = logging.getLogger(__name__).getChild(
            self.name
            if self.name == "cocoa_toolhead"
            else f"cocoa_toolhead[{self.name}]"
        )

        self.extruder_name = config.get(
            "extruder",
            default=DEFAULT_EXTRUDER,
        )
        self.body_heater_name = config.get(
            "body_heater",
            default=DEFAULT_BODY_HEATER,
        )

        self.homing = CocoaHoming(self, config)
        self.load_wizard = CocoaLoadWizard(self, config)
        self.runout = CocoaRunout(self, config)
        self.memory = CocoaMemory(self, config)
        self.preheater = CocoaPreheater(self, config)
        self.nozzle_offsets = CocoaNozzleOffsets(self, config)

        self.attached = None
        self.last_readings = {}
        self.calibration_required = False

        self.printer.register_event_handler("klippy:connect", self._on_connect)
        self.printer.register_event_handler(
            "extruder:activate_extruder", self._extruder_activated
        )

        self.printer.register_event_handler(
            f"cocoa_preheater:{self.name}:start", self._preheater_started
        )
        self.printer.register_event_handler(
            f"cocoa_preheater:{self.name}:update", self._preheater_update
        )
        self.printer.register_event_handler(
            f"cocoa_preheater:{self.name}:stop", self._preheater_stopped
        )
        self.gcode: GCodeDispatch = self.printer.lookup_object("gcode")

        # register commands
        self.gcode.register_mux_command(
            "SET_COCOA_TOOLHEAD",
            "TOOL",
            self.mux_name,
            self.cmd_SET_COCOA_TOOLHEAD,
        )

        gcode_macro: PrinterGCodeMacro = self.printer.load_object(
            config, "gcode_macro"
        )
        self.attach_tmpl = gcode_macro.load_template(config, "attach_gcode", "")
        self.detach_tmpl = gcode_macro.load_template(config, "detach_gcode", "")

    def _on_connect(self):
        self.logger.info("Initializing Cocoa Toolhead")

        self.attached = None

        extruder: PrinterExtruder = self.printer.lookup_object(
            self.extruder_name
        )
        body_heater = self.printer.lookup_object(self.body_heater_name)

        self.logger.debug("Injecting adc callbacks")

        self.inject_adc_callback(extruder.heater)
        self.inject_adc_callback(body_heater)

        self.inject_temp_callback("extruder", extruder.heater)
        self.inject_temp_callback("body", body_heater)

    def _preheater_started(self, profile):
        if not (self.memory.connected):
            return
        self.memory.set("profile", profile)
        self._preheater_update()

    def _preheater_update(self):
        if not self.memory.connected:
            return
        self.memory.set(
            "preheater",
            {
                "status": "preheating",
                "remaining": int(self.preheater.time_remaining),
            },
        )

    def _preheater_stopped(self, profile, reason):
        if not self.memory.connected:
            return
        self.memory.set(
            "preheater",
            {
                "status": "stopped",
                "reason": reason,
                "remaining": int(self.preheater.time_remaining),
            },
        )

    def inject_adc_callback(self, heater: Heater):
        sensor: PrinterADCtoTemperature = heater.sensor
        mcu_adc: MCU_adc = sensor.mcu_adc
        verify_heater = self.printer.lookup_object(
            f"verify_heater {heater.short_name}"
        )

        def new_callback(read_time, read_value):
            if read_value < OPEN_ADC_VALUE:
                sensor.adc_callback(read_time, read_value)
            else:
                heater.set_pwm(read_time, 0.0)
                verify_heater.error = 0.0
            self.receive_sensor_value(heater, read_value)

        setattr(mcu_adc, "_callback", new_callback)
        self.logger.debug(
            f"{self.name}: Intercepted ADC callback for {heater.name}"
        )

    def inject_temp_callback(self, name: str, heater: Heater):
        orig_set_temp = heater.set_temp

        def set_temp(degrees):
            orig_set_temp(degrees)
            if self.memory.connected:
                self.memory.setdefault("heaters", {})[name] = heater.target_temp

        heater.set_temp = set_temp

    def receive_sensor_value(self, heater, value: float):
        toolhead: ToolHead = self.printer.lookup_object("toolhead")
        extruder: PrinterExtruder = self.printer.lookup_object(
            self.extruder_name
        )

        self.last_readings[heater.name] = value

        self.trigger_attach_event(
            toolhead.get_extruder() is extruder and value < OPEN_ADC_VALUE
        )

    def _extruder_activated(self):
        toolhead: ToolHead = self.printer.lookup_object("toolhead")
        extruder: PrinterExtruder = self.printer.lookup_object(
            self.extruder_name
        )

        print(
            f"cocoa_toolhead:{self.mux_name} extruder activated {toolhead.get_extruder().get_name()}"
        )

        self.trigger_attach_event(toolhead.get_extruder() is extruder)

    def trigger_attach_event(self, is_attached: bool):
        if is_attached == self.attached:
            return

        self.attached = is_attached

        self.gcode.respond_info(
            f"Cocoa Press: Toolhead {self.name} {'attached' if is_attached else 'detached'}"
        )

        if is_attached:
            self.printer.send_event(f"cocoa_toolhead:{self.name}:attached")
            self.attach_tmpl()
        else:
            self.printer.send_event(f"cocoa_toolhead:{self.name}:detached")
            self.detach_tmpl()

    def get_status(self, eventtime):
        return {
            **self.load_wizard.get_status(eventtime),
            "attached": self.attached,
            "adc": self.last_readings,
            "calibration_required": self.calibration_required,
            "runout": self.runout.get_status(eventtime),
            "memory": self.memory.get_status(eventtime),
            "offsets": self.nozzle_offsets.get_status(eventtime),
            "preheater": self.preheater.get_status(eventtime),
        }

    # For updating user values (name of the toolhead)
    def cmd_SET_COCOA_TOOLHEAD(self, gcmd: GCodeCommand):
        """
        `SET_COCOA_TOOLHEAD NAME="my fancy toolhead"`

        Update Cocoa Toolhead settings
        """
        if not self.memory.connected:
            raise gcmd.error(
                "Unable to update toolhead memory when it's not connected"
            )

        new_name = gcmd.get("NAME", None)
        if new_name is not None:
            self.memory.set("name", new_name)
            gcmd.respond_info(f"{self.name}: Set name to {new_name}")
