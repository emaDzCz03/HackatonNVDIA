"""Sirve api_auditoria.py desde una VM de Colab con un túnel de Cloudflare.

Requiere el proyecto completo en /content/proyecto (incluidos models/best.pt,
.env y serviceAccountKey.json, que NO están en git). Ejecutar en la VM:

    python ml/vision/colab/servir_api.py

Imprime una URL https://*.trycloudflare.com: ábrela en el teléfono. El túnel
rápido no requiere cuenta, pero la URL es pública mientras la VM esté viva;
no la compartas fuera del equipo.
"""

import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROYECTO = Path(__file__).resolve().parents[3]
CLOUDFLARED = Path("/content/cloudflared")
URL_BINARIO = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"


def main() -> None:
    if not CLOUDFLARED.exists():
        urllib.request.urlretrieve(URL_BINARIO, CLOUDFLARED)
        CLOUDFLARED.chmod(0o755)

    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(PROYECTO / "requirements.txt")], check=True)

    entorno = dict(os.environ, AUDIT_LOCAL_HTTP="1")
    api_log = open("/content/api.log", "w")
    subprocess.Popen([sys.executable, "api_auditoria.py"], cwd=PROYECTO, env=entorno, stdout=api_log, stderr=subprocess.STDOUT)

    tunel_log = Path("/content/tunel.log")
    subprocess.Popen([str(CLOUDFLARED), "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8000"],
                     stdout=open(tunel_log, "w"), stderr=subprocess.STDOUT)

    for _ in range(60):
        time.sleep(2)
        coincidencia = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", tunel_log.read_text(errors="replace"))
        if coincidencia:
            print("API pública:", coincidencia.group(0), flush=True)
            return
    print("No apareció la URL del túnel; revisa /content/tunel.log y /content/api.log")


if __name__ == "__main__":
    main()
