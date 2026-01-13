# mockups/forms.py → Actualizado para mostrar "Gas de 15 kg - $32.800"

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
        label="Sector / Población",
    )

    class Meta:
        model = Pedido
        fields = [
            "balon",
            "cantidad_balon",
            "metodo_pago",
            "sector",
            "direccion_entrega",
        ]
        widgets = {
            "balon": forms.Select(attrs={"class": "form-select"}),
            "cantidad_balon": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "value": 1}
            ),
            "metodo_pago": forms.Select(attrs={"class": "form-select"}),
            "direccion_entrega": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Calle, número, casa esquina, depto, referencia clara...",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        # Solo balones activos
        balones = TipoBalon.objects.filter(activo=True).order_by("peso_neto_gas")
        choices = [("", "— Seleccionar balón —")]
        for b in balones:
            precio_txt = f"${int(b.precio):,}".replace(",", ".")
            choices.append((b.id, f"{b.nombre} - {precio_txt}"))
        self.fields["balon"].choices = choices

        # Bodeguero no ve sector ni dirección
        if self.user and self.user.rol == "bodeguero":
            self.fields["sector"].widget = forms.HiddenInput()
            self.fields["direccion_entrega"].widget = forms.HiddenInput()
            self.fields["sector"].required = False
            self.fields["direccion_entrega"].required = False

    def clean(self):
        cleaned_data = super().clean()
        balon = cleaned_data.get("balon")
        cantidad = cleaned_data.get("cantidad_balon", 1)

        if balon:
            cleaned_data["monto"] = balon.precio * cantidad

        if self.user and self.user.rol == "telefonista":
            if not cleaned_data.get("sector"):
                self.add_error("sector", "Debes seleccionar un sector.")
            if not cleaned_data.get("direccion_entrega"):
                self.add_error("direccion_entrega", "La dirección es obligatoria.")

        return cleaned_data
