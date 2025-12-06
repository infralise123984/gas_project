# mockups/forms.py
# Formulario definitivo para GasFácil - Rancagua (sin Cliente, con TipoBalon)

from django import forms
from .models import Pedido, TipoBalon


SECTORES = [
    ("", "— Seleccionar sector —"),
    ("Población Dintrans", "Población Dintrans"),
    ("Machalí Alto", "Machalí Alto"),
    ("Gultro", "Gultro"),
    ("Villa Los Tilos", "Villa Los Tilos"),
    ("Centro Rancagua", "Centro Rancagua"),
    ("Baquedano", "Baquedano"),
    ("La Granja", "La Granja"),
    ("Rancagua Norte", "Rancagua Norte"),
    ("Villa Teniente", "Villa Teniente"),
    ("Requínoa", "Requínoa"),
    ("Graneros", "Graneros"),
    ("Mostazal", "Mostazal"),
    ("Codegua", "Codegua"),
    ("Otro", "Otro (especificar en dirección)"),
]

class PedidoForm(forms.ModelForm):
    sector = forms.ChoiceField(
        choices=SECTORES,
        widget=forms.Select(attrs={"class": "form-select"}),
        required=False,
        label="Sector / Población"
    )

    class Meta:
        model = Pedido
        fields = ["balon", "cantidad_balon", "metodo_pago", "sector", "direccion_entrega"]
        widgets = {
            "balon": forms.Select(attrs={"class": "form-select"}),
            "cantidad_balon": forms.NumberInput(attrs={
                "class": "form-control", "min": 1, "value": 1, "style": "width: 100px;"
            }),
            "metodo_pago": forms.Select(attrs={"class": "form-select"}),
            "direccion_entrega": forms.Textarea(attrs={
                "class": "form-control", "rows": 3,
                "placeholder": "Calle, número, casa esquina, depto, referencia clara..."
            }),
        }
        labels = {
            "balon": "Tipo de balón",
            "cantidad_balon": "Cantidad",
            "metodo_pago": "Método de pago",
            "direccion_entrega": "Dirección completa o referencia",
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)  # Inyectamos el usuario
        super().__init__(*args, **kwargs)

        # Cargar solo balones activos con precio
        balones = TipoBalon.objects.filter(activo=True).order_by("tamaño")
        choices = [("", "— Seleccionar balón —")]
        for b in balones:
            precio_txt = f"${int(b.precio):,}".replace(",", ".")
            choices.append((b.id, f"{b.tamaño} - {precio_txt}"))
        self.fields["balon"].choices = choices
        self.fields["balon"].widget.attrs.update({"class": "form-select"})

        # Si es bodeguero: ocultar dirección y sector
        if self.user and self.user.rol == "bodeguero":
            self.fields["sector"].widget = forms.HiddenInput()
            self.fields["direccion_entrega"].widget = forms.HiddenInput()
            self.fields["sector"].required = False
            self.fields["direccion_entrega"].required = False

    def clean(self):
        cleaned_data = super().clean()
        balon = cleaned_data.get("balon")
        cantidad = cleaned_data.get("cantidad_balon", 1)

        if balon and hasattr(balon, "precio"):
            cleaned_data["monto"] = balon.precio * cantidad

        # Validación solo para telefonistas
        if self.user and self.user.rol == "telefonista":
            if not cleaned_data.get("sector") or cleaned_data.get("sector") == "":
                self.add_error("sector", "Debes seleccionar un sector.")
            if not cleaned_data.get("direccion_entrega"):
                self.add_error("direccion_entrega", "La dirección es obligatoria.")

        return cleaned_data