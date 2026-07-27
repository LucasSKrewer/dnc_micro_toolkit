"""
foco_teste.py  -  FASE 3: primeiro "alo" FOCAS.
Conecta na maquina, le a identidade dela e desconecta. Nao transfere nada.

Rodar (cmd, na pasta do projeto):  py -3-32 foco_teste.py
"""

import focas


def main():
    print("Conectando em", focas.config.CNC_IP, "...")
    h = focas.conectar()
    print("Conectado! (handle =", h.value, ")")

    try:
        s = focas.sysinfo(h)
        d = lambda b: b.decode(errors="ignore").strip()
        print("------- MAQUINA -------")
        print("Tipo CNC :", d(s.cnc_type))
        print("M/T      :", d(s.mt_type), "(M=fresa/centro, T=torno)")
        print("Serie    :", d(s.series))
        print("Versao   :", d(s.version))
        print("Eixos    :", d(s.axes))
        print("-----------------------")
    finally:
        focas.desconectar(h)
        print("Desconectado.")


if __name__ == "__main__":
    main()
