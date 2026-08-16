"""
dnc_webdav.py - Expose the Micro DNC 2 box's memory as a NETWORK FOLDER (WebDAV).

A bridge: wsgidav (WebDAV server) plus a provider that speaks the reverse-engineered
protocol through dnc_tftp.py. Map the box in Windows Explorer as a drive
(http://<ip-of-this-pc>:8008/) and read/write programs directly - CIMCO, or any
other program, just sees an ordinary folder.

Run (in a venv with wsgidav + cheroot installed):
   python dnc_webdav.py

Map on Windows: Explorer -> "Map network drive" -> http://<ip>:8008/
(or:  net use Z: http://<ip>:8008/ )

NOTE: writes go through the same verified upload as everywhere else - the file
is read back off the device and compared. A silent partial write into a CNC
program is not an acceptable failure mode, even from Explorer.
"""
import io
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import dnc_tftp as dnc

from wsgidav.dav_error import DAVError, HTTP_FORBIDDEN
from wsgidav.dav_provider import DAVCollection, DAVNonCollection, DAVProvider

PORT = 8008   # change to 80 for a fixed service (needs admin/root)


def _qualify(devpath, name):
    """Qualified name on the device: root='FILE', subfolder='0:program\\FILE'."""
    return f"{devpath}\\{name}" if devpath else name


class _WriteBuf(io.BytesIO):
    """Capture the bytes BEFORE wsgidav closes the buffer (it closes before end_write)."""
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
        return io.BytesIO(dnc.download(self.qualified))

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
                dnc.upload(tmp, self.qualified, verify=config.VERIFY_UPLOAD)
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
        self.devpath = devpath  # "" = root 0:, or "0:program"

    def _items(self):
        return [e for e in dnc.list_dir(self.devpath) if e["name"] not in (".", "..")]

    def get_member_names(self):
        return [e["name"] for e in self._items()]

    def get_member(self, name):
        for e in self._items():
            if e["name"] == name:
                child = self.path.rstrip("/") + "/" + name
                if e["is_dir"]:
                    dp = ("0:" + name) if self.devpath == "" else (self.devpath + "\\" + name)
                    return DncCollection(child + "/", self.environ, dp)
                return DncFile(child, self.environ, _qualify(self.devpath, name), e["size"])
        return None

    def get_last_modified(self):
        return time.time()

    def create_empty_resource(self, name):
        child = self.path.rstrip("/") + "/" + name
        return DncFile(child, self.environ, _qualify(self.devpath, name), 0)

    def create_collection(self, name):
        raise DAVError(HTTP_FORBIDDEN)  # creating folders on the device: out of scope

    def support_recursive_delete(self):
        return False

    def delete(self):
        raise DAVError(HTTP_FORBIDDEN)  # never delete the device's system folders


class DncProvider(DAVProvider):
    def get_resource_inst(self, path, environ):
        try:
            self._count_get_resource_inst += 1
        except Exception:
            pass
        p = path.strip("/")
        if p == "":
            return DncCollection("/", environ, "")
        segments = p.split("/")
        if len(segments) == 1:
            return DncCollection("/", environ, "").get_member(segments[0])
        if len(segments) == 2:
            parent = DncCollection("/" + segments[0] + "/", environ, "0:" + segments[0])
            return parent.get_member(segments[1])
        return None


def main():
    from cheroot import wsgi
    from wsgidav.wsgidav_app import WsgiDAVApp

    dav_config = {
        "host": "0.0.0.0",
        "port": PORT,
        "provider_mapping": {"/": DncProvider()},
        "simple_dc": {"user_mapping": {"*": True}},   # anonymous (trusted LAN)
        "http_authenticator": {"accept_basic": True, "accept_digest": False,
                               "default_to_digest": False},
        "lock_storage": True,     # LOCK/UNLOCK (Windows needs it to write)
        "property_manager": True,
        "dir_browser": {"enable": True},   # HTML listing if opened in a browser
        "verbose": 2,
        "logging": {"enable_loggers": []},
    }
    app = WsgiDAVApp(dav_config)
    server = wsgi.Server(("0.0.0.0", PORT), app)
    print(f"WebDAV bridge to the DNC box ({dnc.DNC_IP}) on  http://<ip-of-this-pc>:{PORT}/")
    print(f"Map it: Explorer -> Map network drive -> http://<ip>:{PORT}/  "
          f"(or: net use Z: http://<ip>:{PORT}/)")
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()


if __name__ == "__main__":
    main()
