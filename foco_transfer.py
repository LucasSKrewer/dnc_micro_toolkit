"""
foco_transfer.py  -  FASE 4: passar e receber programas.
====================================================================
Menu simples pra:
  1) RECEBER  um programa do CNC  -> salva em programas/<num>.nc
  2) ENVIAR   um .nc do PC        -> grava na memoria do CNC

*** ATENCAO: esta e a parte mais propensa a AJUSTE FINO NA MAQUINA. ***
As funcoes de transferencia do FOCAS tem variacoes de "type"/formato
conforme a versao do controle. O esqueleto abaixo segue a especificacao,
mas talvez precise de pequenos ajustes quando rodar de verdade
(estao marcados com  # AJUSTE? ).

Rodar (cmd, na pasta):  py -3-32 foco_transfer.py
"""

import ctypes
import os
import config
import focas

lib = focas.lib

# ---- Assinaturas das funcoes de transferencia ----
lib.cnc_upstart4.argtypes = [ctypes.c_ushort, ctypes.c_short, ctypes.c_char_p]
lib.cnc_upstart4.restype  = ctypes.c_short
lib.cnc_upload4.argtypes  = [ctypes.c_ushort, ctypes.POINTER(ctypes.c_long), ctypes.c_char_p]
lib.cnc_upload4.restype   = ctypes.c_short
lib.cnc_upend4.argtypes   = [ctypes.c_ushort]
lib.cnc_upend4.restype    = ctypes.c_short

lib.cnc_dwnstart4.argtypes  = [ctypes.c_ushort, ctypes.c_short]   # AJUSTE? algumas versoes pedem (h, type, path)
lib.cnc_dwnstart4.restype   = ctypes.c_short
lib.cnc_download4.argtypes  = [ctypes.c_ushort, ctypes.POINTER(ctypes.c_long), ctypes.c_char_p]
lib.cnc_download4.restype   = ctypes.c_short
lib.cnc_dwnend4.argtypes    = [ctypes.c_ushort]
lib.cnc_dwnend4.restype     = ctypes.c_short


def receber(h, numero_o):
    """Le um programa do CNC e salva em programas/<numero>.nc"""
    prog = numero_o.encode()
    ret = lib.cnc_upstart4(h, 0, prog)        # AJUSTE? type 0; formato "O1234"
    if ret != focas.EW_OK:
        raise RuntimeError(f"cnc_upstart4 falhou (codigo {ret})")

    pedacos = []
    buf = ctypes.create_string_buffer(1290)
    try:
        while True:
            length = ctypes.c_long(1280)
            ret = lib.cnc_upload4(h, ctypes.byref(length), buf)
            if ret == focas.EW_BUFFER:
                continue                        # ainda nao tem dado -> tenta de novo
            if ret != focas.EW_OK:
                break
            chunk = buf.raw[:length.value]
            pedacos.append(chunk)
            if chunk.rstrip().endswith(b"%"):   # "%" marca o fim do programa
                break
    finally:
        lib.cnc_upend4(h)

    os.makedirs(config.PASTA_PROGRAMAS, exist_ok=True)
    nome = numero_o.lstrip("Oo") + ".nc"
    destino = os.path.join(config.PASTA_PROGRAMAS, nome)
    with open(destino, "wb") as f:
        f.write(b"".join(pedacos))
    return destino


def enviar(h, caminho):
    """Envia um arquivo .nc do PC pra memoria do CNC."""
    with open(caminho, "rb") as f:
        dados = f.read()

    # garante o "embrulho" % ... % (formato fita Fanuc)
    if not dados.lstrip().startswith(b"%"):
        dados = b"%\n" + dados
    if not dados.rstrip().endswith(b"%"):
        dados = dados.rstrip() + b"\n%\n"

    ret = lib.cnc_dwnstart4(h, 0)             # AJUSTE? type; maquina precisa estar em EDIT
    if ret != focas.EW_OK:
        raise RuntimeError(
            f"cnc_dwnstart4 falhou (codigo {ret}). "
            "Maquina em modo EDIT? Protecao de programa desligada?"
        )

    try:
        i = 0
        while i < len(dados):
            pedaco = dados[i:i + 256]
            length = ctypes.c_long(len(pedaco))
            ret = lib.cnc_download4(h, ctypes.byref(length), pedaco)
            if ret == focas.EW_BUFFER:
                continue
            if ret != focas.EW_OK:
                raise RuntimeError(f"cnc_download4 falhou (codigo {ret})")
            i += len(pedaco)
    finally:
        lib.cnc_dwnend4(h)


def menu():
    print("Conectando em", config.CNC_IP, "...")
    h = focas.conectar()
    print("Conectado.")
    try:
        while True:
            print("\n==== TRANSFERENCIA ====")
            print("1) Receber programa do CNC")
            print("2) Enviar programa pro CNC")
            print("0) Sair")
            op = input("> ").strip()
            if op == "1":
                num = input("Numero do programa (ex: O1234): ").strip()
                try:
                    dest = receber(h, num)
                    print("OK! Salvo em:", dest)
                except Exception as e:
                    print("ERRO:", e)
            elif op == "2":
                arq = input("Caminho do arquivo .nc: ").strip().strip('"')
                try:
                    enviar(h, arq)
                    print("OK! Enviado pro CNC.")
                except Exception as e:
                    print("ERRO:", e)
            elif op == "0":
                break
            else:
                print("Opcao invalida.")
    finally:
        focas.desconectar(h)
        print("Desconectado.")


if __name__ == "__main__":
    menu()
