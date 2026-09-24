"""Run with python -m examples.simulator_demo from the project root."""

from qpx_electrolyser.instrument.base_power_supply import PowerSupplyInterface
from qpx_electrolyser.instrument.simulator import SimulatedPowerSupply


def show_measurements(supply: PowerSupplyInterface, channel: int) -> None:
    state = "ON" if supply.read_output_state(channel) else "OFF"
    print(
        f"CH{channel} {state:3s} | {supply.read_voltage(channel):.2f} V | "
        f"{supply.read_current(channel):.2f} A"
    )


def main() -> None:
    supply: PowerSupplyInterface = SimulatedPowerSupply()
    supply.connect()
    try:
        print(supply.identify())
        supply.set_voltage(1, 3.0)
        supply.set_current(1, 10.0)
        supply.output_on(1)
        supply.set_current(2, 20.0)
        supply.set_voltage(2, 2.2)
        supply.output_on(2)
        show_measurements(supply, 1)
        show_measurements(supply, 2)
        supply.output_off(1)
        print("After switching CH1 off:")
        show_measurements(supply, 1)
        show_measurements(supply, 2)
    finally:
        # Backend disconnect is not shutdown. Make each OFF attempt independently.
        try:
            supply.output_off(1)
        finally:
            try:
                supply.output_off(2)
            finally:
                supply.disconnect()


if __name__ == "__main__":
    main()
