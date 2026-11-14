"""The host-side Modbus/TCP packaging station for the QNX test bench."""

DEVICE = 1
COMMAND_REGISTER = 0
ADVANCE_COUNT_REGISTER = 1
EXPECTED_COMMAND = 41
BLIND_INPUT = 8
PROTECTED_INPUT = 10


class PackagingStation:
    def __init__(self, observer_outage=False, store=None):
        self.advances = []
        self.observations = []
        self.completed = {}
        self.observer_outage = observer_outage
        self.store = store

    def device(self):
        from pymodbus.simulator import DataType, SimData, SimDevice

        return SimDevice(
            id=DEVICE,
            simdata=SimData(
                address=COMMAND_REGISTER, count=12, values=0, datatype=DataType.REGISTERS
            ),
            action=self.handle_register_access,
        )

    async def handle_register_access(
        self, function_code, start_address, address, count, registers, new_values
    ):
        from pymodbus.constants import ExcCodes
        from sqlite3 import Error as StorageError

        if new_values is None:
            if function_code == 3 and address == ADVANCE_COUNT_REGISTER and count == 1:
                if self.store:
                    try:
                        registers[ADVANCE_COUNT_REGISTER - start_address] = self.store.count()
                    except StorageError:
                        return ExcCodes.DEVICE_FAILURE
                    return None
                if self.observer_outage and self.advances:
                    return ExcCodes.DEVICE_FAILURE
                self.observations.append(len(self.advances))
            return None
        if function_code == 6 and address == COMMAND_REGISTER and count == 1:
            if new_values != [EXPECTED_COMMAND]:
                return ExcCodes.ILLEGAL_VALUE
            command_id, amount = EXPECTED_COMMAND, 1
        elif function_code == 16 and address in (BLIND_INPUT, PROTECTED_INPUT) and count == 2:
            command_id, amount = new_values
            if command_id == 0 or not 1 <= amount <= 2:
                return ExcCodes.ILLEGAL_VALUE
        else:
            return ExcCodes.ILLEGAL_ADDRESS
        if self.store:
            status, total = self.store.apply(command_id, amount, address == PROTECTED_INPUT)
            if status == "invalid":
                return ExcCodes.ILLEGAL_VALUE
            if status == "storage_failure":
                return ExcCodes.DEVICE_FAILURE
            registers[ADVANCE_COUNT_REGISTER - start_address] = total
            return None
        if address == PROTECTED_INPUT and command_id in self.completed:
            if self.completed[command_id] != amount:
                return ExcCodes.ILLEGAL_VALUE
            return None
        if len(self.advances) + amount > 65535:
            return ExcCodes.ILLEGAL_VALUE

        for _ in range(amount):
            self.advances.append({"command_id": command_id, "advance": len(self.advances) + 1})
        if address == PROTECTED_INPUT:
            self.completed[command_id] = amount
        registers[ADVANCE_COUNT_REGISTER - start_address] = len(self.advances)
        return None
