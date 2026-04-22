"""
SAPCFG: retained as a data-access module only.

The standalone SAPCFG UI has been retired — SAP configuration is now managed
from the Admin panel (/admin/ → SAP Config tab). This module still exposes
`model.get_active_config()` which is imported by sap_builder / sap_client.
"""
