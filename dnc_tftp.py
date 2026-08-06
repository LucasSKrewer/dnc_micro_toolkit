"""
dnc_tftp.py  -  Cliente do Micro DNC 2 pela REDE (protocolo TFTP customizado).
====================================================================
Irmao do focas.py e do serial_adapter.py, pro caminho WiFi: falar DIRETO com a
caixinha DNC, sem depender do QSExplorer (o "Caminho B").

>>> PROTOCOLO MAPEADO BYTE A BYTE por engenharia reversa do QSExplorer 4.06
    (aplicativo .NET oficial do fabricante): executei os construtores de
    pacote via reflection e capturei os bytes reais. Formato abaixo esta
    CONFIRMADO. <<<

FORMATO DO FIO:
  - Opcode: 2 bytes big-endian no inicio de TODO pacote.
  - Comando com caminho: [opcode][ASCII][0x00]   (Rename = duas strings 0x00).
  - Status/Info: [opcode][0x00 0x00].   Stop: [opcode][0x00].
  - Bloco (DATA/ACK/RequestData): [opcode][bloco: 4 bytes big-endian].
  - RRQ/WRQ: [opcode][nome do arquivo][0x00]  (sem o campo 'mode' do TFTP padrao).
  - DATA: [00 03][bloco:4][ate 512 bytes de dados].

OPCODES DE REQUEST (cliente -> aparelho), capturados:
  RRQ=1  WRQ=2  DATA=3  ACK=4  ReadDir=8  NewFolder=10  DeleteFile=12
  DeleteFolder=14  Rename=16  GetInfo=19  SendMsg=21  StartUpCopy=23
  RunFile=25  OpenDir=27  StopDnc=28  GetStatus=50  RequestData=56

Rede: UDP porta 69, bloco 512, timeout 2s, ate 10 reenvios.

STATUS: os PACOTES estao exatos. A SEQUENCIA (handshake) de upload/download
- ordem e numeracao inicial de bloco - merece 1 teste AO VIVO contra o aparelho.
Marcado com  # VALIDAR AO VIVO.  Sem dependencia externa (socket puro).
"""

import socket
import struct
import os
import config

DNC_IP   = getattr(config, "DNC_IP", "192.168.1.236")
DNC_PORT = getattr(config, "DNC_PORT", 69)
BLOCK    = 512
TIMEOUT  = 2
RESENDS  = 10

# Opcodes de request (cliente -> aparelho)
OP_RRQ = 1; OP_WRQ = 2; OP_DATA = 3; OP_ACK = 4
OP_READDIR = 8; OP_NEWFOLDER = 10; OP_DELFILE = 12; OP_DELFOLDER = 14
OP_RENAME = 16; OP_GETINFO = 19; OP_SENDMSG = 21; OP_STARTUPCOPY = 23
OP_RUNFILE = 25; OP_OPENDIR = 27; OP_STOPDNC = 28; OP_GETSTATUS = 50
OP_REQDATA = 56


def _op(code):  return struct.pack(">H", code)          # opcode = 2 bytes BE
def _blk(n):    return struct.pack(">I", n)             # bloco  = 4 bytes BE
def _cmd(code, texto):
    return _op(code) + texto.encode("ascii", "replace") + b"\x00"


# ---- Builders (EXATOS, conferidos com o binario) ----
def pkt_status():        return _op(OP_GETSTATUS) + b"\x00\x00"
def pkt_info():          return _op(OP_GETINFO) + b"\x00\x00"
def pkt_stop():          return _op(OP_STOPDNC) + b"\x00"
def pkt_readdir(path="\\"):  return _cmd(OP_READDIR, path)
def pkt_opendir(path):       return _cmd(OP_OPENDIR, path)
def pkt_run(path):           return _cmd(OP_RUNFILE, path)
def pkt_msg(texto):          return _cmd(OP_SENDMSG, texto)
def pkt_newfolder(path):     return _cmd(OP_NEWFOLDER, path)
def pkt_delfile(path):       return _cmd(OP_DELFILE, path)
def pkt_delfolder(path):     return _cmd(OP_DELFOLDER, path)
def pkt_rename(old, new):
    return (_op(OP_RENAME) + old.encode("ascii", "replace") + b"\x00"
            + new.encode("ascii", "replace") + b"\x00")
