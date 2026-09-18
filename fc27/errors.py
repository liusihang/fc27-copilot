class FC27Error(Exception):
    def __init__(self, code, message, *, retryable=False, recovery=None, details=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.recovery = recovery
        self.details = details

    def as_dict(self):
        error = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.recovery:
            error["recovery"] = self.recovery
        if self.details is not None:
            error["details"] = self.details
        return error
