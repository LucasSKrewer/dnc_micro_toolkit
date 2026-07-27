# DNC Micro Toolkit

Ferramentas em Python para transferir programas G-code (enviar/receber) entre
um PC/servidor e máquinas CNC, por três caminhos diferentes — sem depender de
software proprietário tipo CIMCO/DNC comercial.

## O que tem aqui

| Caminho | Arquivo(s) | Quando usar |
|---|---|---|
| **FOCAS (Fanuc)** | `focas.py`, `foco_teste.py`, `foco_transfer.py` | Controles Fanuc com Ethernet/FOCAS liberado (30i/31i/32i, 0i com a opção). Transferência via rede, sem hardware extra. |
| **Serial (RS-232)** | `serial_adapter.py` | Qualquer controle não-Fanuc (ou Fanuc antigo sem Ethernet) que só tenha porta serial — Romi, Siemens Sinumerik, etc. Funciona direto ou como agente num Raspberry Pi ligado por USB-FTDI. |
| **Caixinha DNC de rede** | `dnc_tftp.py`, `dnc_webdav.py` | Cliente para uma caixinha DNC WiFi (tipo Micro DNC 2), falando o protocolo TFTP customizado dela diretamente — sem o software Windows que normalmente acompanha o aparelho. O `dnc_webdav.py` expõe a caixinha como pasta de rede (WebDAV). |
| **Tela web** | `dnc_web.py` | Interface simples (Flask) pra rodar num Raspberry Pi: botão "Enviar" + recebimento automático em segundo plano. Pensada pra ficar ao lado da máquina, acessível do celular/PC de qualquer lugar da rede. |

Tudo configurado num único lugar: `config.py`.

## Por que existe

Caixinhas DNC comerciais resolvem a transferência mas são um beco sem saída —
protocolo fechado, não integra com nada. Este projeto nasceu de duas frentes:

1. **FOCAS não é só transferência** — a mesma biblioteca que passa programa
   também lê status/contadores da máquina, então usar FOCAS na transferência
   já deixa telemetria futura pronta, de graça.
2. **A caixinha comprada virou o gabarito** — o protocolo dela (TFTP sobre
   UDP/69, mas com opcodes e formato de pacote próprios, nada a ver com TFTP
   padrão) foi mapeado por engenharia reversa do instalador oficial (captura
   de bytes via reflection), documentado byte a byte em `dnc_tftp.py`. Isso
   abriu a porta pra rodar a mesma lógica num Raspberry Pi com um adaptador
   USB-FTDI — a "caixinha própria", bem mais barata por máquina.

O `serial_adapter.py` foi inspirado (não copiado) no projeto OpenDNC, lógica
própria pra evitar o copyleft GPL e manter o estilo do resto do projeto.

## Instalação

Veja [`INSTALACAO.md`](INSTALACAO.md) para o caminho FOCAS (Windows,
Python 32-bit + `Fwlib32.dll` da Fanuc — não incluída aqui, vem com a máquina
ou o SDK oficial).

Para o caminho serial num Raspberry Pi:
```bash
sudo apt install -y python3-pip
pip3 install pyserial
python3 serial_adapter.py
```

Dependências por script — veja [`requirements.txt`](requirements.txt).

## Protocolo da caixinha DNC (resumo técnico)

- Transporte: UDP porta 69, blocos de 512 bytes, timeout 2s, até 10 reenvios.
- Opcode de 2 bytes big-endian no início de todo pacote (não é o TFTP padrão
  da RFC 1350 — reaproveita a porta 69, mas o formato é proprietário).
- Comandos cobertos: status, info, listar diretório (com subpastas), baixar,
  enviar, apagar, renomear, criar pasta, mandar mensagem pro operador.
- Detalhes completos e opcodes comentados em `dnc_tftp.py`.

## Limitações conhecidas

- O protocolo controla arquivo/transferência, não a navegação da telinha do
  aparelho.
- Nomes de arquivo com caracteres especiais podem ser alterados pelo
  firmware do aparelho (limitação do FatFs dele, não do cliente).
- As funções FOCAS de transferência (`cnc_upstart4`/`cnc_dwnstart4` etc.)
  variam de formato conforme a versão do controle — pontos marcados com
  `# AJUSTE?` em `foco_transfer.py`.

## Licença

Ver [`LICENSE`](LICENSE).