def pkt_rrq(name):           return _cmd(OP_RRQ, name)
def pkt_wrq(name):           return _cmd(OP_WRQ, name)
def pkt_ack(block):          return _op(OP_ACK) + _blk(block)
def pkt_data(block, data):   return _op(OP_DATA) + _blk(block) + data
def pkt_reqdata(block):      return _op(OP_REQDATA) + _blk(block)


def opcode_de(resp):
    return struct.unpack(">H", resp[:2])[0] if resp and len(resp) >= 2 else None


def parse_status(resp):
    """Status (resp op 51): [00 33][9 bytes 00][ 'IP|texto' 00 ]. Devolve o texto.
    Ex ao vivo: '192.168.1.236|No File Selected'. (confirmado ao vivo)"""
    if not resp or opcode_de(resp) != OP_GETSTATUS + 1:
        return None
    return resp[11:].split(b"\x00")[0].decode("ascii", "replace")


def parse_info(resp):
    """Info (resp op 20): [00 14][4 bytes 00]['MICRO DNC2' 00]. Devolve o modelo."""
    if not resp or opcode_de(resp) != OP_GETINFO + 1:
        return None
    return resp[6:].split(b"\x00")[0].decode("ascii", "replace")


def open_dir(path):
    """Abre um diretorio (ex '0:program' - SEM barra). Ack = opcode 7. (confirmado ao vivo)"""
    return _rpc(_cmd(OP_OPENDIR, path))


# ---- Rede ----
LOCAL_PORT = 69   # CRITICO: o aparelho SO responde se enviarmos da porta local 69
                  # (simetrico). Porta aleatoria = sem resposta. (confirmado ao vivo 03/07/2026)


def _sock():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", LOCAL_PORT))
    s.settimeout(TIMEOUT)
    return s


def _rpc(pkt, sock=None):
    """Envia um pacote e espera 1 resposta (com reenvio). Devolve bytes ou None."""
    s = sock or _sock()
    try:
        for _ in range(RESENDS):
            s.sendto(pkt, (DNC_IP, DNC_PORT))
            try:
                return s.recvfrom(2048)[0]
            except socket.timeout:
                continue
        return None
    finally:
        if sock is None:
            s.close()


# ---- Comandos (request unico -> resposta) ----
def get_status():        return _rpc(pkt_status())
def get_info():          return _rpc(pkt_info())
def stop_dnc():          return _rpc(pkt_stop())
def run_file(caminho):   return _rpc(pkt_run(caminho))
def send_message(txt):   return _rpc(pkt_msg(txt))
def new_folder(path):    return _rpc(pkt_newfolder(path))
def delete_file(path):   return _rpc(pkt_delfile(path))
def rename(old, new):    return _rpc(pkt_rename(old, new))
def _parse_entry(resp):
    """Entrada de diretorio: [00 09][4x00][nome \\0][seq:2][attrib:1][tam:4].
    attrib 0x10 = pasta, 0x20 = arquivo. (confirmado ao vivo 03/07)"""
    if not resp or opcode_de(resp) != 9:
        return None
    z = resp.index(0, 6)
    nome = resp[6:z].decode("ascii", "replace")
    tail = resp[z + 1:]
    seq = int.from_bytes(tail[0:2], "big") if len(tail) >= 2 else 0
    attrib = tail[2] if len(tail) >= 3 else 0
    tam = int.from_bytes(tail[3:7], "big") if len(tail) >= 7 else 0
    return {"nome": nome, "seq": seq, "pasta": bool(attrib & 0x10), "tamanho": tam}


def listar(path=""):
    """Lista um diretorio (path vazio = raiz '0:'). Devolve lista de dicts.
    CONFIRMADO ao vivo: OpenDir(path) + [op8 + indice(2 bytes) + path + 00];
    cada entrada volta como op9; fim quando seq == 0xFFFF (65535)."""
    itens = []
    s = _sock()
    try:
        for _ in range(RESENDS):                               # abre o diretorio (com retry)
            s.sendto(_cmd(OP_OPENDIR, path), (DNC_IP, DNC_PORT))
            try:
                s.recvfrom(2048)
                break
            except socket.timeout:
                continue
        for i in range(2000):
            req = _op(OP_READDIR) + struct.pack(">H", i) + path.encode("ascii", "replace") + b"\x00"
            r = None
            for _ in range(RESENDS):                           # retry por entrada (UDP perde pacote)
                s.sendto(req, (DNC_IP, DNC_PORT))
                try:
                    r = s.recvfrom(8192)[0]
                    break
                except socket.timeout:
                    continue
            if r is None:
                break
            e = _parse_entry(r)
            if e is None or e["seq"] == 0xFFFF:
                break
            itens.append(e)
    finally:
        s.close()
    return itens


