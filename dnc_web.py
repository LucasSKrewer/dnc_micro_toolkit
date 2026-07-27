"""
dnc_web.py - Tela web do DNC via Raspberry Pi.
====================================================================
Substitui o menu de texto do serial_adapter.py por uma pagina acessada
de qualquer lugar da rede (celular, PC, etc.) - o Pi fica sempre ligado
na maquina, ninguem precisa estar fisicamente perto dele.

Automacao decidida em 15/07/2026:
- ENVIAR: sempre manda o arquivo com nome fixo (config.ARQUIVO_FIXO_ENVIO,
  hoje "1") de dentro de PASTA_PROGRAMAS. Quem troca esse arquivo antes de
  cada peca nova e o PCP/Engenharia - o operador so aperta "Enviar". Igual
  a aparelhos DNC comerciais que os preparadores ja costumam usar.
- RECEBER: totalmente automatico, roda em thread de fundo o tempo todo -
  assim que a maquina manda dado (operador aciona PUNCH/OUTPUT), o Pi
  detecta e salva sozinho, com nome+data/hora. Nao tem botao de receber.

Rodar: python3 dnc_web.py  (ou como servico systemd)
"""

import os
import threading
import time
import datetime

from flask import Flask, render_template_string, redirect, url_for, flash

import config
from serial_adapter import abrir, _embrulhar

app = Flask(__name__)
app.secret_key = os.environ.get("DNC_WEB_SECRET", "troque-esta-chave")

_port_lock = threading.Lock()
_status = {
    "enviado_nome": None, "enviado_hora": None, "enviado_bytes": None,
    "recebido_nome": None, "recebido_hora": None, "recebido_bytes": None,
}


def _caminho_fila():
    return os.path.join(config.PASTA_PROGRAMAS, config.ARQUIVO_FIXO_ENVIO)


def _enviar_fixo():
    caminho = _caminho_fila()
    if not os.path.exists(caminho):
        return False, f'Nenhum programa na fila (arquivo "{config.ARQUIVO_FIXO_ENVIO}" nao encontrado em programas/).'

    with open(caminho, "rb") as f:
        dados = _embrulhar(f.read())

    with _port_lock:
        ser = abrir()
        try:
            ser.write(dados)
            ser.flush()
        finally:
            ser.close()

    _status["enviado_nome"] = config.ARQUIVO_FIXO_ENVIO
    _status["enviado_hora"] = datetime.datetime.now().strftime("%d/%m %H:%M:%S")
    _status["enviado_bytes"] = len(dados)
    return True, f"Enviado ({len(dados)} bytes)."


def _loop_recebimento():
    """Thread de fundo: fica escutando a serial e salva sozinho quando chega programa."""
    os.makedirs(config.PASTA_PROGRAMAS, exist_ok=True)
    while True:
        if not _port_lock.acquire(timeout=0.5):
            continue
        buf = bytearray()
        ser = None
        falhou = False
        try:
            ser = abrir()
            percent = 0
            ocioso = 0
            recebendo = False
            while True:
                chunk = ser.read(256)
                if chunk:
                    if not recebendo and b"%" in chunk:
                        recebendo = True
                    if recebendo:
                        buf.extend(chunk)
                        percent += chunk.count(b"%")
                    ocioso = 0
                    if recebendo and percent >= 2:
                        break
                else:
                    ocioso += 1
                    if recebendo and ocioso >= 3:
                        break
                    if not recebendo and ocioso >= 1:
                        break
        except Exception:
            # Qualquer falha (FTDI desconectado, porta caiu no meio da leitura,
            # etc.) descarta o que tinha e tenta de novo depois - a thread NUNCA
            # pode morrer, senao o recebimento automatico para pra sempre.
            falhou = True
        finally:
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
            _port_lock.release()

        if falhou:
            buf = bytearray()
            time.sleep(2)

        if buf:
            nome = datetime.datetime.now().strftime(f"{config.MAQUINA_NOME}_%Y%m%d_%H%M%S.nc")
            destino = os.path.join(config.PASTA_PROGRAMAS, nome)
            with open(destino, "wb") as f:
                f.write(bytes(buf))
            _status["recebido_nome"] = nome
            _status["recebido_hora"] = datetime.datetime.now().strftime("%d/%m %H:%M:%S")
            _status["recebido_bytes"] = len(buf)

        time.sleep(0.2)


