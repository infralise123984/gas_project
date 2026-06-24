"""Consultas de catálogos (balones, etc.)."""

from django.db.models import Case, IntegerField, Value, When

from mockups.models import TipoBalon


def get_balones_activos_ordenados(solo_activos=True):
    """Balones en el mismo orden usado por los sobres. Solo activos por defecto."""
    qs = TipoBalon.objects.filter(activo=True) if solo_activos else TipoBalon.objects.all()
    return qs.annotate(
        tipo_orden=Case(
            When(tipo_gas='normal', then=Value(0)),
            When(tipo_gas='catalitico', then=Value(1)),
            When(tipo_gas='aluminio', then=Value(2)),
            default=Value(3),
            output_field=IntegerField(),
        )
    ).order_by('tipo_orden', '-peso_neto_gas')