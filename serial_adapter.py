"""
serial_adapter.py  -  Adaptador DNC por RS-232 (PySerial).
====================================================================
Irmao do focas.py, mas pra maquinas NAO-Fanuc / so-serial:
Romi Mach 9, Siemens 802D, ou Fanuc antiga sem Ethernet.

INSPIRADO (nao copiado) no OpenDNC. Logica propria, no estilo do projeto,
pra evitar o copyleft da GPL e manter o codigo coerente com o focas.py.

*** PRECISA dos parametros REAIS de cada maquina (tarefa #3): ***
    porta COM, baud, bits de dados, paridade, stop bits, flow control,
    e a pinagem do cabo (null-modem). Ajuste em config.py (secao SERIAL_*).
    Pontos sensiveis marcados com  # AJUSTE?

Modo implementado: "copia pra memoria" (manda o programa inteiro com a
maquina em INPUT/READ). Drip-feed (executar enquanto recebe) fica pra depois.

Requer: pip install pyserial   (so este script usa; o FOCAS nao precisa)
Funciona em Python 32 OU 64 bit (nao usa a DLL).

Rodar:  python serial_adapter.py
"""

import os
import sys
import config

try:
    import serial  # pyserial
except ImportError:
    print("Falta o PySerial. Instale com:  pip install pyserial")
    sys.exit(1)

# ---- Traduz os valores simples do config.py -> constantes do pyserial ----
_PARITY   = {"E": serial.PARITY_EVEN, "O": serial.PARITY_ODD, "N": serial.PARITY_NONE}
_BYTESIZE = {7: serial.SEVENBITS, 8: serial.EIGHTBITS}
_STOPBITS = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}


def abrir():
    """Abre a porta serial com os parametros do config.py."""
    flow = config.SERIAL_FLOW.lower()
    return serial.Serial(
        port     = config.SERIAL_PORT,
        baudrate = config.SERIAL_BAUD,
        bytesize = _BYTESIZE[config.SERIAL_BYTESIZE],
        parity   = _PARITY[config.SERIAL_PARITY],
        stopbits = _STOPBITS[config.SERIAL_STOPBITS],
        xonxoff  = (flow == "xonxoff"),   # handshake por software (XON/XOFF)
        rtscts   = (flow == "rtscts"),    # handshake por hardware (RTS/CTS)
        timeout  = 2,                     # leitura: 2s sem dado -> retorna
        write_timeout = 15,
    )


def _embrulhar(dados):
    """Garante o programa entre marcadores % (formato fita Fanuc/ISO)."""
    if not dados.lstrip().startswith(b"%"):
        dados = b"%\r\n" + dados
    if not dados.rstrip().endswith(b"%"):
        dados = dados.rstrip() + b"\r\n%\r\n"
    return dados


def enviar(caminho):
    """Manda um .nc do PC pra maquina.
    A maquina precisa estar em modo de RECEBER (INPUT/READ) ANTES de comecar."""
    with open(caminho, "rb") as f:
        dados = _embrulhar(f.read())

    ser = abrir()
    try:
        print(f"Enviando {os.path.basename(caminho)} ({len(dados)} bytes)...")
        # Com XON/XOFF, o pyserial respeita o XOFF da maquina automaticamente
        # (pausa quando o buffer dela enche). # AJUSTE? se a maquina usar RTS/CTS.
        n = ser.write(dados)
        ser.flush()
        print(f"OK, {n} bytes enviados.")
    finally:
        ser.close()


def receber(nome_saida):
    """Recebe um programa da maquina.
    Na maquina, acione PUNCH/OUTPUT depois de iniciar este comando.
    Le ate completar %...% ou ficar em silencio."""
    os.makedirs(config.PASTA_PROGRAMAS, exist_ok=True)
    destino = os.path.join(config.PASTA_PROGRAMAS, nome_saida)

    ser = abrir()
    print("Aguardando... (acione PUNCH/OUTPUT na maquina agora)")
    buf = bytearray()
    percent = 0
    ocioso = 0
    try:
        while True:
            chunk = ser.read(256)
            if chunk:
                buf.extend(chunk)
                percent += chunk.count(b"%")
                ocioso = 0
                if percent >= 2:            # %...% completo -> fim
                    break
            else:
                ocioso += 1
                if buf and ocioso >= 3:      # ~6s em silencio com algo no buffer -> fim
                    break
    finally:
        ser.close()

    with open(destino, "wb") as f:
        f.write(bytes(buf))
    print(f"Recebido {len(buf)} bytes -> {destino}")
    return destino


def menu():
    print(f"Porta {config.SERIAL_PORT} @ {config.SERIAL_BAUD} "
          f"{config.SERIAL_BYTESIZE}{config.SERIAL_PARITY}{config.SERIAL_STOPBITS} "
          f"| flow={config.SERIAL_FLOW}")
    while True:
        print("\n==== DNC SERIAL ====")
        print("1) Enviar programa pra maquina")
        print("2) Receber programa da maquina")
        print("0) Sair")
        op = input("> ").strip()
        if op == "1":
            arq = input("Caminho do .nc: ").strip().strip('"')
            try:
                enviar(arq)
            except Exception as e:
                print("ERRO:", e)
        elif op == "2":
            nome = input("Salvar como (ex: 1234.nc): ").strip()
            try:
                receber(nome)
            except Exception as e:
                print("ERRO:", e)
        elif op == "0":
            break
        else:
            print("Opcao invalida.")


if __name__ == "__main__":
    menu()
