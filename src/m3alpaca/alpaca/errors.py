"""ASCOM Alpaca error numbers (ASCOM.ErrorCodes)."""

NOT_IMPLEMENTED = 0x400
INVALID_VALUE = 0x401
VALUE_NOT_SET = 0x402
NOT_CONNECTED = 0x407
INVALID_OPERATION = 0x40B
ACTION_NOT_IMPLEMENTED = 0x40C
UNSPECIFIED = 0x500


class AlpacaError(Exception):
    def __init__(self, number: int, message: str):
        super().__init__(message)
        self.number = number
        self.message = message


def not_implemented(member: str) -> AlpacaError:
    return AlpacaError(NOT_IMPLEMENTED, f"{member} is not implemented by this camera")
