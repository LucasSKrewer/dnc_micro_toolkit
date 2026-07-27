# Instalação no notebook — CNC FOCAS

Guia pra deixar o pacote rodando num notebook Windows novo.
**Copie a pasta inteira** `CNC_Transferencia_Programas` pro notebook e siga os passos.

---

## Conteúdo do pacote
| Arquivo | O que é |
|---|---|
| `config.py` | Configuração (IP da máquina, porta, pastas). **Edite este.** |
| `focas.py` | Wrapper da biblioteca FOCAS (a fundação). Não precisa mexer. |
| `foco_teste.py` | **Fase 3** — teste de conexão ("primeiro alô"). |
| `foco_transfer.py` | **Fase 4** — menu pra passar/receber via FOCAS. |
| `serial_adapter.py` | Adaptador DNC **serial** (Romi/Siemens). Requer `pip install pyserial`. |
| `rodar.bat` | Atalho pra rodar com o Python 32-bit. |
| `programas\` | Pasta onde os `.nc` são salvos/lidos (criada sozinha). |
| `INSTALACAO.md` | Este guia. |

---

## Passo 1 — Instalar o Python **32-bit**
> ⚠️ Tem que ser **32-bit**, porque a `Fwlib32.dll` é 32-bit. Python 64-bit **não carrega** a DLL.
> ⚠️ **NÃO** use o atalho `python` da Microsoft Store (o stub que dá problema). Instale o real.

1. Baixe em **https://www.python.org/downloads/windows/** →
   procure **"Windows installer (32-bit)"** (não o 64-bit / ARM64).
2. Instale marcando **"Add python.exe to PATH"**.
3. Confirme que é 32-bit (abra o `cmd`):
   ```
   py -3-32 -c "import struct; print(struct.calcsize('P')*8)"
   ```
   Tem que imprimir **32**. Se imprimir 64, você está pegando o Python errado.

## Passo 2 — Colocar a biblioteca FOCAS
1. Pegue a **`Fwlib32.dll`** (vem com a máquina/SDK da Fanuc) **e as DLLs companheiras**
   que vierem junto (ex.: `fwlibe1.dll`, `fwlib0DN.dll`, etc.).
2. Copie **todas** elas pra dentro da pasta `CNC_Transferencia_Programas\`
   (a mesma pasta dos scripts).

## Passo 3 — Configurar o IP
Abra `config.py` num editor e ajuste:
```python
CNC_IP   = "192.168.1.50"   # o IP que voce viu na maquina (Fase 1)
CNC_PORT = 8193             # confirme na tela [FOCAS2]
```

## Passo 4 — Conectar a máquina na rede
Notebook ligado na mesma rede/faixa da máquina e **`ping` respondendo**
(ver o passo-a-passo no arquivo de NOTAS, seção 12). Sem ping, nada funciona.

## Passo 5 — Rodar
Jeito fácil: dê **duplo clique em `rodar.bat`** e escolha a opção.

Ou pelo `cmd`, dentro da pasta:
```
py -3-32 foco_teste.py        (teste de conexao)
py -3-32 foco_transfer.py     (passar/receber)
```

Se o teste imprimir o tipo/série da máquina → **funcionou!** 🎉

---

## Problemas comuns
| Sintoma | Causa provável | Solução |
|---|---|---|
| `ERRO ao carregar Fwlib32.dll` | Python 64-bit, ou faltam DLLs | Use Python 32-bit; coloque todas as DLLs na pasta |
| `Falha ao conectar (codigo ...)` | Sem ping / IP errado / FOCAS não liberado | Confira ping, `config.py` e a opção FOCAS na máquina |
| Campos da máquina "bagunçados" | Alinhamento da struct `ODBSYS` | Me chama — ajuste fino de 5 min em `focas.py` |
| `cnc_dwnstart4 falhou` ao enviar | Máquina não está em EDIT / programa protegido | Põe a máquina em EDIT e desliga a proteção de programa |

> ⚠️ A **Fase 4 (transferir)** é a parte mais propensa a ajuste fino na máquina real.
> A **Fase 3 (conectar/ler)** é a que prova o conceito — comece por ela.
