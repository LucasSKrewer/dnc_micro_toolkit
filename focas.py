"""
focas.py  -  Wrapper minimo da biblioteca FOCAS (Fanuc) via ctypes.
====================================================================
Esta e a FUNDACAO reutilizavel do projeto: carrega a DLL, define as
estruturas e oferece helpers de conexao. Os outros scripts importam daqui.

Requer: Python 32-bit + Fwlib32.dll (e DLLs companheiras) na mesma pasta.
"""

import ctypes
import os
import sys
import config

# ---- Carrega a DLL da pasta do projeto ----
_DLL = os.path.join(config.PASTA_BASE, config.NOME_DLL)
os.environ["PATH"] = config.PASTA_BASE + os.pathsep + os.environ.get("PATH", "")

try:
    lib = ctypes.windll.LoadLibrary(_DLL)   # windll = stdcall (correto p/ FOCAS)
except OSError as e:
    print("ERRO ao carregar", config.NOME_DLL)
    print("  ", e)
    print("Cheque: a DLL (e as companheiras) estao na pasta? O Python e 32-bit?")
    sys.exit(1)

# ---- Codigos de retorno comuns do FOCAS ----
EW_OK     = 0    # sucesso
EW_BUFFER = 10   # buffer cheio/vazio -> tentar de novo (nao e erro fatal)


# ---- Estrutura ODBSYS (retorno do cnc_sysinfo) ----
# OBS: o layout pode variar levemente conforme a versao do fwlib.
class ODBSYS(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("addinfo",  ctypes.c_short),
        ("max_axis", ctypes.c_char),
        ("cnc_type", ctypes.c_char * 2),   # ex: "30" / "0i"
        ("mt_type",  ctypes.c_char * 2),   # "M"=fresa/centro, "T"=torno
        ("series",   ctypes.c_char * 4),
        ("version",  ctypes.c_char * 4),
        ("axes",     ctypes.c_char * 2),
    ]


# ---- Assinaturas (boa pratica: evita bug de tipo no ctypes) ----
lib.cnc_allclibhndl3.argtypes = [
    ctypes.c_char_p, ctypes.c_ushort, ctypes.c_long,
    ctypes.POINTER(ctypes.c_ushort),
]
lib.cnc_allclibhndl3.restype = ctypes.c_short

lib.cnc_freelibhndl.argtypes = [ctypes.c_ushort]
lib.cnc_freelibhndl.restype  = ctypes.c_short

lib.cnc_sysinfo.argtypes = [ctypes.c_ushort, ctypes.POINTER(ODBSYS)]
lib.cnc_sysinfo.restype  = ctypes.c_short


# ---- Helpers de alto nivel ----
def conectar():
    """Abre conexao com a maquina. Devolve o handle (c_ushort)."""
    h = ctypes.c_ushort(0)
    ip = config.CNC_IP.encode()
    ret = lib.cnc_allclibhndl3(ip, config.CNC_PORT, config.TIMEOUT, ctypes.byref(h))
    if ret != EW_OK:
        raise RuntimeError(
            f"Falha ao conectar (codigo {ret}). "
            "Confira IP/porta, o ping e se a opcao FOCAS esta liberada na maquina."
        )
    return h


def desconectar(h):
    """Libera o handle (sempre chame no final)."""
    lib.cnc_freelibhndl(h)


def sysinfo(h):
    """Le a identidade da maquina (ODBSYS)."""
    s = ODBSYS()
    ret = lib.cnc_sysinfo(h, ctypes.byref(s))
    if ret != EW_OK:
        raise RuntimeError(f"cnc_sysinfo falhou (codigo {ret})")
    return s
