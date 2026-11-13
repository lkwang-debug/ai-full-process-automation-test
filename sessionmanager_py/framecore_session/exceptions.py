class FramecoreError(Exception):
    pass


class ProtocolError(FramecoreError):
    pass


class SecurityError(FramecoreError):
    pass


class PayloadTooLargeError(FramecoreError):
    pass


class SessionStateError(FramecoreError):
    pass
