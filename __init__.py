def classFactory(iface):
    from .sls_change_detector import SLSChangeDetectorPlugin
    return SLSChangeDetectorPlugin(iface)