PAGINA = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>DNC Micro Toolkit</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 480px; margin: 2rem auto; padding: 0 1rem;
         background: #F5F4EF; color: #1a1a1a; }
  @media (prefers-color-scheme: dark) { body { background: #1c1c1a; color: #eee; } }
  h1 { font-size: 1.3rem; color: #1B5E20; }
  .card { background: #fff; border-radius: 12px; padding: 1rem 1.2rem; margin-bottom: 1rem;
          border: 1px solid #ddd; }
  @media (prefers-color-scheme: dark) { .card { background: #2a2a27; border-color: #444; } }
  .rotulo { font-size: 0.8rem; color: #777; margin: 0; }
  .valor { font-size: 1rem; margin: 0.2rem 0 0; }
  button { width: 100%; padding: 0.9rem; font-size: 1.05rem; border-radius: 10px; border: none;
           background: #E8590C; color: #fff; font-weight: 600; cursor: pointer; }
  button:active { opacity: 0.85; }
  .msg { padding: 0.7rem 1rem; border-radius: 8px; margin-bottom: 1rem; font-size: 0.9rem; }
  .ok { background: #DCEEDC; color: #1B5E20; }
  .erro { background: #F8D7DA; color: #7a1f1f; }
</style>
</head>
<body>
  <h1>DNC Micro Toolkit - {{ hostname }}</h1>

  {% with msgs = get_flashed_messages(with_categories=true) %}
    {% for cat, msg in msgs %}
      <div class="msg {{ 'ok' if cat=='ok' else 'erro' }}">{{ msg }}</div>
    {% endfor %}
  {% endwith %}

  <div class="card">
    <p class="rotulo">Programa na fila (arquivo "{{ arquivo_fixo }}")</p>
    <p class="valor">{{ fila_status }}</p>
  </div>

  <form method="post" action="{{ url_for('enviar') }}">
    <button type="submit">Enviar o proximo programa</button>
  </form>

  <div class="card" style="margin-top:1.5rem">
    <p class="rotulo">Ultimo enviado</p>
    <p class="valor">{{ status.enviado_nome or '-' }}{% if status.enviado_hora %} - {{ status.enviado_hora }}{% endif %}</p>
  </div>

  <div class="card">
    <p class="rotulo">Ultimo recebido (automatico)</p>
    <p class="valor">{{ status.recebido_nome or '-' }}{% if status.recebido_hora %} - {{ status.recebido_hora }}{% endif %}</p>
  </div>
</body>
</html>
"""


@app.route("/")
def index():
    caminho = _caminho_fila()
    if os.path.exists(caminho):
        tam = os.path.getsize(caminho)
        fila_status = f'"{config.ARQUIVO_FIXO_ENVIO}" pronto ({tam} bytes)'
    else:
        fila_status = "vazio - PCP precisa colocar o proximo programa"
    return render_template_string(
        PAGINA,
        hostname="Raspberry Pi",
        arquivo_fixo=config.ARQUIVO_FIXO_ENVIO,
        fila_status=fila_status,
        status=_status,
    )


@app.route("/enviar", methods=["POST"])
def enviar():
    try:
        ok, msg = _enviar_fixo()
    except Exception as e:
        ok, msg = False, f"Erro: {e}"
    flash(msg, "ok" if ok else "erro")
    return redirect(url_for("index"))


if __name__ == "__main__":
    t = threading.Thread(target=_loop_recebimento, daemon=True)
    t.start()
    app.run(host=config.WEB_HOST, port=config.WEB_PORT)
