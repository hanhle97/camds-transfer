class CamdsError(RuntimeError):
    """Base CAMDS operation error."""


class CamdsNavigationError(CamdsError):
    pass


class CamdsElementNotFound(CamdsError):
    pass


class CamdsSessionExpired(CamdsError):
    pass
