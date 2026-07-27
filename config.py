# config.py - Configuracoes centrais do projeto.
# EDITE SO ESTE ARQUIVO. Os scripts leem os valores daqui.

import os

# ---------- Maquina (Fanuc, via FOCAS) ----------
CNC_IP   = "192.168.1.50"   # IP que voce viu na tela da maquina (Fase 1)
CNC_PORT = 8193             # porta FOCAS (padrao Fanuc; confirme em [FOCAS2])
TIMEOUT  = 10                # timeout de conexao, em segundos

# ---------- Pastas / DLL ----------
PASTA_BASE      = os.path.dirname(os.path.abspath(__file__))
PASTA_PROGRAMAS = os.path.join(PASTA_BASE, "programas")  # onde ficam os .nc
NOME_DLL        = "Fwlib32.dll"                          # coloque-a na PASTA_BASE

# ---------- Maquina serial (Romi, Siemens 802D, Fanuc antiga sem Ethernet) ----------
# Ajuste conforme os parametros REAIS da sua maquina (tela RS232 do controle).
# Se o cabo usado for o mesmo kit da caixinha DNC, o handshake normalmente
# ja vem em curto no lado da maquina -> controle de fluxo por software (XON/XOFF).
#
# NO RASPBERRY PI a porta do FTDI eh "/dev/ttyUSB0".
# NO WINDOWS (se testar no PC antes) eh "COMx" (ver Gerenciador de Dispositivos).
SERIAL_PORT     = "/dev/ttyUSB0"  # Pi: /dev/ttyUSB0  |  Windows: "COM3" etc.
SERIAL_BAUD     = 9600
SERIAL_BYTESIZE = 7
SERIAL_PARITY   = "E"         # "E"=even, "O"=odd, "N"=none
SERIAL_STOPBITS = 2
SERIAL_FLOW     = "xonxoff"   # "xonxoff" (software) ou "rtscts" (hardware)

# ---------- DNC box (Micro DNC 2) - protocolo TFTP customizado na rede ----------
# Descoberto por engenharia reversa do QSExplorer 4.06 (ver dnc_tftp.py).
DNC_IP   = "192.168.1.236"   # troque pelo IP real da sua caixinha DNC
DNC_PORT = 69                # TFTP (DEFAULT_PORT)

# ---------- Web (dnc_web.py) - automacao envio/recebimento ----------
# Modelo sugerido: envio = "arquivo fixo" (o operador so aperta "Enviar", quem
# troca o arquivo antes de cada peca nova e o PCP/Engenharia); recebimento =
# automatico, sempre escutando em background.
ARQUIVO_FIXO_ENVIO = "1"     # nome (sem extensao fixa) do programa "da vez" em PASTA_PROGRAMAS
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000

# Identificador desta maquina (cada Pi fica ligado numa unica maquina, entao
# isto e fixo por instalacao) - entra no nome do arquivo recebido automaticamente.
MAQUINA_NOME = "MAQUINA"
