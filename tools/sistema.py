"""
Estado del equipo
=================
    estado()             batería, procesador, memoria, disco, gráfica (NVIDIA) y tiempo encendido
    frase_estado()       lo mismo dicho en una frase («Te queda un 76 % de batería…»)
    brillo()             brillo de la pantalla del portátil (0-100), o None si no se puede leer
    poner_brillo(valor)  cambia el brillo (WMI de Windows; en monitores externos no suele funcionar)

Todo se lee en el propio ordenador. Con JARVIS_SIMULAR=1 el brillo no se cambia de verdad.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time

from tools.computer import Resultado, simulando

registro = logging.getLogger("jarvis.equipo")
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
PASO_BRILLO = 10


def _gpu() -> dict | None:
    """Uso de la gráfica NVIDIA (si la hay), con nvidia-smi."""
    programa = shutil.which("nvidia-smi")
    if not programa:
        return None
    try:
        salida = subprocess.run(
            [programa, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"], capture_output=True, timeout=5, creationflags=SIN_VENTANA,
        ).stdout.decode("utf-8", errors="replace").strip().splitlines()
        nombre, uso, usada, total, temperatura = [parte.strip() for parte in salida[0].split(",")]
        return {"nombre": nombre, "uso": int(float(uso)), "memoria_usada_mb": int(float(usada)),
                "memoria_total_mb": int(float(total)), "temperatura": int(float(temperatura))}
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def estado() -> dict:
    import psutil
    memoria = psutil.virtual_memory()
    unidad = (os.environ.get("SystemDrive") or "C:") + os.sep
    disco = psutil.disk_usage(unidad)
    datos = {
        "cpu": round(psutil.cpu_percent(interval=0.4)),
        "ram": round(memoria.percent), "ram_total_gb": round(memoria.total / 2 ** 30, 1),
        "disco": unidad.rstrip("\\/"), "disco_libre_gb": round(disco.free / 2 ** 30),
        "disco_uso": round(disco.percent), "encendido_horas": round((time.time() - psutil.boot_time()) / 3600, 1),
        "bateria": None, "gpu": _gpu(),
    }
    bateria = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
    if bateria is not None:
        restante = bateria.secsleft if isinstance(bateria.secsleft, int) and bateria.secsleft > 0 else None
        datos["bateria"] = {"porcentaje": round(bateria.percent), "enchufado": bool(bateria.power_plugged),
                            "segundos_restantes": restante}
    return datos


def _duracion(segundos: int) -> str:
    horas, minutos = divmod(int(segundos) // 60, 60)
    if horas and minutos:
        return f"{horas} h y {minutos} min"
    return f"{horas} h" if horas else f"{minutos} min"


def _coma(numero) -> str:
    """15.3 -> «15,3» (en español, coma decimal)."""
    return f"{numero:g}".replace(".", ",")


def frase_bateria(datos: dict) -> str:
    bateria = datos.get("bateria")
    if not bateria:
        return "Este ordenador no tiene batería: va enchufado a la corriente."
    texto = f"Te queda un {bateria['porcentaje']} % de batería"
    if bateria["enchufado"]:
        return texto + (", y está cargada." if bateria["porcentaje"] >= 99 else ", y se está cargando.")
    if bateria["segundos_restantes"]:
        return texto + f": unas {_duracion(bateria['segundos_restantes'])} de uso."
    return texto + "."


def frase_estado(datos: dict, foco: str = "") -> str:
    """El estado en una frase. `foco`: bateria, cpu, ram, disco, gpu o vacío (todo)."""
    if foco == "bateria":
        return frase_bateria(datos)
    partes = {
        "cpu": f"el procesador va al {datos['cpu']} %",
        "ram": f"la memoria está al {datos['ram']} % (de {_coma(datos['ram_total_gb'])} GB)",
        "disco": f"en el disco {datos['disco']} te quedan {datos['disco_libre_gb']} GB libres",
    }
    gpu = datos.get("gpu")
    if gpu:
        partes["gpu"] = (f"la gráfica va al {gpu['uso']} %, con {_coma(round(gpu['memoria_usada_mb'] / 1024, 1))} de "
                         f"{gpu['memoria_total_mb'] / 1024:.0f} GB de memoria y {gpu['temperatura']} °C")
    if foco in partes:
        texto = partes[foco]
        return texto[0].upper() + texto[1:] + "."
    if foco == "gpu":
        return "No encuentro ninguna gráfica NVIDIA que me diga cómo va."
    texto = "; ".join(partes.values())
    texto = texto[0].upper() + texto[1:] + "."
    if datos.get("bateria"):
        texto += " " + frase_bateria(datos)
    return texto


def _powershell(orden: str, tiempo: int = 15) -> str:
    return subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", orden],
                          capture_output=True, timeout=tiempo, creationflags=SIN_VENTANA,
                          ).stdout.decode("utf-8", errors="replace").strip()


def brillo() -> int | None:
    if os.name != "nt":
        return None
    try:
        salida = _powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness "
                             "-ErrorAction Stop | Select-Object -First 1).CurrentBrightness")
        return int(salida.splitlines()[-1]) if salida else None
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def objetivo_de_brillo(peticion: str, actual: int | None) -> int | None:
    """«+» / «-» (de 10 en 10), «max», «min» o un número; None si no se entiende."""
    peticion = (peticion or "").strip().lower()
    base = 50 if actual is None else actual
    if peticion in ("+", "subir"):
        return min(100, base + PASO_BRILLO)
    if peticion in ("-", "bajar"):
        return max(0, base - PASO_BRILLO)
    if peticion in ("max", "maximo"):
        return 100
    if peticion in ("min", "minimo"):
        return 0
    try:
        return max(0, min(100, int(float(peticion.rstrip("%")))))
    except ValueError:
        return None


def poner_brillo(valor: int) -> Resultado:
    valor = max(0, min(100, int(valor)))
    if simulando():
        registro.info("[SIMULACIÓN] brillo al %d %%", valor)
        return Resultado(True, f"Brillo al {valor} %.", {"brillo": valor})
    try:
        _powershell("Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop | "
                    f"Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={valor}}} "
                    "| Out-Null")
    except (OSError, subprocess.SubprocessError):
        return Resultado(False, "No he podido cambiar el brillo.")
    leido = brillo()
    if leido is None:
        return Resultado(False, "Esta pantalla no deja cambiar el brillo desde Windows (pasa con muchos monitores "
                                "externos): usa sus botones.")
    return Resultado(True, f"Brillo al {leido} %.", {"brillo": leido})
