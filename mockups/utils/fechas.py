"""Utilidades de fechas y zonas horarias (Chile / UTC)."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

TZ_CHILE = ZoneInfo('America/Santiago')
TZ_UTC = ZoneInfo('UTC')

MESES_ES_CAMIONERO = (
    '', 'Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
    'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre',
)

# Rango aceptado para ?mes= / ?fecha=. Sin cota de año, `calendar.monthrange()`
# levanta ValueError y eso terminaba en un 500 con `?mes=999999-01`, tanto en
# /entregas/historial/ de la web como en /api/v1/historial/. La empresa no tiene
# datos fuera de este rango.
ANIO_MINIMO_HISTORIAL = 2000
ANIO_MAXIMO_HISTORIAL = 2100


def now_chile():
    """Hora actual en America/Santiago."""
    return timezone.now().astimezone(TZ_CHILE)


def today_chile():
    """Fecha actual en Chile (evita desfases por UTC)."""
    return now_chile().date()


def rango_dia_chile(fecha_dia):
    """Rango [inicio, fin) del día en zona horaria Chile."""
    inicio = timezone.make_aware(datetime.combine(fecha_dia, time.min), TZ_CHILE)
    fin = inicio + timedelta(days=1)
    return inicio, fin


def get_rango_utc_para_fecha(fecha_objetivo):
    """Inicio y fin del día en UTC para una fecha calendario Chile."""
    inicio_dia = timezone.make_aware(
        datetime.combine(fecha_objetivo, time.min), TZ_CHILE
    ).astimezone(TZ_UTC)
    fin_dia = timezone.make_aware(
        datetime.combine(fecha_objetivo, time.max), TZ_CHILE
    ).astimezone(TZ_UTC)
    return inicio_dia, fin_dia


def parse_mes_param(request, default_hoy=True):
    """Interpreta ?mes=YYYY-MM y devuelve (anio, mes) válidos."""
    hoy = today_chile()
    mes_param = request.GET.get('mes')
    if mes_param:
        try:
            partes = mes_param.split('-')
            anio = int(partes[0])
            mes = int(partes[1])
            if mes < 1 or mes > 12:
                raise ValueError
            if not ANIO_MINIMO_HISTORIAL <= anio <= ANIO_MAXIMO_HISTORIAL:
                raise ValueError
            return anio, mes
        except (ValueError, IndexError):
            pass
    if default_hoy:
        return hoy.year, hoy.month
    return None, None


def navegacion_mes(anio, mes):
    """Mes anterior y siguiente para navegación del historial."""
    if mes == 1:
        mes_anterior = (anio - 1, 12)
    else:
        mes_anterior = (anio, mes - 1)
    if mes == 12:
        mes_siguiente = (anio + 1, 1)
    else:
        mes_siguiente = (anio, mes + 1)
    return mes_anterior, mes_siguiente


def parse_fecha_rango(fechas_str):
    """
    Convierte un string de fecha o rango en objetos date.
    Retorna: (fecha_inicio, fecha_fin, display_str, desde_str, hasta_str)
    """
    if not fechas_str or not fechas_str.strip():
        return None, None, "", "", ""

    fechas_clean = fechas_str.replace("+", " ").strip()

    separador = None
    for sep in [" to ", " a ", " - ", ",", " -", "- "]:
        if sep in fechas_clean:
            separador = sep
            break

    try:
        if separador:
            desde_str, hasta_str = fechas_clean.split(separador, 1)
            desde_str = desde_str.strip()
            hasta_str = hasta_str.strip()
            fecha_inicio = datetime.strptime(desde_str, "%Y-%m-%d").date()
            fecha_fin = datetime.strptime(hasta_str, "%Y-%m-%d").date()
            fecha_display = f"{fecha_inicio.strftime('%d/%m/%Y')} al {fecha_fin.strftime('%d/%m/%Y')}"
            return fecha_inicio, fecha_fin, fecha_display, desde_str, hasta_str

        fecha_inicio = datetime.strptime(fechas_clean, "%Y-%m-%d").date()
        fecha_fin = fecha_inicio
        fecha_display = fecha_inicio.strftime('%d/%m/%Y')
        return fecha_inicio, fecha_fin, fecha_display, fechas_clean, fechas_clean

    except (ValueError, IndexError, AttributeError):
        return None, None, "", "", ""
