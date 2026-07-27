"""
dnc_webdav.py - Expõe a memória da caixinha Micro DNC 2 como uma PASTA DE REDE (WebDAV).

Ponte: wsgidav (servidor WebDAV) + um provider que fala o protocolo TFTP revertido
(via dnc_tftp.py). Assim você mapeia a caixinha no Windows Explorer como um drive
(http://<ip-deste-pc>:8008/) e lê/escreve os programas direto — o CIMCO ou qualquer
programa enxerga como pasta comum.

Rodar (com um venv que tenha wsgidav + cheroot instalados):
   python dnc_webdav.py

Mapear no Windows: Explorer -> "Mapear unidade de rede" -> http://<ip>:8008/
(ou:  net use Z: http://<ip>:8008/ )
"""
import io
import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dnc_tftp as dnc

from wsgidav.dav_provider import DAVProvider, DAVCollection, DAVNonCollection
from wsgidav.dav_error import DAVError, HTTP_FORBIDDEN

PORT = 8008   # troque para 80 se quiser rodar como servico fixo (precisa de admin/root)


def _qual(devpath, nome):
    """Nome qualificado do arquivo no aparelho: raiz='ARQ', subpasta='0:program\\ARQ'."""
    return f"{devpath}\\{nome}" if devpath else nome


class _WriteBuf(io.BytesIO):
    """Captura os bytes ANTES do wsgidav fechar o buffer (ele fecha antes do end_write)."""
    def __init__(self, dnc_file):
        super().__init__()
        self._file = dnc_file

    def close(self):
        try:
            self._file._data = self.getvalue()
        finally:
            super().close()


class DncFile(DAVNonCollection):
    def __init__(self, path, environ, qualified, size):
        super().__init__(path, environ)
        self.qualified = qualified
        self.size = size
        self._data = None

    def get_content_length(self):
        return self.size

    def get_content_type(self):
        return "application/octet-stream"

    def get_last_modified(self):
        return time.time()

    def support_etag(self):
        return False

    def get_etag(self):
        return None

    def support_ranges(self):
        return False

    def get_content(self):
        return io.BytesIO(dnc.baixar(self.qualified))

    def begin_write(self, *, content_type=None):
        self._data = None
        return _WriteBuf(self)

    def end_write(self, *, with_errors):
        if not with_errors and self._data is not None:
            fd, tmp = tempfile.mkstemp(suffix=".nc")
            os.close(fd)
            try:
                with open(tmp, "wb") as f:
                    f.write(self._data)
                dnc.enviar(tmp, self.qualified)
            finally:
                os.remove(tmp)
        self._data = None

    def delete(self):
        dnc.delete_file(self.qualified)
        try:
            self.remove_all_properties()
        except Exception:
            pass
        try:
            self.remove_all_locks(False)
        except Exception:
            pass


class DncCollection(DAVCollection):
    def __init__(self, path, environ, devpath):
        super().__init__(path, environ)
        self.devpath = devpath  # "" = raiz 0:, ou "0:program"

    def _itens(self):
        return [e for e in dnc.listar(self.devpath) if e["nome"] not in (".", "..")]

    def get_member_names(self):
        return [e["nome"] for e in self._itens()]

    def get_member(self, name):
        for e in self._itens():
            if e["nome"] == name:
                child = self.path.rstrip("/") + "/" + name
                if e["pasta"]:
                    dp = ("0:" + name) if self.devpath == "" else (self.devpath + "\\" + name)
                    return DncCollection(child + "/", self.environ, dp)
                return DncFile(child, self.environ, _qual(self.devpath, name), e["tamanho"])
        return None

    def get_last_modified(self):
        return time.time()

    def create_empty_resource(self, name):
        child = self.path.rstrip("/") + "/" + name
        return DncFile(child, self.environ, _qual(self.devpath, name), 0)

    def create_collection(self, name):
        raise DAVError(HTTP_FORBIDDEN)  # criar pasta no aparelho: fora do escopo do protótipo

    def support_recursive_delete(self):
        return False

    def delete(self):
        raise DAVError(HTTP_FORBIDDEN)  # não apagar pastas do sistema do aparelho


class DncProvider(DAVProvider):
    def get_resource_inst(self, path, environ):
        try:
            self._count_get_resource_inst += 1
        except Exception:
            pass
        p = path.strip("/")
        if p == "":
            return DncCollection("/", environ, "")
        segs = p.split("/")
        if len(segs) == 1:
            return DncCollection("/", environ, "").get_member(segs[0])
        if len(segs) == 2:
            pai = DncCollection("/" + segs[0] + "/", environ, "0:" + segs[0])
            return pai.get_member(segs[1])
        return None


def main():
    from wsgidav.wsgidav_app import WsgiDAVApp
    from cheroot import wsgi

    config = {
        "host": "0.0.0.0",
        "port": PORT,
        "provider_mapping": {"/": DncProvider()},
        "simple_dc": {"user_mapping": {"*": True}},   # anônimo (LAN confiável)
        "http_authenticator": {"accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "lock_storage": True,     # LOCK/UNLOCK (o Windows precisa p/ escrever)
        "property_manager": True,
        "dir_browser": {"enable": True},   # listagem HTML se abrir no navegador
        "verbose": 2,
        "logging": {"enable_loggers": []},
    }
    app = WsgiDAVApp(config)
    server = wsgi.Server(("0.0.0.0", PORT), app)
    print(f"WebDAV da caixinha ({dnc.DNC_IP}) em  http://<ip-deste-pc>:{PORT}/")
    print(f"Mapear: Explorer -> Mapear unidade de rede -> http://<ip>:{PORT}/  (ou net use Z: http://<ip>:{PORT}/)")
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