# ---- Download (aparelho -> PC) -- CONFIRMADO AO VIVO (03/07/2026) ----
# Fluxo real (reverso do TFTP_DownloadFileTask):
#   abrir: op 54  ->  [00 36][nome][00]  ->  resp ack op 55
#   bloco N (1,2,3...): op 56 -> [00 38][N:4]  ->  resp op 57 [00 39][N:4][dados]
#   (512 bytes por bloco; ultimo bloco < 512 = fim). Tudo da porta local 69.
OP_DL_OPEN = 54

def baixar(nome_no_dnc, caminho_local=None):
    """Puxa um arquivo da caixinha pro PC. Devolve os bytes (e grava em
    caminho_local, se informado). Protocolo confirmado ao vivo."""
    s = _sock()
    dados = bytearray()
    try:
        if _rpc(_cmd(OP_DL_OPEN, nome_no_dnc), s) is None:
            raise RuntimeError("sem resposta ao abrir o download")
        blk = 1
        while True:
            r = _rpc(_op(OP_REQDATA) + struct.pack(">I", blk), s)
            if r is None:
                break
            payload = r[6:]           # [00 39][bloco:4][dados...]
            dados += payload
            if len(payload) < BLOCK:  # ultimo bloco
                break
            blk += 1
    finally:
        s.close()
    if caminho_local:
        with open(caminho_local, "wb") as f:
            f.write(dados)
    return bytes(dados)


def baixar_todos(destino_dir):
    """Baixa TODOS os arquivos da raiz da caixinha pra uma pasta.
    Devolve a lista de (nome, tamanho_baixado)."""
    os.makedirs(destino_dir, exist_ok=True)
    out = []
    for e in listar(""):
        if e["pasta"]:
            continue
        dados = baixar(e["nome"], os.path.join(destino_dir, e["nome"]))
        out.append((e["nome"], len(dados)))
    return out


# ---- Upload (PC -> aparelho) -- CONFIRMADO AO VIVO (03/07/2026) ----
# abrir:  op 2 (WRQ)   -> [00 02][nome][00]        -> ack op 4
# bloco N (1,2,3...):   DATA op 3 [00 03][N:4][dados<=512]  -> ack op 4
# fechar: op 52 [00 34]['0'][00]                   -> ack op 53
OP_UP_CLOSE = 52

def enviar(caminho_local, nome_no_dnc):
    """Manda um arquivo do PC pra caixinha (memoria do DNC). Confirmado ao vivo."""
    with open(caminho_local, "rb") as f:
        dados = f.read()
    s = _sock()
    try:
        if _rpc(_cmd(OP_WRQ, nome_no_dnc), s) is None:
            raise RuntimeError("sem resposta ao abrir o upload (WRQ)")
        blk = 1
        for i in range(0, len(dados), BLOCK):
            if _rpc(_op(OP_DATA) + struct.pack(">I", blk) + dados[i:i + BLOCK], s) is None:
                raise RuntimeError(f"sem ACK no bloco {blk}")
            blk += 1
        if len(dados) % BLOCK == 0:              # bloco final vazio quando multiplo exato
            _rpc(_op(OP_DATA) + struct.pack(">I", blk) + b"", s)
        _rpc(_cmd(OP_UP_CLOSE, "0"), s)          # fecha o arquivo no aparelho
    finally:
        s.close()


upload_file = enviar   # alias


if __name__ == "__main__":
    print(f"Sondando DNC em {DNC_IP}:{DNC_PORT} (UDP/TFTP)...")
    r = get_status()
    if r is None:
        print("Sem resposta. DNC ligado e na rede? IP certo no config.py?")
    else:
        print(f"Conectado! Status: {parse_status(r)!r}")
        print(f"Modelo:  {parse_info(get_info())!r}")
        print("Arquivos na raiz:")
        for e in listar(""):
            print(f"  {'PASTA' if e['pasta'] else 'arq  '} {e['tamanho']:>9}  {e['nome']}")
