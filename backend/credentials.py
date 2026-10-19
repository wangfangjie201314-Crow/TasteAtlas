"""Optional Windows-local DPAPI credential; cloud deployments use env only."""
import ctypes
import os
from pathlib import Path
from .qloo_client import QlooError


def local_available():
    return os.name == 'nt' and (Path.home()/'.config/TasteAtlas/qloo.dpapi').is_file()


def qloo_key(settings):
    if settings.QLOO_API_KEY:
        return settings.QLOO_API_KEY
    if not settings.LOCAL_QLOO or not local_available():
        raise QlooError('QLOO_API_KEY is not configured on the server.')
    class Blob(ctypes.Structure):
        _fields_=[('size',ctypes.c_uint32),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=(Path.home()/'.config/TasteAtlas/qloo.dpapi').read_bytes()
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    destination=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p]
    kernel.LocalFree.restype=ctypes.c_void_p
    if not crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,0,ctypes.byref(destination)):
        raise QlooError('Local Qloo credential could not be opened.')
    try:
        return ctypes.string_at(destination.data,destination.size).decode('ascii')
    finally:
        ctypes.memset(destination.data,0,destination.size)
        kernel.LocalFree(destination.data)
